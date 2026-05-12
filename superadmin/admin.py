from django.contrib import admin

from .models import PayoutConfig, PayoutRecord, SalesRecord


@admin.register(PayoutConfig)
class PayoutConfigAdmin(admin.ModelAdmin):
    list_display = (
        "payout_delay_hours",
        "minimum_payout_amount",
        "apply_commission_to_store",
        "store_commission_rate",
        "apply_commission_to_subscription",
        "subscription_commission_rate",
        "updated_at",
    )

    # Add inside PayoutConfigAdmin class
    def has_add_permission(self, request):
        return not PayoutConfig.objects.exists()


@admin.register(SalesRecord)
class SalesRecordAdmin(admin.ModelAdmin):
    list_display = (
        "booking",
        "venue",
        "booking_source",
        "gross_amount",
        "commission_amount",
        "net_amount",
        "eligible_for_payout_at",
        "payout_record",
    )
    list_filter = ("booking_source", "payout_record__status")
    search_fields = ("booking__booking_reference", "venue__name", "source_reference")
    readonly_fields = [field.name for field in SalesRecord._meta.fields]

    # After line 23 — add these two methods
    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


@admin.register(PayoutRecord)
class PayoutRecordAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "venue",
        "status",
        "total_net",
        "booking_count",
        "created_at",
        "paid_at",
    )
    list_filter = ("status", "venue")
    search_fields = ("reference", "venue__name", "transfer_reference")
    # Add inside PayoutRecordAdmin class
    readonly_fields = (
        "payout_id",
        "reference",
        "created_at",
        "updated_at",
        "approved_at",
        "approved_by",
        "paid_at",
        "paid_by",
    )
    def has_add_permission(self, request):
        return False
