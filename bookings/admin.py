from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.db.models import Count, Q
from django.utils import timezone
from django.contrib.admin import SimpleListFilter

from .models import Booking, BookingActivity, BookingStatus


class BookingActivityInline(admin.TabularInline):
    """Inline display of booking activity log"""
    model = BookingActivity
    extra = 0
    readonly_fields = ['action', 'performed_by', 'notes', 'created_at']
    can_delete = False
    
    def has_add_permission(self, request, obj=None):
        return False


class CategoryFilter(SimpleListFilter):
    """Custom filter for venue category"""
    title = _('venue category')
    parameter_name = 'venue__category'
    
    def lookups(self, request, model_admin):
        # Get distinct categories from venues
        from venues.models import Venue
        categories = Venue.objects.values_list('category', flat=True).distinct()
        return [(cat, cat) for cat in categories if cat]
    
    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(venue__category=self.value())
        return queryset


class CityFilter(SimpleListFilter):
    """Custom filter for venue city"""
    title = _('venue city')
    parameter_name = 'venue__city'
    
    def lookups(self, request, model_admin):
        # Get distinct cities from venues
        from venues.models import Venue
        cities = Venue.objects.values_list('city', flat=True).distinct()
        return [(city, city) for city in cities if city]
    
    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(venue__city=self.value())
        return queryset


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    """
    Comprehensive booking administration
    """
    list_display = [
        'booking_reference', 'status_badge', 'member_link', 
        'venue_link', 'visit_date', 'guests_count', 
        'days_until', 'created_at'
    ]
    list_filter = [
        'status', 'visit_date', 'created_at',
        CategoryFilter,  # Use custom filter instead
        CityFilter,      # Use custom filter instead
    ]
    search_fields = [
        'booking_reference', 'user__email', 
        'user__first_name', 'user__last_name',  # Changed from profile fields
        'venue__name', 'venue__city'
    ]
    readonly_fields = [
        'booking_id', 'booking_reference', 'status_display',
        'created_at', 'updated_at', 'checked_in_at', 
        'completed_at', 'cancelled_at', 'qr_code_display'
    ]
    date_hierarchy = 'visit_date'
    inlines = [BookingActivityInline]
    
    fieldsets = (
        ('Booking Information', {
            'fields': (
                'booking_id', 'booking_reference', 
                'status', 'status_display'
            )
        }),
        ('Member & Venue', {
            'fields': ('user', 'venue', 'subscription')
        }),
        ('Visit Details', {
            'fields': (
                'visit_date', 'guests_count', 'special_requests'
            )
        }),
        ('Check-In', {
            'fields': (
                'checked_in_at', 'checked_in_by', 'check_in_notes'
            ),
            'classes': ('collapse',)
        }),
        ('Cancellation', {
            'fields': (
                'cancelled_at', 'cancelled_by', 'cancellation_reason'
            ),
            'classes': ('collapse',)
        }),
        ('Completion', {
            'fields': ('completed_at',),
            'classes': ('collapse',)
        }),
        ('QR Code', {
            'fields': ('qr_code_display',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    actions = [
        'mark_as_checked_in', 'mark_as_completed', 
        'mark_as_no_show', 'cancel_bookings'
    ]
    
    # ==================== DISPLAY METHODS ====================
    
    def status_badge(self, obj):
        """Colored status badge"""
        colors = {
            BookingStatus.CONFIRMED: '#28a745',
            BookingStatus.CHECKED_IN: '#17a2b8',
            BookingStatus.COMPLETED: '#6c757d',
            BookingStatus.CANCELLED: '#dc3545',
            BookingStatus.NO_SHOW: '#ffc107',
        }
        color = colors.get(obj.status, '#6c757d')
        
        return format_html(
            '<span style="background: {}; color: white; padding: 5px 12px; '
            'border-radius: 12px; font-size: 11px; font-weight: 600; '
            'text-transform: uppercase; white-space: nowrap;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'
    
    def status_display(self, obj):
        """Detailed status with timestamp"""
        status_map = {
            BookingStatus.CONFIRMED: ('✓', '#28a745', 'Awaiting visit'),
            BookingStatus.CHECKED_IN: ('👤', '#17a2b8', f'Checked in at {obj.checked_in_at.strftime("%I:%M %p")}' if obj.checked_in_at else 'Checked in'),
            BookingStatus.COMPLETED: ('✓✓', '#6c757d', 'Visit completed'),
            BookingStatus.CANCELLED: ('✕', '#dc3545', f'Cancelled: {obj.cancellation_reason[:50]}' if obj.cancellation_reason else 'Cancelled'),
            BookingStatus.NO_SHOW: ('⚠', '#ffc107', 'Member did not show up'),
        }
        
        icon, color, message = status_map.get(obj.status, ('?', '#999', 'Unknown'))
        
        return format_html(
            '<div style="padding: 10px; background: #f8f9fa; border-left: 4px solid {}; border-radius: 4px;">'
            '<span style="font-size: 20px;">{}</span> '
            '<strong style="color: {};">{}</strong><br>'
            '<small style="color: #6c757d;">{}</small>'
            '</div>',
            color, icon, color, obj.get_status_display(), message
        )
    status_display.short_description = 'Status Details'
    
    def member_link(self, obj):
        """Link to member profile"""
        url = reverse('admin:account_customuser_change', args=[obj.user.pk])
        return format_html(
            '<a href="{}">{}</a><br>'
            '<small style="color: #6c757d;">{}</small>',
            url,
            obj.user.get_full_name() or obj.user.email,
            obj.user.email
        )
    member_link.short_description = 'Member'
    
    def venue_link(self, obj):
        """Link to venue"""
        url = reverse('admin:venues_venue_change', args=[obj.venue.pk])
        return format_html(
            '<a href="{}">{}</a><br>'
            '<small style="color: #6c757d;">{}, {}</small>',
            url,
            obj.venue.name,
            obj.venue.city,
            obj.venue.get_category_display() if hasattr(obj.venue, 'get_category_display') else obj.venue.category
        )
    venue_link.short_description = 'Venue'
    
    def days_until(self, obj):
        """Days until/since visit"""
        try:
            delta = obj.days_until_visit()
            
            if delta is None:
                return '-'
            
            if delta < 0:
                return format_html(
                    '<span style="color: #6c757d;">{} days ago</span>',
                    abs(delta)
                )
            elif delta == 0:
                return format_html(
                    '<span style="color: #28a745; font-weight: 600;">Today</span>'
                )
            elif delta == 1:
                return format_html(
                    '<span style="color: #ffc107; font-weight: 600;">Tomorrow</span>'
                )
            else:
                return format_html(
                    '<span style="color: #17a2b8;">In {} days</span>',
                    delta
                )
        except AttributeError:
            return '-'
    days_until.short_description = 'Visit'
    days_until.admin_order_field = 'visit_date'
    
    def qr_code_display(self, obj):
        """Display QR code data"""
        try:
            qr_data = obj.get_qr_code_data()
        except AttributeError:
            qr_data = "Not available"
        
        return format_html(
            '<div style="padding: 15px; background: #f8f9fa; border-radius: 8px; font-family: monospace;">'
            '<strong>QR Data:</strong><br>'
            '<code>{}</code><br><br>'
            '<strong>Reference:</strong> {}'
            '</div>',
            qr_data,
            obj.booking_reference
        )
    qr_code_display.short_description = 'QR Code Data'
    
    # ==================== ACTIONS ====================
    
    def mark_as_checked_in(self, request, queryset):
        """Bulk check-in bookings"""
        updated = 0
        for booking in queryset.filter(status=BookingStatus.CONFIRMED):
            try:
                booking.check_in(checked_in_by=request.user)
                updated += 1
            except Exception as e:
                self.message_user(
                    request, 
                    f'Error checking in {booking.booking_reference}: {str(e)}',
                    level='ERROR'
                )
        
        self.message_user(
            request,
            f'{updated} booking(s) marked as checked in.',
            level='SUCCESS'
        )
    mark_as_checked_in.short_description = 'Mark as Checked In'
    
    def mark_as_completed(self, request, queryset):
        """Bulk complete bookings"""
        updated = 0
        for booking in queryset.filter(
            status__in=[BookingStatus.CONFIRMED, BookingStatus.CHECKED_IN]
        ):
            try:
                booking.complete()
                updated += 1
            except Exception as e:
                self.message_user(
                    request,
                    f'Error completing {booking.booking_reference}: {str(e)}',
                    level='ERROR'
                )
        
        self.message_user(request, f'{updated} booking(s) marked as completed.')
    mark_as_completed.short_description = 'Mark as Completed'
    
    def mark_as_no_show(self, request, queryset):
        """Bulk mark as no-show"""
        updated = 0
        for booking in queryset.filter(status=BookingStatus.CONFIRMED):
            try:
                booking.mark_no_show()
                updated += 1
            except Exception as e:
                self.message_user(
                    request,
                    f'Error marking no-show {booking.booking_reference}: {str(e)}',
                    level='ERROR'
                )
        
        self.message_user(request, f'{updated} booking(s) marked as no-show.')
    mark_as_no_show.short_description = 'Mark as No Show'
    
    def cancel_bookings(self, request, queryset):
        """Bulk cancel bookings"""
        updated = 0
        for booking in queryset.filter(
            status__in=[BookingStatus.CONFIRMED, BookingStatus.CHECKED_IN]
        ):
            try:
                booking.cancel(
                    reason='Cancelled by admin',
                    cancelled_by=request.user
                )
                updated += 1
            except Exception as e:
                self.message_user(
                    request,
                    f'Error cancelling {booking.booking_reference}: {str(e)}',
                    level='ERROR'
                )
        
        self.message_user(request, f'{updated} booking(s) cancelled.')
    cancel_bookings.short_description = 'Cancel bookings'
    
    # ==================== CUSTOM VIEWS ====================
    
    def changelist_view(self, request, extra_context=None):
        """Add statistics to changelist"""
        extra_context = extra_context or {}
        
        # Get date ranges
        today = timezone.now().date()
        
        # Statistics
        try:
            stats = {
                'total': Booking.objects.count(),
                'today': Booking.objects.filter(visit_date=today).count(),
                'upcoming': Booking.objects.filter(
                    status=BookingStatus.CONFIRMED,
                    visit_date__gte=today
                ).count(),
                'pending_checkin': Booking.objects.filter(
                    status=BookingStatus.CONFIRMED,
                    visit_date=today
                ).count(),
                'completed_today': Booking.objects.filter(
                    status=BookingStatus.COMPLETED,
                    completed_at__date=today
                ).count(),
            }
        except Exception:
            stats = {
                'total': 0,
                'today': 0,
                'upcoming': 0,
                'pending_checkin': 0,
                'completed_today': 0,
            }
        
        extra_context['booking_stats'] = stats
        
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(BookingActivity)
class BookingActivityAdmin(admin.ModelAdmin):
    """
    Booking activity log administration
    """
    list_display = [
        'booking_reference', 'action', 'performed_by', 'created_at'
    ]
    list_filter = ['action', 'created_at']
    search_fields = [
        'booking__booking_reference', 
        'performed_by__email', 
        'notes'
    ]
    readonly_fields = [
        'booking', 'action', 'performed_by', 'notes', 'created_at'
    ]
    date_hierarchy = 'created_at'
    
    def booking_reference(self, obj):
        if obj.booking:
            return obj.booking.booking_reference
        return "-"
    booking_reference.short_description = 'Booking'
    booking_reference.admin_order_field = 'booking__booking_reference'
    
    def has_add_permission(self, request):
        return False
