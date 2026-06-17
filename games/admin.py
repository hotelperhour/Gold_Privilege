"""
games/admin.py

Admin registrations for the spin system.

What admins can do here:
  SpinConfigAdmin  → edit daily limit, cooldown, kill switch
  SpinPrizeAdmin   → manage the prize table, see total weight, reorder segments
  SpinRecordAdmin  → read-only audit log of every spin
"""

from django.contrib import admin
from django.db.models import Sum
from django.utils.html import format_html

from .models import SpinConfig, SpinPrize, SpinRecord


# ─────────────────────────────────────────────────────────────────────────────
# SPIN CONFIG
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(SpinConfig)
class SpinConfigAdmin(admin.ModelAdmin):
    """
    Singleton config — only one row ever exists (pk=1).
    Admin can turn the wheel on/off, change daily limits, and cooldown.
    """
    fieldsets = (
        ('Wheel Status', {
            'fields': ('is_active',),
            'description': (
                'Turn OFF to disable the spin wheel for ALL users instantly. '
                'Useful during maintenance or prize rebalancing.'
            ),
        }),
        ('Daily Limits', {
            'fields': ('daily_spin_limit', 'cooldown_minutes'),
        }),
        ('Metadata', {
            'fields': ('updated_by', 'updated_at'),
        }),
    )
    readonly_fields = ('updated_at', 'updated_by')

    def has_add_permission(self, request):
        """Prevent creating a second row — only one config exists."""
        return not SpinConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        """Prevent deleting the config — it must always exist."""
        return False

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


# ─────────────────────────────────────────────────────────────────────────────
# SPIN PRIZE
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(SpinPrize)
class SpinPrizeAdmin(admin.ModelAdmin):
    """
    Manage the prize table.

    The colour preview column shows a live swatch so admins can visually
    verify their colour choices without leaving the list page.

    The total_weight row in the changelist header (via change_list_note)
    shows the running sum so admins know when they've hit exactly 100.
    """
    list_display  = [
        'display_order', 'label', 'coins_value',
        'weight', 'color_swatch', 'is_active',
    ]
    list_display_links = ['label']
    list_editable = ['display_order', 'weight', 'is_active']
    list_filter   = ['is_active']
    ordering      = ['display_order']

    fieldsets = (
        ('Prize Details', {
            'fields': ('label', 'coins_value', 'is_active'),
        }),
        ('Probability', {
            'fields': ('weight',),
            'description': (
                '⚠️  All active prizes must sum to exactly 100. '
                'The system will reject a save that breaks this rule. '
                'Use 0 for a prize you want to deactivate without deleting.'
            ),
        }),
        ('Visual (Wheel Segment)', {
            'fields': ('color', 'text_color', 'display_order'),
            'description': (
                'Recommended gold shades: #E5AD04 (main gold), '
                '#F9B036 (light gold), #5c3001 (chocolate), '
                '#C8860A (dark gold), #020202 (near black).'
            ),
        }),
    )
    readonly_fields = ('created_at', 'updated_at')

    def color_swatch(self, obj):
        """Renders a coloured rectangle in the list for visual reference."""
        return format_html(
            '<div style="width:60px;height:22px;background:{};border-radius:4px;'
            'border:1px solid rgba(0,0,0,0.15);display:inline-block;"></div>',
            obj.color,
        )
    color_swatch.short_description = 'Colour'

    def changelist_view(self, request, extra_context=None):
        total = (
            SpinPrize.objects
            .filter(is_active=True)
            .aggregate(total=Sum('weight'))['total'] or 0
        )
        if total == 100:
            self.message_user(
                request,
                f'✅ Active prizes total exactly 100% — wheel is correctly configured.',
                level='SUCCESS',
            )
        else:
            self.message_user(
                request,
                f'⚠️ Active prizes currently total {total}%. They should sum to '
                f'exactly 100% before going live, or the odds shown to admins won\'t '
                f'match the actual probability used (spins will still work, but the '
                f'percentages will be relative to {total}, not 100).',
                level='WARNING',
            )
        return super().changelist_view(request, extra_context=extra_context)


# ─────────────────────────────────────────────────────────────────────────────
# SPIN RECORD  (read-only audit log)
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(SpinRecord)
class SpinRecordAdmin(admin.ModelAdmin):
    """
    Read-only audit log of every spin. Never editable — financial records
    must not be changed after creation.
    """
    list_display  = [
        'spun_at_display', 'user_email', 'prize_label',
        'coins_awarded', 'win_badge', 'lagos_date', 'ip_address',
    ]
    list_filter   = ['lagos_date', 'prize']
    search_fields = ['user__email', 'prize__label', 'ip_address']
    ordering      = ['-spun_at']
    date_hierarchy = 'spun_at'

    readonly_fields = [
        'user', 'prize', 'coins_awarded',
        'spun_at', 'lagos_date', 'ip_address',
    ]

    def has_add_permission(self, request):
        return False   # Spin records are created only by the system

    def has_change_permission(self, request, obj=None):
        return False   # Immutable — never edited

    def has_delete_permission(self, request, obj=None):
        return False   # Financial audit trail must not be deleted

    def spun_at_display(self, obj):
        return obj.spun_at.strftime('%Y-%m-%d %H:%M:%S UTC')
    spun_at_display.short_description = 'Spun At'
    spun_at_display.admin_order_field = 'spun_at'

    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = 'User'
    user_email.admin_order_field = 'user__email'

    def prize_label(self, obj):
        return obj.prize.label
    prize_label.short_description = 'Prize'

    def win_badge(self, obj):
        if obj.is_win:
            return format_html(
                '<span style="background:#28a745;color:#fff;padding:2px 10px;'
                'border-radius:12px;font-size:11px;font-weight:600;">WIN</span>'
            )
        return format_html(
            '<span style="background:#6c757d;color:#fff;padding:2px 10px;'
            'border-radius:12px;font-size:11px;">loss</span>'
        )
    win_badge.short_description = 'Result'