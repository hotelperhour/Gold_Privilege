from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from .models import (
    VenueAmenity, Venue, VenueImage, 
    VenueReview, VenueFavorite
)


@admin.register(VenueAmenity)
class VenueAmenityAdmin(admin.ModelAdmin):
    """Admin interface for venue amenities"""
    list_display = ['name', 'category', 'icon_preview', 'is_active', 'display_order']
    list_filter = ['is_active', 'category']
    search_fields = ['name', 'category']
    list_editable = ['display_order', 'is_active']
    ordering = ['display_order', 'name']
    
    fieldsets = (
        (None, {
            'fields': ('name', 'icon_class', 'category')
        }),
        ('Settings', {
            'fields': ('is_active', 'display_order')
        }),
    )
    
    def icon_preview(self, obj):
        if obj.icon_class:
            return format_html(
                '<i class="{}" style="font-size: 20px;"></i>',
                obj.icon_class
            )
        return '-'
    icon_preview.short_description = 'Icon'


class VenueImageInline(admin.TabularInline):
    """Inline for venue gallery images"""
    model = VenueImage
    extra = 1
    fields = ['image', 'caption', 'alt_text', 'display_order', 'is_featured']
    readonly_fields = ['image_preview']
    
    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-height: 100px; max-width: 200px;" />',
                obj.image.url
            )
        return '-'
    image_preview.short_description = 'Preview'


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    """Admin interface for venues"""
    list_display = [
        'name', 'partner_link', 'category', 'city', 
        'status_badge', 'rating_display', 'view_count', 'created_at'
    ]
    list_filter = [
        'status', 'category',  'city', 
        'state',  'created_at'
    ]
    search_fields = ['name', 'description', 'address', 'city',]
    readonly_fields = [
        'slug', 'view_count', 'average_rating', 'total_reviews',
        'cover_image_preview', 'approved_by', 'approved_at', 'created_at', 'updated_at'
    ]
    filter_horizontal = ['amenities',]
    #prepopulated_fields = {'slug': ('name',)}
    date_hierarchy = 'created_at'
    inlines = [VenueImageInline]
    
    fieldsets = (
        ('Basic Information', {
            'fields': (
                'partner', 'status', 'name', 'slug', 
                'category', 'tagline', 'description'
            )
        }),
        ('Contact Details', {
            'fields': ('phone', 'email', 'website')
        }),
        ('Location', {
            'fields': (
                'address', 'city', 'state', 'suburb', 
                'postal_code', 'latitude', 'longitude'
            )
        }),
        ('Pricing & Capacity', {
            'fields': (
                'capacity', 
            )
        }),
        ('Features', {
            'fields': ('amenities',),
            'classes': ('collapse',)
        }),
        ('Media', {
            'fields': ('cover_image', 'cover_image_preview')
        }),
        ('Operating Hours', {
            'fields': ('opening_time', 'closing_time', 'open_24_hours')
        }),
        ('Statistics', {
            'fields': ('view_count', 'average_rating', 'total_reviews', 'meta_description'),
            'classes': ('collapse',)
        }),
        ('Approval Details', {
            'fields': (
                'approved_by', 'approved_at', 'rejection_reason'
            ),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['approve_venues', 'reject_venues', 'suspend_venues']
    
    def partner_link(self, obj):
        """Link to partner profile"""
        url = reverse('admin:account_partnerprofile_change', args=[obj.partner.pk])
        return format_html(
            '<a href="{}">{}</a>',
            url,
            obj.partner.business_name
        )
    partner_link.short_description = 'Partner'
    
    def status_badge(self, obj):
        """Colored badge for status"""
        colors = {
            'DRAFT': 'gray',
            'PENDING': 'orange',
            'APPROVED': 'green',
            'REJECTED': 'red',
            'SUSPENDED': 'darkred',
            'CLOSED': 'gray'
        }
        return format_html(
            '<span style="padding: 3px 10px; border-radius: 3px; '
            'background: {}; color: white; font-weight: bold;">{}</span>',
            colors.get(obj.status, 'gray'),
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    
    def rating_display(self, obj):
        """Display rating with stars safely"""
        if obj.average_rating == 0:
            stars = '☆☆☆☆☆'
            rating_text = '0.0'
        else:
            full_stars = int(obj.average_rating)
            half_star = '½' if (obj.average_rating - full_stars >= 0.5) else ''
            empty_stars = '☆' * (5 - full_stars - (1 if half_star else 0))
            stars = '★' * full_stars + half_star + empty_stars
            rating_text = f"{obj.average_rating:.1f}"
        
        return format_html(
            '<span style="color: gold; font-size: 18px;">{}</span> '
            '<span style="color: #666; font-size: 14px;">({}/5)</span>',
            stars,
            rating_text  # Now a plain string
        )
    rating_display.short_description = 'Rating'
    
    def cover_image_preview(self, obj):
        """Preview of cover image"""
        if obj.cover_image:
            return format_html(
                '<img src="{}" style="max-height: 200px; max-width: 400px;" />',
                obj.cover_image.url
            )
        return '-'
    cover_image_preview.short_description = 'Cover Image Preview'
    
    def approve_venues(self, request, queryset):
        """Bulk approve venues"""
        from django.utils import timezone
        count = queryset.update(
            status='APPROVED',
            approved_by=request.user,
            approved_at=timezone.now()
        )
        self.message_user(request, f'{count} venue(s) approved successfully.')
    approve_venues.short_description = 'Approve selected venues'
    
    def reject_venues(self, request, queryset):
        """Bulk reject venues"""
        count = queryset.update(status='REJECTED')
        self.message_user(request, f'{count} venue(s) rejected.')
    reject_venues.short_description = 'Reject selected venues'
    
    def suspend_venues(self, request, queryset):
        """Bulk suspend venues"""
        count = queryset.update(status='SUSPENDED')
        self.message_user(request, f'{count} venue(s) suspended.')
    suspend_venues.short_description = 'Suspend selected venues'


@admin.register(VenueImage)
class VenueImageAdmin(admin.ModelAdmin):
    """Admin interface for venue images"""
    list_display = ['venue', 'image_preview', 'caption', 'display_order', 'is_featured', 'uploaded_at']
    list_filter = ['is_featured', 'uploaded_at']
    search_fields = ['venue__name', 'caption', 'alt_text']
    list_editable = ['display_order', 'is_featured']
    readonly_fields = ['uploaded_at', 'full_image_preview']
    
    fieldsets = (
        (None, {
            'fields': ('venue', 'image', 'full_image_preview')
        }),
        ('Details', {
            'fields': ('caption', 'alt_text', 'display_order', 'is_featured')
        }),
        ('Metadata', {
            'fields': ('uploaded_at',),
            'classes': ('collapse',)
        }),
    )
    
    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-height: 50px; max-width: 100px;" />',
                obj.image.url
            )
        return '-'
    image_preview.short_description = 'Preview'
    
    def full_image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-height: 300px; max-width: 600px;" />',
                obj.image.url
            )
        return '-'
    full_image_preview.short_description = 'Full Preview'


@admin.register(VenueReview)
class VenueReviewAdmin(admin.ModelAdmin):
    """Admin interface for venue reviews"""
    list_display = [
        'user_link', 'venue_link', 'rating_stars', 
        'is_approved', 'is_verified_visit', 'helpful_count', 'created_at'
    ]
    list_filter = [
        'is_approved', 'is_verified_visit', 'rating', 
        'created_at', 'venue__category'
    ]
    search_fields = [
        'user__email', 'venue__name', 
        'title', 'review_text'
    ]
    readonly_fields = ['created_at', 'updated_at', 'helpful_count']
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Review Details', {
            'fields': ('venue', 'user', 'rating', 'title', 'review_text')
        }),
        ('Moderation', {
            'fields': ('is_approved', 'is_verified_visit')
        }),
        ('Engagement', {
            'fields': ('helpful_count',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['approve_reviews', 'reject_reviews', 'verify_visits']
    
    def user_link(self, obj):
        url = reverse('admin:account_customuser_change', args=[obj.user.pk])
        return format_html('<a href="{}">{}</a>', url, obj.user.get_short_name())
    user_link.short_description = 'User'
    
    def venue_link(self, obj):
        url = reverse('admin:venues_venue_change', args=[obj.venue.pk])
        return format_html('<a href="{}">{}</a>', url, obj.venue.name)
    venue_link.short_description = 'Venue'
    
    def rating_stars(self, obj):
        stars = '★' * obj.rating + '☆' * (5 - obj.rating)
        return format_html(
            '<span style="color: gold; font-size: 18px;">{}</span>',
            stars
        )
    rating_stars.short_description = 'Rating'
    
    def approve_reviews(self, request, queryset):
        count = queryset.update(is_approved=True)
        # Update venue ratings
        for review in queryset:
            review.venue.update_rating()
        self.message_user(request, f'{count} review(s) approved.')
    approve_reviews.short_description = 'Approve selected reviews'
    
    def reject_reviews(self, request, queryset):
        count = queryset.update(is_approved=False)
        for review in queryset:
            review.venue.update_rating()
        self.message_user(request, f'{count} review(s) rejected.')
    reject_reviews.short_description = 'Reject selected reviews'
    
    def verify_visits(self, request, queryset):
        count = queryset.update(is_verified_visit=True)
        self.message_user(request, f'{count} review(s) marked as verified visit.')
    verify_visits.short_description = 'Mark as verified visit'


@admin.register(VenueFavorite)
class VenueFavoriteAdmin(admin.ModelAdmin):
    """Admin interface for venue favorites"""
    list_display = ['user_link', 'venue_link', 'added_at']
    list_filter = ['added_at', 'venue__category']
    search_fields = ['user__email', 'venue__name']
    readonly_fields = ['added_at']
    date_hierarchy = 'added_at'
    
    def user_link(self, obj):
        url = reverse('admin:account_customuser_change', args=[obj.user.pk])
        return format_html('<a href="{}">{}</a>', url, obj.user.email)
    user_link.short_description = 'User'
    
    def venue_link(self, obj):
        url = reverse('admin:venues_venue_change', args=[obj.venue.pk])
        return format_html('<a href="{}">{}</a>', url, obj.venue.name)
    venue_link.short_description = 'Venue'