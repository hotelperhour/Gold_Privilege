"""
services/admin.py
Dynamic inline fields: quota field shown depends on service category/delivery_type,
updated via JavaScript so it switches immediately on change (no save-reload needed).
"""

from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Service, ServicePlanQuota, VoucherInventory,
    ServicePurchase, ServiceQuotaUsage, ServiceCategory, DeliveryType
)


# ──────────────────────────────────────────────
# INLINE with JS dynamic field toggling
# ──────────────────────────────────────────────

class ServicePlanQuotaInline(admin.TabularInline):
    model   = ServicePlanQuota
    extra   = 1
    fields  = ('plan', 'monthly_allowance', 'monthly_data_gb', 'monthly_voucher_count')
    verbose_name        = "Plan Quota"
    verbose_name_plural = "Plan Quotas (set the correct field for this service type)"


# ──────────────────────────────────────────────
# SERVICE ADMIN
# ──────────────────────────────────────────────

@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display  = (
        'name', 'category_badge', 'delivery_badge',
        'limits_display', 'voucher_stock_count', 'is_active', 'display_order'
    )
    list_editable = ('is_active', 'display_order')
    list_filter   = ('category', 'delivery_type', 'is_active')
    search_fields = ('name',)
    inlines       = [ServicePlanQuotaInline]

    fieldsets = (
        ('Basic Info', {
            'fields': ('name', 'category', 'delivery_type', 'description', 'icon',
                       'is_active', 'display_order'),
        }),
        ('Airtime Limits', {
            'fields': ('min_transaction_amount', 'max_transaction_amount'),
            'description': (
                '💰 <strong>AIRTIME only.</strong> Min/max naira per single top-up. '
                'Monthly budget per plan is set in Plan Quotas below.'
            ),
            'classes': ('section-airtime',),
        }),
        ('Data Limits', {
            'fields': ('min_data_gb', 'max_data_gb'),
            'description': (
                '📶 <strong>DATA only.</strong> Min/max GB per single bundle. '
                'Monthly GB allowance per plan is set in Plan Quotas below.'
            ),
            'classes': ('section-data',),
        }),
        ('Voucher Settings', {
            'fields': ('fixed_amounts', 'has_inventory'),
            'description': (
                '🎟️ <strong>VOUCHER only.</strong> '
                'Enter fixed denominations as a JSON list e.g. [5000, 10000, 20000].'
            ),
            'classes': ('section-voucher',),
        }),
    )

    class Media:
        js = ('admin/js/service_admin.js',)
        css = {'all': ('admin/css/service_admin.css',)}

    def category_badge(self, obj):
        colours = {
            'AIRTIME':      ('#0d6efd', '#e7f3ff'),
            'DATA':         ('#198754', '#d1e7dd'),
            'RIDE_VOUCHER': ('#fd7e14', '#fff3cd'),
            'FUEL_VOUCHER': ('#dc3545', '#f8d7da'),
            'OTHER':        ('#6c757d', '#f8f9fa'),
        }
        fg, bg = colours.get(obj.category, ('#000', '#fff'))
        return format_html(
            '<span style="color:{};background:{};padding:3px 10px;border-radius:4px;'
            'font-size:12px;font-weight:600">{}</span>',
            fg, bg, obj.get_category_display()
        )
    category_badge.short_description = 'Category'

    def delivery_badge(self, obj):
        if obj.delivery_type == DeliveryType.API_INSTANT:
            return format_html(
                '<span style="color:#0d6efd;background:#e7f3ff;padding:3px 10px;'
                'border-radius:4px;font-size:12px">⚡ Reloadly</span>'
            )
        return format_html(
            '<span style="color:#856404;background:#fff3cd;padding:3px 10px;'
            'border-radius:4px;font-size:12px">📦 Voucher</span>'
        )
    delivery_badge.short_description = 'Delivery'

    def limits_display(self, obj):
        cat = obj.category
        if cat == ServiceCategory.AIRTIME:
            mn = f"₦{obj.min_transaction_amount:,.0f}" if obj.min_transaction_amount else "—"
            mx = f"₦{obj.max_transaction_amount:,.0f}" if obj.max_transaction_amount else "plan cap"
            return format_html('<small>min {} / max {}</small>', mn, mx)
        if cat == ServiceCategory.DATA:
            mn = f"{obj.min_data_gb} GB" if obj.min_data_gb else "—"
            mx = f"{obj.max_data_gb} GB" if obj.max_data_gb else "plan cap"
            return format_html('<small>min {} / max {}</small>', mn, mx)
        if obj.fixed_amounts:
            vals = ', '.join([f'₦{a:,}' for a in obj.fixed_amounts])
            return format_html('<small style="color:#6c757d">{}</small>', vals)
        return '—'
    limits_display.short_description = 'Per-Transaction Limits'

    def voucher_stock_count(self, obj):
        if not obj.has_inventory:
            return '—'
        count  = VoucherInventory.objects.filter(service=obj, status='AVAILABLE').count()
        colour = '#dc3545' if count == 0 else ('#fd7e14' if count < 5 else '#198754')
        return format_html(
            '<span style="color:{};font-weight:bold">{} in stock</span>', colour, count
        )
    voucher_stock_count.short_description = 'Stock'

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # Pass category to template via a data attribute on the form
        return form

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        extra_context = extra_context or {}
        if object_id:
            try:
                obj = Service.objects.get(pk=object_id)
                extra_context['current_category']      = obj.category
                extra_context['current_delivery_type'] = obj.delivery_type
            except Service.DoesNotExist:
                pass
        return super().changeform_view(request, object_id, form_url, extra_context)


# ──────────────────────────────────────────────
# PLAN QUOTA STANDALONE ADMIN
# ──────────────────────────────────────────────

@admin.register(ServicePlanQuota)
class ServicePlanQuotaAdmin(admin.ModelAdmin):
    list_display  = ('plan', 'service', 'category_col', 'quota_display')
    list_filter   = ('plan', 'service__category')
    search_fields = ('plan__name', 'service__name')

    def category_col(self, obj):
        return obj.service.get_category_display()
    category_col.short_description = 'Service Type'

    def quota_display(self, obj):
        cat = obj.service.category
        if cat == ServiceCategory.DATA:
            if obj.monthly_data_gb is None:
                return format_html('<span style="color:#198754;font-weight:bold">∞ Unlimited GB</span>')
            return format_html('<strong>{} GB</strong>/month', obj.monthly_data_gb)
        if obj.service.delivery_type == DeliveryType.MANUAL_CODE:
            if obj.monthly_voucher_count is None:
                return format_html('<span style="color:#198754;font-weight:bold">∞ Unlimited vouchers</span>')
            return format_html('<strong>{}</strong> vouchers/month', obj.monthly_voucher_count)
        # Airtime
        if obj.monthly_allowance is None:
            return format_html('<span style="color:#198754;font-weight:bold">∞ Unlimited ₦</span>')
        return format_html('<strong>₦{}</strong>/month', f'{obj.monthly_allowance:,.0f}')
    quota_display.short_description = 'Monthly Quota'


# ──────────────────────────────────────────────
# VOUCHER INVENTORY
# ──────────────────────────────────────────────

@admin.register(VoucherInventory)
class VoucherInventoryAdmin(admin.ModelAdmin):
    list_display  = (
        'service', 'masked_code', 'amount_display',
        'status_badge', 'assigned_to', 'expires_at', 'created_at'
    )
    list_filter   = ('service', 'status')
    search_fields = ('voucher_code', 'assigned_to__email')
    readonly_fields = ('assigned_to', 'assigned_at', 'created_at')
    actions       = ['mark_available', 'mark_expired', 'export_csv']

    def masked_code(self, obj):
        c = obj.voucher_code
        return (c[:4] + '••••' + c[-4:]) if len(c) > 8 else '••••'
    masked_code.short_description = 'Code'

    def amount_display(self, obj):
        return f'₦{obj.amount:,.0f}'
    amount_display.short_description = 'Amount'

    def status_badge(self, obj):
        colours = {
            'AVAILABLE': ('#198754', '#d1e7dd'),
            'ASSIGNED':  ('#fd7e14', '#fff3cd'),
            'USED':      ('#6c757d', '#f8f9fa'),
            'EXPIRED':   ('#dc3545', '#f8d7da'),
        }
        fg, bg = colours.get(obj.status, ('#000', '#fff'))
        return format_html(
            '<span style="color:{};background:{};padding:2px 8px;border-radius:4px;font-size:12px">{}</span>',
            fg, bg, obj.status
        )
    status_badge.short_description = 'Status'

    def mark_available(self, request, qs):
        qs.update(status='AVAILABLE')
    mark_available.short_description = 'Mark selected as Available'

    def mark_expired(self, request, qs):
        qs.update(status='EXPIRED')
    mark_expired.short_description = 'Mark selected as Expired'

    def export_csv(self, request, qs):
        import csv
        from django.http import HttpResponse
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="vouchers.csv"'
        w = csv.writer(response)
        w.writerow(['Service', 'Code', 'PIN', 'Amount', 'Status', 'Assigned To', 'Expires'])
        for v in qs:
            w.writerow([
                v.service.name, v.voucher_code, v.voucher_pin,
                v.amount, v.status,
                v.assigned_to.email if v.assigned_to else '',
                v.expires_at or '',
            ])
        return response
    export_csv.short_description = 'Export to CSV'


# ──────────────────────────────────────────────
# PURCHASE LOG (read-only)
# ──────────────────────────────────────────────

@admin.register(ServicePurchase)
class ServicePurchaseAdmin(admin.ModelAdmin):
    list_display  = (
        'reference', 'user', 'service', 'amount_display',
        'data_gb', 'recipient_phone', 'network_provider',
        'status_badge', 'created_at'
    )
    list_filter   = ('status', 'service__category', 'network_provider')
    search_fields = ('reference', 'user__email', 'recipient_phone')
    readonly_fields = (
        'purchase_id', 'reference', 'user', 'service', 'subscription',
        'amount', 'data_gb', 'variation_code',
        'recipient_phone', 'network_provider', 'voucher',
        'status', 'api_response', 'api_transaction_id',
        'used_quota', 'delivered_at', 'created_at', 'updated_at',
    )

    def amount_display(self, obj):
        return f'₦{obj.amount:,.0f}'
    amount_display.short_description = 'Amount (₦)'

    def status_badge(self, obj):
        colours = {
            'PENDING':    ('#856404', '#fff3cd'),
            'PROCESSING': ('#0d6efd', '#e7f3ff'),
            'DELIVERED':  ('#198754', '#d1e7dd'),
            'FAILED':     ('#dc3545', '#f8d7da'),
        }
        fg, bg = colours.get(obj.status, ('#000', '#fff'))
        return format_html(
            '<span style="color:{};background:{};padding:2px 8px;border-radius:4px;font-size:12px">{}</span>',
            fg, bg, obj.status
        )
    status_badge.short_description = 'Status'


# ──────────────────────────────────────────────
# QUOTA USAGE (monitoring)
# ──────────────────────────────────────────────

@admin.register(ServiceQuotaUsage)
class ServiceQuotaUsageAdmin(admin.ModelAdmin):
    list_display  = (
        'user', 'service', 'period_display',
        'usage_display', 'last_used_at'
    )
    list_filter   = ('service', 'period_year', 'period_month')
    search_fields = ('user__email', 'service__name')
    readonly_fields = (
        'user', 'service', 'subscription',
        'period_year', 'period_month',
        'amount_used', 'data_gb_used', 'count_used',
        'last_used_at', 'created_at',
    )

    def period_display(self, obj):
        return f'{obj.period_year}-{obj.period_month:02d}'
    period_display.short_description = 'Period'

    def usage_display(self, obj):
        cat = obj.service.category
        if cat == ServiceCategory.DATA:
            return f'{obj.data_gb_used} GB used'
        if obj.service.delivery_type == DeliveryType.MANUAL_CODE:
            return f'{obj.count_used} vouchers used'
        return f'₦{obj.amount_used:,.0f} used'
    usage_display.short_description = 'Usage This Month'