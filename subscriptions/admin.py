from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.db.models import Count, Sum
from .models import (
    PlanFeature, SubscriptionPlan, PlanFeatureAssignment,
    PromoCode, Subscription, Payment, FeatureUsage
)
from decimal import Decimal


@admin.register(PlanFeature)
class PlanFeatureAdmin(admin.ModelAdmin):
    """Admin for plan features"""
    
    list_display = (
        'name', 'icon_preview', 'is_active',
        'display_order', 'plans_count', 'created_at'
    )
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'description')
    list_editable = ('is_active', 'display_order')
    ordering = ('display_order', 'name')
    autocomplete_fields = []
    
    fieldsets = (
        (_('Feature Details'), {
            'fields': ('name', 'description', 'icon')
        }),
        (_('Display Settings'), {
            'fields': ('is_active', 'display_order')
        }),
    )
    
    def icon_preview(self, obj):
        """Show icon preview"""
        if obj.icon:
            return format_html(
                '<i class="fas {} fa-2x" style="color: #d4af37;"></i>',
                obj.icon
            )
        return '-'
    icon_preview.short_description = _('Icon')
    
    def plans_count(self, obj):
        """Count how many plans use this feature"""
        count = obj.plans.count()
        return format_html(
            '<span style="background: #28a745; color: white; padding: 3px 10px; '
            'border-radius: 3px;">{} plans</span>',
            count
        )
    plans_count.short_description = _('Used in Plans')


class PlanFeatureInline(admin.TabularInline):
    """Inline for managing plan features"""
    model = PlanFeatureAssignment
    extra = 1
    fields = ('feature', 'usage_limit', 'is_highlighted', 'display_order')
    autocomplete_fields = ['feature']

    verbose_name = "plan Feature"
    verbose_name_plural = "Plan Features (with usage limits)"


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    """Admin for subscription plans"""
    
    list_display = (
        'name', 'price_display', 'billing_period_display',
        'status_badge', 'subscribers_count', 'display_order', 'venue_tier_access'
    )
    list_filter = (
        'billing_period', 'is_active', 'is_featured', 'created_at'
    )
    search_fields = ('name', 'description', 'slug')
    list_editable = ('display_order',)
    prepopulated_fields = {'slug': ('name',)}
    ordering = ('display_order', 'price')
    
    inlines = [PlanFeatureInline]
    
    fieldsets = (
        (_('Basic Information'), {
            'fields': ('name', 'slug', 'description', 'tagline')
        }),

        (_('Access Level'), {
            'fields': ('venue_tier_access',),
            'description': 'Which venue star tiers this plan unlocks. Level 2 also includes Level 1.'
        }),
        
        (_('Pricing'), {
            'fields': ('price', 'billing_period', 'trial_period_days')
        }),
        (_('Limits & Quotas'), {
            'fields': ('max_guests_per_booking',),
            'description': 'Set booking limits for this plan'
        }),
        (_('Display Settings'), {
            'fields': (
                'is_featured', 'is_coming_soon','highlight_color', 'display_order'
            )
        }),
        (_('Availability'), {
            'fields': ('is_active', 'available_from', 'available_until'),
            'classes': ('collapse',)
        }),
    )
    
    def price_display(self, obj):
        if obj.price is None:
            return "-"
        # Format the price as plain string first
        formatted_price = f"₦{obj.price:,.2f}"
        # Then wrap in HTML
        return format_html(
            '<strong style="color: #d4af37; font-size: 16px;">{}</strong>',
            formatted_price
        )
    price_display.short_description = 'Price'
    price_display.admin_order_field = 'price'  # Allows sorting
    
    def billing_period_display(self, obj):
        """Formatted billing period"""
        colors = {
            'MONTHLY': '#007bff',
            'QUARTERLY': '#28a745',
            'SEMI_ANNUAL': '#ffc107',
            'ANNUAL': '#dc3545',
        }
        color = colors.get(obj.billing_period, '#6c757d')
        return format_html(
            '<span style="background: {}; color: white; padding: 5px 10px; '
            'border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_billing_period_display()
        )
    billing_period_display.short_description = _('Billing')
    
    def status_badge(self, obj):
        """Show availability status"""
        if obj.is_available():
            if obj.is_featured:
                return format_html(
                    '<span style="background: #d4af37; color: white; padding: 5px 10px; '
                    'border-radius: 3px;">⭐ FEATURED</span>'
                )
            return format_html(
                '<span style="background: #28a745; color: white; padding: 5px 10px; '
                'border-radius: 3px;">✓ AVAILABLE</span>'
            )
        return format_html(
            '<span style="background: #6c757d; color: white; padding: 5px 10px; '
            'border-radius: 3px;">✗ UNAVAILABLE</span>'
        )
    status_badge.short_description = _('Status')
    
    def subscribers_count(self, obj):
        """Count active subscribers"""
        count = obj.subscriptions.filter(status__in=['ACTIVE', 'TRIAL']).count()
        return format_html(
            '<span style="font-weight: bold; color: #007bff;">{} subscribers</span>',
            count
        )
    subscribers_count.short_description = _('Active Subscribers')


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    """Admin for promo codes"""
    
    list_display = (
        'code', 'discount_display', 'status_badge',
        'usage_info', 'validity_period', 'created_at'
    )
    list_filter = (
        'discount_type', 'is_active', 'created_at'
    )
    search_fields = ('code', 'description')
    filter_horizontal = ('applicable_plans',)
    
    fieldsets = (
        (_('Code Details'), {
            'fields': ('code', 'description')
        }),
        (_('Discount Settings'), {
            'fields': ('discount_type', 'discount_value')
        }),
        (_('Applicable Plans'), {
            'fields': ('applicable_plans',),
            'description': 'Leave empty to apply to all plans'
        }),
        (_('Usage Limits'), {
            'fields': ('max_uses', 'uses_count', 'max_uses_per_user')
        }),
        (_('Validity Period'), {
            'fields': ('valid_from', 'valid_until', 'is_active')
        }),
    )
    
    readonly_fields = ('uses_count',)
    
    def discount_display(self, obj):
        """Formatted discount display"""
        value = Decimal(obj.discount_value)

        if obj.discount_type == 'PERCENTAGE':
            formatted = f"{value:.0f}% OFF"
        else:
            formatted = f"₦{value:,.2f} OFF"

        return format_html(
            '<strong style="color: #28a745; font-size: 16px;">{}</strong>',
            formatted
        )
    discount_display.short_description = _('Discount')
    
    def status_badge(self, obj):
        """Show validity status"""
        if obj.is_valid():
            return format_html(
                '<span style="background: #28a745; color: white; padding: 5px 10px; '
                'border-radius: 3px;">✓ VALID</span>'
            )
        return format_html(
            '<span style="background: #dc3545; color: white; padding: 5px 10px; '
            'border-radius: 3px;">✗ INVALID</span>'
        )
    status_badge.short_description = _('Status')
    
    def usage_info(self, obj):
        """Show usage statistics"""
        if obj.max_uses:
            percentage = (obj.uses_count / obj.max_uses) * 100
            color = '#28a745' if percentage < 80 else '#ffc107' if percentage < 100 else '#dc3545'
            return format_html(
                '<span style="color: {};">{} / {} uses</span>',
                color,
                obj.uses_count,
                obj.max_uses
            )
        return format_html('{} uses', obj.uses_count)
    usage_info.short_description = _('Usage')
    
    def validity_period(self, obj):
        """Show validity period"""
        if obj.valid_until:
            return f"{obj.valid_from.strftime('%Y-%m-%d')} to {obj.valid_until.strftime('%Y-%m-%d')}"
        return f"From {obj.valid_from.strftime('%Y-%m-%d')}"
    validity_period.short_description = _('Valid Period')


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    """Admin for subscriptions"""
    
    list_display = (
        'subscription_reference', 'user_email', 'plan',
        'status_badge', 'period_info', 'auto_renew_badge',
        'created_at'
    )
    list_filter = (
        'status', 'is_trial', 'auto_renew',
        'plan', 'created_at'
    )
    search_fields = (
        'subscription_id', 'user__email',
        'user__profile__first_name', 'user__profile__last_name'
    )
    readonly_fields = (
        'subscription_id', 'subscription_reference',
        'created_at', 'updated_at',
        'cancelled_at', 'bookings_count'
    )
    
    fieldsets = (
        (_('Subscription Details'), {
            'fields': ('subscription_id', 'subscription_reference', 'user', 'plan',)
        }),
        (_('Period'), {
            'fields': (
                'start_date', 'end_date',
                'is_trial', 'trial_end_date'
            )
        }),
        (_('Pricing'), {
            'fields': (
                'price_paid', 'promo_code_used', 'discount_amount'
            )
        }),
        (_('Settings'), {
            'fields': ('auto_renew', 'status')
        }),
        (_('Usage'), {
            'fields': ('bookings_count',)
        }),
        (_('Cancellation'), {
            'fields': ('cancelled_at', 'cancellation_reason'),
            'classes': ('collapse',)
        }),
        (_('Timestamps'), {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['activate_subscriptions', 'cancel_subscriptions']
    
    def user_email(self, obj):
        """Show user email"""
        return obj.user.email
    user_email.short_description = _('User')
    user_email.admin_order_field = 'user__email'
    
    def status_badge(self, obj):
        """Show status badge"""
        colors = {
            'ACTIVE': '#28a745',
            'TRIAL': '#007bff',
            'EXPIRED': '#6c757d',
            'CANCELLED': '#dc3545',
            'PENDING': '#ffc107',
        }
        color = colors.get(obj.status, '#6c757d')
        return format_html(
            '<span style="background: {}; color: white; padding: 5px 10px; '
            'border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = _('Status')
    
    def period_info(self, obj):
        """Show subscription period"""
        if obj.is_active():
            days = obj.days_remaining()
            return format_html(
                '<span style="color: #28a745;">{} days left</span>',
                days
            )
        return f"{obj.start_date.strftime('%Y-%m-%d')} to {obj.end_date.strftime('%Y-%m-%d')}"
    period_info.short_description = _('Period')
    
    def auto_renew_badge(self, obj):
        """Show auto-renew status"""
        if obj.auto_renew:
            return format_html(
                '<span style="color: #28a745;">✓ Auto-Renew</span>'
            )
        return format_html(
            '<span style="color: #6c757d;">✗ Manual</span>'
        )
    auto_renew_badge.short_description = _('Renewal')
    
    def activate_subscriptions(self, request, queryset):
        """Bulk activate subscriptions"""
        updated = queryset.update(status='ACTIVE')
        self.message_user(
            request,
            f'{updated} subscription(s) have been activated.'
        )
    activate_subscriptions.short_description = _('✓ Activate selected subscriptions')
    
    def cancel_subscriptions(self, request, queryset):
        """Bulk cancel subscriptions"""
        count = 0
        for subscription in queryset:
            subscription.cancel(reason='Cancelled by admin')
            count += 1
        self.message_user(
            request,
            f'{count} subscription(s) have been cancelled.'
        )
    cancel_subscriptions.short_description = _('✗ Cancel selected subscriptions')


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Admin for payments"""
    
    list_display = (
        'payment_reference', 'user_email', 'amount_display',
        'payment_method', 'status_badge', 'created_at'
    )
    list_filter = (
        'status', 'payment_method', 'created_at'
    )
    search_fields = (
        'payment_id', 'gateway_reference',
        'user__email', 'subscription__subscription_id'
    )
    readonly_fields = (
        'payment_id', 'payment_reference',
        'created_at', 'updated_at',
        'paid_at', 'refunded_at', 'gateway_response'
    )
    
    fieldsets = (
        (_('Payment Details'), {
            'fields': ('payment_id', 'payment_reference', 'subscription', 'user', 'amount')
        }),
        (_('Payment Method'), {
            'fields': ('payment_method', 'status')
        }),
        (_('Gateway Information'), {
            'fields': ('gateway_reference', 'gateway_response'),
            'classes': ('collapse',)
        }),
        (_('Timestamps'), {
            'fields': ('created_at', 'updated_at', 'paid_at', 'refunded_at'),
            'classes': ('collapse',)
        }),
    )
    
    def user_email(self, obj):
        """Show user email"""
        return obj.user.email
    user_email.short_description = _('User')
    user_email.admin_order_field = 'user__email'
    
    def amount_display(self, obj):
        amount = Decimal(obj.amount)
        formatted = f"₦{amount:,.2f}"

        return format_html(
            '<strong style="color: #d4af37; font-size: 16px;">{}</strong>',
            formatted
        )
    amount_display.short_description = _('Amount')
    amount_display.admin_order_field = 'amount'
    
    def status_badge(self, obj):
        """Show status badge"""
        colors = {
            'PENDING': '#ffc107',
            'SUCCESS': '#28a745',
            'FAILED': '#dc3545',
            'REFUNDED': '#6c757d',
        }
        color = colors.get(obj.status, '#6c757d')
        return format_html(
            '<span style="background: {}; color: white; padding: 5px 10px; '
            'border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = _('Status')


@admin.register(FeatureUsage)
class FeatureUsageAdmin(admin.ModelAdmin):
    """Admin for feature usage tracking"""
    
    list_display = (
        'user_email', 'plan_name', 'feature_name',
        'usage_display', 'period_display', 'last_used_at'
    )
    list_filter = ('period_year', 'period_month', 'feature')
    search_fields = (
        'subscription__user__email',
        'feature__name'
    )
    readonly_fields = (
        'subscription', 'feature', 'period_year', 'period_month',
        'created_at', 'updated_at'
    )
    
    fieldsets = (
        (_('Subscription & Feature'), {
            'fields': ('subscription', 'feature')
        }),
        (_('Usage'), {
            'fields': ('used_count', 'last_used_at')
        }),
        (_('Period'), {
            'fields': ('period_year', 'period_month')
        }),
        (_('Timestamps'), {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def user_email(self, obj):
        return obj.subscription.user.email
    user_email.short_description = _('User')
    user_email.admin_order_field = 'subscription__user__email'
    
    def plan_name(self, obj):
        return obj.subscription.plan.name
    plan_name.short_description = _('Plan')
    
    def feature_name(self, obj):
        return obj.feature.name
    feature_name.short_description = _('Feature')
    
    def usage_display(self, obj):
        limit = obj.get_limit()
        used = obj.used_count
        percentage = int((used / limit) * 100) if limit > 0 else 0
        
        color = '#28a745' if percentage < 70 else '#ffc107' if percentage < 90 else '#dc3545'
        
        return format_html(
            '<span style="color: {}; font-weight: bold;">{} / {}</span>',
            color,
            used,
            limit
        )
    usage_display.short_description = _('Usage')
    
    def period_display(self, obj):
        from datetime import date
        month_name = date(obj.period_year, obj.period_month, 1).strftime('%B %Y')
        return month_name
    period_display.short_description = _('Period')


# Customize admin site header
admin.site.site_header = "Gold Privilege Administration"
admin.site.site_title = "Gold Privilege Admin"
admin.site.index_title = "Welcome to Gold Privilege Administration"