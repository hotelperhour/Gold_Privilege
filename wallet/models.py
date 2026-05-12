import uuid
from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.hashers import make_password, check_password
from django.core.validators import MinValueValidator

from account.models import CustomUser


class WalletConfig(models.Model):
    """
    Singleton: admin-configurable wallet settings.
    Only one row should ever exist (pk=1 is enforced in save()).
    Edit from Django Admin → Wallet Configuration.
    """
    daily_transfer_limit = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('50000'),
        help_text='Maximum coins a user can transfer OUT per day'
    )
    referral_coins_reward = models.PositiveIntegerField(
        default=500,
        help_text='Coins awarded to referrer when referred user completes first subscription payment'
    )
    monthly_bonus_tier_1 = models.PositiveIntegerField(default=100, help_text='Monthly bonus for Tier 1 plan holders')
    monthly_bonus_tier_2 = models.PositiveIntegerField(default=250, help_text='Monthly bonus for Tier 2 plan holders')
    monthly_bonus_tier_3 = models.PositiveIntegerField(default=500, help_text='Monthly bonus for Tier 3 plan holders')
    min_transfer_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('100'),
        help_text='Minimum coins per single transfer'
    )
    max_failed_pin_attempts = models.PositiveIntegerField(
        default=5, help_text='Failed PIN attempts before 30-min lockout'
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Wallet Configuration'
        verbose_name_plural = 'Wallet Configuration'

    def __str__(self):
        return f'Wallet Config — daily limit: {self.daily_transfer_limit} coins'

    def save(self, *args, **kwargs):
        self.pk = 1  # Enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def get_config(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config


class Wallet(models.Model):
    """
    One wallet per subscriber user.
    NEVER update balance directly — always use credit_wallet() / debit_wallet() in utils.py.
    """
    user = models.OneToOneField(
        CustomUser, on_delete=models.CASCADE, related_name='wallet'
    )
    balance = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))]
    )

    # PIN security
    wallet_pin = models.CharField(
        max_length=128, blank=True,
        help_text='Hashed wallet PIN — never stored in plain text'
    )
    pin_set = models.BooleanField(default=False)
    pin_failed_attempts = models.PositiveIntegerField(default=0)
    pin_locked_until = models.DateTimeField(
        null=True, blank=True,
        help_text='PIN entry locked until this time after too many failed attempts'
    )

    # Daily transfer tracking
    daily_transfer_total = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0')
    )
    daily_transfer_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.user.email} — {self.balance} coins'

    def can_afford(self, amount):
        return self.balance >= Decimal(str(amount))

    # ── PIN helpers ──────────────────────────────────────────────────────────

    def set_pin(self, raw_pin):
        self.wallet_pin = make_password(str(raw_pin))
        self.pin_set = True
        self.pin_failed_attempts = 0
        self.pin_locked_until = None
        self.save(update_fields=['wallet_pin', 'pin_set', 'pin_failed_attempts', 'pin_locked_until', 'updated_at'])

    def check_pin(self, raw_pin):
        return check_password(str(raw_pin), self.wallet_pin)

    def is_pin_locked(self):
        if self.pin_locked_until and timezone.now() < self.pin_locked_until:
            return True
        return False

    def record_failed_pin(self):
        config = WalletConfig.get_config()
        self.pin_failed_attempts += 1
        if self.pin_failed_attempts >= config.max_failed_pin_attempts:
            self.pin_locked_until = timezone.now() + timezone.timedelta(minutes=30)
            self.pin_failed_attempts = 0
        self.save(update_fields=['pin_failed_attempts', 'pin_locked_until', 'updated_at'])

    def reset_pin_attempts(self):
        self.pin_failed_attempts = 0
        self.pin_locked_until = None
        self.save(update_fields=['pin_failed_attempts', 'pin_locked_until', 'updated_at'])

    # ── Daily transfer limit ─────────────────────────────────────────────────

    def get_today_transfer_total(self):
        today = timezone.now().date()
        if self.daily_transfer_date != today:
            self.daily_transfer_total = Decimal('0')
            self.daily_transfer_date = today
            self.save(update_fields=['daily_transfer_total', 'daily_transfer_date'])
        return self.daily_transfer_total

    def can_transfer(self, amount):
        config = WalletConfig.get_config()
        return (self.get_today_transfer_total() + Decimal(str(amount))) <= config.daily_transfer_limit

    def remaining_daily_limit(self):
        config = WalletConfig.get_config()
        return max(Decimal('0'), config.daily_transfer_limit - self.get_today_transfer_total())


class WalletTransaction(models.Model):
    """
    Immutable ledger — every coin movement is recorded here.
    NEVER delete rows from this table. NEVER create rows directly — use utils.py functions.
    """

    class TransactionType(models.TextChoices):
        PURCHASE      = 'PURCHASE',      'Coin Purchase (Real Money)'
        SPEND         = 'SPEND',         'Spent in Discount Store'
        CASHBACK      = 'CASHBACK',      'Cashback Earned'
        CASHBACK_CLAWBACK = 'CASHBACK_CLAWBACK', 'Cashback Removed (Cancellation)'
        REFERRAL      = 'REFERRAL',      'Referral Bonus'
        TRANSFER_IN   = 'TRANSFER_IN',   'Received from User'
        TRANSFER_OUT  = 'TRANSFER_OUT',  'Sent to User'
        MONTHLY_BONUS = 'MONTHLY_BONUS', 'Monthly Subscription Bonus'
        REFUND        = 'REFUND',        'Refund (Cancellation)'
        ADMIN_CREDIT  = 'ADMIN_CREDIT',  'Admin Manual Credit'
        ADMIN_DEBIT   = 'ADMIN_DEBIT',   'Admin Manual Debit'
        STORE_PURCHASE = 'STORE_PURCHASE', 'Discount Store Purchase'
        STORE_REFUND   = 'STORE_REFUND',   'Discount Store Refund'

    CREDIT_TYPES = {
        'PURCHASE', 'CASHBACK', 'REFERRAL',
        'TRANSFER_IN', 'MONTHLY_BONUS', 'REFUND', 'ADMIN_CREDIT', 'STORE_REFUND'
    }

    transaction_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name='transactions')
    type = models.CharField(max_length=20, choices=TransactionType.choices)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        help_text='Always the absolute amount — direction is encoded in type'
    )
    balance_before = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after  = models.DecimalField(max_digits=12, decimal_places=2)

    related_user = models.ForeignKey(
        CustomUser, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='wallet_interactions',
        help_text='For transfers: the other party'
    )
    paystack_reference = models.CharField(max_length=100, blank=True)
    note       = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes  = [models.Index(fields=['wallet', 'type', 'created_at'])]

    def __str__(self):
        return f'{self.wallet.user.email} | {self.type} | {self.amount} coins'

    @property
    def is_credit(self):
        return self.type in self.CREDIT_TYPES

    def delete(self, *args, **kwargs):
        raise Exception('WalletTransaction records are permanent and cannot be deleted.')


class CoinPackage(models.Model):
    """Admin-defined coin purchase packages displayed on the Buy Coins page."""
    name         = models.CharField(max_length=100, help_text='e.g. Starter Pack')
    coins        = models.PositiveIntegerField(help_text='Base coins the user receives')
    price        = models.DecimalField(max_digits=10, decimal_places=2, help_text='Price in Naira (₦)')
    bonus_coins  = models.PositiveIntegerField(default=0, help_text='Bonus coins on top of base amount')
    is_featured  = models.BooleanField(default=False, help_text='Show "Best Value" badge')
    is_active    = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_order', 'price']

    def __str__(self):
        return f'{self.name}: {self.total_coins()} coins for ₦{self.price:,.2f}'

    def total_coins(self):
        return self.coins + self.bonus_coins


class CoinPurchase(models.Model):
    """Tracks each pending/completed coin purchase via Paystack."""

    class Status(models.TextChoices):
        PENDING   = 'PENDING',   'Pending Payment'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED    = 'FAILED',    'Failed'

    user               = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='coin_purchases')
    package            = models.ForeignKey(CoinPackage, null=True, blank=True, on_delete=models.SET_NULL)
    coins_to_credit    = models.PositiveIntegerField()
    amount             = models.DecimalField(max_digits=10, decimal_places=2)
    paystack_reference = models.CharField(max_length=100, unique=True)
    status             = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at         = models.DateTimeField(auto_now_add=True)
    completed_at       = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.user.email} — {self.coins_to_credit} coins ({self.status})'


class CashbackRule(models.Model):
    """
    Admin-configured cashback rules applied after store purchases.
    PER_VENUE rules take priority over GLOBAL rules.
    """

    class RuleType(models.TextChoices):
        GLOBAL    = 'GLOBAL', 'Global (All Purchases)'
        PER_VENUE = 'VENUE',  'Specific Venue'

    rule_type     = models.CharField(max_length=20, choices=RuleType.choices, default=RuleType.GLOBAL)
    percentage    = models.DecimalField(
        max_digits=5, decimal_places=2, help_text='e.g. 5.00 for 5%',
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    venue         = models.ForeignKey('venues.Venue', null=True, blank=True, on_delete=models.SET_NULL)
    minimum_spend = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Minimum order amount to qualify (leave blank = no minimum)'
    )
    is_active   = models.BooleanField(default=True)
    valid_from  = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        if self.rule_type == self.RuleType.GLOBAL:
            return f'Global {self.percentage}% cashback'
        return f'{self.venue.name if self.venue else "N/A"} — {self.percentage}% cashback'

    def is_valid_now(self):
        today = timezone.now().date()
        if not self.is_active:
            return False
        if self.valid_from and today < self.valid_from:
            return False
        if self.valid_until and today > self.valid_until:
            return False
        return True


class ReferralRecord(models.Model):
    """
    Tracks referrals. Coins are awarded only after the referred user pays for their
    FIRST subscription plan.
    """
    referrer      = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='referrals_made')
    referred_user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='referred_by')
    coins_awarded = models.PositiveIntegerField(default=0)
    awarded_at    = models.DateTimeField(null=True, blank=True)
    is_paid       = models.BooleanField(
        default=False, help_text='True once referral bonus coins are credited to referrer'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['referrer', 'referred_user']

    def __str__(self):
        return f'{self.referrer.email} → {self.referred_user.email} | Paid: {self.is_paid}'