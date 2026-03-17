"""
wallet/utils.py

THE ONLY SANCTIONED WAY TO TOUCH WALLET BALANCES.

Rules:
  1. NEVER do wallet.balance += x anywhere else in the codebase.
  2. ALWAYS call credit_wallet() or debit_wallet().
  3. Both use select_for_update() — they manage the DB lock themselves.
  4. transfer_coins() locks both wallets in consistent ID order to prevent deadlocks.
"""

from decimal import Decimal
from django.db import transaction as db_transaction
from django.utils import timezone


def credit_wallet(wallet, amount, txn_type, **kwargs):
    """
    Add coins to wallet atomically.

    Args:
        wallet:   Wallet instance
        amount:   Amount to add (str, int, or Decimal)
        txn_type: WalletTransaction.TransactionType value
        **kwargs: Extra fields for WalletTransaction (note, paystack_reference, ip_address, etc.)

    Returns:
        New balance (Decimal)
    """
    from .models import Wallet, WalletTransaction

    amount = Decimal(str(amount))

    with db_transaction.atomic():
        w = Wallet.objects.select_for_update().get(pk=wallet.pk)
        before = w.balance
        w.balance += amount
        w.save(update_fields=['balance', 'updated_at'])
        WalletTransaction.objects.create(
            wallet=w,
            type=txn_type,
            amount=amount,
            balance_before=before,
            balance_after=w.balance,
            **kwargs
        )

    return w.balance


def debit_wallet(wallet, amount, txn_type, **kwargs):
    """
    Remove coins from wallet atomically.

    Raises:
        ValueError if balance is insufficient.

    Returns:
        New balance (Decimal)
    """
    from .models import Wallet, WalletTransaction

    amount = Decimal(str(amount))

    with db_transaction.atomic():
        w = Wallet.objects.select_for_update().get(pk=wallet.pk)
        if w.balance < amount:
            raise ValueError(
                f'Insufficient balance. Current: {w.balance} coins, needed: {amount} coins.'
            )
        before = w.balance
        w.balance -= amount
        w.save(update_fields=['balance', 'updated_at'])
        WalletTransaction.objects.create(
            wallet=w,
            type=txn_type,
            amount=amount,
            balance_before=before,
            balance_after=w.balance,
            **kwargs
        )

    return w.balance


def transfer_coins(sender, recipient, amount, note='', ip_address=None):
    """
    Transfer coins between two users atomically.
    Wallets are locked in consistent ID order to prevent deadlocks.

    Args:
        sender:     CustomUser sending coins
        recipient:  CustomUser receiving coins
        amount:     Amount to transfer
        note:       Optional message
        ip_address: Sender IP for audit trail

    Raises:
        ValueError for insufficient balance, daily limit, same user, or missing wallet.
    """
    from .models import Wallet, WalletTransaction, WalletConfig

    if sender.id == recipient.id:
        raise ValueError('Cannot transfer coins to yourself.')

    amount = Decimal(str(amount))
    config = WalletConfig.get_config()

    if amount < config.min_transfer_amount:
        raise ValueError(f'Minimum transfer is {config.min_transfer_amount} coins.')

    with db_transaction.atomic():
        # Lock both wallets in consistent order (lower ID first) to prevent deadlocks
        user_ids = sorted([sender.id, recipient.id])
        wallets  = Wallet.objects.select_for_update().filter(
            user_id__in=user_ids
        ).order_by('user_id')

        wallet_map       = {w.user_id: w for w in wallets}
        sender_wallet    = wallet_map.get(sender.id)
        recipient_wallet = wallet_map.get(recipient.id)

        if not sender_wallet:
            raise ValueError('Sender wallet not found.')
        if not recipient_wallet:
            raise ValueError('Recipient does not have a wallet.')

        if sender_wallet.balance < amount:
            raise ValueError(f'Insufficient balance. You have {sender_wallet.balance} coins.')

        # Daily limit check (refresh inside the lock)
        today = timezone.now().date()
        if sender_wallet.daily_transfer_date != today:
            sender_wallet.daily_transfer_total = Decimal('0')
            sender_wallet.daily_transfer_date  = today

        projected_daily = sender_wallet.daily_transfer_total + amount
        if projected_daily > config.daily_transfer_limit:
            remaining = config.daily_transfer_limit - sender_wallet.daily_transfer_total
            raise ValueError(
                f'Daily transfer limit exceeded. You can still send {remaining} coins today.'
            )

        # Debit sender
        before_sender = sender_wallet.balance
        sender_wallet.balance -= amount
        sender_wallet.daily_transfer_total = projected_daily
        sender_wallet.daily_transfer_date  = today
        sender_wallet.save(update_fields=[
            'balance', 'daily_transfer_total', 'daily_transfer_date', 'updated_at'
        ])
        WalletTransaction.objects.create(
            wallet=sender_wallet,
            type=WalletTransaction.TransactionType.TRANSFER_OUT,
            amount=amount,
            balance_before=before_sender,
            balance_after=sender_wallet.balance,
            related_user=recipient,
            note=note,
            ip_address=ip_address,
        )

        # Credit recipient
        before_recipient = recipient_wallet.balance
        recipient_wallet.balance += amount
        recipient_wallet.save(update_fields=['balance', 'updated_at'])
        WalletTransaction.objects.create(
            wallet=recipient_wallet,
            type=WalletTransaction.TransactionType.TRANSFER_IN,
            amount=amount,
            balance_before=before_recipient,
            balance_after=recipient_wallet.balance,
            related_user=sender,
            note=note,
        )


def get_applicable_cashback_rule(venue=None, order_amount=None):
    """
    Returns the best applicable CashbackRule for a given venue and order amount.
    PER_VENUE rules beat GLOBAL rules.
    Returns None if no rule applies.
    """
    from .models import CashbackRule
    from django.db.models import Q

    today = timezone.now().date()
    date_filter = (
        Q(valid_from__isnull=True) | Q(valid_from__lte=today),
        Q(valid_until__isnull=True) | Q(valid_until__gte=today),
    )

    if venue:
        venue_rule = CashbackRule.objects.filter(
            rule_type=CashbackRule.RuleType.PER_VENUE,
            venue=venue,
            is_active=True,
        ).filter(*date_filter).first()

        if venue_rule:
            if venue_rule.minimum_spend and order_amount and order_amount < venue_rule.minimum_spend:
                pass  # Fall through to global
            else:
                return venue_rule

    global_rule = CashbackRule.objects.filter(
        rule_type=CashbackRule.RuleType.GLOBAL,
        is_active=True,
    ).filter(*date_filter).order_by('-percentage').first()

    if global_rule:
        if global_rule.minimum_spend and order_amount and order_amount < global_rule.minimum_spend:
            return None
        return global_rule

    return None


def get_wallet_stats(wallet):
    """Returns lifetime summary stats for the wallet dashboard."""
    from .models import WalletTransaction
    from django.db.models import Sum

    txns = wallet.transactions.all()

    total_purchased = txns.filter(
        type=WalletTransaction.TransactionType.PURCHASE
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0')

    total_spent = txns.filter(
        type__in=[WalletTransaction.TransactionType.SPEND,
                  WalletTransaction.TransactionType.TRANSFER_OUT]
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0')

    total_earned = txns.filter(
        type__in=[
            WalletTransaction.TransactionType.CASHBACK,
            WalletTransaction.TransactionType.REFERRAL,
            WalletTransaction.TransactionType.MONTHLY_BONUS,
        ]
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0')

    return {
        'total_purchased': total_purchased,
        'total_spent':     total_spent,
        'total_earned':    total_earned,
    }