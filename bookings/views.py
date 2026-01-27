from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy, reverse
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_POST, require_GET
from django.db.models import Q, Count
from django.utils import timezone
from django.core.paginator import Paginator
from datetime import date, timedelta
import json
from django.db import transaction
from account.permissions import subscriber_required, IsApprovedPartnerMixin
from .models import Booking, BookingStatus, BookingActivity
from .forms import BookingCreateForm, BookingCancelForm, VenueCheckInForm
from venues.models import Venue
from subscriptions.models import Subscription


# ==================== MEMBER VIEWS ====================

@login_required
@subscriber_required
def booking_create(request, venue_slug=None):
    """
    Create new booking
    """
    # Get active subscription
    active_subscription = Subscription.objects.filter(
        user=request.user,
        status__in=['ACTIVE', 'TRIAL'],
        end_date__gte=timezone.now().date()
    ).first()
    
    if not active_subscription:
        messages.error(
            request,
            'You need an active subscription to make bookings. '
            'Please subscribe or renew your membership.'
        )
        return redirect('subscriptions:list')
    
    # Check booking quota
    can_book, remaining, message = Booking.check_booking_available(
        request.user, 
        active_subscription
    )
    
    if not can_book:
        messages.error(request, message)
        return redirect('bookings:list')
    
    # Pre-select venue if slug is provided
    initial_data = {}
    if venue_slug:
        try:
            venue = Venue.objects.get(slug=venue_slug, status='APPROVED')
            initial_data['venue'] = venue
        except Venue.DoesNotExist:
            messages.error(request, 'Venue not found or not available for booking.')
            return redirect('venues:list')
    
    if request.method == 'POST':
        form = BookingCreateForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                with transaction.atomic():  # Start transaction
                    # Check for existing booking for same user, venue, and date
                    existing_booking = Booking.objects.filter(
                        user=request.user,
                        venue=form.cleaned_data['venue'],
                        visit_date=form.cleaned_data['visit_date'],
                        status=BookingStatus.CONFIRMED
                    ).first()
                    
                    if existing_booking:
                        messages.warning(
                            request,
                            f'You already have a booking for this venue on {existing_booking.visit_date}'
                        )
                        return redirect('bookings:detail', booking_reference=existing_booking.booking_reference)
                    
                    booking = form.save(commit=False)
                    booking.user = request.user
                    booking.subscription = active_subscription
                    booking.save()
                
                
                
                # Send email notification (implement in signals)
                # send_booking_confirmation_email(booking)
                
                messages.success(
                    request,
                    f'Booking confirmed! Reference: {booking.booking_reference}'
                )
                
                # Redirect to booking detail or list
                return redirect('bookings:detail', booking_reference=booking.booking_reference)
            
            except Exception as e:
                messages.error(request, f'Error creating booking: {str(e)}')
                return redirect('bookings:create')
    else:
        form = BookingCreateForm(user=request.user, initial=initial_data)
    
    # Set min date for date picker
    min_date = date.today()
    max_date = date.today() + timedelta(days=90)  # 90 days advance
    
    context = {
        'form': form,
        'subscription': active_subscription,
        'remaining_bookings': remaining,
        'min_date': min_date.isoformat(),
        'max_date': max_date.isoformat(),
        'venue_slug': venue_slug,
    }
    
    return render(request, 'bookings/member/create.html', context)


@login_required
@subscriber_required
def booking_list(request):
    """
    Display member's bookings
    """
    # Get bookings for current user
    bookings = Booking.objects.filter(user=request.user)\
        .select_related('venue', 'subscription')\
        .order_by('-visit_date', '-created_at')
    
    # Get active subscription
    active_subscription = Subscription.objects.filter(
        user=request.user,
        status__in=['ACTIVE', 'TRIAL'],
        end_date__gte=timezone.now().date()
    ).first()
    
    # Get quota info
    if active_subscription:
        # Get current month's CONFIRMED bookings (not cancelled or completed)
        current_month = timezone.now().month
        current_year = timezone.now().year
        
        used_bookings = Booking.objects.filter(
            user=request.user,
            subscription=active_subscription,
            visit_date__year=current_year,
            visit_date__month=current_month,
            status__in=['CONFIRMED', 'CHECKED_IN']
        ).count()
        
        max_bookings = active_subscription.plan.max_bookings_per_month
        
        if max_bookings:
            remaining = max(0, max_bookings - used_bookings)
            can_book = remaining > 0
            quota_message = f"{remaining} booking(s) remaining this month" if remaining > 0 else "Monthly limit reached"
        else:
            remaining = float('inf')  # Unlimited
            can_book = True
            quota_message = "Unlimited bookings"
        
        # Also update the monthly_stats to reflect active bookings only
        monthly_stats = {
            'total': used_bookings,
            'confirmed': Booking.objects.filter(
                user=request.user,
                visit_date__year=current_year,
                visit_date__month=current_month,
                status='CONFIRMED'
            ).count(),
            'checked_in': Booking.objects.filter(
                user=request.user,
                visit_date__year=current_year,
                visit_date__month=current_month,
                status='CHECKED_IN'
            ).count(),
            'completed': Booking.objects.filter(
                user=request.user,
                visit_date__year=current_year,
                visit_date__month=current_month,
                status='COMPLETED'
            ).count(),
            'cancelled': Booking.objects.filter(
                user=request.user,
                visit_date__year=current_year,
                visit_date__month=current_month,
                status='CANCELLED'
            ).count(),
            'no_show': Booking.objects.filter(
                user=request.user,
                visit_date__year=current_year,
                visit_date__month=current_month,
                status='NO_SHOW'
            ).count(),
        }
    else:
        can_book, remaining, quota_message = False, 0, "No active subscription"
    
    # Separate bookings by status
    today = date.today()
    
    upcoming_bookings = bookings.filter(
        status=BookingStatus.CONFIRMED,
        visit_date__gte=today
    )
    
    today_bookings = bookings.filter(
        status__in=[BookingStatus.CONFIRMED, BookingStatus.CHECKED_IN],
        visit_date=today
    )
    
    past_bookings = bookings.filter(
        Q(status__in=[BookingStatus.COMPLETED, BookingStatus.NO_SHOW]) |
        Q(visit_date__lt=today, status=BookingStatus.CONFIRMED)  # Past confirmed become no-show candidates
    )
    
    cancelled_bookings = bookings.filter(status=BookingStatus.CANCELLED)
    
    # Get monthly stats
    current_month = today.month
    current_year = today.year
    monthly_stats = Booking.get_monthly_stats(request.user, current_year, current_month)
    
    context = {
        'upcoming_bookings': upcoming_bookings,
        'today_bookings': today_bookings,
        'past_bookings': past_bookings,
        'cancelled_bookings': cancelled_bookings,
        'active_subscription': active_subscription,
        'remaining_bookings': remaining,
        'quota_message': quota_message,
        'monthly_stats': monthly_stats,
        'today': today,
        'remaining': remaining,
        'monthly_stats': monthly_stats,
    }
    
    return render(request, 'bookings/member/list.html', context)


@login_required
@subscriber_required
def booking_detail(request, booking_reference):
    """
    View single booking details
    """
    booking = get_object_or_404(
        Booking.objects.select_related('venue', 'subscription'),
        booking_reference=booking_reference,
        user=request.user
    )
    
    # Check if user can cancel
    can_cancel = booking.can_cancel()
    
    # Get venue details for map/directions
    venue = booking.venue
    map_url = None
    if venue.latitude and venue.longitude:
        map_url = f"https://www.google.com/maps?q={venue.latitude},{venue.longitude}"
    
    # Get QR code data
    qr_data = booking.get_qr_code_data()
    
    context = {
        'booking': booking,
        'venue': venue,
        'can_cancel': can_cancel,
        'map_url': map_url,
        'qr_data': qr_data,
        'today': date.today(),
    }
    
    return render(request, 'bookings/member/detail.html', context)


@login_required
@subscriber_required
@require_POST
def booking_cancel(request, booking_reference):
    """
    Cancel a booking
    """
    booking = get_object_or_404(
        Booking,
        booking_reference=booking_reference,
        user=request.user
    )
    
    if not booking.can_cancel():
        messages.error(request, 'This booking cannot be cancelled.')
        return redirect('bookings:detail', booking_reference=booking_reference)
    
    form = BookingCancelForm(request.POST)
    
    if form.is_valid():
        reason = form.cleaned_data.get('cancellation_reason', '')
        
        try:
            booking.cancel(reason=reason, cancelled_by=request.user)
            
            # Create activity log
            BookingActivity.objects.create(
                booking=booking,
                action='CANCELLED',
                performed_by=request.user,
                notes=f'Cancelled: {reason[:100]}'
            )
            
            messages.success(request, 'Booking cancelled successfully.')
            return redirect('bookings:list')
        
        except Exception as e:
            messages.error(request, f'Error cancelling booking: {str(e)}')
            return redirect('bookings:detail', booking_reference=booking_reference)
    
    # If form is invalid, show errors
    for error in form.errors.values():
        messages.error(request, error)
    
    return redirect('bookings:detail', booking_reference=booking_reference)


# ==================== PARTNER VIEWS ====================

class PartnerBookingsListView(IsApprovedPartnerMixin, ListView):
    """
    Partner view: All bookings for their venues
    """
    model = Booking
    template_name = 'bookings/partner/list.html'
    context_object_name = 'bookings'
    paginate_by = 20
    
    def get_queryset(self):
        # Get partner's venues
        partner_venues = self.request.user.partner_profile.venues.all()
        
        # Get filter parameters
        status = self.request.GET.get('status', '')
        venue_id = self.request.GET.get('venue', '')
        date_from = self.request.GET.get('date_from', '')
        date_to = self.request.GET.get('date_to', '')
        
        # Base queryset
        queryset = Booking.objects.filter(
            venue__in=partner_venues
        ).select_related(
            'user', 'venue', 'subscription'
        ).order_by('-visit_date', '-created_at')
        
        # Apply filters
        if status:
            queryset = queryset.filter(status=status)
        
        if venue_id:
            queryset = queryset.filter(venue_id=venue_id)
        
        if date_from:
            queryset = queryset.filter(visit_date__gte=date_from)
        
        if date_to:
            queryset = queryset.filter(visit_date__lte=date_to)
        
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get partner's venues for filter dropdown
        partner_venues = self.request.user.partner_profile.venues.all()
        
        # Get statistics
        today = date.today()
        partner_venue_ids = partner_venues.values_list('id', flat=True)
        
        stats = {
            'today': Booking.objects.filter(
                venue_id__in=partner_venue_ids,
                visit_date=today
            ).count(),
            'upcoming': Booking.objects.filter(
                venue_id__in=partner_venue_ids,
                status=BookingStatus.CONFIRMED,
                visit_date__gte=today
            ).count(),
            'checked_in_today': Booking.objects.filter(
                venue_id__in=partner_venue_ids,
                status=BookingStatus.CHECKED_IN,
                checked_in_at__date=today
            ).count(),
            'total_month': Booking.objects.filter(
                venue_id__in=partner_venue_ids,
                visit_date__year=today.year,
                visit_date__month=today.month
            ).count(),
        }
        
        context.update({
            'partner_venues': partner_venues,
            'booking_stats': stats,
            'status_choices': BookingStatus.choices,
            'today': today,
            'filter_status': self.request.GET.get('status', ''),
            'filter_venue': self.request.GET.get('venue', ''),
            'filter_date_from': self.request.GET.get('date_from', ''),
            'filter_date_to': self.request.GET.get('date_to', ''),
        })
        
        return context


@login_required
def partner_check_in(request):
    """
    Check in a booking via QR scan or manual entry
    """
    # Verify user is a partner
    if not hasattr(request.user, 'partner_profile'):
        return HttpResponseForbidden("Access denied. Partner account required.")
    
    # Get partner's venues
    partner_venues = request.user.partner_profile.venues.all()
    
    if request.method == 'POST':
        form = VenueCheckInForm(request.POST)
        
        if form.is_valid():
            booking = form.cleaned_data['booking']
            notes = form.cleaned_data.get('check_in_notes', '')
            
            # Verify booking is for partner's venue
            if booking.venue not in partner_venues:
                messages.error(request, 'This booking is not for one of your venues.')
                return redirect('bookings:partner_check_in')
            
            try:
                booking.check_in(
                    checked_in_by=request.user,
                    notes=notes
                )
                
                # Create activity log
                BookingActivity.objects.create(
                    booking=booking,
                    action='CHECKED_IN',
                    performed_by=request.user,
                    notes=f'Checked in by partner: {notes[:100]}'
                )
                
                messages.success(
                    request,
                    f'Successfully checked in {booking.user.get_full_name()} '
                    f'for booking {booking.booking_reference}'
                )
                
                # Clear form
                form = VenueCheckInForm()
                
            except Exception as e:
                messages.error(request, f'Error checking in: {str(e)}')
    
    else:
        form = VenueCheckInForm()
    
    # Get today's bookings for quick reference
    today_bookings = Booking.objects.filter(
        venue__in=partner_venues,
        visit_date=date.today(),
        status=BookingStatus.CONFIRMED
    ).select_related('user').order_by('created_at')
    
    context = {
        'form': form,
        'today_bookings': today_bookings,
        'partner_venues': partner_venues,
        'today': date.today(),
    }
    
    return render(request, 'bookings/partner/check_in.html', context)


@login_required
def partner_booking_detail(request, booking_reference):
    """
    Partner view of a specific booking
    """
    # Verify user is a partner
    if not hasattr(request.user, 'partner_profile'):
        return HttpResponseForbidden("Access denied. Partner account required.")
    
    booking = get_object_or_404(
        Booking.objects.select_related('user', 'venue', 'subscription'),
        booking_reference=booking_reference
    )
    
    # Verify booking is for partner's venue
    if booking.venue not in request.user.partner_profile.venues.all():
        return HttpResponseForbidden("You don't have permission to view this booking.")
    
    # Get booking activities
    activities = BookingActivity.objects.filter(booking=booking).order_by('-created_at')
    
    context = {
        'booking': booking,
        'activities': activities,
        'today': date.today(),
    }
    
    return render(request, 'bookings/partner/detail.html', context)


# ==================== AJAX ENDPOINTS ====================

@login_required
@require_POST
def quick_check_in(request):
    """
    AJAX endpoint for quick check-in
    """
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'success': False, 'error': 'Invalid request'})
    
    try:
        data = json.loads(request.body)
        booking_reference = data.get('booking_reference', '').upper().strip()
        
        # Get booking
        booking = Booking.objects.get(booking_reference=booking_reference)
        
        # Verify user is a partner and has permission
        if not hasattr(request.user, 'partner_profile'):
            return JsonResponse({
                'success': False,
                'error': 'Partner access required'
            })
        
        if booking.venue not in request.user.partner_profile.venues.all():
            return JsonResponse({
                'success': False,
                'error': 'Booking not for your venue'
            })
        
        # Check in
        booking.check_in(checked_in_by=request.user)
        
        # Create activity log
        BookingActivity.objects.create(
            booking=booking,
            action='CHECKED_IN',
            performed_by=request.user,
            notes='Quick check-in via dashboard'
        )
        
        return JsonResponse({
            'success': True,
            'booking_reference': booking.booking_reference,
            'member_name': booking.user.get_full_name(),
            'status': booking.get_status_display(),
            'checked_in_at': booking.checked_in_at.strftime('%I:%M %p')
        })
    
    except Booking.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Booking not found'
        })
    
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })


@login_required
@require_GET
def check_booking_quota(request):
    """
    AJAX: Check remaining booking quota
    """
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'success': False, 'error': 'Invalid request'})
    
    # Get active subscription
    active_subscription = Subscription.objects.filter(
        user=request.user,
        status__in=['ACTIVE', 'TRIAL'],
        end_date__gte=timezone.now().date()
    ).first()
    
    if not active_subscription:
        return JsonResponse({
            'success': False,
            'can_book': False,
            'remaining': 0,
            'message': 'No active subscription'
        })
    
    can_book, remaining, message = Booking.check_booking_available(
        request.user, active_subscription
    )
    
    return JsonResponse({
        'success': True,
        'can_book': can_book,
        'remaining': remaining,
        'message': message
    })


@login_required
@require_GET
def get_available_dates(request, venue_id):
    """
    AJAX: Get available dates for a venue (considering capacity, etc.)
    """
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'success': False, 'error': 'Invalid request'})
    
    try:
        venue = Venue.objects.get(id=venue_id, status='APPROVED')
        
        # Get booked dates for next 90 days
        today = date.today()
        max_date = today + timedelta(days=90)
        
        # Get all confirmed/checked-in bookings for this venue
        booked_dates = Booking.objects.filter(
            venue=venue,
            visit_date__range=[today, max_date],
            status__in=[BookingStatus.CONFIRMED, BookingStatus.CHECKED_IN]
        ).values_list('visit_date', flat=True)
        
        # Convert to string format for JS
        booked_dates_list = [d.isoformat() for d in booked_dates]
        
        # Get venue capacity constraints (if any)
        max_capacity = getattr(venue, 'max_daily_bookings', None)
        
        return JsonResponse({
            'success': True,
            'booked_dates': booked_dates_list,
            'max_capacity': max_capacity,
            'max_advance_days': 90
        })
    
    except Venue.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Venue not found'
        })