"""
services/admin.py
Dynamic inline fields: quota field shown depends on service category/delivery_type,
updated via JavaScript so it switches immediately on change (no save-reload needed).
"""

from django.contrib import admin, messages
from django.utils.html import format_html
from .models import (
    Service, ServicePlanQuota, VoucherInventory,
    ServicePurchase, ServiceQuotaUsage, ServiceCategory, DeliveryType
)
import csv
import io
from django import forms
from django.shortcuts import render, redirect
from django.urls import path
from decimal import Decimal



# ──────────────────────────────────────────────
# INLINE with JS dynamic field toggling
# ──────────────────────────────────────────────

class ServicePlanQuotaInline(admin.TabularInline):
    model   = ServicePlanQuota
    extra   = 1
    fields  = (
        'plan',
        'monthly_allowance',        # AIRTIME
        'monthly_data_gb',          # DATA
        'monthly_voucher_count',    # VOUCHER: how many per month
        'voucher_type',             # VOUCHER: FIXED or PERCENT
        'voucher_fixed_amount',     # VOUCHER + FIXED: naira value
        'voucher_discount_percentage',  # VOUCHER + PERCENT: %
    )
    verbose_name = "Plan Quota"
    verbose_name_plural = "Plan Quotas (set correct fields for this service type)"
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
            'HOTEL_VOUCHER': ('#6f42c1', '#f3e7ff'),
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
    list_display  = ('plan', 'service', 'category_col', 'quota_display', 'voucher_entitlement')
    list_filter   = ('plan', 'service__category')
    search_fields = ('plan__name', 'service__name')
 
    def category_col(self, obj):
        return obj.service.get_category_display()
    category_col.short_description = 'Service Type'
 
    def quota_display(self, obj):
        from django.utils.html import format_html
        from .models import ServiceCategory, DeliveryType
        cat = obj.service.category
        if cat == ServiceCategory.DATA:
            if obj.monthly_data_gb is None:
                return format_html('<span style="color:#198754;font-weight:bold">∞ Unlimited GB</span>')
            return format_html('<strong>{} GB</strong>/month', obj.monthly_data_gb)
        if obj.service.delivery_type == DeliveryType.MANUAL_CODE:
            if obj.monthly_voucher_count is None:
                return format_html('<span style="color:#198754;font-weight:bold">∞ Unlimited vouchers</span>')
            return format_html('<strong>{}</strong> vouchers/month', obj.monthly_voucher_count)
        if obj.monthly_allowance is None:
            return format_html('<span style="color:#198754;font-weight:bold">∞ Unlimited ₦</span>')
        return format_html('<strong>₦{}</strong>/month', f'{obj.monthly_allowance:,.0f}')
    quota_display.short_description = 'Monthly Quota'
 
    def voucher_entitlement(self, obj):
        """Shows what specific voucher value this plan provides."""
        from django.utils.html import format_html
        from .models import DeliveryType, VoucherType
        if obj.service.delivery_type != DeliveryType.MANUAL_CODE:
            return '—'
        if not obj.voucher_type:
            return format_html('<span style="color:#dc3545;font-size:12px;">⚠ Not configured</span>')
        if obj.voucher_type == VoucherType.FIXED_AMOUNT:
            val = f'₦{obj.voucher_fixed_amount:,.0f}' if obj.voucher_fixed_amount else '—'
        else:
            val = f'{obj.voucher_discount_percentage}% off' if obj.voucher_discount_percentage else '—'
        return format_html('<span style="color:#198754;font-weight:bold">{}</span>', val)
    voucher_entitlement.short_description = 'Voucher Value'


# ──────────────────────────────────────────────
# VOUCHER INVENTORY
# ──────────────────────────────────────────────

class VoucherCSVUploadForm(forms.Form):
    """
    Form for uploading vouchers in bulk via CSV.
 
    Expected CSV columns (header row required):
        code, service_id, amount, voucher_type, discount_percentage, expires_at
 
    Example rows:
        HPH-ABC123,3,5000,FIXED,,2026-12-31
        HPH-DEF456,3,0,PERCENT,20.00,2026-12-31
 
    - voucher_type: FIXED or PERCENT
    - discount_percentage: leave blank for FIXED vouchers
    - expires_at: YYYY-MM-DD format, or blank for no expiry
    """
    csv_file = forms.FileField(
        label='CSV File',
        help_text=(
            'Required columns: code, service_id, amount, voucher_type, '
            'discount_percentage, expires_at. '
            'See column notes above. Max 1000 rows per upload.'
        ),
    )
    default_service = forms.ModelChoiceField(
        queryset=None,  # set in __init__
        required=False,
        label='Default Service (optional)',
        help_text='Used when the CSV row has no service_id column.',
    )
 
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Import here to avoid circular import
        from services.models import Service
        self.fields['default_service'].queryset = Service.objects.filter(is_active=True)
 
 
# ── REPLACE your existing VoucherInventoryAdmin with this ─────────────────
 
@admin.register(VoucherInventory)
class VoucherInventoryAdmin(admin.ModelAdmin):
    list_display = [
        'voucher_code', 'service', 'voucher_type', 'display_value_col',
        'status', 'assigned_to', 'expires_at', 'created_at',
    ]
    list_filter  = ['service', 'status', 'voucher_type']
    search_fields = ['voucher_code', 'assigned_to__email']
    readonly_fields = ['assigned_to', 'assigned_at', 'created_at']
 
    # Register the extra URL for bulk upload
    def get_urls(self):
        urls = super().get_urls()
        extra = [
            path(
                'bulk-upload/',
                self.admin_site.admin_view(self.bulk_upload_view),
                name='services_voucherinventory_bulk_upload',
            ),
        ]
        return extra + urls
 
    # Add a button to the changelist page header
    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['bulk_upload_url'] = 'bulk-upload/'
        return super().changelist_view(request, extra_context=extra_context)
 
    @admin.display(description='Value')
    def display_value_col(self, obj):
        """Shows ₦5,000 for fixed or 20% for percentage."""
        return obj.display_value
 
    # ── Bulk upload view ──────────────────────────────────────────────────
 
    def bulk_upload_view(self, request):
        from services.models import Service, VoucherInventory
 
        if request.method == 'POST':
            form = VoucherCSVUploadForm(request.POST, request.FILES)
            if form.is_valid():
                csv_file = form.cleaned_data['csv_file']
                default_service = form.cleaned_data.get('default_service')
 
                # Decode and parse
                try:
                    decoded = csv_file.read().decode('utf-8-sig')  # handles BOM
                    reader  = csv.DictReader(io.StringIO(decoded))
                except Exception as e:
                    messages.error(request, f'Could not read file: {e}')
                    return redirect('.')
 
                created = 0
                skipped = 0
                errors  = []
 
                for i, row in enumerate(reader, start=2):  # start=2 because row 1 is header
                    if i > 1001:  # max 1000 data rows
                        errors.append('Stopped at row 1001 — max 1000 rows per upload.')
                        break
 
                    code = (row.get('code') or '').strip()
                    if not code:
                        errors.append(f'Row {i}: missing code — skipped.')
                        skipped += 1
                        continue
 
                    # Skip duplicates silently
                    if VoucherInventory.objects.filter(voucher_code=code).exists():
                        skipped += 1
                        continue
 
                    # Service
                    service_id = (row.get('service_id') or '').strip()
                    if service_id:
                        try:
                            service = Service.objects.get(pk=service_id)
                        except Service.DoesNotExist:
                            errors.append(f'Row {i}: service_id {service_id} not found — skipped.')
                            skipped += 1
                            continue
                    elif default_service:
                        service = default_service
                    else:
                        errors.append(f'Row {i}: no service_id and no default service — skipped.')
                        skipped += 1
                        continue
 
                    # Voucher type
                    vtype_raw = (row.get('voucher_type') or 'FIXED').strip().upper()
                    vtype     = 'PERCENT' if vtype_raw in ('PERCENT', 'PERCENTAGE_DISCOUNT') else 'FIXED'
 
                    # Amount
                    try:
                        amount = Decimal(row.get('amount') or '0')
                    except Exception:
                        amount = Decimal('0')
 
                    # Discount %
                    try:
                        disc = Decimal(row.get('discount_percentage') or '0') or None
                    except Exception:
                        disc = None
 
                    # Expiry
                    expires_raw = (row.get('expires_at') or '').strip()
                    expires_at  = None
                    if expires_raw:
                        try:
                            from datetime import date
                            expires_at = date.fromisoformat(expires_raw)
                        except ValueError:
                            errors.append(f'Row {i}: bad expires_at "{expires_raw}" — ignored.')
 
                    VoucherInventory.objects.create(
                        voucher_code        = code,
                        service             = service,
                        amount              = amount,
                        voucher_type        = vtype,
                        discount_percentage = disc,
                        expires_at          = expires_at,
                        status              = VoucherInventory.VoucherStatus.AVAILABLE,
                    )
                    created += 1
 
                # Summary
                msg = f'Upload complete: {created} vouchers created, {skipped} skipped (duplicates or errors).'
                if errors:
                    msg += f' Warnings: {"; ".join(errors[:5])}'
                    messages.warning(request, msg)
                else:
                    messages.success(request, msg)
 
                return redirect('../')  # back to changelist
 
        else:
            form = VoucherCSVUploadForm()
 
        context = {
            **self.admin_site.each_context(request),
            'form':        form,
            'title':       'Bulk Upload Vouchers',
            'opts':        VoucherInventory._meta,
            'app_label':   VoucherInventory._meta.app_label,
        }
        return render(request, 'admin/services/voucherinventory/bulk_upload.html', context)


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