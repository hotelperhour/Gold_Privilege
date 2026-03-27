from decimal import Decimal
from django.contrib import admin
from django.utils.html import format_html
from django.contrib import messages as django_messages

from .models import (
    Wallet, WalletTransaction, CoinPackage, CoinPurchase,
    CashbackRule, ReferralRecord, WalletConfig,
)
from .utils import credit_wallet, debit_wallet


# ─────────────────────────────────────────────────────────────────────────────

@admin.register(WalletConfig)
class WalletConfigAdmin(admin.ModelAdmin):
    fieldsets = (
        ('Transfer Limits', {
            'fields': ('daily_transfer_limit', 'min_transfer_amount'),
        }),
        ('Referral & Monthly Bonuses', {
            'fields': (
                'referral_coins_reward',
                'monthly_bonus_tier_1', 'monthly_bonus_tier_2', 'monthly_bonus_tier_3',
            ),
        }),
        ('PIN Security', {
            'fields': ('max_failed_pin_attempts',),
        }),
    )

    def has_add_permission(self, request):
        return not WalletConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


# ─────────────────────────────────────────────────────────────────────────────

class WalletTransactionInline(admin.TabularInline):
    model         = WalletTransaction
    extra         = 0
    readonly_fields = (
        'transaction_id', 'type', 'amount', 'balance_before',
        'balance_after', 'related_user', 'note', 'created_at',
    )
    can_delete    = False
    max_num       = 10
    ordering      = ['-created_at']

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display    = ('user', 'balance_display', 'pin_set', 'pin_locked_until', 'updated_at')
    list_filter     = ('pin_set',)
    search_fields   = ('user__email', 'user__gp_id')
    readonly_fields = (
        'user', 'balance', 'pin_set', 'pin_failed_attempts',
        'pin_locked_until', 'daily_transfer_total', 'daily_transfer_date',
        'created_at', 'updated_at','wallet_pin'
    )
    inlines = [WalletTransactionInline]

    fieldsets = (
        ('Wallet', {
            'fields': ('user', 'balance'),
        }),
        ('PIN Status', {
            'fields': ('pin_set', 'pin_failed_attempts', 'pin_locked_until', 'wallet_pin'),
        }),
        ('Daily Transfer', {
            'fields': ('daily_transfer_total', 'daily_transfer_date'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
        }),
    )

    def balance_display(self, obj):
        balance = f"{int(obj.balance):,}"
        return format_html('<strong>{} coins</strong>', balance)

    # Admin manual credit/debit actions
    actions = ['action_credit_coins', 'action_debit_coins']

    def action_credit_coins(self, request, queryset):
        for wallet in queryset:
            try:
                credit_wallet(
                    wallet, 100,
                    WalletTransaction.TransactionType.ADMIN_CREDIT,
                    note=f'Admin test credit by {request.user.email}',
                )
            except Exception as e:
                self.message_user(request, f'Error crediting {wallet.user.email}: {e}',
                                  level=django_messages.ERROR)
    action_credit_coins.short_description = 'Test: Credit 100 coins'


# ─────────────────────────────────────────────────────────────────────────────

@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display  = (
        'short_id', 'user_email', 'type', 'amount_display',
        'balance_after', 'related_user', 'created_at',
    )
    list_filter   = ('type', 'created_at')
    search_fields = ('wallet__user__email', 'wallet__user__gp_id', 'paystack_reference', 'note')
    readonly_fields = (
        'transaction_id', 'wallet', 'type', 'amount', 'balance_before',
        'balance_after', 'related_user', 'paystack_reference', 'note',
        'ip_address', 'created_at',
    )
    ordering      = ['-created_at']

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False  # Immutable ledger

    def has_change_permission(self, request, obj=None):
        return False  # Read-only

    def short_id(self, obj):
        return str(obj.transaction_id)[:8] + '…'
    short_id.short_description = 'ID'

    def user_email(self, obj):
        return obj.wallet.user.email
    user_email.short_description = 'User'

    def amount_display(self, obj):
        colour = '#28a745' if obj.is_credit else '#dc3545'
        sign   = '+' if obj.is_credit else '-'
        amount_str   = f'{int(obj.amount):,}'   # format first, plain string
        return format_html(
            '<span style="color:{}; font-weight:600;">{}{}</span>',
            colour, sign, amount_str,
        )
    amount_display.short_description = 'Amount'


# ─────────────────────────────────────────────────────────────────────────────

@admin.register(CoinPackage)
class CoinPackageAdmin(admin.ModelAdmin):
    list_display  = ('name', 'coins', 'bonus_coins', 'total_display', 'price', 'is_featured', 'is_active', 'display_order')
    list_editable = ('is_featured', 'is_active', 'display_order')
    ordering      = ['display_order', 'price']

    def total_display(self, obj):
        return f'{obj.total_coins():,} coins'
    total_display.short_description = 'Total Coins'


@admin.register(CoinPurchase)
class CoinPurchaseAdmin(admin.ModelAdmin):
    list_display    = ('user', 'coins_to_credit', 'amount', 'status', 'paystack_reference', 'created_at')
    list_filter     = ('status', 'created_at')
    search_fields   = ('user__email', 'paystack_reference')
    readonly_fields = ('user', 'package', 'coins_to_credit', 'amount', 'paystack_reference',
                       'status', 'created_at', 'completed_at')


@admin.register(CashbackRule)
class CashbackRuleAdmin(admin.ModelAdmin):
    list_display  = ('__str__', 'rule_type', 'percentage', 'minimum_spend', 'is_active', 'valid_from', 'valid_until')
    list_editable = ('is_active',)
    list_filter   = ('rule_type', 'is_active')


@admin.register(ReferralRecord)
class ReferralRecordAdmin(admin.ModelAdmin):
    list_display    = ('referrer', 'referred_user', 'coins_awarded', 'is_paid', 'awarded_at', 'created_at')
    list_filter     = ('is_paid',)
    search_fields   = ('referrer__email', 'referrer__gp_id', 'referred_user__email')
    readonly_fields = ('referrer', 'referred_user', 'coins_awarded', 'awarded_at', 'created_at')