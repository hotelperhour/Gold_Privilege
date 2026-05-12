"""
discount_store/views.py

Removed: my_orders, order_confirmation
All post-payment redirects now go to bookings:list so users see
their unified booking history in one place.

cancel_order now redirects to bookings:list and also triggers
booking cancellation so both the StoreOrder and Booking stay in sync.
"""

import datetime
import logging
import secrets
from decimal import Decimal, InvalidOperation
from datetime import date, datetime

import requests as http_requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import models, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from account.permissions import subscriber_required
from bookings.models import Booking, BookingStatus
from subscriptions.models import Subscription
from wallet.models import Wallet, WalletTransaction
from wallet.utils import credit_wallet, debit_wallet

from .models import StoreConfig, StoreOrder, StoreProduct

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_wallet(user):
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


def _create_booking_from_order(order):
    """
    Create a Booking from a paid StoreOrder and link them together.
    The booking_reference is set to the order reference (GP-DS-XXXXXX)
    so there is only ONE reference the user ever needs to present.
    """
    booking = Booking.objects.create(
        user              = order.user,
        venue             = order.product.venue,
        subscription      = None,               # store bookings have no subscription
        booking_source    = 'STORE',
        visit_date        = order.visit_date,
        guests_count      = order.quantity,
        special_requests  = order.special_notes or '',
        status            = 'CONFIRMED',
        booking_reference = order.reference,    # same GP-DS- reference — ONE reference only
    )
    order.booking = booking
    order.status  = StoreOrder.OrderStatus.PAID
    order.save(update_fields=['booking', 'status', 'updated_at'])
    return booking


def _award_cashback(order):
    """Credit cashback. Idempotent — checks flag first. Called outside atomic blocks."""
    if order.cashback_awarded or order.cashback_coins == 0:
        return
    try:
        wallet = _get_wallet(order.user)
        credit_wallet(
            wallet   = wallet,
            amount   = order.cashback_coins,
            txn_type = WalletTransaction.TransactionType.CASHBACK,
            note     = f'Cashback for store order {order.reference} ({order.product.name})',
        )
        order.cashback_awarded = True
        order.save(update_fields=['cashback_awarded', 'updated_at'])
        logger.info(f'Cashback awarded: {order.cashback_coins} coins → {order.user.email}')
    except Exception as e:
        logger.error(f'Cashback failed for {order.reference}: {e}', exc_info=True)


def _send_confirmation_email(order):
    """Send the store booking confirmation email. Never raises — logs failures."""
    try:
        from django.core.mail import EmailMultiAlternatives
        from django.template.loader import render_to_string
        from django.utils.html import strip_tags

        html  = render_to_string('discount_store/emails/order_confirmation.html', {
            'order': order, 'booking': order.booking, 'user': order.user,
        })
        plain = strip_tags(html)
        msg   = EmailMultiAlternatives(
            subject   = f'Booking Confirmed — {order.product.name} | {order.reference}',
            body      = plain,
            from_email= settings.DEFAULT_FROM_EMAIL,
            to        = [order.user.email],
        )
        msg.attach_alternative(html, 'text/html')
        msg.send(fail_silently=True)
    except Exception as e:
        logger.error(f'Confirmation email failed for {order.reference}: {e}', exc_info=True)


def _verify_paystack(reference):
    url     = f'https://api.paystack.co/transaction/verify/{reference}'
    headers = {'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}'}
    try:
        resp = http_requests.get(url, headers=headers, timeout=10)
        return resp.json().get('data', {}).get('status') == 'success'
    except Exception as e:
        logger.error(f'Paystack verification error for {reference}: {e}')
        return False


# ─────────────────────────────────────────────────────────────────────────────
# STORE HOME
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@subscriber_required
def store_home(request):
    """
    Browse all active Discount Store products.
    No tier locking — any active subscriber can purchase any product.
    """
    wallet = _get_wallet(request.user)
    config = StoreConfig.get_config()

    products = StoreProduct.objects.filter(
        is_active=True,
        venue__access_mode__in=['STORE', 'BOTH'],
        venue__status='APPROVED',
        price__gt=0,
    ).select_related('venue').order_by('display_order', 'name')

    venue_id  = request.GET.get('venue')
    min_price = request.GET.get('min_price')
    max_price = request.GET.get('max_price')
    search    = request.GET.get('q', '').strip()
    tier      = request.GET.get('tier')

    if venue_id:
        products = products.filter(venue_id=venue_id)
    if min_price:
        try:
            products = products.filter(price__gte=Decimal(min_price))
        except Exception:
            pass
    if max_price:
        try:
            products = products.filter(price__lte=Decimal(max_price))
        except Exception:
            pass
    if tier:
        products = products.filter(venue__star_tier=tier)
    if search:
        products = products.filter(
            models.Q(name__icontains=search) |
            models.Q(venue__name__icontains=search)
        )

    product_list = []
    for p in products:
        p.cashback_preview = config.calculate_cashback(p.price)
        product_list.append(p)

    paginator   = Paginator(product_list, 60)
    page_obj    = paginator.get_page(request.GET.get('page'))

    from venues.models import Venue
    venues = Venue.objects.filter(
        access_mode__in=['STORE', 'BOTH'], status='APPROVED'
    ).order_by('name')

    return render(request, 'discount_store/store_home.html', {
        'products':    page_obj.object_list,
        'page_obj':    page_obj,
        'is_paginated':page_obj.has_other_pages(),
        'wallet':      wallet,
        'venues':      venues,
        'config':      config,
    })


# ─────────────────────────────────────────────────────────────────────────────
# PRODUCT DETAIL
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@subscriber_required
def store_product_detail(request, product_id):
    from venues.models import VenueReview

    product = get_object_or_404(
        StoreProduct,
        id=product_id, is_active=True, price__gt=0,
        venue__status='APPROVED',
        venue__access_mode__in=['STORE', 'BOTH'],
    )
    venue  = product.venue
    wallet = _get_wallet(request.user)
    config = StoreConfig.get_config()

    reviews_qs   = VenueReview.objects.filter(venue=venue, is_approved=True).select_related('user').order_by('-created_at')
    page_obj     = Paginator(reviews_qs, 5).get_page(request.GET.get('page'))

    user_has_reviewed = VenueReview.objects.filter(user=request.user, venue=venue).exists() if request.user.is_authenticated else False

    return render(request, 'discount_store/product_detail.html', {
        'product':             product,
        'venue':               venue,
        'wallet':              wallet,
        'config':              config,
        'cashback_preview':    config.calculate_cashback(product.price),
        'paystack_public_key': settings.PAYSTACK_PUBLIC_KEY,
        'today':               date.today().isoformat(),
        'reviews':             page_obj,
        'review_count':        Paginator(reviews_qs, 5).count,
        'page_obj':            page_obj,
        'is_paginated':        page_obj.has_other_pages(),
        'user_has_reviewed':   user_has_reviewed,
        'rating_distribution': {i: reviews_qs.filter(rating=i).count() for i in range(5, 0, -1)},
        'other_products':      StoreProduct.objects.filter(venue=venue, is_active=True, price__gt=0).exclude(id=product_id)[:4],
        'can_afford_coins':    wallet.balance >= product.price,
    })


# ─────────────────────────────────────────────────────────────────────────────
# CHECKOUT
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@subscriber_required
def store_checkout(request, product_id):
    product = get_object_or_404(
        StoreProduct, id=product_id, is_active=True, price__gt=0,
        venue__status='APPROVED', venue__access_mode__in=['STORE', 'BOTH'],
    )
    wallet = _get_wallet(request.user)
    config = StoreConfig.get_config()

    return render(request, 'discount_store/checkout.html', {
        'product':             product,
        'wallet':              wallet,
        'config':              config,
        'cashback_per_unit':   config.calculate_cashback(product.price),
        'max_quantity':        config.max_quantity_per_order,
        'paystack_public_key': settings.PAYSTACK_PUBLIC_KEY,
        'today':               date.today().isoformat(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# CARD PAYMENT — INITIATION
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@subscriber_required
@require_POST
def initiate_card_payment(request, product_id):
    from venues.models import VenueAccessMode

    product = get_object_or_404(
        StoreProduct, id=product_id, is_active=True,
        venue__access_mode__in=[VenueAccessMode.STORE, VenueAccessMode.BOTH],
    )

    if product.venue.access_mode == 'SUBSCRIPTION':
        return JsonResponse({'success': False, 'error': 'This venue requires a subscription booking.'})

    try:
        quantity         = int(request.POST.get('quantity', 1))
        visit_date_raw = request.POST.get('visit_date', '').strip()

        if not visit_date_raw:
            messages.error(request, 'Please select a visit date.')
            return redirect('discount_store:store_checkout', product_id=product_id)

        try:
            visit_date = datetime.strptime(visit_date_raw, "%Y-%m-%d").date()
        except ValueError:
            messages.error(request, 'Invalid visit date selected.')
            return redirect('discount_store:store_checkout', product_id=product_id)

        if visit_date < date.today():
            messages.error(request, 'Visit date cannot be in the past.')
            return redirect('discount_store:store_checkout', product_id=product_id)

        visit_time       = request.POST.get('visit_time', '').strip() or None
        special_requests = request.POST.get('special_requests', '').strip()[:1000]

        if not visit_date:
            return JsonResponse({'success': False, 'error': 'Please select a visit date.'})

        config = StoreConfig.get_config()
        if not (1 <= quantity <= config.max_quantity_per_order):
            return JsonResponse({'success': False, 'error': f'Quantity must be 1–{config.max_quantity_per_order}.'})

        amount         = product.price * quantity
        cashback_coins = config.calculate_cashback(amount)
        paystack_ref   = f'GP-DS-{secrets.token_hex(6).upper()}'

        order = StoreOrder.objects.create(
            user               = request.user,
            product            = product,
            quantity           = quantity,
            amount_paid        = amount,
            payment_method     = StoreOrder.PaymentMethod.CARD,
            paystack_reference = paystack_ref,
            status             = StoreOrder.OrderStatus.PENDING,
            cashback_coins     = cashback_coins,
            visit_date         = visit_date,
            visit_time         = visit_time,
            special_notes      = special_requests,
        )

        return JsonResponse({
            'success':     True,
            'reference':   paystack_ref,
            'amount_kobo': int(amount * 100),
            'email':       request.user.email,
            'order_id':    str(order.order_id),
        })

    except Exception as e:
        logger.error(f'initiate_card_payment error: {e}', exc_info=True)
        return JsonResponse({'success': False, 'error': 'An error occurred. Please try again.'})


# ─────────────────────────────────────────────────────────────────────────────
# CARD PAYMENT — CALLBACK
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def card_payment_callback(request):
    """
    Paystack browser redirect after payment.
    On success → redirects to bookings:list so user sees their unified booking.
    """
    reference = request.GET.get('reference', '').strip()
    if not reference:
        messages.error(request, 'No payment reference found.')
        return redirect('discount_store:store_home')

    try:
        order = StoreOrder.objects.get(paystack_reference=reference, user=request.user)
    except StoreOrder.DoesNotExist:
        messages.error(request, 'Order not found.')
        return redirect('discount_store:store_home')

    # Already processed — idempotency guard
    if order.status == StoreOrder.OrderStatus.PAID:
        messages.success(request, f'Your booking at {order.product.venue.name} is confirmed!')
        return redirect('bookings:list')

    if not _verify_paystack(reference):
        messages.error(request, 'Payment verification failed. Contact support if you were charged.')
        return redirect('discount_store:store_checkout', product_id=order.product.id)

    try:
        with transaction.atomic():
            _create_booking_from_order(order)
    except Exception as e:
        logger.error(f'Booking creation failed for order {order.reference}: {e}', exc_info=True)
        messages.error(request, 'Payment received but booking setup failed. Contact support.')
        return redirect('discount_store:store_home')

    _award_cashback(order)
    _send_confirmation_email(order)

    cashback_msg = f' You earned {order.cashback_coins:,} cashback coins!' if order.cashback_coins > 0 else ''
    messages.success(
        request,
        f'Booking confirmed at {order.product.venue.name}!{cashback_msg} '
        f'Your reference is {order.reference}.'
    )
    # ← Redirect to unified booking list, not a separate confirmation page
    return redirect('bookings:list')


# ─────────────────────────────────────────────────────────────────────────────
# COIN PAYMENT
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@subscriber_required
@require_POST
def pay_with_coins(request, product_id):
    """
    Atomic wallet deduction + order creation + booking creation.
    Redirects to bookings:list on success.
    """
    from venues.models import VenueAccessMode

    product = get_object_or_404(
        StoreProduct, id=product_id, is_active=True,
        venue__access_mode__in=[VenueAccessMode.STORE, VenueAccessMode.BOTH],
    )

    if product.venue.access_mode == 'SUBSCRIPTION':
        messages.error(request, 'This venue requires a subscription booking.')
        return redirect('venues:detail', slug=product.venue.slug)

    try:
        quantity         = int(request.POST.get('quantity', 1))
        visit_date_raw = request.POST.get('visit_date', '').strip()

        if not visit_date_raw:
            messages.error(request, 'Please select a visit date.')
            return redirect('discount_store:store_checkout', product_id=product_id)

        try:
            visit_date = datetime.strptime(visit_date_raw, "%Y-%m-%d").date()
        except ValueError:
            messages.error(request, 'Invalid visit date selected.')
            return redirect('discount_store:store_checkout', product_id=product_id)

        if visit_date < date.today():
            messages.error(request, 'Visit date cannot be in the past.')
            return redirect('discount_store:store_checkout', product_id=product_id)

        visit_time       = request.POST.get('visit_time', '').strip() or None
        special_requests = request.POST.get('special_requests', '').strip()[:1000]

        if not visit_date:
            messages.error(request, 'Please select a visit date.')
            return redirect('discount_store:store_checkout', product_id=product_id)

        config = StoreConfig.get_config()
        if not (1 <= quantity <= config.max_quantity_per_order):
            messages.error(request, f'Quantity must be 1–{config.max_quantity_per_order}.')
            return redirect('discount_store:store_checkout', product_id=product_id)

        amount         = product.price * quantity
        coins_needed   = int(amount)
        cashback_coins = config.calculate_cashback(amount)
        wallet         = _get_wallet(request.user)

        if wallet.balance < coins_needed:
            messages.error(
                request,
                f'You need {coins_needed:,} coins but have {int(wallet.balance):,}. '
                f'Top up your wallet to continue.'
            )
            return redirect('discount_store:store_checkout', product_id=product_id)

        with transaction.atomic():
            debit_wallet(
                wallet   = wallet,
                amount   = coins_needed,
                txn_type = WalletTransaction.TransactionType.STORE_PURCHASE,
                note     = f'Store purchase: {product.name} at {product.venue.name} × {quantity}',
            )
            order = StoreOrder.objects.create(
                user           = request.user,
                product        = product,
                quantity       = quantity,
                amount_paid    = amount,
                payment_method = StoreOrder.PaymentMethod.COINS,
                status         = StoreOrder.OrderStatus.PENDING,
                cashback_coins = cashback_coins,
                visit_date     = visit_date,
                visit_time     = visit_time,
                special_notes  = special_requests,
            )
            _create_booking_from_order(order)

    except ValueError as e:
        messages.error(request, str(e))
        return redirect('discount_store:store_checkout', product_id=product_id)
    except Exception as e:
        logger.error(f'pay_with_coins error: {e}', exc_info=True)
        messages.error(request, 'An unexpected error occurred. Please try again.')
        return redirect('discount_store:store_checkout', product_id=product_id)

    _award_cashback(order)
    _send_confirmation_email(order)

    cashback_msg = f' You earned {order.cashback_coins:,} cashback coins!' if order.cashback_coins > 0 else ''
    messages.success(
        request,
        f'Booking confirmed at {order.product.venue.name}!{cashback_msg} '
        f'Your reference is {order.reference}.'
    )
    # ← Unified booking list
    return redirect('bookings:list')


# ─────────────────────────────────────────────────────────────────────────────
# CANCEL ORDER (redirects to bookings:list)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def cancel_order(request, order_id):
    """
    Cancel a PAID store order before the cutoff window.
    Always refunds coins to wallet (card and coin payments both get wallet credit).
    Claws back cashback coins if they were awarded.
    Cancels the linked Booking row too.
    Redirects to bookings:list — no separate store orders page.
    """
    order = get_object_or_404(StoreOrder, order_id=order_id, user=request.user)

    if not order.can_cancel():
        config = StoreConfig.get_config()
        messages.error(
            request,
            f'Cannot cancel — the {config.cancellation_cutoff_hours}‑hour window has passed.'
        )
        return redirect('bookings:list')

    reason = request.POST.get('reason', 'User requested cancellation')[:500]

    with transaction.atomic():
        wallet = _get_wallet(request.user)

        # Refund full amount to wallet for both payment methods
        credit_wallet(
            wallet   = wallet,
            amount   = int(order.amount_paid),
            txn_type = WalletTransaction.TransactionType.STORE_REFUND,
            note     = f'Refund for cancelled store order {order.reference}',
        )

        # Claw back cashback if it was awarded
        if order.cashback_awarded and order.cashback_coins > 0:
            try:
                debit_wallet(
                    wallet   = wallet,
                    amount   = order.cashback_coins,
                    txn_type = WalletTransaction.TransactionType.CASHBACK_CLAWBACK,
                    note     = f'Cashback removed for cancelled order {order.reference}',
                )
                order.cashback_awarded = False
                order.cashback_coins   = 0
            except ValueError:
                logger.warning(f'Could not claw back cashback for {order.reference} — insufficient balance.')

        # Cancel the linked booking
        if order.booking:
            order.booking.status = 'CANCELLED'
            order.booking.save(update_fields=['status'])

        order.status              = StoreOrder.OrderStatus.CANCELLED
        order.cancelled_by        = 'USER'
        order.cancellation_reason = reason
        order.cancelled_at        = timezone.now()
        order.save(update_fields=[
            'status', 'cancelled_by', 'cancellation_reason',
            'cancelled_at', 'cashback_awarded', 'cashback_coins', 'updated_at'
        ])

    messages.success(
        request,
        f'Booking cancelled. {int(order.amount_paid):,} coins refunded to your wallet.'
    )
    return redirect('bookings:list')   # ← unified list