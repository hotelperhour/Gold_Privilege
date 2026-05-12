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
from django import forms
from datetime import date, timedelta
import json
from django.db import transaction
from account.permissions import subscriber_required, IsApprovedPartnerMixin, approved_partner_required
from subscriptions.utils import get_all_feature_usage, decrement_feature_usage, can_use_feature, increment_feature_usage,get_or_create_feature_usage
from .models import Booking, BookingStatus, BookingActivity
from .forms import BookingCreateForm, BookingCancelForm, VenueCheckInForm
from venues.models import Venue
from subscriptions.models import Subscription
from django.core.cache import cache
from django.http import HttpResponse
import logging
logger = logging.getLogger(__name__)


def check_rate_limit(request, action='checkin', limit=10, period=60):
    """
    Rate limit check-in attempts to prevent abuse
    Returns None if OK, HttpResponse if rate limited
    """
    key = f'{action}_attempts_{request.user.id}'
    attempts = cache.get(key, 0)
    
    if attempts >= limit:
        return HttpResponse(
            f'Too many {action} attempts. Please wait a moment.',
            status=429
        )
    
    cache.set(key, attempts + 1, period)
    return None

# ==================== MEMBER VIEWS ====================


@login_required
@subscriber_required
def booking_create(request, venue_slug):
    """
    Create a booking with feature-based quota validation
    """
    venue = get_object_or_404(
        Venue.objects.select_related('partner', 'primary_feature'),
        slug=venue_slug,
        status='APPROVED'
    )
    
    # Get active subscription
    subscription = Subscription.objects.filter(
        user=request.user,
        status__in=['ACTIVE', 'TRIAL'],
        end_date__gte=timezone.now().date()
    ).first()
    
    if not subscription:
        messages.error(request, 'You need an active subscription to book venues.')
        return redirect('subscriptions:plans_list')
    can_access, access_message = venue.is_accessible_by(request.user)
    if not can_access:
        if venue.access_mode == 'STORE':
            # Store-only: send to the Discount Store
            messages.error(
                request,
                'This venue is only bookable through the Discount Store.'
            )
            return redirect('discount_store:store_home')
        else:
            # Tier too low: send to subscription upgrade page
            messages.error(request, access_message)
            return redirect('venues:detail', slug=venue_slug)
    
    # ── CHECK FEATURE QUOTA ──
    feature_name = None
    can_book = True
    remaining = None
    used = None
    limit = None
    
    if venue.primary_feature:
        can_book, remaining, msg = can_use_feature(
            subscription,
            venue.primary_feature
        )
        
        if not can_book:
            messages.error(request, msg)
            return redirect('venues:detail', slug=venue_slug)
        
        # Get usage stats for display
        from subscriptions.utils import get_or_create_feature_usage
        feature_usage, _ = get_or_create_feature_usage(
            subscription,
            venue.primary_feature
        )
        
        feature_name = venue.primary_feature.name
        used = feature_usage.used_count
        limit = feature_usage.get_limit()
    
    # Max guests from plan
    max_guests = subscription.plan.max_guests_per_booking
    
    if request.method == 'POST':
        form = BookingCreateForm(request.POST, user=request.user)
        
        if form.is_valid():
            # Double-check quota (race condition prevention)
            if venue.primary_feature:
                can_book, remaining, msg = can_use_feature(
                    subscription,
                    venue.primary_feature
                )
                
                if not can_book:
                    messages.error(request, msg)
                    return redirect('venues:detail', slug=venue_slug)
            
            try:
                with transaction.atomic():
                    booking = form.save(commit=False)
                    booking.user = request.user
                    booking.venue = venue  # ← Override venue from form
                    booking.subscription = subscription
                    booking.save()
                    
                    # Increment feature usage
                    if venue.primary_feature:
                        increment_feature_usage(
                            subscription,
                            venue.primary_feature
                        )
                    
                    messages.success(
                        request,
                        f'Booking confirmed for {venue.name} on {booking.visit_date.strftime("%B %d, %Y")}'
                    )
                    
                    return redirect('bookings:list')
            
            except Exception as e:
                messages.error(request, 'An error occurred while creating your booking. Please try again.')
    
    else:
        # Initialize form with venue pre-selected
        form = BookingCreateForm(
            user=request.user,
            initial={'venue': venue.id, 'guests_count': 1}  # ← Pass venue ID in initial
        )
        # Hide venue field since it's already selected
        form.fields['venue'].widget = forms.HiddenInput()
    
    feature_icon = None
    if venue.primary_feature:
        feature_icon = venue.primary_feature.icon
    
    context = {
        'form': form,
        'venue': venue,
        'subscription': subscription,
        'max_guests': max_guests,
        'feature_name': feature_name,
        'feature_icon': feature_icon,
        'remaining': remaining,
        'used': used,
        'limit': limit,
    }
    
    return render(request, 'bookings/member/create.html', context)


# ════════════════════════════════════════════════════════════════════════════
# REPLACE your existing booking_list view with this version.
# Adds two filters:
#   - source: ALL / SUBSCRIPTION / STORE
#   - category: any VenueCategory value
# Everything else is identical to your existing view.
# ════════════════════════════════════════════════════════════════════════════

@login_required
@subscriber_required
def booking_list(request):
    """
    Unified booking list — shows subscription AND store bookings together.
    Filters: source (ALL/SUBSCRIPTION/STORE) and venue category.

    Teaching note:
      We annotate each booking with `is_store_booking` in Python rather than
      adding a template tag, because templates should stay logic-light.
      The annotation also lets us look up the linked StoreOrder cheaply —
      we do it once in the view rather than once per row in the template.
    """
    from venues.models import VenueCategory

    # ── Base queryset ─────────────────────────────────────────────────────
    all_bookings = (
        Booking.objects
        .filter(user=request.user)
        .select_related('venue', 'subscription', 'venue__primary_feature')
        .order_by('-visit_date', '-created_at')
    )

    # ── Filters ───────────────────────────────────────────────────────────
    source_filter   = request.GET.get('source', 'ALL').upper()
    category_filter = request.GET.get('category', '').strip()

    if source_filter == 'SUBSCRIPTION':
        all_bookings = all_bookings.filter(booking_source='SUBSCRIPTION')
    elif source_filter == 'STORE':
        all_bookings = all_bookings.filter(booking_source='STORE')
    # 'ALL' — no filter

    if category_filter and category_filter in dict(VenueCategory.choices):
        all_bookings = all_bookings.filter(venue__category=category_filter)

    # ── Date groupings (before pagination) ────────────────────────────────
    today = date.today()

    today_bookings = all_bookings.filter(
        status__in=[BookingStatus.CONFIRMED, BookingStatus.CHECKED_IN],
        visit_date=today,
    )
    upcoming_bookings = all_bookings.filter(
        status=BookingStatus.CONFIRMED,
        visit_date__gt=today,
    )

    # ── Feature usage (subscription plan quota) ───────────────────────────
    active_subscription = (
        Subscription.objects
        .filter(user=request.user, status__in=['ACTIVE', 'TRIAL'], end_date__gte=today)
        .select_related('plan')
        .first()
    )
    feature_usage = {}
    if active_subscription:
        feature_usage = get_all_feature_usage(active_subscription)

    # ── Pagination ────────────────────────────────────────────────────────
    paginator   = Paginator(all_bookings, 10)
    page_obj    = paginator.get_page(request.GET.get('page'))

    # ── Per-row data: quota remaining + store order link ──────────────────
    # We batch-fetch store orders for all bookings on this page to avoid
    # N+1 queries (one extra query total, not one per booking row).
    page_references = [b.booking_reference for b in page_obj]
    store_orders_map = {}

    try:
        from discount_store.models import StoreOrder
        store_orders = (
            StoreOrder.objects
            .filter(reference__in=page_references)
            .select_related('product')
        )
        store_orders_map = {o.reference: o for o in store_orders}
    except Exception:
        pass  # discount_store app not yet fully migrated — graceful degradation

    bookings_with_quota = []
    for booking in page_obj:
        store_order = store_orders_map.get(booking.booking_reference)
        row = {
            'booking':          booking,
            'remaining_quota':  None,
            'is_store_booking': booking.booking_source == 'STORE',
            'store_order':      store_order,   # None for subscription bookings
        }

        # Remaining quota only makes sense for subscription bookings
        if booking.booking_source != 'STORE' and booking.venue.primary_feature and booking.subscription:
            try:
                fu, _ = get_or_create_feature_usage(booking.subscription, booking.venue.primary_feature)
                row['remaining_quota'] = fu.get_limit() - fu.used_count + 1
            except Exception:
                pass

        bookings_with_quota.append(row)

    context = {
        # Bookings
        'all_bookings':       all_bookings,
        'today_bookings':     today_bookings,
        'upcoming_bookings':  upcoming_bookings,
        'page_obj':           page_obj,
        'bookings_with_quota':bookings_with_quota,
        'is_paginated':       page_obj.has_other_pages(),
        'today':              today,
        # Subscription
        'active_subscription':active_subscription,
        'feature_usage':      feature_usage,
        # Filters — passed back so the template can pre-select them
        'source_filter':      source_filter,
        'category_filter':    category_filter,
        'categories':         VenueCategory.choices,
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
    store_order = None
    try:
        if booking.booking_source == 'STORE':
            from discount_store.models import StoreOrder
            store_order = booking.store_order
    except Exception:
        pass
    
    context = {
        'booking': booking,
        'venue': venue,
        'can_cancel': can_cancel,
        'map_url': map_url,
        'qr_data': qr_data,
        'store_order': store_order,
        'today': date.today(),
        'is_store_booking': booking.booking_source == 'STORE',
    }
    
    return render(request, 'bookings/member/detail.html', context)



@login_required
@subscriber_required
@require_POST
def booking_cancel(request, booking_reference):
    """
    Cancel a booking - RESTORES FEATURE QUOTA
    """
    booking = get_object_or_404(
        Booking,
        booking_reference=booking_reference,
        user=request.user
    )
    
    if not booking.can_cancel():
        messages.error(request, 'This booking cannot be cancelled.')
        return redirect('bookings:list')
    
    form = BookingCancelForm(request.POST)
    
    if form.is_valid():
        reason = form.cleaned_data.get('cancellation_reason', '')
        
        try:
            with transaction.atomic():
                # Cancel booking
                booking.cancel(reason=reason, cancelled_by=request.user)

                # ── RESTORE FEATURE QUOTA (subscription bookings only) ──
                if booking.booking_source != 'STORE' and booking.venue.primary_feature and booking.subscription:
                    decrement_feature_usage(
                        booking.subscription,
                        booking.venue.primary_feature
                    )

                # ── STORE ORDER REFUND (store bookings only) ──
                if booking.booking_source == 'STORE':
                    try:
                        store_order = booking.store_order  # reverse OneToOne from StoreOrder.booking
                    except Exception:
                        store_order = None

                    if store_order and store_order.status == 'PAID':
                        from wallet.models import Wallet, WalletTransaction
                        from wallet.utils import credit_wallet, debit_wallet

                        wallet, _ = Wallet.objects.get_or_create(user=request.user)

                        # Refund to wallet regardless of payment method
                        credit_wallet(
                            wallet=wallet,
                            amount=int(store_order.amount_paid),
                            txn_type=WalletTransaction.TransactionType.STORE_REFUND,
                            note=f'Refund for cancelled store order {store_order.reference}',
                        )

                        # Claw back cashback using CASHBACK_CLAWBACK (shows as debit correctly)
                        if store_order.cashback_awarded and store_order.cashback_coins > 0:
                            try:
                                debit_wallet(
                                    wallet=wallet,
                                    amount=store_order.cashback_coins,
                                    txn_type=WalletTransaction.TransactionType.CASHBACK_CLAWBACK,
                                    note=f'Cashback removed for cancelled order {store_order.reference}',
                                )
                                store_order.cashback_awarded = False
                                store_order.cashback_coins   = 0
                            except ValueError:
                                logger.warning(
                                    f'Could not claw back cashback for {store_order.reference} — insufficient balance.'
                                )

                        store_order.status              = 'CANCELLED'
                        store_order.cancelled_by        = 'USER'
                        store_order.cancellation_reason = reason
                        store_order.cancelled_at        = timezone.now()
                        store_order.save(update_fields=[
                            'status', 'cancelled_by', 'cancellation_reason',
                            'cancelled_at', 'cashback_awarded', 'cashback_coins', 'updated_at'
                        ])
                # Log activity
                BookingActivity.objects.create(
                    booking=booking,
                    action='CANCELLED',
                    performed_by=request.user,
                    notes=f'Cancelled: {reason[:100]}'
                )
            
            if booking.booking_source == 'STORE':
                try:
                    store_order = booking.store_order
                    messages.success(
                        request,
                        f'Booking cancelled. {int(store_order.amount_paid):,} coins have been refunded to your wallet.'
                    )
                except Exception:
                    messages.success(request, 'Booking cancelled successfully.')
            else:
                messages.success(request, 'Booking cancelled successfully. Your quota has been restored.')
        
        except Exception as e:
            messages.error(request, f'Error cancelling booking: {str(e)}')
            return redirect('bookings:list')
    
    for error in form.errors.values():
        messages.error(request, error)
    
    return redirect('bookings:list')
# ==================== PARTNER VIEWS ====================

class PartnerBookingsListView(IsApprovedPartnerMixin, ListView):
    """
    Partner view: All bookings for their venues
    """
    model = Booking
    template_name = 'bookings/partner/list.html'
    context_object_name = 'bookings'
    paginate_by = 10
    
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

        # Mask Booking References 
        bookings = context['bookings']
        for booking in bookings:
            if booking.status == BookingStatus.CONFIRMED:
                # Hide reference until check-in
                booking.display_reference = '••••••' + booking.booking_reference[-4:]
            else:
                # Show full reference after check-in
                booking.display_reference = booking.booking_reference
        
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


@approved_partner_required
@login_required
def partner_check_in(request):
    """
    Check in a booking via QR scan, upload, or manual entry
    SECURE: No quick check-in, rate limited, audit logged
    """
    # Verify user is a partner
    if not hasattr(request.user, 'partner_profile'):
        return HttpResponseForbidden("Access denied. Partner account required.")
    
    # Get partner's venues
    partner_venues = request.user.partner_profile.venues.all()
    
    if request.method == 'POST':
        # ── RATE LIMITING ──
        rate_limit_response = check_rate_limit(request, 'checkin', limit=10, period=60)
        if rate_limit_response:
            messages.error(request, 'Too many check-in attempts. Please wait a moment.')
            return redirect('bookings:partner_check_in')
        
        form = VenueCheckInForm(request.POST)
        
        if form.is_valid():
            booking = form.cleaned_data['booking']
            notes = form.cleaned_data.get('check_in_notes', '')[:500]  # ← LIMIT LENGTH
            
            # Verify booking is for partner's venue
            if booking.venue not in partner_venues:
                # ── LOG FAILED ATTEMPT ──
                BookingActivity.objects.create(
                    booking=booking,
                    action='CHECKIN_FAILED',
                    performed_by=request.user,
                    notes='Attempted check-in for wrong venue'
                )
                messages.error(request, 'This booking is not for one of your venues.')
                return redirect('bookings:partner_check_in')
            
            # Check if already checked in
            if booking.status != BookingStatus.CONFIRMED:
                messages.warning(request, f'This booking is already {booking.get_status_display()}.')
                return redirect('bookings:partner_check_in')
            
            try:
                with transaction.atomic():
                    booking.check_in(
                        checked_in_by=request.user,
                        notes=notes
                    )
                    
                    # ── AUDIT LOG SUCCESS ──
                    BookingActivity.objects.create(
                        booking=booking,
                        action='CHECKED_IN',
                        performed_by=request.user,
                        notes=f'Checked in by partner: {notes[:100]}'
                    )
                    
                    # ── EMAIL NOTIFICATION (Optional) ──
                    try:
                        from django.core.mail import send_mail
                        send_mail(
                            subject='Check-in Confirmed',
                            message=f'You have been checked in at {booking.venue.name}',
                            from_email='noreply@goldprivilege.com',
                            recipient_list=[booking.user.email],
                            fail_silently=True
                        )
                    except Exception as e:
                        # Don't fail check-in if email fails
                        pass
                
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
            # ── LOG FORM ERRORS ──
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'{field}: {error}')
    
    else:
        form = VenueCheckInForm()
    
    # Get today's bookings with pagination
    today_bookings_all = Booking.objects.filter(
        venue__in=partner_venues,
        visit_date=date.today()
    ).select_related('user', 'venue').order_by('-created_at')
    
    # ── MASK REFERENCES IN TODAY'S LIST TOO ──
    for booking in today_bookings_all:
        if booking.status == BookingStatus.CONFIRMED:
            booking.display_reference = '••••••' + booking.booking_reference[-4:]
        else:
            booking.display_reference = booking.booking_reference
    
    # Pagination (10 per page)
    paginator = Paginator(today_bookings_all, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'form': form,
        'page_obj': page_obj,
        'is_paginated': page_obj.has_other_pages(),
        'today_bookings': page_obj,
        'partner_venues': partner_venues,
        'today': date.today(),
    }
    
    return render(request, 'bookings/partner/check_in.html', context)



@login_required
def partner_booking_detail(request, booking_uuid):
    """
    Partner view of a specific booking
    """
    # Verify user is a partner
    if not hasattr(request.user, 'partner_profile'):
        return HttpResponseForbidden("Access denied. Partner account required.")
    
    booking = get_object_or_404(
        Booking.objects.select_related('user', 'venue', 'subscription'),
        booking_id=booking_uuid
    )
    if booking.status == BookingStatus.CONFIRMED:
        booking.display_reference = '••••••' + booking.booking_reference[-4:]
    else:
        booking.display_reference = booking.booking_reference
    
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