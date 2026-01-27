from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.generic import ListView, DetailView, TemplateView
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_http_methods
from django.views.decorators.csrf import csrf_protect
from django.db import transaction
from django.db.models import F
from django.core.cache import cache
from django.conf import settings
from datetime import timedelta
from decimal import Decimal
import logging
import requests
import secrets

from account.permissions import subscriber_required
from .models import SubscriptionPlan, PromoCode, Subscription, Payment

logger = logging.getLogger(__name__)


class PlanListView(ListView):
    """Display all available subscription plans"""
    model = SubscriptionPlan
    template_name = 'subscriptions/plans_list.html'
    context_object_name = 'plans'
    
    def get_queryset(self):
        """Get only available plans"""
        return SubscriptionPlan.objects.filter(
            is_active=True
        ).prefetch_related(
            'feature_assignments__feature'
        ).order_by('display_order', 'price')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Check if user has active subscription
        if self.request.user.is_authenticated:
            context['has_active_subscription'] = Subscription.objects.filter(
                user=self.request.user,
                status__in=['ACTIVE', 'TRIAL']
            ).exists()
            
            context['current_subscription'] = Subscription.objects.filter(
                user=self.request.user,
                status__in=['ACTIVE', 'TRIAL']
            ).first()
        
        return context


class PlanDetailView(DetailView):
    """Display detailed information about a specific plan"""
    model = SubscriptionPlan
    template_name = 'subscriptions/plan_detail.html'
    context_object_name = 'plan'
    slug_field = 'slug'
    slug_url_kwarg = 'slug'
    
    def get_queryset(self):
        """Only show available plans"""
        return SubscriptionPlan.objects.filter(is_active=True)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        plan = self.object
        
        # Get plan features ordered properly
        context['features'] = plan.feature_assignments.select_related(
            'feature'
        ).order_by('display_order')
        
        # Check user's current subscription
        if self.request.user.is_authenticated:
            context['has_active_subscription'] = Subscription.objects.filter(
                user=self.request.user,
                status__in=['ACTIVE', 'TRIAL']
            ).exists()
        
        return context


@login_required
@subscriber_required
@require_http_methods(["GET", "POST"])
def subscribe_to_plan(request, slug):
    """
    Subscribe user to a plan with comprehensive security
    
    FIXED: Promo code is only incremented AFTER successful payment
    Security Features:
    - Rate limiting per user
    - Atomic transactions
    - Race condition prevention
    - Promo code reserved (not consumed) until payment succeeds
    """
    
    # SECURITY: Rate limiting - max 3 subscription attempts per 5 minutes
    rate_limit_key = f"subscription_attempt:{request.user.id}"
    attempts = cache.get(rate_limit_key, 0)
    
    if attempts >= 3:
        messages.error(request, 'Too many subscription attempts. Please try again later.')
        logger.warning(f"Rate limit exceeded for user {request.user.id}")
        return redirect('subscriptions:plans_list')
    
    plan = get_object_or_404(SubscriptionPlan, slug=slug, is_active=True)
    
    # SECURITY: Check plan availability
    if not plan.is_available():
        messages.error(request, 'This plan is currently not available.')
        return redirect('subscriptions:plans_list')
    
    # SECURITY: Atomic check for existing subscription
    with transaction.atomic():
        existing_subscription = Subscription.objects.select_for_update().filter(
            user=request.user,
            status__in=['ACTIVE', 'TRIAL', 'PENDING']
        ).first()
        
        if existing_subscription:
            messages.warning(
                request,
                'You already have an active subscription. Please cancel it first.'
            )
            return redirect('subscriptions:my_subscription')
    
    if request.method == 'POST':
        # Increment rate limit counter
        cache.set(rate_limit_key, attempts + 1, timeout=300)  # 5 minutes
        
        promo_code = request.POST.get('promo_code', '').strip()
        
        # Calculate pricing
        price = Decimal(str(plan.price))
        discount = Decimal('0')
        promo_code_obj = None
        
        # FIXED: Validate promo code WITHOUT incrementing usage
        if promo_code:
            promo_validation = _validate_promo_code_internal(
                promo_code, 
                plan, 
                request.user
            )
            
            if promo_validation['valid']:
                promo_code_obj = promo_validation['promo_code']
                discount = Decimal(str(promo_validation['discount']))
            else:
                messages.warning(request, promo_validation['message'])
        
        # SECURITY: Ensure non-negative final price
        final_price = max(Decimal('0'), price - discount)
        
        # Calculate subscription dates
        start_date = timezone.now()
        duration_days = plan.get_duration_in_days()
        
        # Check for trial period (prevent trial abuse)
        is_trial = plan.trial_period_days > 0 and not Subscription.objects.filter(
            user=request.user, 
            is_trial=True
        ).exists()
        
        trial_end_date = None
        if is_trial:
            trial_end_date = start_date + timedelta(days=plan.trial_period_days)
            end_date = start_date + timedelta(days=duration_days + plan.trial_period_days)
        else:
            end_date = start_date + timedelta(days=duration_days)
        
        # SECURITY: Use atomic transaction for entire subscription creation
        try:
            with transaction.atomic():
                # Double-check no subscription was created during transaction
                if Subscription.objects.filter(
                    user=request.user,
                    status__in=['ACTIVE', 'TRIAL', 'PENDING']
                ).exists():
                    raise ValueError("Duplicate subscription detected")
                
                # Create subscription
                subscription = Subscription.objects.create(
                    user=request.user,
                    plan=plan,
                    start_date=start_date,
                    end_date=end_date,
                    is_trial=is_trial,
                    trial_end_date=trial_end_date,
                    price_paid=final_price,
                    promo_code_used=promo_code_obj,  # Store reference but DON'T increment
                    discount_amount=discount,
                    status=Subscription.Status.PENDING if final_price > 0 else Subscription.Status.ACTIVE
                )
                
                logger.info(f"Subscription created: {subscription.subscription_id} for user {request.user.id}")
                payment_reference = f"SUB-{secrets.token_hex(10).upper()}"
                
                # If free (after discount) or trial, activate immediately
                if final_price == 0 or (is_trial and final_price == 0):
                    subscription.status = Subscription.Status.TRIAL if is_trial else Subscription.Status.ACTIVE
                    subscription.save()
                    
                    # FIXED: Only increment promo code for FREE subscriptions that activate immediately
                    if promo_code_obj:
                        PromoCode.objects.filter(pk=promo_code_obj.pk).update(
                            uses_count=F('uses_count') + 1
                        )
                    
                    messages.success(
                        request,
                        f'Successfully subscribed to {plan.name}! {"Enjoy your trial period." if is_trial else ""}'
                    )
                    
                    # Clear rate limit on success
                    cache.delete(rate_limit_key)
                    
                    return redirect('subscriptions:my_subscription')
                
                
                
                # Create payment record
                # NOTE: Promo code is NOT incremented yet - will be done in webhook after payment success
                payment = Payment.objects.create(
                    subscription=subscription,
                    user=request.user,
                    amount=final_price,
                    status=Payment.PaymentStatus.PENDING
                )
                payment.gateway_reference = payment.payment_reference
                payment.save()
                
                logger.info(f"Payment created: {payment.payment_id} for subscription {subscription.subscription_id}")
                
                # Clear rate limit on successful creation
                cache.delete(rate_limit_key)
                
                # Redirect to payment page
                return redirect('subscriptions:payment', payment_id=payment.payment_id)
        
        except Exception as e:
            logger.error(f"Subscription creation failed for user {request.user.id}: {str(e)}", exc_info=True)
            messages.error(request, 'An error occurred while processing your subscription. Please try again.')
            return redirect('subscriptions:plan_detail', slug=slug)
    
    # GET request - show subscription form
    return render(request, 'subscriptions/subscribe.html', {
        'plan': plan,
    })


def _validate_promo_code_internal(code, plan, user):
    """
    Internal promo code validation without side effects
    
    CRITICAL: Does NOT increment usage counter
    Usage is incremented ONLY after successful payment in webhook
    """
    try:
        promo_code = PromoCode.objects.get(code=code.upper(), is_active=True)
        
        if not promo_code.can_be_used_by(user):
            return {
                'valid': False,
                'message': 'This promo code cannot be used by you.',
                'promo_code': None,
                'discount': 0
            }
        
        # Check if applicable to plan
        if promo_code.applicable_plans.exists() and \
           plan not in promo_code.applicable_plans.all():
            return {
                'valid': False,
                'message': 'This promo code is not applicable to this plan.',
                'promo_code': None,
                'discount': 0
            }
        
        discount = promo_code.calculate_discount(plan.price)
        
        return {
            'valid': True,
            'message': f'Promo code valid! You save ₦{discount:,.2f}',
            'promo_code': promo_code,
            'discount': discount
        }
    
    except PromoCode.DoesNotExist:
        return {
            'valid': False,
            'message': 'Invalid promo code.',
            'promo_code': None,
            'discount': 0
        }


@login_required
@subscriber_required
def my_subscription(request):
    """View user's current subscription"""
    subscription = Subscription.objects.filter(
        user=request.user,
        status__in=['ACTIVE', 'TRIAL']
    ).select_related('plan').first()
    
    # Get subscription history
    past_subscriptions = Subscription.objects.filter(
        user=request.user,
        status__in=['EXPIRED', 'CANCELLED']
    ).select_related('plan').order_by('-created_at')[:5]
    
    # Check for pending payments
    pending_payment = None
    if subscription and subscription.status == Subscription.Status.PENDING:
        pending_payment = Payment.objects.filter(
            subscription=subscription,
            status=Payment.PaymentStatus.PENDING
        ).first()
    
    return render(request, 'subscriptions/my_subscription.html', {
        'subscription': subscription,
        'past_subscriptions': past_subscriptions,
        'pending_payment': pending_payment,
    })


@login_required
@subscriber_required
@require_POST
def cancel_subscription(request, subscription_id):
    """
    Cancel user's subscription with security checks
    
    Security: Verify ownership before cancellation
    """
    
    # SECURITY: Verify subscription belongs to requesting user
    subscription = get_object_or_404(
        Subscription,
        subscription_id=subscription_id,
        user=request.user  # Critical: ensure ownership
    )
    
    if subscription.status in ['CANCELLED', 'EXPIRED']:
        messages.warning(request, 'This subscription is already cancelled or expired.')
        return redirect('subscriptions:my_subscription')
    
    # SECURITY: Use atomic transaction
    with transaction.atomic():
        reason = request.POST.get('reason', 'User requested cancellation')
        # Sanitize reason input
        reason = reason[:500]  # Limit length
        
        subscription.cancel(reason=reason)
        
        logger.info(f"Subscription cancelled: {subscription.subscription_id} by user {request.user.id}")
    
    messages.success(
        request,
        'Your subscription has been cancelled. You can continue using it until the end of your billing period.'
    )
    return redirect('subscriptions:my_subscription')


@login_required
@subscriber_required
def payment_page(request, payment_id):
    """
    Payment page with enhanced security
    
    Security Features:
    - Ownership verification
    - Payment timeout check (15 minutes)
    - Status validation
    - Amount verification
    """
    
    # SECURITY: Verify payment belongs to user and load related data efficiently
    payment = get_object_or_404(
        Payment.objects.select_related('subscription', 'subscription__plan'),
        payment_id=payment_id,
        user=request.user,  # Critical: verify ownership
    )
    
    # SECURITY: Check payment is still pending (not already processed)
    if payment.status != Payment.PaymentStatus.PENDING:
        messages.info(request, 'This payment has already been processed.')
        return redirect('subscriptions:my_subscription')
    
    # SECURITY: Check payment hasn't expired (15 minutes timeout)
    payment_age = timezone.now() - payment.created_at
    if payment_age > timedelta(minutes=15):
        payment.status = Payment.PaymentStatus.FAILED
        payment.save()
        
        messages.error(request, 'This payment link has expired. Please create a new subscription.')
        logger.warning(f"Expired payment attempt: {payment.payment_id} by user {request.user.id}")
        return redirect('subscriptions:plans_list')
    
    subscription = payment.subscription
    
    # SECURITY: Verify subscription also belongs to user
    if subscription.user != request.user:
        logger.error(f"Unauthorized payment access attempt: {payment.payment_id} by user {request.user.id}")
        messages.error(request, 'Unauthorized access.')
        return redirect('subscriptions:plans_list')
    
    # SECURITY: Verify amounts match
    if payment.amount != subscription.price_paid:
        logger.error(
            f"Amount mismatch for payment {payment_id}: "
            f"payment={payment.amount}, subscription={subscription.price_paid}"
        )
        messages.error(request, 'Payment validation error. Please contact support.')
        return redirect('subscriptions:my_subscription')
    
    return render(request, 'subscriptions/payment.html', {
        'payment': payment,
        'subscription': subscription,
        'plan': subscription.plan,
        'paystack_public_key': settings.PAYSTACK_PUBLIC_KEY,
    })


@csrf_protect
@require_POST
def validate_promo_code(request):
    """
    AJAX endpoint to validate promo code
    
    Security: CSRF protected, authenticated only, rate limited
    """
    if not request.user.is_authenticated:
        return JsonResponse({'valid': False, 'message': 'Please login first'}, status=401)
    
    # SECURITY: Rate limiting for AJAX endpoint
    rate_limit_key = f'promo_validate:{request.user.id}'
    attempts = cache.get(rate_limit_key, 0)
    
    if attempts >= 10:  # Max 10 validations per minute
        return JsonResponse({
            'valid': False,
            'message': 'Too many attempts. Please wait a moment.'
        }, status=429)
    
    cache.set(rate_limit_key, attempts + 1, 60)  # 1 minute
    
    code = request.POST.get('code', '').strip().upper()
    plan_slug = request.POST.get('plan_slug', '').strip()
    
    # SECURITY: Input validation
    if not code or not plan_slug:
        return JsonResponse({'valid': False, 'message': 'Invalid request'}, status=400)
    
    # SECURITY: Limit code length to prevent DOS
    if len(code) > 50:
        return JsonResponse({'valid': False, 'message': 'Invalid promo code'}, status=400)
    
    try:
        plan = SubscriptionPlan.objects.get(slug=plan_slug, is_active=True)
        validation = _validate_promo_code_internal(code, plan, request.user)
        
        if validation['valid']:
            return JsonResponse({
                'valid': True,
                'message': validation['message'],
                'discount': float(validation['discount']),
                'final_price': float(plan.price - Decimal(str(validation['discount']))),
                'discount_display': validation['promo_code'].get_discount_display()
            })
        else:
            return JsonResponse({
                'valid': False,
                'message': validation['message']
            })
    
    except SubscriptionPlan.DoesNotExist:
        return JsonResponse({
            'valid': False,
            'message': 'Invalid plan.'
        }, status=400)
    except Exception as e:
        logger.error(f"Promo validation error: {str(e)}", exc_info=True)
        return JsonResponse({
            'valid': False,
            'message': 'An error occurred. Please try again.'
        }, status=500)


class SubscriptionSuccessView(TemplateView):
    """Success page after subscription"""
    template_name = 'subscriptions/success.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        if self.request.user.is_authenticated:
            context['subscription'] = Subscription.objects.filter(
                user=self.request.user,
                status__in=['ACTIVE', 'TRIAL']
            ).select_related('plan').first()
        
        return context
    

def payment_callback(request):
    reference = request.GET.get('reference')

    if not reference:
        messages.error(request, 'No payment reference provided.')
        return redirect('subscriptions:plans_list')

    if not verify_payment_with_paystack(reference):
        messages.error(request, 'Payment verification failed.')
        return redirect('subscriptions:plans_list')

    payment = Payment.objects.filter(payment_reference=reference).first()

    if not payment:
        messages.error(request, 'Payment record not found.')
        return redirect('subscriptions:plans_list')

    # 🔒 Prevent double-processing
    if payment.status == Payment.PaymentStatus.SUCCESS:
        return redirect('subscriptions:success')

    with transaction.atomic():
        # ✅ MARK PAYMENT AS SUCCESS
        payment.status = Payment.PaymentStatus.SUCCESS
        payment.paid_at = timezone.now()
        payment.gateway_reference = reference
        payment.save()

        subscription = payment.subscription
        if subscription.status == Subscription.Status.PENDING:
            subscription.status = Subscription.Status.ACTIVE
            subscription.save()

            if subscription.promo_code_used:
                PromoCode.objects.filter(
                    pk=subscription.promo_code_used.pk
                ).update(uses_count=F('uses_count') + 1)

        logger.info(
            f"Payment {payment.payment_reference} completed, "
            f"Subscription {subscription.subscription_id} activated"
        )

    return redirect('subscriptions:success')



def verify_payment_with_paystack(reference):
    """Verify payment with Paystack API"""
    url = f'https://api.paystack.co/transaction/verify/{reference}'
    headers = {
        'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data.get('data', {}).get('status') == 'success'
    except Exception as e:
        logger.error(f"Payment verification error: {e}")
    
    return False