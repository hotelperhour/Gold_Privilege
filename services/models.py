"""
services/models.py

Two service types:
  1. API-based  – Reloadly airtime / data (instant delivery)
  2. Manual     – Pre-purchased voucher codes (Uber, Bolt, Petrol)

QUOTA MODEL:
  Airtime  → value-based  (naira)  — monthly_allowance e.g. ₦10,000/month
  Data     → volume-based (GB)     — monthly_data_gb   e.g. 5 GB/month
  Vouchers → count-based           — monthly_voucher_count e.g. 2/month

Per-transaction limits:
  Airtime  → min_transaction_amount / max_transaction_amount  (naira)
  Data     → min_data_gb / max_data_gb                        (GB per bundle)
  Vouchers → fixed_amounts JSON list of denominations

Race-condition protection: ServiceQuotaUsage row locked with
select_for_update() inside every atomic deduction.
"""

from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from account.models import CustomUser
from subscriptions.models import SubscriptionPlan, Subscription
import uuid


# ──────────────────────────────────────────────
# CHOICES
# ──────────────────────────────────────────────

class ServiceCategory(models.TextChoices):
    AIRTIME      = 'AIRTIME',      _('Mobile Airtime')
    DATA         = 'DATA',         _('Internet Data')
    RIDE_VOUCHER = 'RIDE_VOUCHER', _('Ride Voucher')
    FUEL_VOUCHER = 'FUEL_VOUCHER', _('Fuel Voucher')
    HOTEL_VOUCHER= 'HOTEL_VOUCHER', _('Hotel Voucher')
    OTHER        = 'OTHER',        _('Other Service')


class DeliveryType(models.TextChoices):
    API_INSTANT = 'API_INSTANT', _('API – Instant Delivery (Reloadly)')
    MANUAL_CODE = 'MANUAL_CODE', _('Manual – Voucher Code')

class VoucherType(models.TextChoices):
    FIXED_AMOUNT       = 'FIXED',      'Fixed Amount (₦)'
    PERCENTAGE_DISCOUNT = 'PERCENT',   'Percentage Discount (%)'


class NetworkProvider(models.TextChoices):
    MTN     = 'mtn',      _('MTN')
    GLO     = 'glo',      _('Glo')
    AIRTEL  = 'airtel',   _('Airtel')
    MOBILE9 = 'etisalat', _('9mobile')


# ──────────────────────────────────────────────
# SERVICE  (catalogue — admin managed)
# ──────────────────────────────────────────────

class Service(models.Model):
    """
    One row per service type.

    AIRTIME fields:
      min_transaction_amount, max_transaction_amount  (naira per top-up)

    DATA fields:
      min_data_gb, max_data_gb  (GB per single bundle purchase)
      Data bundles are selected by variation_code from Reloadly

    VOUCHER fields:
      fixed_amounts  e.g. [5000, 10000, 20000]
      has_inventory  = True
    """
    name          = models.CharField(max_length=200)
    category      = models.CharField(max_length=20, choices=ServiceCategory.choices)
    delivery_type = models.CharField(max_length=20, choices=DeliveryType.choices)
    description   = models.TextField(blank=True)
    icon          = models.CharField(max_length=100, blank=True,
                                     help_text='Font Awesome class e.g. fa-mobile-alt')

    # ── Airtime: per-transaction naira limits ──────────────────────
    min_transaction_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, default=100,
        help_text='AIRTIME: Minimum naira per top-up (e.g. 100)'
    )
    max_transaction_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='AIRTIME: Maximum naira per top-up (e.g. 5000). Leave blank to use plan allowance.'
    )

    # ── Data: per-bundle GB limits ─────────────────────────────────
    min_data_gb = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True, default=Decimal('0.5'),
        help_text='DATA: Minimum GB per single data bundle (e.g. 0.5 for 500MB)'
    )
    max_data_gb = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True,
        help_text='DATA: Maximum GB per single bundle purchase. Leave blank to use plan allowance.'
    )

    # ── Voucher denominations ──────────────────────────────────────
    fixed_amounts = models.JSONField(
        null=True, blank=True,
        help_text='VOUCHER: Fixed naira denominations e.g. [5000, 10000, 20000]'
    )
    has_inventory = models.BooleanField(
        default=False,
        help_text='Tick for Uber/Bolt/Petrol vouchers that need stored codes'
    )

    is_active     = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ['display_order', 'name']
        verbose_name        = _('service')
        verbose_name_plural = _('services')

    def __str__(self):
        return self.name

    def get_icon(self):
        defaults = {
            'AIRTIME':      'fa-mobile-alt',
            'DATA':         'fa-wifi',
            'RIDE_VOUCHER': 'fa-car',
            'HOTEL_VOUCHER': 'fa-bed',
            'FUEL_VOUCHER': 'fa-gas-pump',
            'OTHER':        'fa-concierge-bell',
        }
        return self.icon or defaults.get(self.category, 'fa-star')

    def is_api_based(self):
        return self.delivery_type == DeliveryType.API_INSTANT

    def is_voucher(self):
        return self.delivery_type == DeliveryType.MANUAL_CODE

    def is_data(self):
        return self.category == ServiceCategory.DATA

    def is_airtime(self):
        return self.category == ServiceCategory.AIRTIME

    def get_unit(self):
        """Returns the display unit for quota/amounts."""
        if self.category == ServiceCategory.DATA:
            return 'GB'
        if self.delivery_type == DeliveryType.MANUAL_CODE:
            return 'vouchers'
        return '₦'


# ──────────────────────────────────────────────
# PER-PLAN QUOTA
# ──────────────────────────────────────────────

class ServicePlanQuota(models.Model):
    """
    Monthly allowance a plan gets for a service.

    AIRTIME  → monthly_allowance (naira)
                 e.g. Gold = ₦10,000/month
    DATA     → monthly_data_gb (Decimal GB)
                 e.g. Gold = 5.0 GB/month
    VOUCHERS → monthly_voucher_count (integer)
                 e.g. Gold = 2 Uber vouchers/month

    Leave a field blank/None for unlimited.
    """
    plan    = models.ForeignKey(SubscriptionPlan, on_delete=models.CASCADE,
                                related_name='service_quotas')
    service = models.ForeignKey(Service, on_delete=models.CASCADE,
                                related_name='plan_quotas')

    # Airtime
    monthly_allowance = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='AIRTIME: Total naira per month (e.g. 10000). Blank = unlimited.'
    )

    # Data
    monthly_data_gb = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True,
        help_text='DATA: Total GB per month (e.g. 5.0 for 5GB). Blank = unlimited.'
    )

    # Vouchers
    monthly_voucher_count = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='VOUCHER: Number of voucher codes per month. Blank = unlimited.'
    )
    voucher_type = models.CharField(
        max_length=10,
        choices=VoucherType.choices,
        null=True, blank=True,
        help_text=(
            'VOUCHER: Which type of voucher this plan provides. '
            'FIXED = naira value, PERCENT = percentage discount.'
        ),
    )
    voucher_fixed_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='VOUCHER + FIXED only: Face value in naira. e.g. 20000 = ₦20,000 voucher.',
    )
    voucher_discount_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='VOUCHER + PERCENT only: Discount percentage. e.g. 15.00 = 15% off.',
    )

    class Meta:
        unique_together     = ['plan', 'service']
        verbose_name        = _('service plan quota')
        verbose_name_plural = _('service plan quotas')

    def __str__(self):
        cat = self.service.category
        if cat == ServiceCategory.DATA:
            limit = f"{self.monthly_data_gb} GB" if self.monthly_data_gb else "unlimited"
        elif self.service.delivery_type == DeliveryType.MANUAL_CODE:
            limit = f"{self.monthly_voucher_count} vouchers" if self.monthly_voucher_count else "unlimited"
        else:
            limit = f"₦{self.monthly_allowance:,.0f}" if self.monthly_allowance else "unlimited"
        return f"{self.plan.name} → {self.service.name}: {limit}/month"

    def is_unlimited(self):
        cat = self.service.category
        if cat == ServiceCategory.DATA:
            return self.monthly_data_gb is None
        if self.service.delivery_type == DeliveryType.MANUAL_CODE:
            return self.monthly_voucher_count is None
        return self.monthly_allowance is None

    def get_monthly_limit(self):
        """Returns the numeric monthly limit (regardless of unit), or None if unlimited."""
        cat = self.service.category
        if cat == ServiceCategory.DATA:
            return self.monthly_data_gb
        if self.service.delivery_type == DeliveryType.MANUAL_CODE:
            return self.monthly_voucher_count
        return self.monthly_allowance

    @property
    def limit_display(self):
        """Human-readable monthly limit (e.g., '₦10,000', '5 GB', '2 vouchers')"""
        if self.is_unlimited():
            return "Unlimited"
        cat = self.service.category
        if cat == ServiceCategory.DATA:
            return f"{self.monthly_data_gb} GB"
        if self.service.delivery_type == DeliveryType.MANUAL_CODE:
            return f"{self.monthly_voucher_count} voucher(s)"
        # Airtime or other value-based
        return f"₦{self.monthly_allowance:,.0f}"

    @property
    def icon(self):
        """Font Awesome icon HTML for the service category"""
        cat = self.service.category
        icons = {
            ServiceCategory.AIRTIME:      '<i class="fas fa-mobile-alt"></i>',
            ServiceCategory.DATA:         '<i class="fas fa-wifi"></i>',
            ServiceCategory.RIDE_VOUCHER: '<i class="fas fa-car"></i>',
            ServiceCategory.HOTEL_VOUCHER:'<i class="fas fa-bed"></i>',
            ServiceCategory.FUEL_VOUCHER: '<i class="fas fa-gas-pump"></i>',
            ServiceCategory.OTHER:        '<i class="fas fa-concierge-bell"></i>',
        }
        return icons.get(cat, '<i class="fas fa-star"></i>')


# ──────────────────────────────────────────────
# VOUCHER INVENTORY
# ──────────────────────────────────────────────

class VoucherInventory(models.Model):

    class VoucherStatus(models.TextChoices):
        AVAILABLE = 'AVAILABLE', _('Available')
        ASSIGNED  = 'ASSIGNED',  _('Assigned to User')
        USED      = 'USED',      _('Confirmed Used')
        EXPIRED   = 'EXPIRED',   _('Expired')

    service      = models.ForeignKey(Service, on_delete=models.CASCADE,
                                     related_name='vouchers')
    voucher_code = models.CharField(max_length=200, unique=True)
    voucher_pin  = models.CharField(max_length=100, blank=True)
    voucher_type = models.CharField(
    max_length=10,
    choices=VoucherType.choices,
    default=VoucherType.FIXED_AMOUNT,
    help_text='Fixed: deducts exact naira amount. Percent: deducts a % off.',
    )
    
    discount_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True, blank=True,
        help_text='Only used when voucher_type is PERCENTAGE_DISCOUNT. e.g. 20.00 = 20% off.',
    )
    amount       = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='leave at 0 for percentage-based vouchers')
    cost_price   = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status       = models.CharField(max_length=20, choices=VoucherStatus.choices,
                                    default=VoucherStatus.AVAILABLE, db_index=True)
    expires_at   = models.DateField(null=True, blank=True)
    assigned_to  = models.ForeignKey(CustomUser, null=True, blank=True,
                                     on_delete=models.SET_NULL,
                                     related_name='assigned_vouchers')
    assigned_at  = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = _('voucher inventory')
        verbose_name_plural = _('voucher inventory')
        indexes             = [models.Index(fields=['service', 'status'])]

    def __str__(self):
        return f"{self.service.name} | {self.voucher_code[:12]}… | {self.status}"

    def is_available(self):
        if self.status != self.VoucherStatus.AVAILABLE:
            return False
        if self.expires_at and self.expires_at < timezone.now().date():
            return False
        return True

    @property
    def display_value(self):
        """Human-readable value for admin and templates."""
        if self.voucher_type == VoucherType.PERCENTAGE_DISCOUNT:
            return f'{self.discount_percentage}% discount'
        return f'₦{self.amount:,.0f}'
    
    @property
    def is_percentage(self):
        return self.voucher_type == VoucherType.PERCENTAGE_DISCOUNT


# ──────────────────────────────────────────────
# SERVICE PURCHASE
# ──────────────────────────────────────────────

class ServicePurchase(models.Model):

    class PurchaseStatus(models.TextChoices):
        PENDING    = 'PENDING',    _('Pending')
        PROCESSING = 'PROCESSING', _('Processing')
        DELIVERED  = 'DELIVERED',  _('Delivered')
        FAILED     = 'FAILED',     _('Failed')

    purchase_id      = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    reference        = models.CharField(max_length=30, unique=True, editable=False)
    user             = models.ForeignKey(CustomUser, on_delete=models.CASCADE,
                                         related_name='service_purchases')
    service          = models.ForeignKey(Service, on_delete=models.CASCADE,
                                         related_name='purchases')
    subscription     = models.ForeignKey(Subscription, on_delete=models.CASCADE,
                                         related_name='service_purchases')
    amount           = models.DecimalField(max_digits=10, decimal_places=2)

    # Data purchases: store GB amount separately for clarity
    data_gb          = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True,
        help_text='For data purchases: GB purchased'
    )
    variation_code   = models.CharField(
        max_length=100, blank=True,
        help_text='Reloadly variation code for data bundles'
    )

    recipient_phone  = models.CharField(max_length=20, blank=True)
    network_provider = models.CharField(max_length=20, choices=NetworkProvider.choices, blank=True)
    voucher          = models.OneToOneField(VoucherInventory, null=True, blank=True,
                                            on_delete=models.SET_NULL, related_name='purchase')
    status           = models.CharField(max_length=20, choices=PurchaseStatus.choices,
                                        default=PurchaseStatus.PENDING, db_index=True)
    api_response       = models.JSONField(null=True, blank=True)
    api_transaction_id = models.CharField(max_length=200, blank=True)
    used_quota         = models.BooleanField(default=True)
    delivered_at       = models.DateTimeField(null=True, blank=True)
    created_at         = models.DateTimeField(auto_now_add=True)
    updated_at         = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ['-created_at']
        verbose_name        = _('service purchase')
        verbose_name_plural = _('service purchases')
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"{self.reference} | {self.user.email} | {self.service.name}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = self._generate_reference()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_reference():
        from django.utils.crypto import get_random_string
        while True:
            ref = f"GP-SVC-{get_random_string(6, '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"
            if not ServicePurchase.objects.filter(reference=ref).exists():
                return ref


# ──────────────────────────────────────────────
# QUOTA USAGE  (monthly tracker — the lock target)
# ──────────────────────────────────────────────

class ServiceQuotaUsage(models.Model):
    """
    One row per user × service × month.

    Airtime  → amount_used  (Decimal naira consumed this month)
    Data     → data_gb_used (Decimal GB consumed this month)
    Vouchers → count_used   (int — vouchers taken this month)

    This row is locked with select_for_update() during deductions.
    """
    user         = models.ForeignKey(CustomUser, on_delete=models.CASCADE,
                                     related_name='service_quota_usages')
    service      = models.ForeignKey(Service, on_delete=models.CASCADE,
                                     related_name='quota_usages')
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE,
                                     related_name='service_quota_usages')
    period_year  = models.PositiveIntegerField()
    period_month = models.PositiveIntegerField()

    # Airtime
    amount_used  = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))

    # Data
    data_gb_used = models.DecimalField(max_digits=8, decimal_places=3, default=Decimal('0'))

    # Vouchers
    count_used   = models.PositiveIntegerField(default=0)

    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together     = ['user', 'service', 'period_year', 'period_month']
        verbose_name        = _('service quota usage')
        verbose_name_plural = _('service quota usages')
        indexes             = [models.Index(fields=['user', 'period_year', 'period_month'])]

    def __str__(self):
        cat = self.service.category
        if cat == ServiceCategory.DATA:
            return (f"{self.user.email} | {self.service.name} | "
                    f"{self.period_year}-{self.period_month:02d}: {self.data_gb_used} GB used")
        if self.service.delivery_type == DeliveryType.MANUAL_CODE:
            return (f"{self.user.email} | {self.service.name} | "
                    f"{self.period_year}-{self.period_month:02d}: {self.count_used} vouchers")
        return (f"{self.user.email} | {self.service.name} | "
                f"{self.period_year}-{self.period_month:02d}: ₦{self.amount_used:,.0f} used")