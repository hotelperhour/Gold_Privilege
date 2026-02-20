from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.db.models import Q
import uuid
from datetime import date, timedelta

from account.models import CustomUser
from venues.models import Venue
from subscriptions.models import Subscription


class BookingStatus(models.TextChoices):
    """Booking lifecycle states"""
    CONFIRMED = 'CONFIRMED', _('Confirmed')
    CHECKED_IN = 'CHECKED_IN', _('Checked In')
    COMPLETED = 'COMPLETED', _('Completed')
    CANCELLED = 'CANCELLED', _('Cancelled')
    NO_SHOW = 'NO_SHOW', _('No Show')


class Booking(models.Model):
    """
    Core booking/redemption model - tracks when subscribers visit venues
    
    Business Rules:
    - Only active subscribers can create bookings
    - Enforces subscription plan limits (max_bookings_per_month)
    - Generates unique human-readable reference (GP-BKABCD12)
    - Cannot book for past dates
    - Cannot exceed subscription quota
    
    Status Flow:
    CONFIRMED → CHECKED_IN → COMPLETED
              ↘ CANCELLED
              ↘ NO_SHOW
    """
    
    # Unique Identifiers
    booking_id = models.UUIDField(
        _('booking UUID'),
        default=uuid.uuid4,
        editable=False,
        unique=True,
        help_text='Internal unique identifier (never exposed to users)'
    )
    booking_reference = models.CharField(
        _('booking reference'),
        max_length=20,
        unique=True,
        editable=False,
        help_text='Human-readable reference (e.g., GP-BKABCD12)'
    )
    
    # Core Relations
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.PROTECT,
        related_name='bookings',
        limit_choices_to={'user_type': 'SUBSCRIBER'},
        verbose_name=_('member')
    )
    venue = models.ForeignKey(
        Venue,
        on_delete=models.PROTECT,
        related_name='bookings',
        limit_choices_to={'status': 'APPROVED'},
        verbose_name=_('venue')
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.PROTECT,
        related_name='bookings',
        verbose_name=_('subscription used'),
        help_text='Which subscription plan was used for this booking'
    )
    
    # Visit Details
    visit_date = models.DateField(
        _('visit date'),
        help_text='Date member plans to visit'
    )
    guests_count = models.PositiveIntegerField(
        _('number of guests'),
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(20)],
        help_text='Total guests including member (1-20)'
    )
    special_requests = models.TextField(
        _('special requests'),
        blank=True,
        help_text='Optional notes for venue (e.g., dietary restrictions, occasion)'
    )
    
    # Status & Lifecycle
    status = models.CharField(
        _('status'),
        max_length=20,
        choices=BookingStatus.choices,
        default=BookingStatus.CONFIRMED
    )
    
    # Timestamps
    created_at = models.DateTimeField(
        _('created at'),
        auto_now_add=True
    )
    updated_at = models.DateTimeField(
        _('updated at'),
        auto_now=True
    )
    checked_in_at = models.DateTimeField(
        _('checked in at'),
        null=True,
        blank=True,
        help_text='When member arrived at venue'
    )
    completed_at = models.DateTimeField(
        _('completed at'),
        null=True,
        blank=True,
        help_text='When visit was completed'
    )
    cancelled_at = models.DateTimeField(
        _('cancelled at'),
        null=True,
        blank=True
    )
    
    # Cancellation
    cancellation_reason = models.TextField(
        _('cancellation reason'),
        blank=True
    )
    cancelled_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cancelled_bookings',
        verbose_name=_('cancelled by')
    )
    
    # Check-in Details
    checked_in_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='checked_in_bookings',
        verbose_name=_('checked in by'),
        help_text='Staff member who processed check-in'
    )
    check_in_notes = models.TextField(
        _('check-in notes'),
        blank=True,
        help_text='Internal notes from venue staff'
    )
    
    class Meta:
        verbose_name = _('booking')
        verbose_name_plural = _('bookings')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['venue', 'visit_date', 'status']),
            models.Index(fields=['subscription', 'status']),
            models.Index(fields=['booking_reference']),
            models.Index(fields=['-created_at']),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(guests_count__gte=1) & Q(guests_count__lte=20),
                name='valid_guest_count'
            )
        ]
    
    def __str__(self):
        user = self.user.get_short_name() if self.user_id else "Unknown User"
        venue = self.venue.name if self.venue_id else "Unknown Venue"
        return f"{self.booking_reference} - {user} @ {venue}"

    
    def save(self, *args, **kwargs):
        """Generate unique reference and validate before saving"""
        # Generate reference on creation
        if not self.booking_reference:
            self.booking_reference = self._generate_reference()
        
        # Run full validation
        
        
        super().save(*args, **kwargs)
    
    @staticmethod
    def _generate_reference():
        """
        Generate unique booking reference: GP-BK + 6 alphanumeric
        Format: GP-BKABCD12
        """
        from django.utils.crypto import get_random_string
        
        while True:
            # GP-BK prefix + 6 random chars
            code = get_random_string(6, allowed_chars='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')
            reference = f"GP-BK{code}"
            
            # Ensure uniqueness
            if not Booking.objects.filter(booking_reference=reference).exists():
                return reference
    
    def clean(self):
        super().clean()
        errors = {}

        # ⛔ Skip FK-dependent validation until relations exist
        if not self.user_id or not self.subscription_id or not self.venue_id:
            return

        # 1. Cannot book for past dates
        if self.visit_date and self.visit_date < date.today():
            errors['visit_date'] = _('Cannot book visits in the past')

        # 2. User must be subscriber
        if not self.user.is_subscriber:
            errors['user'] = _('Only subscribers can create bookings')

        # 3. Subscription must be active
        if not self.subscription.is_active():
            errors['subscription'] = _(
                'Subscription is not active. Please renew to continue booking.'
            )

        # 4. Venue must be approved
        if self.venue.status != 'APPROVED':
            errors['venue'] = _('Venue is not currently accepting bookings')

        # 5. Enforce feature-based booking limits (new bookings only)
        if not self.pk and self.venue.primary_feature:
            from subscriptions.utils import can_use_feature
            
            can_use, remaining, msg = can_use_feature(
                self.subscription,
                self.venue.primary_feature
            )
            
            if not can_use:
                errors['venue'] = _(msg)

        # 6. Prevent modifying completed/cancelled bookings
        if self.pk:
            old = Booking.objects.get(pk=self.pk)
            if old.status in [BookingStatus.COMPLETED, BookingStatus.CANCELLED]:
                if self.status != old.status:
                    errors['status'] = _(
                        f'Cannot modify {old.get_status_display()} bookings'
                    )

        if errors:
            raise ValidationError(errors)

    
    # ==================== STATUS TRANSITIONS ====================
    
    def check_in(self, checked_in_by=None, notes=''):
        """
        Mark booking as checked in
        Called when member arrives at venue
        """
        if self.status != BookingStatus.CONFIRMED:
            raise ValidationError(
                f'Can only check in CONFIRMED bookings. Current status: {self.get_status_display()}'
            )
        
        self.status = BookingStatus.CHECKED_IN
        self.checked_in_at = timezone.now()
        self.checked_in_by = checked_in_by
        self.check_in_notes = notes
        self.save(update_fields=['status', 'checked_in_at', 'checked_in_by', 'check_in_notes', 'updated_at'])
    
    def complete(self):
        """
        Mark booking as completed
        Called after visit ends
        """
        if self.status not in [BookingStatus.CHECKED_IN, BookingStatus.CONFIRMED]:
            raise ValidationError(
                f'Can only complete CHECKED_IN or CONFIRMED bookings. Current status: {self.get_status_display()}'
            )
        
        self.status = BookingStatus.COMPLETED
        self.completed_at = timezone.now()
        self.save(update_fields=['status', 'completed_at', 'updated_at'])
    
    def cancel(self, reason='', cancelled_by=None):
        """
        Cancel booking - ONLY allowed before check-in
        Quota is automatically restored because we don't count cancelled bookings
        """
        # FIXED: Can only cancel CONFIRMED bookings (not checked in)
        if self.status != BookingStatus.CONFIRMED:
            raise ValidationError(
                f'Cannot cancel {self.get_status_display()} booking. '
                'You can only cancel before checking in.'
            )
        
        # Check if within reasonable cancellation window
        if self.visit_date and (self.visit_date - date.today()).days < 0:
            raise ValidationError('Cannot cancel bookings for past dates')
        
        self.status = BookingStatus.CANCELLED
        self.cancelled_at = timezone.now()
        self.cancellation_reason = reason
        self.cancelled_by = cancelled_by or self.user
        self.save(update_fields=[
            'status', 'cancelled_at', 'cancellation_reason', 
            'cancelled_by', 'updated_at'
        ])
        
        # NOTE: Quota is automatically restored because check_booking_available()
        # only counts CONFIRMED and CHECKED_IN bookings, not CANCELLED ones
    
    def mark_no_show(self):
        """
        Mark as no-show if member didn't arrive
        Called by venue staff or automated job
        """
        if self.status != BookingStatus.CONFIRMED:
            raise ValidationError('Can only mark CONFIRMED bookings as no-show')
        
        # Only mark no-show after visit date has passed
        if self.visit_date >= date.today():
            raise ValidationError('Cannot mark future bookings as no-show')
        
        self.status = BookingStatus.NO_SHOW
        self.save(update_fields=['status', 'updated_at'])
    
    # ==================== UTILITY METHODS ====================
    
    def can_cancel(self):
        """Check if booking can be cancelled - only BEFORE check-in"""
        return self.status == BookingStatus.CONFIRMED
    
    def can_check_in(self):
        """Check if booking can be checked in"""
        return (
            self.status == BookingStatus.CONFIRMED and
            self.visit_date == date.today()  # Only check in on visit day
        )
    
    def is_upcoming(self):
        """Check if booking is in the future"""
        return (
            self.status == BookingStatus.CONFIRMED and
            self.visit_date >= date.today()
        )
    
    def is_past(self):
        """Check if booking date has passed"""
        return self.visit_date < date.today()
    
    def days_until_visit(self):
        """Get number of days until visit"""
        if self.visit_date:
            delta = self.visit_date - date.today()
            return delta.days
        return None
    
    def get_status_badge_color(self):
        """Get Bootstrap color class for status badge"""
        colors = {
            BookingStatus.CONFIRMED: 'success',
            BookingStatus.CHECKED_IN: 'info',
            BookingStatus.COMPLETED: 'secondary',
            BookingStatus.CANCELLED: 'danger',
            BookingStatus.NO_SHOW: 'warning',
        }
        return colors.get(self.status, 'secondary')
    
    def get_qr_code_data(self):
        """
        Get data string for QR code generation
        Format: GP-BKABCD12|VenueID|Date
        """
        return f"{self.booking_reference}|{self.venue.id}|{self.visit_date.isoformat()}"
    
    @classmethod
    def get_monthly_stats(cls, user, year=None, month=None):
        """
        Get booking statistics for a user in a specific month
        Used to check quota and display usage
        """
        year = year or timezone.now().year
        month = month or timezone.now().month
        
        bookings = cls.objects.filter(
            user=user,
            visit_date__year=year,
            visit_date__month=month
        )
        
        return {
            'total': bookings.count(),
            'confirmed': bookings.filter(status=BookingStatus.CONFIRMED).count(),
            'checked_in': bookings.filter(status=BookingStatus.CHECKED_IN).count(),
            'completed': bookings.filter(status=BookingStatus.COMPLETED).count(),
            'cancelled': bookings.filter(status=BookingStatus.CANCELLED).count(),
            'no_show': bookings.filter(status=BookingStatus.NO_SHOW).count(),
        }
    
    @classmethod
    def check_booking_available(cls, user, subscription):
        """
        Check if user can create new booking this month
        Returns: (can_book: bool, remaining: int, message: str)
        """
        if not subscription or not subscription.is_active():
            return False, 0, "No active subscription"
        
        max_bookings = subscription.plan.max_bookings_per_month
        
        # Unlimited bookings
        if max_bookings is None:
            return True, float('inf'), "Unlimited bookings"
        
        # Count current month bookings
        current_bookings = cls.objects.filter(
            subscription=subscription,
            visit_date__year=timezone.now().year,
            visit_date__month=timezone.now().month,
            status__in=[BookingStatus.CONFIRMED, BookingStatus.CHECKED_IN]
        ).count()
        
        remaining = max_bookings - current_bookings
        
        if remaining <= 0:
            return False, 0, f"Monthly limit reached ({max_bookings} bookings)"
        
        return True, remaining, f"{remaining} booking(s) remaining this month"


class BookingActivity(models.Model):
    """
    Optional: Track all activities/changes to a booking
    Useful for audit trail and customer support
    """
    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name='activities'
    )
    action = models.CharField(
        _('action'),
        max_length=50,
        choices=[
            ('CREATED', 'Booking Created'),
            ('CHECKED_IN', 'Checked In'),
            ('COMPLETED', 'Completed'),
            ('CANCELLED', 'Cancelled'),
            ('NO_SHOW', 'Marked No Show'),
            ('MODIFIED', 'Details Modified'),
        ]
    )
    performed_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_('performed by')
    )
    notes = models.TextField(
        _('notes'),
        blank=True
    )
    created_at = models.DateTimeField(
        _('timestamp'),
        auto_now_add=True
    )
    
    class Meta:
        verbose_name = _('booking activity')
        verbose_name_plural = _('booking activities')
        ordering = ['-created_at']
    
    def __str__(self):
        # CORRECT: Access user and venue through the booking relationship
        booking_ref = self.booking.booking_reference if self.booking else "No Booking"
        user_name = self.booking.user.get_short_name() if self.booking and self.booking.user else "Unknown User"
        venue_name = self.booking.venue.name if self.booking and self.booking.venue else "Unknown Venue"
        return f"{booking_ref} - {user_name} @ {venue_name}"
