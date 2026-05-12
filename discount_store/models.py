"""
discount_store/models.py

Two models:
  StoreConfig  — singleton admin-configurable settings
  StoreProduct — a service offered by a venue in the Discount Store
  StoreOrder   — one purchase record per transaction

PAYMENT FLOW:
  User fills checkout form (date, time, quantity) → pays (card or coins)
  → booking created immediately on payment confirmation → emails sent.

No booking credit / expiry window — booking is always created on payment.

REFERENCE FORMAT:  GP-DS-XXXXXX  (distinguishes from GP-COIN- and subscription refs)
"""

import uuid
import datetime
from decimal import Decimal

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account.models import CustomUser


class StoreConfig(models.Model):
    """
    Singleton: admin-configurable Discount Store settings.
    Edit at Django Admin → Discount Store → Store Configuration.
    """
    cashback_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('5.00'),
        help_text='Cashback as % of amount paid. e.g. 5.00 = 5 coins per ₦100 spent.',
    )
    cancellation_cutoff_hours = models.PositiveIntegerField(
        default=4,
        help_text='Users cannot cancel a booking within this many hours of their visit time.',
    )
    max_quantity_per_order = models.PositiveIntegerField(
        default=10,
        help_text='Maximum number of people/tickets per single order.',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Store Configuration'
        verbose_name_plural = 'Store Configuration'

    def __str__(self):
        return f'Store Config — {self.cashback_percentage}% cashback | {self.cancellation_cutoff_hours}h cancel window'

    def save(self, *args, **kwargs):
        self.pk = 1  # Enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def get_config(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config

    def calculate_cashback(self, amount):
        """
        Returns the number of coins earned for a given naira amount.

        Teaching note:
          We use Decimal arithmetic here, not floats, because floating point
          maths on money causes subtle rounding bugs. e.g. 0.1 + 0.2 != 0.3
          in float. Decimal('0.1') + Decimal('0.2') == Decimal('0.3').
          int() truncates (floors) to whole coins — no fractional coins.
        """
        return int(Decimal(str(amount)) * self.cashback_percentage / 100)


class StoreProduct(models.Model):
    """
    A service offered by a venue in the Discount Store.
    Admin or venue sets the price. Each product belongs to one venue.

    Pricing rule: 1 naira = 1 coin (1:1). Admin can change this per product
    in the future but starts at 1:1.
    """
    name          = models.CharField(max_length=200)
    venue         = models.ForeignKey(
        'venues.Venue', on_delete=models.CASCADE,
        related_name='store_products',
    )
    description   = models.TextField(blank=True)
    price         = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text='Price per person in naira. 1 naira = 1 coin.',
    )
    is_active     = models.BooleanField(default=True)
    image         = models.ImageField(
        upload_to='store_products/', null=True, blank=True,
    )
    display_order = models.IntegerField(default=0)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ['display_order', 'name']
        verbose_name        = _('store product')
        verbose_name_plural = _('store products')

    def __str__(self):
        return f"{self.venue.name} — {self.name}"

    @property
    def coin_price(self):
        if not self.price:
            return 0
        return int(self.price)

    def cashback_for_quantity(self, quantity=1):
        if not self.price:
            return 0
        config = StoreConfig.get_config()
        return config.calculate_cashback(self.price * quantity)


class StoreOrder(models.Model):
    """
    One row = one purchase in the Discount Store.

    Lifecycle:
      PENDING → user started checkout, payment not yet confirmed
      PAID    → payment confirmed, Booking created, emails sent
      USED    → staff scanned QR code on visit day
      CANCELLED → user cancelled before cutoff window
      REFUNDED  → admin-initiated refund

    The booking_date, booking_time, and guest_count are stored HERE (on the order)
    because they were collected at checkout BEFORE payment. The Booking object
    created on payment confirmation mirrors these values.
    """

    class OrderStatus(models.TextChoices):
        PENDING   = 'PENDING',   _('Pending Payment')
        PAID      = 'PAID',      _('Paid — Booking Active')
        USED      = 'USED',      _('Used / Attended')
        CANCELLED = 'CANCELLED', _('Cancelled')
        REFUNDED  = 'REFUNDED',  _('Refunded')

    class PaymentMethod(models.TextChoices):
        CARD  = 'CARD',  _('Credit/Debit Card (Paystack)')
        COINS = 'COINS', _('Gold Coins (Wallet)')

    order_id   = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    reference  = models.CharField(
        max_length=30, unique=True, editable=False,
        help_text='Auto-generated. Format: GP-DS-XXXXXX',
    )

    # ── Who bought what ──────────────────────────────────────────────────
    user    = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name='store_orders',
    )
    product = models.ForeignKey(
        StoreProduct, on_delete=models.PROTECT, related_name='orders',
        help_text='PROTECT prevents deleting a product that has orders.',
    )
    quantity = models.PositiveIntegerField(
        default=1,
        help_text='Number of people / tickets. Price × quantity = amount_paid.',
    )

    # ── Payment ──────────────────────────────────────────────────────────
    amount_paid        = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method     = models.CharField(max_length=10, choices=PaymentMethod.choices)
    paystack_reference = models.CharField(
        max_length=100, blank=True, db_index=True,
        help_text='Set for CARD payments. Format: GP-DS-XXXXXX (same as reference).',
    )
    status = models.CharField(
        max_length=20, choices=OrderStatus.choices,
        default=OrderStatus.PENDING, db_index=True,
    )

    # ── Cashback ─────────────────────────────────────────────────────────
    cashback_coins   = models.PositiveIntegerField(
        default=0,
        help_text='Coins to award when payment is confirmed. Calculated at checkout.',
    )
    cashback_awarded = models.BooleanField(default=False)

    # ── Booking link ─────────────────────────────────────────────────────
    # Created immediately when payment is confirmed.
    booking = models.OneToOneField(
        'bookings.Booking', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='store_order',
    )

    # ── Booking details collected at checkout ────────────────────────────
    visit_date    = models.DateField(help_text='Date the user will visit the venue.')
    visit_time    = models.TimeField(blank=True, null=True, help_text='Preferred visit time.')
    special_notes = models.TextField(blank=True)

    # ── Cancellation ─────────────────────────────────────────────────────
    cancelled_by = models.CharField(
        max_length=10,
        choices=[('USER', 'User'), ('VENUE', 'Venue'), ('ADMIN', 'Admin')],
        blank=True,
    )
    cancellation_reason = models.TextField(blank=True)
    cancelled_at        = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ['-created_at']
        verbose_name        = _('store order')
        verbose_name_plural = _('store orders')
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['paystack_reference']),
        ]

    def __str__(self):
        return f"{self.reference} | {self.user.email} | {self.product.name} × {self.quantity}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = self._generate_reference()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_reference():
        """
        Generate a unique GP-DS-XXXXXX reference.

        Teaching note:
          We use a while loop to retry on collision rather than assuming
          uniqueness. In practice the chance of collision is astronomically
          small (36^6 = 2.1 billion combinations), but the guarantee is
          important for payment systems.
        """
        from django.utils.crypto import get_random_string
        while True:
            ref = f"GP-DS{get_random_string(6, '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"
            if not StoreOrder.objects.filter(reference=ref).exists():
                return ref

    def can_cancel(self):
        if self.status != self.OrderStatus.PAID:
            return False

        # 🚨 Fix: handle missing time
        if not self.visit_time:
            return False  # or decide your business logic

        config = StoreConfig.get_config()
        visit_dt = datetime.datetime.combine(self.visit_date, self.visit_time)

        if timezone.is_naive(visit_dt):
            visit_dt = timezone.make_aware(visit_dt)

        cutoff = visit_dt - datetime.timedelta(hours=config.cancellation_cutoff_hours)
        return timezone.now() < cutoff
    @property
    def total_naira(self):
        return self.product.price * self.quantity

    @property
    def is_card_payment(self):
        return self.payment_method == self.PaymentMethod.CARD

    @property
    def is_coin_payment(self):
        return self.payment_method == self.PaymentMethod.COINS