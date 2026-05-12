from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import strip_tags
from .models import PayoutConfig, PayoutRecord, SalesRecord, quantize_money

logger = logging.getLogger(__name__)


OPEN_PAYOUT_STATUSES = [
    PayoutRecord.Status.PENDING,
    PayoutRecord.Status.APPROVED,
    PayoutRecord.Status.FAILED,
]


def get_partner_bank_details(venue):
    partner = getattr(venue, "partner", None)
    if not partner:
        return {
            "bank_name": "",
            "account_number": "",
            "account_name": "",
            "is_complete": False,
        }

    bank_name = (partner.bank_name or "").strip()
    account_number = (partner.account_number or "").strip()
    account_name = (partner.account_name or "").strip()
    return {
        "bank_name": bank_name,
        "account_number": account_number,
        "account_name": account_name,
        "is_complete": all([bank_name, account_number, account_name]),
    }


def calculate_sales_snapshot(booking, config=None):
    config = config or PayoutConfig.get_config()
    booking_source = (booking.booking_source or SalesRecord.BookingSource.SUBSCRIPTION).upper()

    gross_amount = Decimal("0.00")
    payment_method = ""
    source_reference = booking.booking_reference
    notes = ""

    if booking_source == SalesRecord.BookingSource.STORE:
        store_order = getattr(booking, "store_order", None)
        if store_order:
            gross_amount = quantize_money(store_order.amount_paid)
            payment_method = store_order.payment_method or ""
            source_reference = store_order.reference or booking.booking_reference
        else:
            gross_amount = quantize_money(getattr(booking.venue, "store_price", 0))
            notes = "Store booking had no linked order snapshot; venue price fallback used."
    else:
        gross_amount = quantize_money(getattr(booking.venue, "store_price", 0))
        if gross_amount <= 0:
            return None

    commission_rate = config.commission_rate_for(booking_source)
    commission_enabled = config.commission_enabled_for(booking_source)
    commission_amount = config.commission_amount_for(booking_source, gross_amount)
    net_amount = quantize_money(gross_amount - commission_amount)

    return {
        "booking_source": booking_source,
        "source_reference": source_reference,
        "checked_in_at": booking.checked_in_at,
        "eligible_for_payout_at": config.eligible_at_for(booking.checked_in_at),
        "gross_amount": gross_amount,
        "commission_amount": commission_amount,
        "net_amount": net_amount,
        "commission_rate_snapshot": commission_rate,
        "commission_enabled_snapshot": commission_enabled,
        "payment_method_snapshot": payment_method,
        "guests_count": booking.guests_count or 1,
        "notes": notes,
    }


@transaction.atomic
def create_sales_record_for_booking(booking, config=None):
    if booking.status != "CHECKED_IN" or not booking.checked_in_at:
        return None, False

    snapshot = calculate_sales_snapshot(booking, config=config)
    if snapshot is None:
        return None, False

    record, created = SalesRecord.objects.get_or_create(
        booking=booking,
        defaults={
            "venue": booking.venue,
            "booking_source": snapshot["booking_source"],
            "source_reference": snapshot["source_reference"],
            "checked_in_at": snapshot["checked_in_at"],
            "eligible_for_payout_at": snapshot["eligible_for_payout_at"],
            "gross_amount": snapshot["gross_amount"],
            "commission_amount": snapshot["commission_amount"],
            "net_amount": snapshot["net_amount"],
            "commission_rate_snapshot": snapshot["commission_rate_snapshot"],
            "commission_enabled_snapshot": snapshot["commission_enabled_snapshot"],
            "payment_method_snapshot": snapshot["payment_method_snapshot"],
            "guests_count": snapshot["guests_count"],
            "notes": snapshot["notes"],
        },
    )
    return record, created



def _get_eligible_unbatched_sales(now=None):
    now = now or timezone.now()
    return (
        SalesRecord.objects.filter(
            payout_record__isnull=True,
            eligible_for_payout_at__lte=now,
        )
        .select_related("venue", "venue__partner", "booking", "booking__user")
        .order_by("venue_id", "eligible_for_payout_at", "checked_in_at", "id")
    )


@transaction.atomic
def create_payout_batch_for_venue(venue, sales_records):
    sales_records = list(sales_records)
    if not sales_records:
        return None

    

    bank_details = get_partner_bank_details(venue)
    if not bank_details["is_complete"]:
        return None

    total_gross = quantize_money(sum(record.gross_amount for record in sales_records))
    total_commission = quantize_money(sum(record.commission_amount for record in sales_records))
    total_net = quantize_money(sum(record.net_amount for record in sales_records))
    booking_count = len(sales_records)
    store_count = sum(1 for record in sales_records if record.booking_source == SalesRecord.BookingSource.STORE)
    subscription_count = booking_count - store_count
    period_start = min(record.checked_in_at for record in sales_records)
    period_end = max(record.checked_in_at for record in sales_records)

    payout = PayoutRecord.objects.create(
        venue=venue,
        period_start=period_start,
        period_end=period_end,
        total_gross=total_gross,
        total_commission=total_commission,
        total_net=total_net,
        booking_count=booking_count,
        store_count=store_count,
        subscription_count=subscription_count,
        bank_name_snapshot=bank_details["bank_name"],
        account_number_snapshot=bank_details["account_number"],
        account_name_snapshot=bank_details["account_name"],
    )
    SalesRecord.objects.filter(id__in=[record.id for record in sales_records]).update(payout_record=payout)
    return payout


def auto_create_pending_payouts(config=None, now=None):
    config = config or PayoutConfig.get_config()
    now = now or timezone.now()
    created_payouts = []
    blockers = {}

    venue_buckets = {}
    for record in _get_eligible_unbatched_sales(now=now):
        venue_buckets.setdefault(record.venue_id, {"venue": record.venue, "records": []})
        venue_buckets[record.venue_id]["records"].append(record)

    for venue_id, bucket in venue_buckets.items():
        venue = bucket["venue"]
        records = bucket["records"]
        bank_details = get_partner_bank_details(venue)

        
        if not bank_details["is_complete"]:
            blockers[venue_id] = "missing_bank_details"
            continue

        venue_net = quantize_money(sum(record.net_amount for record in records))
        if venue_net < config.minimum_payout_amount:
            blockers[venue_id] = "below_minimum"
            continue

        payout = create_payout_batch_for_venue(venue, records)
        if payout:
            created_payouts.append(payout)

    return created_payouts, blockers

def build_admin_rollups(config=None, now=None):
    config = config or PayoutConfig.get_config()
    now = now or timezone.now()

    sales_records = (
        SalesRecord.objects.filter(payout_record__isnull=True)
        .select_related("venue", "venue__partner")
        .order_by("venue__name", "eligible_for_payout_at")
    )

    venues_with_open_payouts = set(
        PayoutRecord.objects.filter(
            status__in=OPEN_PAYOUT_STATUSES
        ).values_list("venue_id", flat=True)
    )

    buckets = {}
    for record in sales_records:
        item = buckets.setdefault(
            record.venue_id,
            {
                "venue": record.venue,
                "total_records": 0,
                "eligible_records": 0,
                "total_net": Decimal("0.00"),
                "eligible_net": Decimal("0.00"),
                "next_release_at": record.eligible_for_payout_at,
            },
        )
        item["total_records"] += 1
        item["total_net"] += record.net_amount

        if record.eligible_for_payout_at <= now:
            item["eligible_records"] += 1
            item["eligible_net"] += record.net_amount

        if record.eligible_for_payout_at < item["next_release_at"]:
            item["next_release_at"] = record.eligible_for_payout_at

    rollups = []
    for item in buckets.values():
        bank_details = get_partner_bank_details(item["venue"])
        has_open_payout = item["venue"].id in venues_with_open_payouts
        eligible_net = quantize_money(item["eligible_net"])

        if has_open_payout:
            blocker = "Open payout already awaiting action."
        elif not bank_details["is_complete"]:
            blocker = "Bank details missing on partner profile."
        elif eligible_net and eligible_net < config.minimum_payout_amount:
            blocker = f"Eligible net is below the minimum payout amount of {config.minimum_payout_amount}."
        elif not item["eligible_records"]:
            blocker = "No sales records are eligible yet."
        else:
            blocker = ""

        rollups.append(
            {
                "venue": item["venue"],
                "total_records": item["total_records"],
                "eligible_records": item["eligible_records"],
                "total_net": quantize_money(item["total_net"]),
                "eligible_net": eligible_net,
                "next_release_at": item["next_release_at"],
                "bank_ready": bank_details["is_complete"],
                "has_open_payout": has_open_payout,
                "blocker": blocker,
                "ready": not blocker,
            }
        )

    return sorted(rollups, key=lambda row: (not row["ready"], row["venue"].name.lower()))



def payout_totals_for_queryset(queryset):
    totals = queryset.aggregate(
        total_gross=Sum("gross_amount"),
        total_commission=Sum("commission_amount"),
        total_net=Sum("net_amount"),
        total_sales=Count("id"),
        store_sales=Count("id", filter=Q(booking_source=SalesRecord.BookingSource.STORE)),
        subscription_sales=Count(
            "id",
            filter=Q(booking_source=SalesRecord.BookingSource.SUBSCRIPTION),
        ),
    )
    for key in ("total_gross", "total_commission", "total_net"):
        totals[key] = totals[key] or Decimal("0.00")
    return totals

def notify_venue_payout_completed(payout, request=None):
    """
    Send payout completed email to the venue partner.

    Returns True if sent successfully, otherwise False.
    Never raises.
    """
    partner = getattr(getattr(payout, "venue", None), "partner", None)
    recipient = getattr(getattr(partner, "user", None), "email", "") or ""

    if not recipient:
        logger.warning(
            "Payout completed email skipped for %s: partner email missing.",
            payout.reference,
        )
        return False

    try:
        from django.conf import settings
        path = reverse("venues:partner_sales_report")
        if request:
            venue_dashboard_url = request.build_absolute_uri(path)
        else:
            base = getattr(settings, "SITE_URL", "https://goldprivilege.net")
            venue_dashboard_url = f"{base.rstrip('/')}{path}"
        

        context = {
            "payout": payout,           # pass actual model, not dict
            "venue": payout.venue,
            "venue_dashboard_url": venue_dashboard_url,
        }


        html_message = render_to_string(
            "superadmin/admin/emails/payout_completed.html",
            context,
        )
        plain_message = strip_tags(html_message)

        email = EmailMultiAlternatives(
            subject=f"Payout Received - {payout.reference}",
            body=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
        )
        email.attach_alternative(html_message, "text/html")

        sent = email.send()
        return bool(sent)

    except Exception:
        logger.exception(
            "Failed to send payout completed email for payout %s",
            payout.reference,
        )
        return False

