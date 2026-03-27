"""
services/views.py

Views for the Gold Privilege services feature.
Quota is VALUE-BASED (naira) for airtime/data, COUNT-BASED for vouchers.
"""

import logging
from decimal import Decimal, InvalidOperation
from collections import defaultdict
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from datetime import date
from subscriptions.models import Subscription
from .models import Service, ServiceCategory, DeliveryType, ServicePurchase, VoucherInventory, NetworkProvider, VoucherType
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
 
    # ── Voucher: derive the SINGLE option from the plan quota ───────────────
    # Do NOT show all inventory — show only what this plan specifies.
    if is_voucher and plan_quota:
        today = date.today()
        voucher_option = None  # single option, not a list
 
        if plan_quota.voucher_type == VoucherType.FIXED_AMOUNT and plan_quota.voucher_fixed_amount:
            val = plan_quota.voucher_fixed_amount
            in_stock = VoucherInventory.objects.filter(
                service=service,
                status=VoucherInventory.VoucherStatus.AVAILABLE,
                voucher_type=VoucherType.FIXED_AMOUNT,
                amount=val,
            ).exclude(expires_at__lt=today).exists()
            voucher_option = {
                'type':     VoucherType.FIXED_AMOUNT,
                'value':    val,
                'label':    f'₦{val:,.0f}',
                'in_stock': in_stock,
            }
 
        elif plan_quota.voucher_type == VoucherType.PERCENTAGE_DISCOUNT and plan_quota.voucher_discount_percentage:
            val = plan_quota.voucher_discount_percentage
            in_stock = VoucherInventory.objects.filter(
                service=service,
                status=VoucherInventory.VoucherStatus.AVAILABLE,
                voucher_type=VoucherType.PERCENTAGE_DISCOUNT,
                discount_percentage=val,
            ).exclude(expires_at__lt=today).exists()
            voucher_option = {
                'type':     VoucherType.PERCENTAGE_DISCOUNT,
                'value':    val,
                'label':    f'{val}% discount',
                'in_stock': in_stock,
            }
 
        ctx['voucher_option'] = voucher_option
    # ────────────────────────────────────────────────────────────────────────
 
    return render(request, 'services/service_detail.html', ctx)


def _handle_service_post(request, service, subscription,
                          allowed, plan_quota, min_amount, max_amount, remaining):
    """
    Process the POST request for service detail.
 
    SECURITY: For voucher services, the voucher type and value are derived
    from the server-side plan_quota — NOT from POST data. This prevents a
    user from manipulating the form to claim a higher-value voucher.
    """
    redirect_url = redirect('services:detail', service_id=service.id)
 
    if not allowed:
        messages.error(request, "You have exhausted your monthly quota for this service.")
        return redirect_url
 
    # ── VOUCHER PATH ─────────────────────────────────────────────────────────
    if service.delivery_type == DeliveryType.MANUAL_CODE:
 
        # Derive voucher spec from plan quota — NEVER from POST
        if not plan_quota:
            messages.error(request, "This service is not configured for your plan. Contact support.")
            return redirect_url
 
        if not plan_quota.voucher_type:
            messages.error(request, "Your plan does not have a voucher type configured. Contact support.")
            return redirect_url
 
        if plan_quota.voucher_type == VoucherType.FIXED_AMOUNT:
            if not plan_quota.voucher_fixed_amount:
                messages.error(request, "Voucher value is not configured. Contact support.")
                return redirect_url
            voucher_type  = VoucherType.FIXED_AMOUNT
            voucher_value = plan_quota.voucher_fixed_amount
            amount        = voucher_value
 
        else:  # PERCENTAGE_DISCOUNT
            if not plan_quota.voucher_discount_percentage:
                messages.error(request, "Voucher discount is not configured. Contact support.")
                return redirect_url
            voucher_type  = VoucherType.PERCENTAGE_DISCOUNT
            voucher_value = plan_quota.voucher_discount_percentage
            amount        = Decimal('0')  # No naira face value for percentage vouchers
 
        result, error = process_service_request(
            user=request.user,
            service=service,
            subscription=subscription,
            amount=amount,
            phone='',
            network='',
            voucher_type=voucher_type,
            voucher_value=voucher_value,
        )
 
        if error:
            messages.error(request, error)
            return redirect_url
 
        # Safety check before accessing .voucher
        if not result or not result.voucher:
            logger.error(
                f'Voucher assigned but purchase.voucher is None: '
                f'user={request.user.email} service={service.name}'
            )
            messages.error(request, "Voucher was assigned but could not be retrieved. Contact support.")
            return redirect_url
 
        return redirect('services:confirmation', purchase_id=result.purchase_id)
 
    # ── AIRTIME PATH ─────────────────────────────────────────────────────────
    amount_str = request.POST.get('amount', '').strip()
    if not amount_str:
        messages.error(request, "Please enter a valid amount.")
        return redirect_url
 
    try:
        amount = Decimal(amount_str)
        if amount <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        messages.error(request, "Invalid amount.")
        return redirect_url
 
    # Validate min/max
    if min_amount and amount < min_amount:
        messages.error(request, f"Minimum amount is ₦{min_amount:,.0f}.")
        return redirect_url
    if max_amount and amount > max_amount:
        messages.error(request, f"Maximum amount is ₦{max_amount:,.0f}.")
        return redirect_url
    if remaining is not None and amount > remaining:
        messages.error(request, f"This would exceed your monthly balance of ₦{remaining:,.0f}.")
        return redirect_url
 
    phone = request.POST.get('phone', '').strip()
    if not phone:
        messages.error(request, "Phone number is required.")
        return redirect_url
    if not phone.isdigit() or len(phone) != 11:
        messages.error(request, "Phone number must be 11 digits (e.g. 08012345678).")
        return redirect_url
 
    network = request.POST.get('network', '').strip()
    if not network:
        messages.error(request, "Please select a network.")
        return redirect_url
 
    # Validate network is a known provider (prevents arbitrary values)
    valid_networks = [v for v, _ in NetworkProvider.choices]
    if network not in valid_networks:
        messages.error(request, "Invalid network selected.")
        return redirect_url
 
    result, error = process_service_request(
        user=request.user,
        service=service,
        subscription=subscription,
        amount=amount,
        phone=phone,
        network=network,
        variation_code='',
        data_gb=None,
    )
 
    if error:
        messages.error(request, error)
        return redirect_url
 
    return redirect('services:confirmation', purchase_id=result.purchase_id)

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