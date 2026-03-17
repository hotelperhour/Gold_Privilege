import logging
import secrets
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from account.models import CustomUser
from account.permissions import subscriber_required

from .emails import (
    send_coin_purchase_confirmation,
    send_transfer_sent_email,
    send_transfer_received_email,
    send_pin_locked_email,
    send_pin_changed_email,
)
from .forms import BuyCoinsForm, SetPinForm, TransferCoinsForm, WalletHistoryFilterForm
from .models import CoinPackage, CoinPurchase, Wallet, WalletConfig, WalletTransaction
from .utils import credit_wallet, get_wallet_stats, transfer_coins

logger = logging.getLogger(__name__)


def _get_or_create_wallet(user):
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


def _get_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@subscriber_required
def wallet_dashboard(request):
    wallet      = _get_or_create_wallet(request.user)
    recent_txns = wallet.transactions.all()[:8]
    stats       = get_wallet_stats(wallet)
    config      = WalletConfig.get_config()

    return render(request, 'wallet/dashboard.html', {
        'wallet':              wallet,
        'recent_transactions': recent_txns,
        'stats':               stats,
        'daily_limit':         config.daily_transfer_limit,
        'remaining_today':     wallet.remaining_daily_limit(),
        'page_title':          'My Wallet',
    })


# ─────────────────────────────────────────────────────────────────────────────
# BUY COINS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@subscriber_required
def buy_coins(request):
    packages = CoinPackage.objects.filter(is_active=True).order_by('display_order', 'price')
    return render(request, 'wallet/buy_coins.html', {
        'packages':            packages,
        'paystack_public_key': settings.PAYSTACK_PUBLIC_KEY,
        'page_title':          'Buy Gold Coins',
    })


@login_required
@subscriber_required
@require_POST
def initiate_coin_purchase(request):
    package_id = request.POST.get('package_id')
    custom_ngn = request.POST.get('custom_amount', '').strip()

    try:
        if package_id:
            package = get_object_or_404(CoinPackage, pk=package_id, is_active=True)
            coins   = package.total_coins()
            amount  = package.price
        elif custom_ngn:
            amount = Decimal(custom_ngn)
            if amount < Decimal('100'):
                return JsonResponse({'success': False, 'error': 'Minimum purchase is ₦100.'})
            if amount > Decimal('9999999'):    
                return JsonResponse({'success': False, 'error': 'Maximum purchase is ₦9,999,999.'})
            coins   = int(amount)
            package = None
        else:
            return JsonResponse({'success': False, 'error': 'No package or amount provided.'})

        reference = f'GP-COIN-{secrets.token_hex(6).upper()}'
        purchase  = CoinPurchase.objects.create(
            user=request.user,
            package=package,
            coins_to_credit=coins,
            amount=amount,
            paystack_reference=reference,
        )

        return JsonResponse({
            'success':     True,
            'reference':   reference,
            'amount_kobo': int(amount * 100),
            'email':       request.user.email,
            'coins':       coins,
            'purchase_id': str(purchase.pk),
        })

    except Exception as e:
        logger.error(f'initiate_coin_purchase error for {request.user.email}: {e}')
        return JsonResponse({'success': False, 'error': 'An error occurred. Please try again.'})


@login_required
@subscriber_required
def coin_purchase_callback(request):
    """
    Paystack redirect after payment.
    The webhook is the primary handler — this is the browser-redirect fallback.
    """
    import requests as req

    reference = request.GET.get('reference')
    if not reference:
        messages.error(request, 'No payment reference found.')
        return redirect('wallet:buy')

    try:
        purchase = CoinPurchase.objects.get(
            paystack_reference=reference, user=request.user
        )
    except CoinPurchase.DoesNotExist:
        messages.error(request, 'Purchase record not found.')
        return redirect('wallet:buy')

    if purchase.status == CoinPurchase.Status.COMPLETED:
        messages.info(request, f'{purchase.coins_to_credit:,} coins already credited to your wallet.')
        return redirect('wallet:wallet_dashboard')

    url     = f'https://api.paystack.co/transaction/verify/{reference}'
    headers = {'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}'}
    try:
        resp = req.get(url, headers=headers, timeout=10)
        data = resp.json()
        if data.get('data', {}).get('status') == 'success':
            wallet = _get_or_create_wallet(request.user)
            credit_wallet(
                wallet=wallet,
                amount=purchase.coins_to_credit,
                txn_type=WalletTransaction.TransactionType.PURCHASE,
                paystack_reference=reference,
                note=f'Purchased {purchase.coins_to_credit:,} Gold Coins',
                ip_address=_get_ip(request),
            )
            purchase.status       = CoinPurchase.Status.COMPLETED
            purchase.completed_at = timezone.now()
            purchase.save()

            # ── Email: purchase confirmation ──────────────────────────────
            wallet.refresh_from_db()
            send_coin_purchase_confirmation(
                user=request.user,
                coins_credited=purchase.coins_to_credit,
                amount_paid=purchase.amount,
                paystack_reference=reference,
                new_balance=wallet.balance,
                request=request,
            )
            # ─────────────────────────────────────────────────────────────

            messages.success(
                request, f'🎉 {purchase.coins_to_credit:,} Gold Coins added to your wallet!'
            )
        else:
            messages.error(
                request, 'Payment verification failed. Contact support if you were charged.'
            )
    except Exception as e:
        logger.error(f'Coin purchase callback error: {e}')
        messages.error(request, 'Could not verify payment. Please contact support.')

    return redirect('wallet:wallet_dashboard')


# ─────────────────────────────────────────────────────────────────────────────
# TRANSFER COINS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@subscriber_required
def transfer_coins_view(request):
    wallet = _get_or_create_wallet(request.user)
    config = WalletConfig.get_config()

    if not wallet.pin_set:
        messages.warning(request, 'Please set a wallet PIN before transferring coins.')
        return redirect('wallet:set_pin')

    if request.method == 'POST':
        form = TransferCoinsForm(request.POST)
        if form.is_valid():
            gp_id   = form.cleaned_data['gp_id']
            amount  = form.cleaned_data['amount']
            note    = form.cleaned_data.get('note', '')
            raw_pin = form.cleaned_data['pin']

            # Check if already locked BEFORE attempting PIN
            if wallet.is_pin_locked():
                messages.error(
                    request,
                    f'Too many incorrect PINs. Wallet locked until '
                    f'{wallet.pin_locked_until.strftime("%H:%M")}.'
                )
                return render(request, 'wallet/transfer.html',
                              {'form': form, 'wallet': wallet, 'config': config})

            # Verify PIN
            if not wallet.check_pin(raw_pin):
                wallet.record_failed_pin()

                # ── Email: send lock notification the moment the wallet gets locked ──
                wallet.refresh_from_db()
                if wallet.is_pin_locked():
                    send_pin_locked_email(request.user, wallet.pin_locked_until)
                    messages.error(
                        request,
                        f'Too many incorrect PINs. Wallet locked until '
                        f'{wallet.pin_locked_until.strftime("%H:%M")}.'
                    )
                else:
                    remaining = config.max_failed_pin_attempts - wallet.pin_failed_attempts
                    messages.error(
                        request, f'Incorrect PIN. {max(0, remaining)} attempt(s) remaining.'
                    )
                # ──────────────────────────────────────────────────────────────────

                return render(request, 'wallet/transfer.html',
                              {'form': form, 'wallet': wallet, 'config': config})

            wallet.reset_pin_attempts()

            try:
                recipient = CustomUser.objects.get(
                    gp_id=gp_id,
                    user_type=CustomUser.UserType.SUBSCRIBER,
                    is_active=True,
                )
            except CustomUser.DoesNotExist:
                form.add_error('gp_id', 'No active subscriber found with this GP ID.')
                return render(request, 'wallet/transfer.html',
                              {'form': form, 'wallet': wallet, 'config': config})

            try:
                transfer_coins(
                    sender=request.user,
                    recipient=recipient,
                    amount=amount,
                    note=note,
                    ip_address=_get_ip(request),
                )

                # ── Emails: both parties notified ────────────────────────────────
                sender_wallet    = Wallet.objects.get(user=request.user)
                recipient_wallet = Wallet.objects.get(user=recipient)

                send_transfer_sent_email(
                    sender=request.user,
                    recipient=recipient,
                    amount=amount,
                    note=note,
                    new_balance=sender_wallet.balance,
                    request=request,
                )
                send_transfer_received_email(
                    recipient=recipient,
                    sender=request.user,
                    amount=amount,
                    note=note,
                    new_balance=recipient_wallet.balance,
                    request=request,
                )
                # ─────────────────────────────────────────────────────────────────

                messages.success(
                    request,
                    f'✅ {int(amount):,} Gold Coins sent to {recipient.get_full_name()} ({gp_id}).'
                )
                return redirect('wallet:wallet_dashboard')

            except ValueError as e:
                messages.error(request, str(e))
    else:
        form = TransferCoinsForm()

    return render(request, 'wallet/transfer.html', {
        'form':       form,
        'wallet':     wallet,
        'config':     config,
        'page_title': 'Send Gold Coins',
    })


# ─────────────────────────────────────────────────────────────────────────────
# TRANSACTION HISTORY
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@subscriber_required
def wallet_history(request):
    wallet      = _get_or_create_wallet(request.user)
    filter_form = WalletHistoryFilterForm(request.GET or None)
    txns        = wallet.transactions.all()

    if filter_form.is_valid():
        txn_type  = filter_form.cleaned_data.get('txn_type')
        date_from = filter_form.cleaned_data.get('date_from')
        date_to   = filter_form.cleaned_data.get('date_to')
        if txn_type:
            txns = txns.filter(type=txn_type)
        if date_from:
            txns = txns.filter(created_at__date__gte=date_from)
        if date_to:
            txns = txns.filter(created_at__date__lte=date_to)

    paginator = Paginator(txns, 15)
    page_obj  = paginator.get_page(request.GET.get('page'))

    return render(request, 'wallet/history.html', {
        'wallet':       wallet,
        'page_obj':     page_obj,
        'filter_form':  filter_form,
        'is_paginated': page_obj.has_other_pages(),
        'page_title':   'Transaction History',
    })


# ─────────────────────────────────────────────────────────────────────────────
# PIN MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@subscriber_required
def set_pin(request):
    wallet = _get_or_create_wallet(request.user)

    if request.method == 'POST':
        form = SetPinForm(request.POST)
        if form.is_valid():
            raw_new     = form.cleaned_data['new_pin']
            raw_current = form.cleaned_data.get('current_pin', '')
            was_already_set = wallet.pin_set

            if was_already_set:
                if not raw_current:
                    form.add_error('current_pin', 'Current PIN is required to change your PIN.')
                    return render(request, 'wallet/set_pin.html',
                                  {'form': form, 'wallet': wallet})
                if not wallet.check_pin(raw_current):
                    wallet.record_failed_pin()
                    form.add_error('current_pin', 'Incorrect current PIN.')
                    return render(request, 'wallet/set_pin.html',
                                  {'form': form, 'wallet': wallet})

            wallet.set_pin(raw_new)

            # ── Email: notify user that PIN was changed (not for first-time set) ──
            if was_already_set:
                send_pin_changed_email(request.user)
            # ─────────────────────────────────────────────────────────────────────

            messages.success(request, 'Wallet PIN saved successfully! 🔐')
            return redirect('wallet:wallet_dashboard')
    else:
        form = SetPinForm()

    return render(request, 'wallet/set_pin.html', {
        'form':       form,
        'wallet':     wallet,
        'page_title': 'Wallet PIN',
    })


# ─────────────────────────────────────────────────────────────────────────────
# AJAX: GP ID recipient lookup
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@subscriber_required
def lookup_recipient(request):
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)

    gp_id = request.GET.get('gp_id', '').strip().upper()
    if not gp_id:
        return JsonResponse({'success': False, 'error': 'GP ID required'})

    if gp_id == request.user.gp_id:
        return JsonResponse({'success': False, 'error': 'You cannot send coins to yourself.'})

    try:
        user = CustomUser.objects.get(
            gp_id=gp_id,
            user_type=CustomUser.UserType.SUBSCRIBER,
            is_active=True,
        )
        return JsonResponse({
            'success': True,
            'name':    user.get_full_name(),
            'gp_id':   user.gp_id,
        })
    except CustomUser.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'No subscriber found with this GP ID.'})