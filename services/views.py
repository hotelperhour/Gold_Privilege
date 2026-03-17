"""
services/views.py

Views for the Gold Privilege services feature.
Quota is VALUE-BASED (naira) for airtime/data, COUNT-BASED for vouchers.
"""

import logging
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from subscriptions.models import Subscription
from .models import Service, ServiceCategory, DeliveryType, ServicePurchase, VoucherInventory, NetworkProvider
from .utils import (
    check_service_quota,
    get_all_service_quotas,
    process_service_request,
)

logger = logging.getLogger(__name__)


def _get_active_subscription(user):
    return Subscription.objects.filter(
        user=user,
        status__in=['ACTIVE', 'TRIAL'],
        end_date__gte=timezone.now(),
    ).select_related('plan').first()


# ──────────────────────────────────────────────
# SERVICES HOME
# ──────────────────────────────────────────────

@login_required
def services_home(request):
    subscription = _get_active_subscription(request.user)
    if not subscription:
        return render(request, 'services/no_subscription.html')

    quota_summary = get_all_service_quotas(request.user, subscription)

    grouped = {}
    for item in quota_summary:
        cat = item['service'].get_category_display()
        grouped.setdefault(cat, []).append(item)

    return render(request, 'services/services_home.html', {
        'subscription':  subscription,
        'quota_summary': quota_summary,
        'grouped':       grouped,
    })


# ──────────────────────────────────────────────
# SERVICE DETAIL / REQUEST FORM
# ──────────────────────────────────────────────

@login_required
def service_detail(request, service_id):
    service      = get_object_or_404(Service, id=service_id, is_active=True)
    subscription = _get_active_subscription(request.user)

    if not subscription:
        messages.error(request, "You need an active subscription to use this service.")
        return redirect('services:home')

    # check_service_quota now returns 6 values
    allowed, remaining, quota_msg, plan_quota, min_amount, max_amount = check_service_quota(
        request.user, service, subscription
    )

    if request.method == 'POST':
        return _handle_service_post(
            request, service, subscription,
            allowed, plan_quota, min_amount, max_amount, remaining
        )

    is_voucher = service.delivery_type == DeliveryType.MANUAL_CODE
    is_airtime = service.category == ServiceCategory.AIRTIME
    is_data    = service.category == ServiceCategory.DATA

    ctx = {
        'service':      service,
        'subscription': subscription,
        'allowed':      allowed,
        'remaining':    remaining,
        'quota_msg':    quota_msg,
        'plan_quota':   plan_quota,
        'min_amount':   min_amount,
        'max_amount':   max_amount,
        'networks':     NetworkProvider.choices,
        'is_airtime':   is_airtime,
        'is_data':      is_data,
        'is_voucher':   is_voucher,
    }

    if is_voucher and service.fixed_amounts:
        ctx['voucher_amounts'] = service.fixed_amounts
        stock = {}
        for amt in service.fixed_amounts:
            stock[amt] = VoucherInventory.objects.filter(
                service=service,
                status='AVAILABLE',
                amount=amt,
            ).exclude(expires_at__lt=timezone.now().date()).exists()
        ctx['voucher_stock'] = stock

    return render(request, 'services/service_detail.html', ctx)


def _handle_service_post(request, service, subscription,
                          allowed, plan_quota, min_amount, max_amount, remaining):
    if not allowed:
        messages.error(request, "You have no remaining quota for this service this month.")
        return redirect('services:detail', service_id=service.id)

    phone    = request.POST.get('phone', '').strip()
    network  = request.POST.get('network', '').strip()
    amount_s = request.POST.get('amount', '').strip()

    # Validate amount first
    try:
        amount = Decimal(amount_s)
        if amount <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        messages.error(request, "Please enter a valid amount.")
        return redirect('services:detail', service_id=service.id)

    # Per-transaction min/max checks (while amount still holds the user's input)
    if service.category == ServiceCategory.DATA:
        # For data, amount = GB entered by user, min/max are in GB
        if min_amount and amount < min_amount:
            messages.error(request, f"Minimum is {min_amount:.1f} GB per top-up.")
            return redirect('services:detail', service_id=service.id)
        if max_amount and amount > max_amount:
            messages.error(request, f"Maximum is {max_amount:.1f} GB per top-up.")
            return redirect('services:detail', service_id=service.id)
        # NOW reassign after checks pass
        data_gb = amount
        #amount  = Decimal('0')
    else:
        data_gb = None
        if min_amount and amount < min_amount:
            messages.error(request, f"Minimum top-up amount is ₦{min_amount:,.0f}.")
            return redirect('services:detail', service_id=service.id)
        if max_amount and amount > max_amount:
            if remaining is not None and amount > remaining:
                messages.error(request, f"You have ₦{remaining:,.0f} left this month.")
            else:
                messages.error(request, f"Maximum single top-up is ₦{max_amount:,.0f}.")
            return redirect('services:detail', service_id=service.id)

    # Airtime/data: require phone + network
    if service.category in (ServiceCategory.AIRTIME, ServiceCategory.DATA):
        if not phone:
            messages.error(request, "Please enter a phone number.")
            return redirect('services:detail', service_id=service.id)
        if not network:
            messages.error(request, "Please select a network provider.")
            return redirect('services:detail', service_id=service.id)

    purchase, error = process_service_request(
        user=request.user,
        service=service,
        subscription=subscription,
        amount=amount,
        phone=phone,
        network=network,
        data_gb=data_gb,
    )

    if purchase:
        return redirect('services:confirmation', purchase_id=purchase.purchase_id)

    messages.error(request, error or "Something went wrong. Please try again.")
    return redirect('services:detail', service_id=service.id)


# ──────────────────────────────────────────────
# CONFIRMATION
# ──────────────────────────────────────────────

@login_required
def service_confirmation(request, purchase_id):
    purchase = get_object_or_404(
        ServicePurchase, purchase_id=purchase_id, user=request.user
    )
    return render(request, 'services/service_confirmation.html', {'purchase': purchase})


# ──────────────────────────────────────────────
# PURCHASE HISTORY
# ──────────────────────────────────────────────

@login_required
def purchase_history(request):
    purchases = ServicePurchase.objects.filter(
        user=request.user
    ).select_related('service').order_by('-created_at')
    return render(request, 'services/purchase_history.html', {'purchases': purchases})


# ──────────────────────────────────────────────
# AJAX QUOTA CHECK
# ──────────────────────────────────────────────

@login_required
def ajax_check_quota(request, service_id):
    service      = get_object_or_404(Service, id=service_id, is_active=True)
    subscription = _get_active_subscription(request.user)

    if not subscription:
        return JsonResponse({'allowed': False, 'message': 'No active subscription.'})

    allowed, remaining, msg, quota, min_amount, max_amount = check_service_quota(
        request.user, service, subscription
    )

    return JsonResponse({
        'allowed':     allowed,
        'remaining':   str(remaining) if remaining is not None else None,
        'unlimited':   quota.is_unlimited() if quota else False,
        'message':     msg,
        'min_amount':  str(min_amount) if min_amount else None,
        'max_amount':  str(max_amount) if max_amount else None,
    })