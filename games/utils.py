"""
games/utils.py

Pure business-logic functions for the spin system.
No HTTP, no templates — just data in, data out.

Teaching note:
  Keeping logic in utils.py (rather than in views.py or models.py) means:
  - Views stay thin: validate → call util → return response
  - Utils are easy to unit-test without a request object
  - The same logic can be reused from management commands, signals, etc.
"""

import secrets
import logging
from django.db import transaction
from django.utils import timezone
import django.db.models as models  # needed for models.Q in get_spin_page_context
from .models import SpinConfig, SpinPrize, SpinRecord, lagos_today
import json
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SPIN ELIGIBILITY CHECK
# ─────────────────────────────────────────────────────────────────────────────

def can_spin(user):
    """
    Returns (allowed: bool, reason: str, spins_used: int, spins_total: int).

    Checks in order:
      1. Spin wheel is globally active
      2. User has an active subscription
      3. User has not exceeded daily limit (by Lagos date)
      4. User has waited the cooldown period since their last spin

    Teaching note:
      We return a named tuple of values rather than raising exceptions
      so the view can give the user a specific, helpful error message.
      "You have no spins left today" is more useful than a generic 403.
    """
    config = SpinConfig.get_config()

    # ── 1. Global kill switch ─────────────────────────────────────────────
    if not config.is_active:
        return False, 'The spin wheel is currently unavailable. Check back soon.', 0, 0

    # ── 2. Active subscription required ──────────────────────────────────
    from subscriptions.models import Subscription
    has_active_sub = Subscription.objects.filter(
        user=user,
        status__in=['ACTIVE', 'TRIAL'],
        end_date__gte=timezone.now(),
    ).exists()

    if not has_active_sub:
        return (
            False,
            'An active Gold Privilege subscription is required to spin.',
            0,
            config.daily_spin_limit,
        )

    # ── 3. Daily limit (Lagos date) ───────────────────────────────────────
    today_lagos = lagos_today()
    spins_today = SpinRecord.objects.filter(
        user=user,
        lagos_date=today_lagos,
    ).count()

    if spins_today >= config.daily_spin_limit:
        return (
            False,
            f'You have used all {config.daily_spin_limit} spins for today. '
            f'Come back tomorrow after midnight Lagos time!',
            spins_today,
            config.daily_spin_limit,
        )

    # ── 4. Cooldown between spins ─────────────────────────────────────────
    if config.cooldown_minutes > 0:
        last_spin = (
            SpinRecord.objects
            .filter(user=user)
            .order_by('-spun_at')
            .first()
        )
        if last_spin:
            from datetime import timedelta
            elapsed = timezone.now() - last_spin.spun_at
            cooldown = timedelta(minutes=config.cooldown_minutes)
            if elapsed < cooldown:
                remaining_seconds = int((cooldown - elapsed).total_seconds())
                remaining_minutes = remaining_seconds // 60
                remaining_secs    = remaining_seconds % 60
                return (
                    False,
                    f'Please wait {remaining_minutes}m {remaining_secs}s before spinning again.',
                    spins_today,
                    config.daily_spin_limit,
                )

    return True, 'OK', spins_today, config.daily_spin_limit


# ─────────────────────────────────────────────────────────────────────────────
# PRIZE SELECTION  (the secure random pick)
# ─────────────────────────────────────────────────────────────────────────────

def pick_prize():
    """
    Weighted random prize selection.

    Works correctly whether weights sum to 100 or any other total —
    secrets.choice() picks uniformly from the pool, so relative
    probabilities are always correct regardless of pool size.

    Blocks the spin if total weight is 0 (nothing configured yet).
    Logs a warning if weights don't sum to 100 (table still being built).
    """
    active_prizes = list(SpinPrize.objects.filter(is_active=True))

    if not active_prizes:
        logger.error(
            'spin.pick_prize: No active prizes configured. '
            'Admin → Games → Spin Prizes → add prizes before enabling spins.'
        )
        return None

    total_weight = sum(p.weight for p in active_prizes)

    if total_weight == 0:
        logger.error('spin.pick_prize: All active prizes have weight=0. Cannot pick a prize.')
        return None

    if total_weight != 100:
        logger.warning(
            f'spin.pick_prize: Active prize weights sum to {total_weight}, not 100. '
            f'Spin will still work but odds may not match your intended percentages. '
            f'Finish configuring prizes in Admin → Games → Spin Prizes.'
        )

    # Build weighted pool — works for any total weight, not just 100
    pool = []
    for prize in active_prizes:
        pool.extend([prize] * prize.weight)

    return secrets.choice(pool)


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTE SPIN  (the main transaction)
# ─────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def execute_spin(user, ip_address=None):
    """
    The single function that runs a complete spin:

      1. Re-check eligibility inside the atomic block (race condition guard)
      2. Pick a prize (server-side, cryptographically random)
      3. Create the SpinRecord FIRST (before crediting wallet)
      4. Credit wallet if coins > 0
      5. Return result dict

    Teaching note — why create SpinRecord BEFORE crediting the wallet?
      If the wallet credit fails for any reason, the transaction rolls back
      and the SpinRecord is also rolled back — the user loses nothing and
      can try again. If we credited first and THEN wrote the record and the
      write failed, the user would have coins with no audit trail.
      Always write the audit record in the same transaction as the money.

    Teaching note — why @transaction.atomic?
      atomic() means both the SpinRecord write and the wallet credit happen
      together or not at all. The database guarantees this even if the server
      crashes between the two operations. This is essential for any financial
      operation.
    """
    # ── Re-check eligibility (inside atomic block, race-condition safe) ──
    allowed, reason, spins_used, spins_total = can_spin(user)
    if not allowed:
        return {
            'success': False,
            'reason':  reason,
            'spins_used':  spins_used,
            'spins_total': spins_total,
        }

    # ── Pick prize ────────────────────────────────────────────────────────
    prize = pick_prize()
    if prize is None:
        return {
            'success': False,
            'reason':  'Spin wheel is temporarily misconfigured. Please try again later.',
        }

    # ── Create the audit record (BEFORE wallet credit) ────────────────────
    now_utc = timezone.now()
    record = SpinRecord.objects.create(
        user          = user,
        prize         = prize,
        coins_awarded = prize.coins_value,
        spun_at       = now_utc,
        ip_address    = ip_address,
    )

    # ── Credit wallet if it's a win ───────────────────────────────────────
    if prize.coins_value > 0:
        try:
            from wallet.models import Wallet, WalletTransaction
            from wallet.utils import credit_wallet

            wallet, _ = Wallet.objects.get_or_create(user=user)
            credit_wallet(
                wallet   = wallet,
                amount   = prize.coins_value,
                txn_type = WalletTransaction.TransactionType.SPIN_WIN,
                note     = f'Spin & Win: {prize.label} ({prize.coins_value} coins)',
            )
            logger.info(
                f'spin.execute_spin: {user.email} won {prize.coins_value} coins '
                f'(prize={prize.label}, record_id={record.id})'
            )
        except Exception as e:
            # Log but raise so the transaction rolls back completely
            logger.error(
                f'spin.execute_spin: Wallet credit failed for {user.email}: {e}',
                exc_info=True,
            )
            raise  # This causes the atomic block to roll back

    # ── Build response ────────────────────────────────────────────────────
    config = SpinConfig.get_config()
    new_spins_used = spins_used + 1
    # Build ordered list the same way the template does
    ordered_prizes = list(SpinPrize.objects.filter(is_active=True).order_by('display_order'))
    prize_index = next(
        (i for i, p in enumerate(ordered_prizes) if p.id == prize.id),
        0
    )

    return {
        'success':       True,
        'is_win':        prize.coins_value > 0,
        'prize_label':   prize.label,
        'coins_awarded': prize.coins_value,
        'prize_color':   prize.color,

        # Tell the frontend which segment to land on (by display_order)
        # The wheel animation uses this to rotate to the correct position
        'prize_index':   prize_index,

        'spins_used':    new_spins_used,
        'spins_total':   config.daily_spin_limit,
        'spins_left':    config.daily_spin_limit - new_spins_used,
        'record_id':     record.id,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD DATA  (for the spin page context)
# ─────────────────────────────────────────────────────────────────────────────

def get_spin_page_context(user):
    config      = SpinConfig.get_config()
    prizes      = SpinPrize.objects.filter(is_active=True).order_by('display_order')
    today_lagos = lagos_today()

    spins_today = SpinRecord.objects.filter(
        user=user, lagos_date=today_lagos
    ).count()

    recent_spins = (
        SpinRecord.objects
        .filter(user=user)
        .select_related('prize')
        .order_by('-spun_at')[:5]
    )

    allowed, reason, _, _ = can_spin(user)

    next_spin_at = None
    if not allowed and config.cooldown_minutes > 0:
        last_spin = SpinRecord.objects.filter(user=user).order_by('-spun_at').first()
        if last_spin:
            from datetime import timedelta
            next_spin_at = last_spin.spun_at + timedelta(minutes=config.cooldown_minutes)

    from django.db.models import Sum, Count, Q
    lifetime = SpinRecord.objects.filter(user=user).aggregate(
        total_spins = Count('id'),
        total_coins = Sum('coins_awarded'),
        total_wins  = Count('id', filter=Q(coins_awarded__gt=0)),
    )

    # Build the prizes list as plain Python data first
    prizes_data = list(prizes.values(
        'id', 'label', 'color', 'text_color', 'display_order'
    ))

    return {
        'config':        config,
        'prizes':        prizes,
        'prizes_list':   prizes_data,
        # ✅ THIS is the key fix — a proper JSON STRING for the template to inject
        'prizes_json':   json.dumps(prizes_data),
        'spins_used':    spins_today,
        'spins_left':    max(0, config.daily_spin_limit - spins_today),
        'spins_total':   config.daily_spin_limit,
        'can_spin':      allowed,
        'cant_spin_reason': reason if not allowed else '',
        'next_spin_at':  next_spin_at,
        'recent_spins':  recent_spins,
        'lifetime_spins': lifetime['total_spins'] or 0,
        'lifetime_coins': lifetime['total_coins'] or 0,
        'lifetime_wins':  lifetime['total_wins'] or 0,
    }

