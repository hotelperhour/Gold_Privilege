import uuid
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.core.validators import MinValueValidator, MaxValueValidator

MONEY_STEP = Decimal("0.01")


def quantize_money(value):
    return Decimal(str(value or 0)).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


class PayoutConfig(models.Model):
    apply_commission_to_store = models.BooleanField(
        default=True,
        help_text=(
            "If enabled, GP keeps the configured percentage from Discount Store "
            "sales before paying the venue."
        ),
    )
    store_commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("10.00"),
        help_text="Commission GP keeps from Discount Store sales.",
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    apply_commission_to_subscription = models.BooleanField(
        default=False,
        help_text=(
            "If enabled, GP also keeps a percentage from subscription venue "
            "payouts. Leave off if GP margin is already baked into subscription pricing."
        ),
    )
    subscription_commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Commission GP keeps from subscription check-ins.",
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    payout_delay_hours = models.PositiveIntegerField(
        default=24,
        help_text=(
            "Hours after check-in before a sales record becomes payout-eligible. "
            "Examples: 1, 12, 24."
        ),
    )
    minimum_payout_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1000.00"),
        help_text="Minimum net amount required before an automatic payout batch is created.",
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = "Payout Configuration"
        verbose_name_plural = "Payout Configuration"

    def __str__(self):
        return (
            f"PayoutConfig | delay={self.payout_delay_hours}h | "
            f"store={self.store_commission_rate}% | sub={self.subscription_commission_rate}%"
        )

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_config(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config


    def eligible_at_for(self, checked_in_at):
        return checked_in_at + timedelta(hours=self.payout_delay_hours)
    
    def commission_enabled_for(self, booking_source):
        if booking_source == "STORE":
            return self.apply_commission_to_store
        return self.apply_commission_to_subscription

    def commission_rate_for(self, booking_source):
        if booking_source == "STORE":
            return self.store_commission_rate if self.apply_commission_to_store else Decimal("0.00")
        return self.subscription_commission_rate if self.apply_commission_to_subscription else Decimal("0.00")

    def commission_amount_for(self, booking_source, gross_amount):
        rate = self.commission_rate_for(booking_source)
        return quantize_money(Decimal(str(gross_amount or 0)) * rate / Decimal("100"))


class PayoutRecord(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending Review")
        APPROVED = "APPROVED", _("Approved")
        PAID = "PAID", _("Paid")
        FAILED = "FAILED", _("Failed")
        CANCELLED = "CANCELLED", _("Cancelled")

    payout_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    reference = models.CharField(max_length=30, unique=True, editable=False)
    venue = models.ForeignKey(
        "venues.Venue",
        on_delete=models.PROTECT,
        related_name="payout_records",
    )
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    total_gross = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_commission = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_net = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    booking_count = models.PositiveIntegerField(default=0)
    store_count = models.PositiveIntegerField(default=0)
    subscription_count = models.PositiveIntegerField(default=0)

    bank_name_snapshot = models.CharField(max_length=100, blank=True)
    account_number_snapshot = models.CharField(max_length=20, blank=True)
    account_name_snapshot = models.CharField(max_length=255, blank=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_venue_payouts",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="paid_venue_payouts",
    )
    paid_at = models.DateTimeField(null=True, blank=True)

    transfer_reference = models.CharField(max_length=150, blank=True)
    transfer_notes = models.TextField(blank=True)
    admin_notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["venue", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"{self.reference} | {self.venue.name} | {self.total_net} | {self.status}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = self._generate_reference()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_reference():
        from django.utils.crypto import get_random_string

        while True:
            reference = f"GP-PO-{get_random_string(6, '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"
            if not PayoutRecord.objects.filter(reference=reference).exists():
                return reference

    def approve(self, admin_user, notes=""):
        if self.status != self.Status.PENDING:
            raise ValueError("Only pending payouts can be approved.")
        self.status = self.Status.APPROVED
        self.approved_by = admin_user
        self.approved_at = timezone.now()
        self.admin_notes = (notes or "").strip()
        self.save(update_fields=["status", "approved_by", "approved_at", "admin_notes", "updated_at"])

    def mark_paid(self, admin_user, transfer_reference, notes=""):
        if self.status not in (self.Status.APPROVED, self.Status.FAILED):
            raise ValueError("Only approved or failed payouts can be marked as paid.")
        if not (transfer_reference or "").strip():
            raise ValueError("Transfer reference is required.")
        self.status = self.Status.PAID
        self.transfer_reference = transfer_reference.strip()
        self.transfer_notes = (notes or "").strip()
        self.paid_by = admin_user
        self.paid_at = timezone.now()
        self.save(
            update_fields=[
                "status",
                "transfer_reference",
                "transfer_notes",
                "paid_by",
                "paid_at",
                "updated_at",
            ]
        )

    def mark_failed(self, notes=""):
        if self.status not in (self.Status.PENDING, self.Status.APPROVED):
            raise ValueError("Only pending or approved payouts can be marked failed.")
        self.status = self.Status.FAILED
        self.admin_notes = (notes or "").strip()
        self.save(update_fields=["status", "admin_notes", "updated_at"])

    def cancel(self, notes=""):
        if self.status == self.Status.PAID:
            raise ValueError("Paid payouts cannot be cancelled.")
        with transaction.atomic():
            self.sales_records.update(payout_record=None)
            self.status = self.Status.CANCELLED
            self.admin_notes = (notes or "").strip()
            self.save(update_fields=["status", "admin_notes", "updated_at"])

    @property
    def is_open(self):
        return self.status in {self.Status.PENDING, self.Status.APPROVED, self.Status.FAILED}


class SalesRecord(models.Model):
    class BookingSource(models.TextChoices):
        SUBSCRIPTION = "SUBSCRIPTION", _("Subscription Visit")
        STORE = "STORE", _("Discount Store Purchase")

    booking = models.OneToOneField(
        "bookings.Booking",
        on_delete=models.PROTECT,
        related_name="sales_record",
    )
    venue = models.ForeignKey(
        "venues.Venue",
        on_delete=models.PROTECT,
        related_name="sales_records",
    )
    payout_record = models.ForeignKey(
        PayoutRecord,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales_records",
    )

    booking_source = models.CharField(max_length=20, choices=BookingSource.choices)
    source_reference = models.CharField(
        max_length=40,
        blank=True,
        help_text="Snapshot of the customer-facing source reference.",
    )

    checked_in_at = models.DateTimeField()
    eligible_for_payout_at = models.DateTimeField(db_index=True)

    gross_amount = models.DecimalField(max_digits=10, decimal_places=2)
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2)
    net_amount = models.DecimalField(max_digits=10, decimal_places=2)

    commission_rate_snapshot = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    commission_enabled_snapshot = models.BooleanField(default=False)

    payment_method_snapshot = models.CharField(max_length=20, blank=True)
    guests_count = models.PositiveIntegerField(default=1)
    notes = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-checked_in_at", "-created_at"]
        indexes = [
            models.Index(fields=["venue", "payout_record"]),
            models.Index(fields=["venue", "eligible_for_payout_at"]),
            models.Index(fields=["booking_source", "eligible_for_payout_at"]),
            models.Index(fields=["booking_source"]),
        ]

    def __str__(self):
        return (
            f"{self.booking.booking_reference} | {self.venue.name} | "
            f"net {self.net_amount} | {self.booking_source}"
        )

    @property
    def is_paid(self):
        return self.payout_record_id is not None and self.payout_record.status == PayoutRecord.Status.PAID

    @property
    def is_in_open_payout(self):
        return self.payout_record_id is not None and self.payout_record.status in {
            PayoutRecord.Status.PENDING,
            PayoutRecord.Status.APPROVED,
            PayoutRecord.Status.FAILED,
        }
