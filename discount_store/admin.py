"""
discount_store/admin.py
"""

from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils import timezone
from django.contrib.humanize.templatetags.humanize import intcomma
from .models import StoreConfig, StoreProduct, StoreOrder


# ──────────────────────────────────────────────
# STORE CONFIG (singleton)
# ──────────────────────────────────────────────

@admin.register(StoreConfig)
class StoreConfigAdmin(admin.ModelAdmin):
    """
    Singleton config — only ever one row.
    The save() override on the model enforces pk=1.
    """
    fieldsets = (
        ('Cashback Settings', {
            'fields': ('cashback_percentage',),
            'description': 'Cashback coins = amount_paid × cashback_percentage / 100. e.g. ₦5,000 × 5% = 250 coins.',
        }),
        ('Cancellation Policy', {
            'fields': ('cancellation_cutoff_hours',),
            'description': 'Users cannot cancel within this many hours of their visit time.',
        }),
        ('Order Limits', {
            'fields': ('max_quantity_per_order',),
        }),
    )

    def has_add_permission(self, request):
        # Only one config row should ever exist
        return not StoreConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False  # Never delete the config


# ──────────────────────────────────────────────
# STORE PRODUCT
# ──────────────────────────────────────────────

@admin.register(StoreProduct)
class StoreProductAdmin(admin.ModelAdmin):
    list_display  = ['name', 'venue_link', 'price_display', 'cashback_display', 'is_active', 'display_order']
    list_filter   = ['is_active', 'venue__star_tier', 'venue__access_mode']
    search_fields = ['name', 'venue__name']
    list_editable = ['is_active', 'display_order']
    readonly_fields = ['coin_price_display', 'cashback_display']

    fieldsets = (
        ('Product Details', {
            'fields': ('name', 'venue', 'description', 'image', 'is_active', 'display_order'),
        }),
        ('Pricing', {
            'fields': ('price', 'coin_price_display', 'cashback_display'),
            'description': '1 naira = 1 coin. Cashback calculated from Store Configuration settings.',
        }),
    )

    def venue_link(self, obj):
        url = reverse('admin:venues_venue_change', args=[obj.venue.pk])
        return format_html('<a href="{}">{}</a>', url, obj.venue.name)
    venue_link.short_description = 'Venue'

    def price_display(self, obj):
        return format_html('<strong>₦{}</strong>', intcomma(obj.price))
    price_display.short_description = 'Price'

    def cashback_display(self, obj):
        coins = obj.cashback_for_quantity(1)
        return format_html('<span style="color:#E5AD04;font-weight:600">💰 {} coins</span>', coins)
    cashback_display.short_description = 'Cashback (per unit)'

    def coin_price_display(self, obj):
        return format_html('{} coins', intcomma(obj.coin_price))
    coin_price_display.short_description = 'Coin Price'


# ──────────────────────────────────────────────
# STORE ORDER
# ──────────────────────────────────────────────

@admin.register(StoreOrder)
class StoreOrderAdmin(admin.ModelAdmin):
    list_display = [
        'reference', 'user_email', 'product_name', 'quantity',
        'amount_display', 'payment_badge', 'status_badge',
        'visit_date', 'cashback_awarded', 'created_at',
    ]
    list_filter  = ['status', 'payment_method', 'cashback_awarded', 'visit_date']
    search_fields = ['reference', 'user__email', 'product__name', 'paystack_reference']
    readonly_fields = [
        'order_id', 'reference', 'user', 'product', 'quantity', 'amount_paid',
        'payment_method', 'paystack_reference', 'status',
        'cashback_coins', 'cashback_awarded', 'booking',
        'visit_date', 'visit_time', 'special_notes',
        'cancelled_by', 'cancellation_reason', 'cancelled_at',
        'created_at', 'updated_at',
    ]
    actions = ['mark_as_used', 'mark_as_refunded']

    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = 'User'

    def product_name(self, obj):
        return obj.product.name
    product_name.short_description = 'Product'

    def amount_display(self, obj):
        return format_html('₦{}', intcomma(obj.amount_paid))
    amount_display.short_description = 'Amount'

    def payment_badge(self, obj):
        if obj.payment_method == 'CARD':
            return format_html(
                '<span style="color:#0d6efd;background:#e7f3ff;padding:2px 8px;border-radius:4px;font-size:12px">💳 Card</span>'
            )
        return format_html(
            '<span style="color:#E5AD04;background:#fff8e1;padding:2px 8px;border-radius:4px;font-size:12px">🪙 Coins</span>'
        )
    payment_badge.short_description = 'Payment'

    def status_badge(self, obj):
        colours = {
            'PENDING':   ('#856404', '#fff3cd'),
            'PAID':      ('#0d6efd', '#e7f3ff'),
            'USED':      ('#198754', '#d1e7dd'),
            'CANCELLED': ('#dc3545', '#f8d7da'),
            'REFUNDED':  ('#6c757d', '#f8f9fa'),
        }
        fg, bg = colours.get(obj.status, ('#000', '#fff'))
        return format_html(
            '<span style="color:{};background:{};padding:2px 8px;border-radius:4px;font-size:12px">{}</span>',
            fg, bg, obj.status
        )
    status_badge.short_description = 'Status'

    @admin.action(description='Mark selected orders as USED')
    def mark_as_used(self, request, queryset):
        updated = queryset.filter(status='PAID').update(status='USED')
        self.message_user(request, f'{updated} order(s) marked as USED.')

    @admin.action(description='Mark selected orders as REFUNDED (admin-initiated)')
    def mark_as_refunded(self, request, queryset):
        updated = queryset.filter(status__in=['PAID', 'CANCELLED']).update(
            status='REFUNDED', cancelled_by='ADMIN', cancelled_at=timezone.now()
        )
        self.message_user(request, f'{updated} order(s) marked as REFUNDED.')