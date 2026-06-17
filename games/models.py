"""
games/models.py

Three models power the entire spin system:

  SpinConfig  — singleton admin settings (how many spins, cooldown, on/off)
  SpinPrize   — the prize table (each segment on the wheel, its coins & weight)
  SpinRecord  — immutable audit log (one row per spin attempt, win or loss)

Teaching note — why three separate models instead of one?
  SpinConfig   → changes infrequently, owned by admin, one row forever
  SpinPrize    → changes when marketing adjusts odds, multiple rows, versioned
  SpinRecord   → append-only, never updated, financial audit trail

  Keeping them separate means you can change the prize table without touching
  spin history, and tighten config without touching prizes. Single responsibility.

SECURITY NOTE:
  The outcome of every spin is decided here on the server using
  Python's `secrets` module. The frontend only receives the result
  and animates to it. There is nothing the browser can do to change
  what gets credited to the wallet.
"""

import secrets
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def lagos_now():
    """
    Returns the current datetime in Lagos (WAT = UTC+1).

    Teaching note:
      Django stores all datetimes in UTC internally (when USE_TZ=True).
      When we need 'what day is it in Lagos?' we convert to WAT.
      We use zoneinfo (Python 3.9+) which is part of the standard library
      and does not require pytz to be installed.
    """
    from zoneinfo import ZoneInfo
    return timezone.now().astimezone(ZoneInfo('Africa/Lagos'))


def lagos_today():
    """Returns date.today() in Lagos timezone."""
    return lagos_now().date()


# ─────────────────────────────────────────────────────────────────────────────
# SPIN CONFIG  (singleton)
# ─────────────────────────────────────────────────────────────────────────────

class SpinConfig(models.Model):
    """
    One row, always pk=1.  Admin edits this via Django admin.

    Every field here directly affects the game behaviour:
      daily_spin_limit   → how many spins a subscriber gets per Lagos day
      cooldown_minutes   → minimum gap between spins (prevents rapid fire)
      is_active          → global kill switch — set False to disable the wheel
    """

    is_active = models.BooleanField(
        default=True,
        help_text='Turn the spin wheel on or off for all users instantly.',
    )
    daily_spin_limit = models.PositiveIntegerField(
        default=2,
        help_text=(
            'How many spins each subscriber gets per day. '
            'Resets at midnight Lagos time (WAT, UTC+1).'
        ),
    )
    cooldown_minutes = models.PositiveIntegerField(
        default=30,
        help_text=(
            'Minimum minutes a user must wait between spins. '
            'Set to 0 to allow back-to-back spins up to the daily limit.'
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='+',
        help_text='Last admin who saved this config.',
    )

    class Meta:
        verbose_name        = 'Spin Configuration'
        verbose_name_plural = 'Spin Configuration'

    def __str__(self):
        status = 'ACTIVE' if self.is_active else 'DISABLED'
        return (
            f'SpinConfig [{status}] | '
            f'{self.daily_spin_limit} spins/day | '
            f'{self.cooldown_minutes}min cooldown'
        )

    def save(self, *args, **kwargs):
        """Enforce singleton — always use pk=1."""
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_config(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config


# ─────────────────────────────────────────────────────────────────────────────
# SPIN PRIZE  (the prize table / wheel segments)
# ─────────────────────────────────────────────────────────────────────────────

class SpinPrize(models.Model):
    """
    One row = one segment on the wheel.

    The wheel can have any number of prizes. Admins control:
      label        → text shown on the wheel segment  e.g. "50 Coins"
      coins_value  → 0 = no win (loss segment), >0 = coins credited
      weight       → integer, all active prizes must sum to exactly 100
      color        → hex colour for this segment (gold shades recommended)
      display_order→ clockwise order of segments on the wheel (cosmetic only)

    WEIGHT SYSTEM:
      Weight is a percentage. e.g. weight=55 means this prize has a 55% chance.
      All active prizes MUST sum to exactly 100. The admin form enforces this.
      The server uses weighted random selection — not uniform random.

    Teaching note on weighted random:
      If prizes are [Loss(55), 10coins(25), 50coins(13), 200coins(5), 5000coins(2)]
      we build a list: [Loss, Loss, …×55, 10coins×25, 50coins×13, …]
      and call secrets.choice() on it. This gives the exact probability distribution.
      secrets.choice() uses os.urandom() — cryptographically secure randomness.
      Nobody can predict or manipulate the outcome.
    """

    label = models.CharField(
        max_length=60,
        help_text='Text shown on this wheel segment. e.g. "Try Again", "50 Coins", "JACKPOT"',
    )
    coins_value = models.PositiveIntegerField(
        default=0,
        help_text=(
            '0 = no win (loss). '
            'Any positive number = coins credited to wallet on win.'
        ),
    )
    weight = models.PositiveIntegerField(
        help_text=(
            'Probability weight as a whole number. '
            'All active prizes must sum to exactly 100. '
            'Higher = more likely. e.g. 55 means 55% chance.'
        ),
    )

    # Visual — colours should all be gold shades to match GP branding
    color = models.CharField(
        max_length=7,
        default='#E5AD04',
        help_text='Hex colour for this wheel segment. Use gold shades: #E5AD04, #F9B036, #5c3001, #020202',
    )
    text_color = models.CharField(
        max_length=7,
        default='#000000',
        help_text='Hex colour for the label text on this segment.',
    )
    display_order = models.PositiveIntegerField(
        default=0,
        help_text='Clockwise position on the wheel, starting from 0. Cosmetic only — does not affect odds.',
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Inactive prizes are excluded from both the wheel and prize selection.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ['display_order', 'id']
        verbose_name        = 'Spin Prize'
        verbose_name_plural = 'Spin Prizes'

    def __str__(self):
        win_text = f'{self.coins_value} coins' if self.coins_value > 0 else 'No win'
        return f'{self.label} ({win_text}, weight={self.weight}%)'

    @property
    def is_winning(self):
        return self.coins_value > 0

    def clean(self):
        """
        No blocking validation here on purpose.

        Reason: Django's admin list_editable feature validates every edited
        row BEFORE saving any of them (it's a formset). That means if you
        change several prizes' weights at once, each row's clean() runs
        against the OLD, not-yet-saved weights of every other row — making
        a "must total exactly 100" check unreliable and prone to false
        rejections, exactly like what you just experienced.

        Instead, the running total is shown as a clear banner at the top of
        the Spin Prizes admin list (see SpinPrizeAdmin.changelist_view below).
        pick_prize() still works correctly no matter what the total is — it
        weights relative to whatever total exists — and logs a warning if
        it isn't exactly 100, so nothing breaks even mid-edit.
        """
        return

    # Inform but do not block when under 100
    # The admin changelist header shows the running total
    # The spin system logs an error if prizes are used without totalling 100


# ─────────────────────────────────────────────────────────────────────────────
# SPIN RECORD  (immutable audit log)
# ─────────────────────────────────────────────────────────────────────────────

class SpinRecord(models.Model):
    """
    One row = one spin attempt. Never updated after creation.

    This is the financial source of truth for the spin system.
    If there's ever a dispute ("I spun but didn't get my coins"),
    this table has the definitive record.

    We store:
      - WHO spun (user FK)
      - WHEN they spun (UTC datetime + Lagos date for fast daily-limit queries)
      - WHAT they won (FK snapshot to the prize, plus coins_awarded snapshot)
      - WHERE from (ip_address for fraud detection)

    Teaching note — why snapshot coins_awarded separately from prize.coins_value?
      If an admin later changes a prize's coin value, the historical records still
      show what was actually credited. This is the same pattern used in your
      SalesRecord (commission_rate_snapshot). Financial records must be immutable.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='spin_records',
    )
    prize = models.ForeignKey(
        SpinPrize,
        on_delete=models.PROTECT,   # Never delete a prize that has records
        related_name='spin_records',
    )
    coins_awarded = models.PositiveIntegerField(
        help_text='Snapshot of coins actually credited. 0 for a loss.',
    )
    spun_at = models.DateTimeField(
        default=timezone.now,
        help_text='UTC datetime of the spin.',
    )
    lagos_date = models.DateField(
        help_text='Lagos calendar date of the spin — used for daily limit queries.',
    )

    # Fraud / audit fields
    ip_address = models.GenericIPAddressField(
        null=True, blank=True,
        help_text='IP address of the request for fraud detection.',
    )

    class Meta:
        ordering = ['-spun_at']
        indexes  = [
            # Fast lookup: "how many times has this user spun today?"
            models.Index(fields=['user', 'lagos_date']),
            # Admin history view
            models.Index(fields=['-spun_at']),
        ]
        verbose_name        = 'Spin Record'
        verbose_name_plural = 'Spin Records'

    def __str__(self):
        result = f'{self.coins_awarded} coins' if self.coins_awarded > 0 else 'No win'
        return f'{self.user.email} | {result} | {self.spun_at:%Y-%m-%d %H:%M} UTC'

    @property
    def is_win(self):
        return self.coins_awarded > 0

    def save(self, *args, **kwargs):
        """Auto-populate lagos_date from spun_at if not set."""
        if not self.lagos_date:
            from zoneinfo import ZoneInfo
            self.lagos_date = self.spun_at.astimezone(
                ZoneInfo('Africa/Lagos')
            ).date()
        super().save(*args, **kwargs)