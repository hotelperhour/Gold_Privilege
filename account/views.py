from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.sites.shortcuts import get_current_site
from django.contrib import messages
from django.views.generic import CreateView, TemplateView
from django.urls import reverse_lazy
from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.contrib.auth.views import (
    PasswordResetView, 
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetCompleteView
)
from django.templatetags.static import static

from .forms import (
    UserRegistrationForm,
    PartnerRegistrationForm,
    CustomLoginForm,
    CustomPasswordResetForm,
    CustomSetPasswordForm,
    PartnerProfileUpdateForm,
    UserProfileUpdateForm
)
from .models import CustomUser, PartnerProfile
from .permissions import (
    IsSubscriberUserMixin,
    IsPartnerMixin,
    IsApprovedPartnerMixin,
    
)
from subscriptions.models import Subscription
from bookings.models import Booking, BookingStatus
from subscriptions.utils import get_subscription_state, get_all_feature_usage, get_or_create_feature_usage
from django.views.generic import UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse
from datetime import datetime, date
from django.utils import timezone
from wallet.models import ReferralRecord
import logging
logger = logging.getLogger(__name__)

class UnifiedRegistrationView(TemplateView):
    """Unified registration view for both customers and partners"""
    template_name = 'account/register.html'

    def get_context_data(self, **kwargs):
        """Always provide both forms"""
        context = super().get_context_data(**kwargs)
        context['customer_form'] = UserRegistrationForm()
        context['partner_form'] = PartnerRegistrationForm()
        context['active_tab'] = 'customer'  # default
        return context

    def get(self, request, *args, **kwargs):
        """Capture referral code from URL and store in session."""
        ref_code = request.GET.get('ref', '').strip().upper()
        if ref_code:
            # Validate the code belongs to a real active subscriber
            from account.models import CustomUser
            if CustomUser.objects.filter(
                gp_id=ref_code,
                user_type=CustomUser.UserType.SUBSCRIBER,
                is_active=True,
            ).exists():
                request.session['referral_code'] = ref_code
            # If invalid, silently ignore — don't block registration
        return super().get(request, *args, **kwargs)
    
    def post(self, request, *args, **kwargs):
        # Determine which form to use based on the URL
        if 'register/partner' in request.path:
            return self.handle_partner_registration(request)
        else:
            return self.handle_customer_registration(request)
    
    def handle_customer_registration(self, request):
        """Handle customer/subscriber registration"""
        customer_form = UserRegistrationForm(request.POST)
        partner_form = PartnerRegistrationForm()  # Empty form for context
        
        if customer_form.is_valid():
            user = customer_form.save(commit=True)
            user.is_active = False
            user.save()

            # ── Referral: create ReferralRecord if a valid code is in session ──
            ref_code = request.session.pop('referral_code', None)
            if ref_code:
                try:
                    referrer = CustomUser.objects.get(
                        gp_id=ref_code,
                        user_type=CustomUser.UserType.SUBSCRIBER,
                        is_active=True,
                    )
                    if referrer.pk != user.pk:  
                        ReferralRecord.objects.get_or_create(
                            referrer=referrer,
                            referred_user=user,
                        )
                except CustomUser.DoesNotExist:
                    pass  
            
            self.send_activation_email(user, 'subscriber')
            
            return redirect('account:activation_sent')
        
        # Form has errors - re-render with context
        return render(request, self.template_name, {
            'customer_form': customer_form,
            'partner_form': partner_form,
            'active_tab': 'customer'
        })
    
    def handle_partner_registration(self, request):
        """Handle partner registration"""
        partner_form = PartnerRegistrationForm(request.POST)
        customer_form = UserRegistrationForm()  # Empty form for context
        
        if partner_form.is_valid():
            user = partner_form.save(commit=True)
            user.is_active = False
            user.save()
            
            self.send_activation_email(user, 'partner')
            self.notify_admins_new_partner(user)
            
            messages.success(
                request,
                'Partnership application submitted! Please check your email to activate your account.'
            )
            return redirect('account:activation_sent')
        
        # Form has errors - re-render with context
        return render(request, self.template_name, {
            'customer_form': customer_form,
            'partner_form': partner_form,
            'active_tab': 'partner'
        })
    
    def send_activation_email(self, user, user_type):
        """Send activation email with proper logging so failures are never silent."""
        logger.info(f'Sending activation email to {user.email} (type={user_type})')
 
        try:
            current_site = get_current_site(self.request)
            token = default_token_generator.make_token(user)
            uid   = urlsafe_base64_encode(force_bytes(user.pk))
 
            if user_type == 'partner':
                subject  = 'Activate Your Gold Privilege Partner Account'
                template = 'account/emails/activation_email_partner.html'
            else:
                subject  = 'Activate Your Gold Privilege Account'
                template = 'account/emails/activation_email.html'
 
        except Exception as e:
            logger.error(
                f'Failed to prepare activation email for {user.email}: {e}',
                exc_info=True,
            )
            return
 
        # Render template separately so a template error doesn't look like
        # an SMTP error in the logs
        try:
            html_message = render_to_string(template, {
                'user':     user,
                'domain':   current_site.domain,
                'uid':      uid,
                'token':    token,
                'protocol': 'https' if self.request.is_secure() else 'http',
            })
            plain_message = strip_tags(html_message)
        except Exception as e:
            logger.error(
                f'Failed to render activation email template ({template}) '
                f'for {user.email}: {e}',
                exc_info=True,
            )
            return
 
        # Send
        try:
            email_obj = EmailMultiAlternatives(
                subject=subject,
                body=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email],
            )
            email_obj.attach_alternative(html_message, 'text/html')
            email_obj.send()
            logger.info(f'Activation email sent successfully to {user.email}')
 
        except Exception as e:
            logger.error(
                f'SMTP failure sending activation email to {user.email}: {e}',
                exc_info=True,
            )
            # Show visible error on the page so the user knows to try again
            messages.error(
                self.request,
                'We could not send your activation email. '
                'Please try registering again or contact support.'
            )
    
    def notify_admins_new_partner(self, user):
        """Notify admins of new partner application"""
        admin_emails = CustomUser.objects.filter(
            is_staff=True,
            is_active=True
        ).values_list('email', flat=True)
        
        if admin_emails:
            subject = 'New Partner Application - Gold Privilege'
            
            # Safely get partner_profile
            try:
                partner_profile = user.partner_profile
            except PartnerProfile.DoesNotExist:
                print(f"Partner profile not found for user {user.email}")
                return
            
            html_message = render_to_string('account/emails/admin_new_partner.html', {
                'user': user,
                'partner_profile': partner_profile,
            })
            
            plain_message = strip_tags(html_message)
            
            email = EmailMultiAlternatives(
                subject=subject,
                body=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=list(admin_emails)
            )
            email.attach_alternative(html_message, "text/html")
            
            try:
                email.send()
            except Exception as e:
                print(f"Failed to notify admins: {str(e)}")


# Update your existing views - keep these as they are but add new ones
class UserRegisterView(UnifiedRegistrationView):
    """Customer registration endpoint"""
    pass


class PartnerRegisterView(UnifiedRegistrationView):
    """Partner registration endpoint"""
    pass


def activation_sent(request):
    """Confirmation page after registration"""
    return render(request, 'account/activation_sent.html')


def activate(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = CustomUser.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, CustomUser.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        if user.is_active:
            messages.info(request, "Your account is already activated. Please log in.")
            return redirect('account:login')
        user.is_active = True
        user.is_verified = True
        user.save()
        send_welcome_email(user, request)
        _send_welcome_notification(user) 
        messages.success(request, "Your account has been activated successfully! Please log in.")
        return redirect('account:login')
    else:
        return render(request, 'account/activation_invalid.html')

def send_welcome_email(user, request=None):
    """Send welcome email after activation."""
    subject = 'Welcome to Gold Privilege!'
 
    if user.user_type == 'PARTNER':
        template = 'account/emails/welcome_partner.html'
    else:
        template = 'account/emails/welcome_subscriber.html'
 
    # Build absolute URLs for email clients. Email images must not be relative.
    if request is not None:
        site_url = request.build_absolute_uri('/').rstrip('/')
        wallet_url = request.build_absolute_uri(reverse('wallet:wallet_dashboard'))
        dashboard_url = request.build_absolute_uri(reverse('account:dashboard'))
    else:
        site_url = getattr(settings, 'SITE_URL', 'https://goldprivilege.net').rstrip('/')
        wallet_url = f'{site_url}/wallet/'
        dashboard_url = f'{site_url}/dashboard/'

    if user.user_type == 'PARTNER':
        welcome_hero_url = f"{site_url}{static('images/emails/welcome-partner.jpg')}"
    else:
        welcome_hero_url = f"{site_url}{static('images/emails/welcome-subscriber.jpg')}"

 
    html_message = render_to_string(template, {
        'user':              user,
        'user_name':         user.get_full_name(),
        'gp_id':             user.gp_id,
        'wallet_url':        wallet_url,
        'user_dashboard_url': dashboard_url,
        'partner_dashboard_url': dashboard_url,
    'site_url': site_url,
    'welcome_hero_url': welcome_hero_url,
    })
    plain_message = strip_tags(html_message)
 
    email = EmailMultiAlternatives(
        subject=subject,
        body=plain_message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    email.attach_alternative(html_message, 'text/html')
 
    try:
        email.send()
    except Exception as e:
        print(f'Failed to send welcome email: {str(e)}')

def _send_welcome_notification(user):
    """Send an in-app welcome notification on first account activation."""
    try:
        from notifications.models import Notification
        if user.user_type == 'PARTNER':
            title = 'Welcome to Gold Privilege Partners!'
            body  = (
                'Your partner account is now active. '
                'Submit your venue for approval and start receiving bookings.'
            )
        else:
            title = 'Welcome to Gold Privilege!'
            body  = (
                'Your account is active. Explore venues, book visits, '
                'and earn Gold Coins with every purchase.'
            )
        Notification.objects.create(
            recipient=user,
            title=title,
            body=body,
            
        )
    except Exception as e:
        logger.error(f'Welcome notification failed for {user.email}: {e}', exc_info=True)


# ==================== LOGIN/LOGOUT VIEWS ====================

def login_view(request):
    """Custom login view with account status checking"""
    if request.user.is_authenticated:
        return redirect('account:dashboard')
    
    if request.method == 'POST':
        form = CustomLoginForm(request, data=request.POST)
        if form.is_valid():
            email = form.cleaned_data.get('username').lower()
            password = form.cleaned_data.get('password')
            remember_me = form.cleaned_data.get('remember_me')
            
            # Check if user exists
            try:
                user_obj = CustomUser.objects.get(email=email)
            except CustomUser.DoesNotExist:
                messages.error(request, "No account found with this email.")
                return render(request, 'account/login.html', {'form': form})
            
            # Check if account is activated
            if not user_obj.is_active:
                # Resend activation email
                current_site = get_current_site(request)
                token = default_token_generator.make_token(user_obj)
                uid = urlsafe_base64_encode(force_bytes(user_obj.pk))
                
                subject = 'Activate Your Gold Privilege Account'
                
                template = 'account/emails/activation_email_partner.html' if user_obj.user_type == 'PARTNER' else 'account/emails/activation_email.html'
                
                html_message = render_to_string(template, {
                    'user': user_obj,
                    'domain': current_site.domain,
                    'uid': uid,
                    'token': token,
                    'protocol': 'https' if request.is_secure() else 'http',
                })
                
                plain_message = strip_tags(html_message)
                
                email = EmailMultiAlternatives(
                    subject=subject,
                    body=plain_message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[user_obj.email]
                )
                email.attach_alternative(html_message, "text/html")
                email.send()
                
                messages.error(
                    request, 
                    "Your account is not activated. We've sent you a new activation email."
                )
                return render(request, 'account/login.html', {'form': form})
            
            # Authenticate user
            user = authenticate(request, username=email, password=password)
            
            if user is None:
                messages.error(request, "Invalid password. Please try again.")
                return render(request, 'account/login.html', {'form': form})
            
            # Login successful
            login(request, user)
            
            # Set session expiry based on remember_me
            if not remember_me:
                request.session.set_expiry(0)  # Session expires on browser close
            
            messages.success(request, f'Welcome back, {user.get_full_name()}!')
            
            # Redirect based on user type
            next_url = request.GET.get('next')
            if next_url:
                return redirect(next_url)
            
            if user.user_type == 'PARTNER':
                # Check if partner is approved
                if hasattr(user, 'partner_profile') and user.partner_profile.is_approved:
                    return redirect('account:partner_dashboard')
                else:
                    return redirect('account:partner_pending')
            elif user.is_staff:
                return redirect('admin:index')
            else:
                return redirect('account:user_dashboard')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = CustomLoginForm()
    
    return render(request, 'account/login.html', {'form': form})


@login_required
def logout_view(request):
    """Logout view"""
    logout(request)
    messages.info(request, 'You have been logged out successfully.')
    return redirect('account:login')


# ==================== DASHBOARD VIEWS ====================

@login_required
def dashboard(request):
    """Universal dashboard redirect based on user type"""
    user = request.user
    
    if user.user_type == 'PARTNER':
        if hasattr(user, 'partner_profile') and user.partner_profile.is_approved:
            return redirect('account:partner_dashboard')
        else:
            return redirect('account:partner_pending')
    elif user.is_staff:
        return redirect('admin:index')
    else:
        return redirect('account:user_dashboard')

class UserDashboardView(IsSubscriberUserMixin, TemplateView):
    """Dashboard for subscribers with precise state handling"""
    template_name = 'account/user_dashboard.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        from bookings.models import Booking, BookingStatus
        
        # ── SUBSCRIPTION STATE ──
        sub_state = get_subscription_state(user)
        context['subscription_state'] = sub_state
        
        # Feature usage if active
        if sub_state['has_active']:
            context['feature_usage'] = get_all_feature_usage(sub_state['subscription'])
        
        # ── BOOKING STATS ──
        recent_bookings = Booking.objects.filter(
            user=user
        ).select_related('venue', 'venue__primary_feature', 'subscription').order_by('-created_at')[:10]
        
        context['recent_bookings'] = recent_bookings
        
        # ── ADD REMAINING QUOTA FOR EACH BOOKING (for cancel modal) ──
        bookings_with_quota = []
        for booking in recent_bookings:
            booking_data = {
                'booking': booking,
                'remaining_quota': None,
            }
            
            # Calculate remaining quota if booking has primary feature
            if booking.venue.primary_feature and booking.subscription:
                feature_usage_obj, _ = get_or_create_feature_usage(
                    booking.subscription,
                    booking.venue.primary_feature
                )
                # Calculate what remaining will be AFTER cancellation (current remaining + 1)
                # Since used_count will decrease by 1, remaining will increase by 1
                current_remaining = feature_usage_obj.get_limit() - feature_usage_obj.used_count
                booking_data['remaining_quota'] = current_remaining + 1
            
            bookings_with_quota.append(booking_data)
        
        context['bookings_with_quota'] = bookings_with_quota
        
        context['upcoming_bookings_count'] = Booking.objects.filter(
            user=user,
            status=BookingStatus.CONFIRMED,
            visit_date__gte=date.today()
        ).count()
        
        context['total_bookings'] = Booking.objects.filter(user=user).count()
        context['completed_visits'] = Booking.objects.filter(
            user=user, 
            status=BookingStatus.COMPLETED
        ).count()
        
        # Add active_subscription for convenience
        context['active_subscription'] = sub_state.get('subscription')
        
        return context


class PartnerDashboardView(IsApprovedPartnerMixin, TemplateView):
    """Dashboard for approved partners"""
    template_name = 'account/partner_dashboard.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        partner_profile = user.partner_profile
        
        # Partner profile
        context['partner_profile'] = partner_profile
        
        # Get all venues for this partner
        from venues.models import Venue
        venues = Venue.objects.filter(partner=partner_profile).order_by('-created_at')
        context['venues'] = venues
        
        # Get bookings related to partner's venues
        from bookings.models import Booking
        all_bookings = Booking.objects.filter(
            venue__partner=partner_profile
        ).select_related('venue', 'user', 'subscription').order_by('-created_at')

        for booking in all_bookings:
            if booking.status == BookingStatus.CONFIRMED:
                booking.display_reference = '••••••' + booking.booking_reference[-4:]
            else:
                booking.display_reference = booking.booking_reference
        
        # Total bookings count
        context['total_bookings'] = all_bookings.count()
        
        # Pending bookings (CONFIRMED status - waiting for check-in)
        context['pending_bookings'] = all_bookings.filter(status='CONFIRMED').count()
        
        # Recent bookings (last 5)
        context['recent_bookings'] = all_bookings[:5]
        
        # Today's bookings (for check-in)
        from django.utils import timezone
        today = timezone.now().date()
        context['today_bookings'] = all_bookings.filter(visit_date=today)
        
        # Bookings this month
        context['month_bookings'] = all_bookings.filter(
            visit_date__year=timezone.now().year,
            visit_date__month=timezone.now().month
        ).count()
        
        return context


class PartnerPendingView(IsPartnerMixin, TemplateView):
    """View for partners with pending approval"""
    template_name = 'account/partner_pending.html'
    
    def get(self, request, *args, **kwargs):
        # Redirect approved partners to dashboard
        if hasattr(request.user, 'partner_profile'):
            if request.user.partner_profile.is_approved:
                return redirect('account:partner_dashboard')
        
        return super().get(request, *args, **kwargs)


# ==================== PROFILE VIEWS ====================

class PartnerProfileUpdateView(LoginRequiredMixin, IsApprovedPartnerMixin, UpdateView):
    """View for partners to update their business profile"""
    model = PartnerProfile
    form_class = PartnerProfileUpdateForm
    template_name = 'account/partner_profile_update.html'
    
    def get_object(self):
        # Get the partner profile for the logged-in user
        return self.request.user.partner_profile
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['user'] = self.request.user
        return context
    
    def form_valid(self, form):
        messages.success(self.request, 'Profile updated successfully!')
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse('account:partner_profile')


# Update the existing profile_view function to handle both GET and POST for partners
@login_required
def profile_view(request):
    """View and update user profile"""
    user = request.user
    
    if user.user_type == 'PARTNER':
        profile = user.partner_profile
        template = 'account/partner_profile.html'
        
        if request.method == 'POST':
            form = PartnerProfileUpdateForm(request.POST, request.FILES, instance=profile, user=user)
            if form.is_valid():
                form.save()
                messages.success(request, 'Profile updated successfully!')
                return redirect('account:profile')
        else:
            form = PartnerProfileUpdateForm(instance=profile, user=user)
        
        return render(request, template, {
            'user': user,
            'profile': profile,
            'form': form,
        })
    
    else:
        # Subscriber profile handling
        profile = user.profile
        template = 'account/user_profile.html'
        
        if request.method == 'POST':
            form = UserProfileUpdateForm(request.POST, request.FILES, instance=profile, user=user)
            if form.is_valid():
                form.save()
                messages.success(request, 'Profile updated successfully!')
                return redirect('account:profile')
        else:
            form = UserProfileUpdateForm(instance=profile, user=user)
        
        from wallet.models import ReferralRecord
        from django.db.models import Sum
    
        referral_stats = {
            'total_referred':   ReferralRecord.objects.filter(referrer=user).count(),
            'total_converted':  ReferralRecord.objects.filter(referrer=user, is_paid=True).count(),
            'total_coins_earned': ReferralRecord.objects.filter(
                referrer=user, is_paid=True
            ).aggregate(total=Sum('coins_awarded'))['total'] or 0,
        }
        
        return render(request, template, {
            'user': user,
            'profile': profile,
            'form': form,
            'referral_stats': referral_stats,
        })


# ==================== PASSWORD RESET VIEWS ====================

class CustomPasswordResetView(PasswordResetView):
    """Custom password reset view"""
    template_name = 'account/password_reset.html'
    email_template_name = 'account/emails/password_reset_email.html'
    #subject_template_name = 'account/emails/password_reset_subject.txt'
    success_url = reverse_lazy('account:password_reset_done')
    form_class = CustomPasswordResetForm
    
    def form_valid(self, form):
        messages.success(
            self.request,
            'Password reset email has been sent. Please check your inbox.'
        )
        return super().form_valid(form)


class CustomPasswordResetDoneView(PasswordResetDoneView):
    """Password reset email sent confirmation"""
    template_name = 'account/password_reset_done.html'


class CustomPasswordResetConfirmView(PasswordResetConfirmView):
    """Password reset confirmation view"""
    template_name = 'account/password_reset_confirm.html'
    success_url = reverse_lazy('account:password_reset_complete')
    form_class = CustomSetPasswordForm
    
    def form_valid(self, form):
        messages.success(
            self.request,
            'Your password has been reset successfully! You can now log in.'
        )
        return super().form_valid(form)


class CustomPasswordResetCompleteView(PasswordResetCompleteView):
    """Password reset complete view"""
    template_name = 'account/password_reset_complete.html'


def home(request):
    """Home page view"""
    return render(request, 'home.html')

def about(request):
    return render(request, 'about.html')