from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils.deconstruct import deconstructible
from PIL import Image
import os
import logging
from decimal import Decimal
from django.db.models import PROTECT

from account.models import CustomUser, PartnerProfile

logger = logging.getLogger(__name__)


# ==================== VALIDATORS ====================

def validate_image_size(value):
    """Validate image file size (max 3MB)"""
    if value.size > 3 * 1024 * 1024:
        raise ValidationError("Image must be under 3MB")


@deconstructible
class VenueUploadPath:
    """Dynamic upload path for venue files"""
    def __init__(self, subfolder):
        self.subfolder = subfolder.rstrip('/')

    def __call__(self, instance, filename):
        venue = instance if hasattr(instance, 'slug') else instance.venue
        slug = getattr(venue, 'slug', 'unsaved')
        name, ext = os.path.splitext(filename)
        safe_name = name.replace(' ', '_')[:50]  # Limit filename length
        return f"venues/{slug}/{self.subfolder}/{safe_name}{ext.lower()}"


# ==================== CHOICES ====================

class VenueCategory(models.TextChoices):
    """Venue category types"""
    RESTAURANT = 'RESTAURANT', _('Restaurant & Dining')
    BAR_LOUNGE = 'BAR_LOUNGE', _('Bar & Lounge')
    NIGHTCLUB = 'NIGHTCLUB', _('Nightclub')
    GYM = 'GYM', _('Gym & Fitness Center')
    SPA = 'SPA', _('Spa & Wellness')
    HOTEL = 'HOTEL', _('Hotel & Resort')
    BEACH_CLUB = 'BEACH_CLUB', _('Beach Club')
    POOL = 'POOL', _('Swimming Pool')
    CINEMA = 'CINEMA', _('Cinema & Theater')
    ENTERTAINMENT = 'ENTERTAINMENT', _('Entertainment Venue')
    EVENT_SPACE = 'EVENT_SPACE', _('Event Space')
    SPORTS = 'SPORTS', _('Sports Facility')
    OTHER = 'OTHER', _('Other')


class VenueStatus(models.TextChoices):
    """Venue approval and operational status"""
    DRAFT = 'DRAFT', _('Draft')
    PENDING = 'PENDING', _('Pending Approval')
    APPROVED = 'APPROVED', _('Approved & Active')
    REJECTED = 'REJECTED', _('Rejected')
    SUSPENDED = 'SUSPENDED', _('Suspended')
    CLOSED = 'CLOSED', _('Temporarily Closed')


class PriceRange(models.TextChoices):
    """Price range indicator"""
    BUDGET = 'BUDGET', _('₦ Budget-Friendly')
    MODERATE = 'MODERATE', _('₦₦ Moderate')
    UPSCALE = 'UPSCALE', _('₦₦₦ Upscale')
    LUXURY = 'LUXURY', _('₦₦₦₦ Luxury')


# ==================== CORE MODELS ====================

class VenueAmenity(models.Model):
    """
    Reusable amenities/features for venues
    Admin can add/edit amenities dynamically
    """
    name = models.CharField(
        _('amenity name'),
        max_length=100,
        unique=True,
        help_text='e.g., "Free WiFi", "Parking", "Air Conditioning"'
    )
    icon_class = models.CharField(
        _('icon class'),
        max_length=100,
        blank=True,
        help_text='Font Awesome class (e.g., "fa-wifi")'
    )
    category = models.CharField(
        _('category'),
        max_length=50,
        blank=True,
        help_text='e.g., "Facilities", "Services", "Entertainment"'
    )
    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('venue amenity')
        verbose_name_plural = _('venue amenities')
        ordering = ['display_order', 'name']
    
    def __str__(self):
        return self.name


class Venue(models.Model):
    """
    Main venue model - represents physical locations
    Owned by partners, accessible to subscribers
    """
    
    # Owner & Status
    partner = models.ForeignKey(
        PartnerProfile,
        on_delete=models.CASCADE,
        related_name='venues',
        verbose_name=_('partner owner')
    )
    status = models.CharField(
        _('status'),
        max_length=20,
        choices=VenueStatus.choices,
        default=VenueStatus.DRAFT
    )
    
    # Basic Information
    name = models.CharField(
        _('business name'),
        max_length=255,
        help_text='Official name of the business'
    )
    slug = models.SlugField(
        _('slug'),
        max_length=255,
        unique=True,
        blank=True
    )
    category = models.CharField(
        _('category'),
        max_length=20,
        choices=VenueCategory.choices
    )
    tagline = models.CharField(
        _('tagline'),
        max_length=200,
        blank=True,
        help_text='Short catchy description (e.g., "Lagos\' Premier Rooftop Bar")'
    )
    description = models.TextField(
        _('business description'),
        help_text='Detailed description of the business'
    )
    
    # Contact Information
    phone = models.CharField(
        _('Business phone number'),
        max_length=20,
        help_text='Primary contact number'
    )
    email = models.EmailField(
        _('Business email'),
        blank=True
    )
    website = models.URLField(
        _('Business website'),
        blank=True
    )
    
    # Location
    address = models.CharField(
        _('Business address'),
        max_length=255
    )
    city = models.CharField(
        _('city'),
        max_length=100
    )
    state = models.CharField(
        _('state'),
        max_length=100
    )
    suburb = models.CharField(
        _('suburb/area'),
        max_length=100,
        blank=True
    )
    postal_code = models.CharField(
        _('postal code'),
        max_length=20,
        blank=True
    )
    latitude = models.FloatField(
        _('latitude'),
        null=True,
        blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)]
    )
    longitude = models.FloatField(
        _('longitude'),
        null=True,
        blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)]
    )
    
    capacity = models.IntegerField(
        _('capacity'),
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text='Maximum number of guests'
    )
    
    # Features & Amenities
    amenities = models.ManyToManyField(
        VenueAmenity,
        related_name='venues',
        blank=True,
        verbose_name=_('amenities')
    )
    primary_feature = models.ForeignKey(
        'subscriptions.PlanFeature',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='venues',
        verbose_name=_('primary feature'),
        help_text='Which subscription feature does visiting this venue consume? (e.g., Gym Access, Buffet)'
    )
    
    # Media
    cover_image = models.ImageField(
        _('cover image'),
        upload_to=VenueUploadPath('cover'),
        validators=[validate_image_size],
        help_text='Main venue image (will be optimized to WebP)'
    )
    
    # Operating Hours
    opening_time = models.TimeField(
        _('opening time'),
        null=True,
        blank=True
    )
    closing_time = models.TimeField(
        _('closing time'),
        null=True,
        blank=True
    )
    open_24_hours = models.BooleanField(
        _('open 24 hours'),
        default=False
    )
    
    
    # SEO & Discoverability
    meta_description = models.TextField(
        _('meta description'),
        max_length=160,
        blank=True,
        help_text='For search engines (max 160 chars)'
    )
    
    # Statistics & Engagement
    view_count = models.IntegerField(
        _('view count'),
        default=0,
        editable=False
    )
    average_rating = models.DecimalField(
        _('average rating'),
        max_digits=3,
        decimal_places=2,
        default=0,
        editable=False,
        validators=[MinValueValidator(0), MaxValueValidator(5)]
    )
    total_reviews = models.IntegerField(
        _('total reviews'),
        default=0,
        editable=False
    )
    
    # Approval Details
    approved_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_venues',
        limit_choices_to={'is_staff': True}
    )
    approved_at = models.DateTimeField(
        _('approved at'),
        null=True,
        blank=True
    )
    rejection_reason = models.TextField(
        _('rejection reason'),
        blank=True
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('venue')
        verbose_name_plural = _('venues')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['status', 'category']),
            models.Index(fields=['city', 'state']),
            models.Index(fields=['-average_rating']),
            models.Index(fields=['latitude', 'longitude']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"
    
    def save(self, *args, **kwargs):
        # Generate slug if not present
        if not self.slug:
            self.slug = slugify(self.name)
            original_slug = self.slug
            counter = 1
            while Venue.objects.filter(slug=self.slug).exclude(id=self.id).exists():
                self.slug = f"{original_slug}-{counter}"
                counter += 1
        
        # Auto-set city from partner if not provided
        if not self.city and self.partner:
            self.city = self.partner.city
        
        super().save(*args, **kwargs)
        
        # Process cover image to WebP
        if self.cover_image and not self.cover_image.name.lower().endswith('.webp'):
            self._process_image()
    
    def _process_image(self):
        """Convert cover image to optimized WebP format"""
        try:
            original_path = self.cover_image.path
            img = Image.open(original_path)
            
            # Convert to RGB if necessary
            if img.mode in ('RGBA', 'LA', 'P'):
                rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = rgb_img
            
            # Resize while maintaining aspect ratio
            img.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
            
            # Save as WebP
            webp_path = os.path.splitext(original_path)[0] + ".webp"
            img.save(webp_path, "WEBP", quality=80, method=6)
            
            # Update database path
            old_name = self.cover_image.name
            new_name = os.path.splitext(old_name)[0] + ".webp"
            self.cover_image.name = new_name
            self.save(update_fields=['cover_image'])
            
            # Delete original
            if os.path.exists(original_path) and original_path != webp_path:
                os.remove(original_path)
                logger.info(f"Deleted original: {original_path}")
            
            logger.info(f"Converted: {old_name} → {new_name}")
        
        except Exception as e:
            logger.error(f"Image processing failed for {self.name}: {e}")
    
    def clean(self):
        """Model validation"""
        super().clean()
        
        # Validate operating hours
        if not self.open_24_hours:
            if not self.opening_time or not self.closing_time:
                raise ValidationError({
                    'opening_time': 'Opening and closing times are required unless venue is open 24 hours'
                })
        
        
    
    # In Venue model:
    def is_accessible_by(self, user):
        """Check if user can access venue - SIMPLE"""
        # Staff always can
        if user.is_staff:
            return True, "Staff access"
        
        # Must be subscriber
        if not user.is_subscriber:
            return False, "Subscribe to access venues"
        
        # Must have active subscription
        from subscriptions.models import Subscription
        active_sub = Subscription.objects.filter(
            user=user,
            status__in=['ACTIVE', 'TRIAL'],
            end_date__gte=timezone.now()
        ).first()
        
        if not active_sub:
            return False, "Active subscription required"
        
        return True, "Access granted"
    
    def increment_view_count(self):
        """Increment view counter (atomic operation)"""
        from django.db.models import F
        Venue.objects.filter(pk=self.pk).update(view_count=F('view_count') + 1)
    
    def update_rating(self):
        """Recalculate average rating from reviews"""
        from django.db.models import Avg, Count
        stats = self.reviews.filter(is_approved=True).aggregate(
            avg=Avg('rating'),
            count=Count('id')
        )
        self.average_rating = stats['avg'] or 0
        self.total_reviews = stats['count']
        self.save(update_fields=['average_rating', 'total_reviews'])
    
    def get_operating_hours_display(self):
        """Get formatted operating hours"""
        if self.open_24_hours:
            return "Open 24 Hours"
        if self.opening_time and self.closing_time:
            return f"{self.opening_time.strftime('%I:%M %p')} - {self.closing_time.strftime('%I:%M %p')}"
        return "Hours not specified"
    def to_map_json(self):
        """Serialize venue data for map display"""
        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'category': self.category,
            'category_display': self.get_category_display(),
            'city': self.city,
            'suburb': self.suburb or '',
            'address': self.address,
            'latitude': float(self.latitude) if self.latitude else None,
            'longitude': float(self.longitude) if self.longitude else None,
            'cover_image': self.cover_image.url if self.cover_image else None,
            'average_rating': float(self.average_rating),
            'total_reviews': self.total_reviews,
            'tagline': self.tagline or '',
        }
   


class VenueImage(models.Model):
    """
    Gallery images for venues
    Supports multiple images with ordering
    """
    venue = models.ForeignKey(
        Venue,
        on_delete=models.CASCADE,
        related_name='images'
    )
    image = models.ImageField(
        _('image'),
        upload_to=VenueUploadPath('gallery'),
        validators=[validate_image_size]
    )
    caption = models.CharField(
        _('caption'),
        max_length=255,
        blank=True
    )
    alt_text = models.CharField(
        _('alt text'),
        max_length=255,
        blank=True,
        help_text='For accessibility'
    )
    display_order = models.IntegerField(
        _('display order'),
        default=0
    )
    is_featured = models.BooleanField(
        _('featured'),
        default=False,
        help_text='Show in main gallery highlights'
    )
    
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = _('venue image')
        verbose_name_plural = _('venue images')
        ordering = ['display_order', '-uploaded_at']
    
    def __str__(self):
        return f"Image for {self.venue.name} ({self.display_order})"
    
    def save(self, *args, **kwargs):
        # Auto-assign order if not set
        if not self.display_order and self.venue_id:
            max_order = VenueImage.objects.filter(
                venue=self.venue
            ).aggregate(models.Max('display_order'))['display_order__max']
            self.display_order = (max_order or 0) + 1
        
        super().save(*args, **kwargs)
        
        # Process image to WebP
        if self.image and not self.image.name.lower().endswith('.webp'):
            self._process_image()
    
    def _process_image(self):
        """Convert image to optimized WebP format"""
        try:
            original_path = self.image.path
            img = Image.open(original_path)
            
            # Convert to RGB if necessary
            if img.mode in ('RGBA', 'LA', 'P'):
                rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = rgb_img
            
            img.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
            
            webp_path = os.path.splitext(original_path)[0] + ".webp"
            img.save(webp_path, "WEBP", quality=80, method=6)
            
            old_name = self.image.name
            new_name = os.path.splitext(old_name)[0] + ".webp"
            self.image.name = new_name
            self.save(update_fields=['image'])
            
            if os.path.exists(original_path) and original_path != webp_path:
                os.remove(original_path)
            
            logger.info(f"Converted gallery image: {old_name} → {new_name}")
        
        except Exception as e:
            logger.error(f"Gallery image processing failed: {e}")


class VenueReview(models.Model):
    """
    User reviews and ratings for venues
    Only subscribers can leave reviews
    """
    venue = models.ForeignKey(
        Venue,
        on_delete=models.CASCADE,
        related_name='reviews'
    )
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='venue_reviews',
        limit_choices_to={'user_type': 'SUBSCRIBER'}
    )
    
    # Review Content
    rating = models.IntegerField(
        _('rating'),
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text='Rating from 1 to 5 stars'
    )
    title = models.CharField(
        _('review title'),
        max_length=200,
        blank=True
    )
    review_text = models.TextField(
        _('review'),
        help_text='Share your experience'
    )
    
    # Moderation
    is_approved = models.BooleanField(
        _('approved'),
        default=False
    )
    is_verified_visit = models.BooleanField(
        _('verified visit'),
        default=False,
        help_text='User has booking/visit record'
    )
    
    # Engagement
    helpful_count = models.IntegerField(
        _('helpful votes'),
        default=0
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('venue review')
        verbose_name_plural = _('venue reviews')
        ordering = ['-created_at']
        unique_together = ['venue', 'user']  # One review per user per venue
        indexes = [
            models.Index(fields=['venue', 'is_approved']),
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self):
        return f"{self.user.get_short_name()} - {self.venue.name} ({self.rating}★)"
    
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Update venue's average rating
        self.venue.update_rating()


class VenueFavorite(models.Model):
    """
    User favorites/bookmarks for venues
    """
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='favorite_venues'
    )
    venue = models.ForeignKey(
        Venue,
        on_delete=models.CASCADE,
        related_name='favorited_by'
    )
    added_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = _('venue favorite')
        verbose_name_plural = _('venue favorites')
        unique_together = ['user', 'venue']
        ordering = ['-added_at']
    
    def __str__(self):
        return f"{self.user.get_short_name()} → {self.venue.name}"
    

class VenueBooking(models.Model):
    """
    Core transaction model - tracks when members actually use venues
    This is what makes your subscription valuable
    """
    # Reference
    booking_reference = models.CharField(max_length=20, unique=True, editable=False)

    # Who & What
    user = models.ForeignKey(CustomUser, on_delete=PROTECT, limit_choices_to={'user_type': 'SUBSCRIBER'})
    venue = models.ForeignKey(Venue, on_delete=PROTECT)
    subscription = models.ForeignKey('subscriptions.Subscription', on_delete=PROTECT)

    # Visit Details
    visit_date = models.DateField()
    guests_count = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    
    # Status (SIMPLE)
    STATUS_CHOICES = [
        ('CONFIRMED', 'Confirmed'),
        ('CHECKED_IN', 'Checked In'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='CONFIRMED')
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['venue', 'visit_date']),
        ]
    
    def save(self, *args, **kwargs):
        if not self.booking_reference:
            self.booking_reference = self._generate_reference()
        super().save(*args, **kwargs)
    
    @staticmethod
    def _generate_reference():
        from django.utils.crypto import get_random_string
        while True:
            ref = f"GP-VEN-{get_random_string(7, '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"
            if not VenueBooking.objects.filter(booking_reference=ref).exists():
                return ref
    
    def clean(self):
        # Enforce subscription booking limits
        if self.subscription and self.subscription.plan.max_bookings_per_month:
            bookings_this_month = VenueBooking.objects.filter(
                user=self.user,
                subscription=self.subscription,
                visit_date__year=timezone.now().year,
                visit_date__month=timezone.now().month,
                status__in=['CONFIRMED', 'CHECKED_IN', 'COMPLETED']
            ).count()
            
            if bookings_this_month >= self.subscription.plan.max_bookings_per_month:
                raise ValidationError(
                    f"Booking limit reached ({self.subscription.plan.max_bookings_per_month}/month). "
                    "Please upgrade your subscription."
                )