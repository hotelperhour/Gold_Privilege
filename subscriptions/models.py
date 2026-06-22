from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.core.validators import MinValueValidator, MaxValueValidator
from account.models import CustomUser
import uuid


class PlanFeature(models.Model):
    """
    Individual features that can be added to plans
    Fully dynamic - admin can add/edit/remove features
    """
    name = models.CharField(
        _('feature name'),
        max_length=200,
        help_text='e.g., "Access to Premium Gyms"'
    )
    description = models.TextField(
        _('description'),
        blank=True,
        help_text='Detailed description of this feature'
    )
    icon = models.CharField(
        _('icon'),
        max_length=50,
        blank=True,
        help_text='Font Awesome icon class (e.g., "fa-dumbbell")'
    )
    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(
        default=0,
        help_text='Order in which feature appears (lower = first)'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('plan feature')
        verbose_name_plural = _('plan features')
        ordering = ['display_order', 'name']
    
    def __str__(self):
        return self.name


class SubscriptionPlan(models.Model):
    """
    Subscription plans - fully dynamic and configurable from admin
    """
    
    class BillingPeriod(models.TextChoices):
        MONTHLY = 'MONTHLY', _('Monthly')
        QUARTERLY = 'QUARTERLY', _('Quarterly (3 months)')
        SEMI_ANNUAL = 'SEMI_ANNUAL', _('Semi-Annual (6 months)')
        ANNUAL = 'ANNUAL', _('Annual (12 months)')
    
    # Basic Information
    name = models.CharField(
        _('plan name'),
        max_length=100,
        help_text='e.g., "Gold", "Platinum", "Diamond"'
    )
    slug = models.SlugField(
        _('slug'),
        max_length=100,
        unique=True,
        help_text='URL-friendly name (auto-generated from name)'
    )
    description = models.TextField(
        _('description'),
        help_text='Short description of the plan'
    )
    tagline = models.CharField(
        _('tagline'),
        max_length=200,
        blank=True,
        help_text='Catchy tagline (e.g., "Most Popular")'
    )
    
    # Pricing
    price = models.DecimalField(
        _('price'),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text='Price in Naira (NGN)'
    )
    billing_period = models.CharField(
        _('billing period'),
        max_length=20,
        choices=BillingPeriod.choices,
        default=BillingPeriod.MONTHLY
    )
    
    # Trial Period
    trial_period_days = models.IntegerField(
        _('trial period (days)'),
        default=0,
        validators=[MinValueValidator(0)],
        help_text='Number of free trial days (0 for no trial)'
    )

    venue_tier_access = models.PositiveSmallIntegerField(
        default=1,
        choices=[
            (1, 'Level 1 — Standard venues'),
            (2, 'Level 2 — Level 1 + Premium venues'),
            (3, 'Level 3 — All venues'),
        ],
        help_text='Which venue star tiers this plan unlocks. Level 2 also includes Level 1.',
    )
    
    # Features
    features = models.ManyToManyField(
        PlanFeature,
        through='PlanFeatureAssignment',
        related_name='plans',
        blank=True
    )
    
    
    
    max_guests_per_booking = models.IntegerField(
        _('max guests per booking'),
        default=2,
        validators=[MinValueValidator(1)]
    )
    
    # Display Settings
    is_featured = models.BooleanField(
        _('featured plan'),
        default=False,
        help_text='Highlight this plan (e.g., "Most Popular")'
    )
    is_coming_soon = models.BooleanField(
        _('coming soon'),
        default=False,
        help_text='Mark this plan as Coming Soon — visible but not subscribable'
    )
    highlight_color = models.CharField(
        _('highlight color'),
        max_length=7,
        default='#d4af37',
        help_text='Hex color code for plan badge/border'
    )
    display_order = models.IntegerField(
        default=0,
        help_text='Order in which plan appears (lower = first)'
    )
    
    # Availability
    is_active = models.BooleanField(
        _('active'),
        default=True,
        help_text='Is this plan available for purchase?'
    )
    available_from = models.DateTimeField(
        _('available from'),
        null=True,
        blank=True,
        help_text='Plan becomes available on this date'
    )
    available_until = models.DateTimeField(
        _('available until'),
        null=True,
        blank=True,
        help_text='Plan stops being available on this date'
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('subscription plan')
        verbose_name_plural = _('subscription plans')
        ordering = ['display_order', 'price']
    
    def __str__(self):
        return f"{self.name} - ₦{self.price:,.2f}/{self.get_billing_period_display()}"
    
    def is_available(self):
        """Check if plan is currently available"""
        if not self.is_active:
            return False
        
        now = timezone.now()
        
        if self.available_from and now < self.available_from:
            return False
        
        if self.available_until and now > self.available_until:
            return False
        
        return True
    
    def get_price_display(self):
        """Get formatted price"""
        return f"₦{self.price:,.2f}"
    
    def get_duration_in_days(self):
        """
        Get billing period in days
        
        PRODUCTION NOTE: For exact calendar months, use dateutil.relativedelta:
        - pip install python-dateutil
        - from dateutil.relativedelta import relativedelta
        - end_date = start_date + relativedelta(months=1)
        
        Current approximation is fine for < 1000 users
        For production scaling, consider exact calendar calculations
        """
        durations = {
            'MONTHLY': 30,      # Approximation - actual months vary (28-31 days)
            'QUARTERLY': 90,    # 3 months
            'SEMI_ANNUAL': 180, # 6 months
            'ANNUAL': 365,      # 1 year (366 for leap years)
        }
        return durations.get(self.billing_period, 30)
    
    # PRODUCTION TIP: Add this method for exact calendar calculations
    def get_next_billing_date(self, from_date=None):
        """
        Calculate exact next billing date using calendar months
        Requires: pip install python-dateutil
        """
        try:
            from dateutil.relativedelta import relativedelta
            
            start = from_date or timezone.now()
            
            if self.billing_period == 'MONTHLY':
                return start + relativedelta(months=1)
            elif self.billing_period == 'QUARTERLY':
                return start + relativedelta(months=3)
            elif self.billing_period == 'SEMI_ANNUAL':
                return start + relativedelta(months=6)
            elif self.billing_period == 'ANNUAL':
                return start + relativedelta(years=1)
            else:
                # Fallback to days-based calculation
                from datetime import timedelta
                return start + timedelta(days=self.get_duration_in_days())
        except ImportError:
            # If dateutil not installed, use days-based calculation
            from datetime import timedelta
            return from_date or timezone.now() + timedelta(days=self.get_duration_in_days())


class PlanFeatureAssignment(models.Model):
    """
    Through model for Plan-Feature relationship
    Allows customizing feature details per plan
    """
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.CASCADE,
        related_name='feature_assignments'
    )
    feature = models.ForeignKey(
        PlanFeature,
        on_delete=models.CASCADE,
        related_name='plan_assignments'
    )
    usage_limit = models.IntegerField(
        _('usage limit'),
        default=1,
        validators=[MinValueValidator(1)],
        help_text='Number of times this feature can be used per billing period (e.g., 5 gym sessions)'
    )
    
    # Optional: Customize feature for this plan
    
    is_highlighted = models.BooleanField(
        default=False,
        help_text='Highlight this feature for this plan'
    )
    display_order = models.IntegerField(default=0)
    
    class Meta:
        verbose_name = _('plan feature assignment')
        verbose_name_plural = _('plan feature assignments')
        ordering = ['display_order', 'feature__display_order']
        unique_together = ['plan', 'feature']
    
    def __str__(self):
        return f"{self.plan.name} - {self.feature.name}"


class PromoCode(models.Model):
    """
    Promotional discount codes - fully manageable from admin
    """
    
    class DiscountType(models.TextChoices):
        PERCENTAGE = 'PERCENTAGE', _('Percentage')
        FIXED_AMOUNT = 'FIXED_AMOUNT', _('Fixed Amount')
    
    # Code Details
    code = models.CharField(
        _('promo code'),
        max_length=50,
        unique=True,
        help_text='Coupon code (e.g., "LAUNCH2025")'
    )
    description = models.CharField(
        _('description'),
        max_length=200,
        blank=True
    )
    
    # Discount Settings
    discount_type = models.CharField(
        _('discount type'),
        max_length=20,
        choices=DiscountType.choices,
        default=DiscountType.PERCENTAGE
    )
    discount_value = models.DecimalField(
        _('discount value'),
        max_digits=10,
        decimal_places=2,
        validators=[
            MinValueValidator(0),
            MaxValueValidator(100)  # Prevent percentage > 100
        ],
        help_text='Percentage (0-100) or Fixed Amount in Naira'
    )
    
    # Applicable Plans
    applicable_plans = models.ManyToManyField(
        SubscriptionPlan,
        related_name='promo_codes',
        blank=True,
        help_text='Leave empty to apply to all plans'
    )
    
    # Usage Limits
    max_uses = models.IntegerField(
        _('max uses'),
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text='Maximum number of times this code can be used (empty = unlimited)'
    )
    uses_count = models.IntegerField(
        _('uses count'),
        default=0,
        help_text='Number of times this code has been used'
    )
    max_uses_per_user = models.IntegerField(
        _('max uses per user'),
        default=1,
        validators=[MinValueValidator(1)]
    )
    
    # Validity Period
    valid_from = models.DateTimeField(
        _('valid from'),
        default=timezone.now
    )
    valid_until = models.DateTimeField(
        _('valid until'),
        null=True,
        blank=True
    )
    
    # Status
    is_active = models.BooleanField(
        _('active'),
        default=True
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('promo code')
        verbose_name_plural = _('promo codes')
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.code} ({self.get_discount_display()})"
    
    def get_discount_display(self):
        """Get formatted discount"""
        if self.discount_type == self.DiscountType.PERCENTAGE:
            return f"{self.discount_value}% off"
        return f"₦{self.discount_value:,.2f} off"
    
    def is_valid(self):
        """Check if promo code is currently valid"""
        if not self.is_active:
            return False
        
        now = timezone.now()
        
        if now < self.valid_from:
            return False
        
        if self.valid_until and now > self.valid_until:
            return False
        
        if self.max_uses and self.uses_count >= self.max_uses:
            return False
        
        return True
    
    def can_be_used_by(self, user):
        """Check if user can use this promo code"""
        if not self.is_valid():
            return False
        
        # Check user usage count
        user_usage = self.usages.filter(user=user).count()
        if user_usage >= self.max_uses_per_user:
            return False
        
        return True
    
    def calculate_discount(self, amount):
        """
        Calculate discount amount with validation
        """
        from decimal import Decimal
        
        if self.discount_type == self.DiscountType.PERCENTAGE:
            # Convert percentage to Decimal
            percentage = Decimal(str(self.discount_value))
            # Ensure percentage is between 0-100
            percentage = min(max(percentage, Decimal(0)), Decimal(100))
            # Calculate discount using Decimal arithmetic
            discount = (amount * percentage) / Decimal(100)
        else:
            # Fixed amount - can't discount more than total
            fixed_amount = Decimal(str(self.discount_value))
            discount = min(fixed_amount, amount)
        
        # Ensure discount doesn't exceed amount
        discount = min(discount, amount)
        
        # Return rounded Decimal with 2 decimal places
        return max(discount, Decimal(0)).quantize(Decimal('0.01'))


class Subscription(models.Model):
    """
    User subscriptions - tracks active memberships
    """
    
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', _('Active')
        TRIAL = 'TRIAL', _('Trial Period')
        EXPIRED = 'EXPIRED', _('Expired')
        CANCELLED = 'CANCELLED', _('Cancelled')
        PENDING = 'PENDING', _('Pending Payment')
    
    # Unique identifier
    subscription_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        help_text='Internal unique identifier (secure)'
    )

    # User-friendly reference (for display)
    subscription_reference = models.CharField(
        _('subscription reference'),
        max_length=20,
        unique=True,
        editable=False,
        help_text='Customer-facing reference (e.g., GP-SUB-A7X9)'
    )
   

    # Relations
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='subscriptions',
        limit_choices_to={'user_type': 'SUBSCRIBER'}
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name='subscriptions'
    )
    
    # Subscription Period
    start_date = models.DateTimeField(_('start date'))
    end_date = models.DateTimeField(_('end date'))
    
    # Trial
    is_trial = models.BooleanField(default=False)
    trial_end_date = models.DateTimeField(null=True, blank=True)
    
    # Auto-renewal
    auto_renew = models.BooleanField(
        _('auto renew'),
        default=True,
        help_text='Automatically renew subscription on expiry'
    )
    
    # Status
    status = models.CharField(
        _('status'),
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING
    )
    
    # Pricing (stored for historical record)
    price_paid = models.DecimalField(
        _('price paid'),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)]
    )
    promo_code_used = models.ForeignKey(
        PromoCode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='usages'
    )
    discount_amount = models.DecimalField(
        _('discount amount'),
        max_digits=10,
        decimal_places=2,
        default=0
    )
    
    # Usage Tracking
    bookings_count = models.IntegerField(
        _('bookings count'),
        default=0,
        help_text='Number of bookings made in current period'
    )
    
    # Metadata
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('subscription')
        verbose_name_plural = _('subscriptions')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['end_date']),
        ]
    
    def __str__(self):
        return f"{self.user.email} - {self.plan.name} ({self.status})"
    
    def save(self, *args, **kwargs):
        # Generate user-friendly reference on first save
        if not self.subscription_reference:
            self.subscription_reference = self._generate_subscription_reference()
        super().save(*args, **kwargs)
    
    @staticmethod
    def _generate_subscription_reference():
        """Generate short reference like GP-SUB-A7X9"""
        from django.utils.crypto import get_random_string
        prefix = "GP-SUB-"
        while True:
            code = get_random_string(4, allowed_chars='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')
            reference = prefix + code
            if not Subscription.objects.filter(subscription_reference=reference).exists():
                return reference
    
    def is_active(self):
        """
        Check if subscription is currently active
        More robust check combining status and date range
        """
        # Must have active or trial status
        if self.status not in [self.Status.ACTIVE, self.Status.TRIAL]:
            return False
        
        # Must be within subscription period
        now = timezone.now()
        return self.start_date <= now <= self.end_date
    
    def days_remaining(self):
        """Get number of days remaining"""
        if not self.is_active():
            return 0
        
        delta = self.end_date - timezone.now()
        return max(0, delta.days)
    
    
    
    def cancel(self, reason=''):
        """Cancel subscription"""
        self.status = self.Status.CANCELLED
        self.cancelled_at = timezone.now()
        self.cancellation_reason = reason
        self.auto_renew = False
        self.save()


class Payment(models.Model):
    """
    Payment records for subscriptions
    """
    
    class PaymentStatus(models.TextChoices):
        PENDING = 'PENDING', _('Pending')
        SUCCESS = 'SUCCESS', _('Success')
        FAILED = 'FAILED', _('Failed')
        REFUNDED = 'REFUNDED', _('Refunded')
    
    class PaymentMethod(models.TextChoices):
        CARD = 'CARD', _('Card')
        BANK_TRANSFER = 'BANK_TRANSFER', _('Bank Transfer')
        USSD = 'USSD', _('USSD')
        MOBILE_MONEY = 'MOBILE_MONEY', _('Mobile Money')
    
    # Unique identifier
    payment_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        help_text='Internal unique identifier (secure, never exposed)'
    )

    payment_reference = models.CharField(
        _('payment reference'),
        max_length=20,
        unique=True,
        editable=False,
        help_text='Customer-facing payment reference (e.g., GP-TW7268)'
    )
    
    # Relations
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name='payments'
    )
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='payments'
    )
    
    # Payment Details
    amount = models.DecimalField(
        _('amount'),
        max_digits=10,
        decimal_places=2
    )
    payment_method = models.CharField(
        _('payment method'),
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CARD
    )
    status = models.CharField(
        _('status'),
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING
    )
    
    # Gateway Details (Paystack)
    gateway_reference = models.CharField(
        _('gateway reference'),
        max_length=255,
        blank=True,
        help_text='Reference from payment gateway (e.g., Paystack)'
    )
    gateway_response = models.JSONField(
        _('gateway response'),
        null=True,
        blank=True,
        help_text='Full response from payment gateway'
    )
    
    # Metadata
    paid_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('payment')
        verbose_name_plural = _('payments')
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Payment {self.payment_id} - ₦{self.amount} ({self.status})"
    

    def save(self, *args, **kwargs):
        # Generate user-friendly reference on first save
        if not self.payment_reference:
            self.payment_reference = self._generate_payment_reference()
        super().save(*args, **kwargs)
    
    @staticmethod
    def _generate_payment_reference():
        """Generate short, user-friendly reference like GP-TW7268"""
        from django.utils.crypto import get_random_string
        prefix = "GP-"
        while True:
            # 6 alphanumeric characters
            code = get_random_string(6, allowed_chars='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')
            reference = prefix + code
            if not Payment.objects.filter(payment_reference=reference).exists():
                return reference
    
    def mark_as_paid(self):
        """Mark payment as successful"""
        self.status = self.PaymentStatus.SUCCESS
        self.paid_at = timezone.now()
        self.save()
        
        # Activate subscription
        if self.subscription.status == Subscription.Status.PENDING:
            self.subscription.status = Subscription.Status.ACTIVE
            self.subscription.save()

# ────────────────────────────────────────────────────────────────────
# ADD THIS to subscriptions/models.py (at the end, after Subscription)
# ────────────────────────────────────────────────────────────────────

class FeatureUsage(models.Model):
    """
    Tracks how many times a user has used each feature in their current subscription period.
    
    Example rows:
    - User A, Gold Subscription, Gym Feature: used 5 / limit 8
    - User A, Gold Subscription, Buffet Feature: used 2 / limit 5
    - User B, Family Subscription, Pool Feature: used 3 / limit 5
    
    Resets monthly or when subscription period changes.
    """
    
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name='feature_usages'
    )
    feature = models.ForeignKey(
        PlanFeature,
        on_delete=models.CASCADE,
        related_name='usages'
    )
    
    # Usage tracking
    used_count = models.IntegerField(
        _('times used'),
        default=0,
        validators=[MinValueValidator(0)]
    )
    
    # Period tracking (to know when to reset)
    period_year = models.IntegerField(_('period year'))
    period_month = models.IntegerField(_('period month'))
    
    # Timestamps
    last_used_at = models.DateTimeField(
        _('last used at'),
        null=True,
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('feature usage')
        verbose_name_plural = _('feature usages')
        unique_together = ['subscription', 'feature', 'period_year', 'period_month']
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['subscription', 'period_year', 'period_month']),
        ]
    
    def __str__(self):
        limit = self.get_limit()
        return f"{self.subscription.user.email} - {self.feature.name}: {self.used_count}/{limit}"
    
    def get_limit(self):
        """Get the usage limit for this feature from the plan."""
        try:
            assignment = PlanFeatureAssignment.objects.get(
                plan=self.subscription.plan,
                feature=self.feature
            )
            return assignment.usage_limit
        except PlanFeatureAssignment.DoesNotExist:
            return 0  # Feature not in plan
    
    def can_use(self):
        """Check if user can still use this feature."""
        limit = self.get_limit()
        if limit is None:
            return True  # Unlimited
        return self.used_count < limit
    
    def remaining(self):
        """Get remaining uses."""
        limit = self.get_limit()
        if limit is None:
            return float('inf')
        return max(0, limit - self.used_count)
    
    def increment(self):
        """Increment usage count (called when booking is created)."""
        self.used_count += 1
        self.last_used_at = timezone.now()
        self.save(update_fields=['used_count', 'last_used_at', 'updated_at'])
    
    def decrement(self):
        """Decrement usage count (called when booking is cancelled)."""
        if self.used_count > 0:
            self.used_count -= 1
            self.save(update_fields=['used_count', 'updated_at'])
