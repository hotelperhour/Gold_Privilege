from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from account.models import CustomUser, PartnerProfile
from account.permissions import admin_required
from subscriptions.models import PromoCode, Subscription
from venues.models import Venue, VenueAccessMode, VenueCategory, VenueStatus

from .forms import PayoutApprovalForm, PayoutConfigForm, PayoutPaymentForm
from .models import PayoutConfig, PayoutRecord, SalesRecord
from .utils import (
    auto_create_pending_payouts,
    build_admin_rollups,
    notify_venue_payout_completed,
    payout_totals_for_queryset,
)


def _subscription_prefetch():
    return Prefetch(
        "subscriptions",
        queryset=Subscription.objects.select_related("plan").order_by("-end_date", "-created_at"),
        to_attr="superadmin_subscriptions",
    )


def _decorate_subscribers(subscribers):
    now = timezone.now()
    for subscriber in subscribers:
        subscriber.display_name = (
            subscriber.profile.get_full_name()
            if hasattr(subscriber, "profile") and subscriber.profile
            else subscriber.email
        )

        subscriptions = getattr(subscriber, "superadmin_subscriptions", [])
        current_subscription = None

        for sub in subscriptions:
            if sub.status in [Subscription.Status.ACTIVE, Subscription.Status.TRIAL, Subscription.Status.PENDING]:
                current_subscription = sub
                break

        if current_subscription is None and subscriptions:
            current_subscription = subscriptions[0]

        subscriber.current_subscription = current_subscription
        subscriber.subscription_active = bool(
            current_subscription
            and current_subscription.status in [Subscription.Status.ACTIVE, Subscription.Status.TRIAL]
            and current_subscription.start_date <= now <= current_subscription.end_date
        )
        subscriber.renewal_date = current_subscription.end_date if current_subscription else None
    return subscribers


def _base_superadmin_context():
    now = timezone.now()

    active_subscriptions_qs = Subscription.objects.filter(
        status__in=[Subscription.Status.ACTIVE, Subscription.Status.TRIAL],
        start_date__lte=now,
        end_date__gte=now,
    )

    open_payouts_qs = PayoutRecord.objects.filter(
        status__in=[
            PayoutRecord.Status.PENDING,
            PayoutRecord.Status.APPROVED,
            PayoutRecord.Status.FAILED,
        ]
    )

    return {
        "sidebar_counts": {
            "subscribers": CustomUser.objects.filter(user_type=CustomUser.UserType.SUBSCRIBER).count(),
            "active_subscriptions": active_subscriptions_qs.count(),
            "partners": PartnerProfile.objects.count(),
            "approved_venues": Venue.objects.filter(status=VenueStatus.APPROVED).count(),
            "open_payouts": open_payouts_qs.count(),
        }
    }


@login_required
@admin_required
def admin_payout_dashboard(request):
    config = PayoutConfig.get_config()

    if request.method == "POST" and request.POST.get("action") == "save_config":
        form = PayoutConfigForm(request.POST, instance=config)
        if form.is_valid():
            config = form.save(commit=False)
            config.updated_by = request.user
            config.save()
            messages.success(request, "Payout settings updated successfully.")
            return redirect("superadmin:dashboard")
        messages.error(request, "Please fix the payout settings form.")
    else:
        form = PayoutConfigForm(instance=config)

    pending_payouts = (
        PayoutRecord.objects.filter(
            status__in=[
                PayoutRecord.Status.PENDING,
                PayoutRecord.Status.APPROVED,
                PayoutRecord.Status.FAILED,
            ]
        )
        .select_related("venue", "venue__partner")
        .order_by("status", "-created_at")
    )

    recent_paid_payouts = (
        PayoutRecord.objects.filter(status=PayoutRecord.Status.PAID)
        .select_related("venue", "venue__partner")
        .order_by("-paid_at", "-created_at")[:8]
    )

    rollups = build_admin_rollups(config=config)
    ready_rollups = [row for row in rollups if row["ready"]]
    blocked_rollups = [row for row in rollups if not row["ready"]]

    sales_totals = payout_totals_for_queryset(SalesRecord.objects.all())

    recent_subscribers = list(
        CustomUser.objects.filter(user_type=CustomUser.UserType.SUBSCRIBER)
        .select_related("profile")
        .prefetch_related(_subscription_prefetch())
        .order_by("-date_joined")[:6]
    )
    _decorate_subscribers(recent_subscribers)

    recent_partners = (
        PartnerProfile.objects.select_related("user")
        .annotate(venue_count=Count("venues", distinct=True))
        .order_by("-created_at")[:6]
    )

    recent_venues = (
        Venue.objects.select_related("partner", "partner__user")
        .annotate(bookings_total=Count("bookings", distinct=True))
        .order_by("-created_at")[:6]
    )

    now = timezone.now()
    dashboard_stats = {
        "subscriber_count": CustomUser.objects.filter(user_type=CustomUser.UserType.SUBSCRIBER).count(),
        "active_subscriptions": Subscription.objects.filter(
            status__in=[Subscription.Status.ACTIVE, Subscription.Status.TRIAL],
            start_date__lte=now,
            end_date__gte=now,
        ).count(),
        "partner_count": PartnerProfile.objects.count(),
        "approved_venues": Venue.objects.filter(status=VenueStatus.APPROVED).count(),
        "pending_batches": pending_payouts.count(),
        "pending_total_net": pending_payouts.aggregate(total=Sum("total_net"))["total"] or 0,
        "eligible_venues": len(ready_rollups),
        "blocked_venues": len(blocked_rollups),
        "lifetime_sales_net": sales_totals["total_net"],
        "lifetime_commission": sales_totals["total_commission"],
    }

    context = {
        **_base_superadmin_context(),
        "config": config,
        "config_form": form,
        "dashboard_stats": dashboard_stats,
        "pending_payouts": pending_payouts[:12],
        "recent_paid_payouts": recent_paid_payouts,
        "ready_rollups": ready_rollups[:12],
        "blocked_rollups": blocked_rollups[:12],
        "recent_subscribers": recent_subscribers,
        "recent_partners": recent_partners,
        "recent_venues": recent_venues,
    }
    return render(request, "superadmin/admin/dashboard.html", context)


@login_required
@admin_required
@require_POST
def run_auto_create_payouts(request):
    config = PayoutConfig.get_config()
    venue_id = request.POST.get("venue_id", "").strip()

    if venue_id:
        venue = get_object_or_404(Venue, id=venue_id, status=VenueStatus.APPROVED)
        from .utils import _get_eligible_unbatched_sales, create_payout_batch_for_venue

        records = list(_get_eligible_unbatched_sales(now=timezone.now()).filter(venue=venue))
        payout = create_payout_batch_for_venue(venue, records)
        if payout:
            messages.success(request, f"1 payout batch created for {venue.name}.")
        else:
            messages.info(request, f"No eligible records for {venue.name}.")
    else:
        created_payouts, _ = auto_create_pending_payouts(config=config)
        if created_payouts:
            messages.success(request, f"{len(created_payouts)} payout batch(es) created.")
        else:
            messages.info(request, "No new payout batches were created.")

    return redirect("superadmin:dashboard")


@login_required
@admin_required
def superadmin_venues_list(request):
    venues_qs = (
        Venue.objects.select_related("partner", "partner__user")
        .annotate(
            bookings_total=Count("bookings", distinct=True),
            sales_total=Count("sales_records", distinct=True),
        )
        .order_by("-created_at")
    )

    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    category = request.GET.get("category", "").strip()
    access_mode = request.GET.get("access_mode", "").strip()

    if q:
        venues_qs = venues_qs.filter(
            Q(name__icontains=q)
            | Q(city__icontains=q)
            | Q(state__icontains=q)
            | Q(partner__business_name__icontains=q)
            | Q(partner__user__email__icontains=q)
        )
    if status:
        venues_qs = venues_qs.filter(status=status)
    if category:
        venues_qs = venues_qs.filter(category=category)
    if access_mode:
        venues_qs = venues_qs.filter(access_mode=access_mode)

    paginator = Paginator(venues_qs, 15)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        **_base_superadmin_context(),
        "page_obj": page_obj,
        "venues": page_obj.object_list,
        "q": q,
        "status_filter": status,
        "category_filter": category,
        "access_mode_filter": access_mode,
        "status_choices": VenueStatus.choices,
        "category_choices": VenueCategory.choices,
        "access_mode_choices": VenueAccessMode.choices,
        "summary": {
            "total": Venue.objects.count(),
            "approved": Venue.objects.filter(status=VenueStatus.APPROVED).count(),
            "pending": Venue.objects.filter(status=VenueStatus.PENDING).count(),
            "store_enabled": Venue.objects.filter(access_mode__in=[VenueAccessMode.STORE, VenueAccessMode.BOTH]).count(),
        },
    }
    return render(request, "superadmin/admin/venues_list.html", context)


@login_required
@admin_required
def superadmin_partners_list(request):
    partners_qs = (
        PartnerProfile.objects.select_related("user")
        .annotate(venue_count=Count("venues", distinct=True))
        .order_by("-created_at")
    )

    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()

    if q:
        partners_qs = partners_qs.filter(
            Q(business_name__icontains=q)
            | Q(user__email__icontains=q)
            | Q(user__gp_id__icontains=q)
        )
    if status:
        partners_qs = partners_qs.filter(status=status)

    paginator = Paginator(partners_qs, 15)
    page_obj = paginator.get_page(request.GET.get("page"))

    for partner in page_obj.object_list:
        partner.bank_ready = all(
            [
                (partner.bank_name or "").strip(),
                (partner.account_number or "").strip(),
                (partner.account_name or "").strip(),
            ]
        )

    context = {
        **_base_superadmin_context(),
        "page_obj": page_obj,
        "partners": page_obj.object_list,
        "q": q,
        "status_filter": status,
        "status_choices": PartnerProfile.PartnerStatus.choices,
        "summary": {
            "total": PartnerProfile.objects.count(),
            "approved": PartnerProfile.objects.filter(status=PartnerProfile.PartnerStatus.APPROVED).count(),
            "pending": PartnerProfile.objects.filter(status=PartnerProfile.PartnerStatus.PENDING).count(),
            "with_bank_details": PartnerProfile.objects.exclude(bank_name="").exclude(account_number="").exclude(account_name="").count(),
        },
    }
    return render(request, "superadmin/admin/partners_list.html", context)


@login_required
@admin_required
def superadmin_subscribers_list(request):
    subscribers_qs = (
        CustomUser.objects.filter(user_type=CustomUser.UserType.SUBSCRIBER)
        .select_related("profile")
        .prefetch_related(_subscription_prefetch())
        .order_by("-date_joined")
    )

    q = request.GET.get("q", "").strip()
    subscription_status = request.GET.get("subscription_status", "").strip().upper()

    if q:
        subscribers_qs = subscribers_qs.filter(
            Q(email__icontains=q)
            | Q(gp_id__icontains=q)
            | Q(profile__first_name__icontains=q)
            | Q(profile__last_name__icontains=q)
        )

    if subscription_status == "NONE":
        subscribers_qs = subscribers_qs.filter(subscriptions__isnull=True)
    elif subscription_status in [
        Subscription.Status.ACTIVE,
        Subscription.Status.TRIAL,
        Subscription.Status.EXPIRED,
        Subscription.Status.CANCELLED,
        Subscription.Status.PENDING,
    ]:
        subscribers_qs = subscribers_qs.filter(subscriptions__status=subscription_status)

    subscribers_qs = subscribers_qs.distinct()

    paginator = Paginator(subscribers_qs, 15)
    page_obj = paginator.get_page(request.GET.get("page"))
    _decorate_subscribers(page_obj.object_list)

    now = timezone.now()
    context = {
        **_base_superadmin_context(),
        "page_obj": page_obj,
        "subscribers": page_obj.object_list,
        "q": q,
        "subscription_status_filter": subscription_status,
        "subscription_status_choices": Subscription.Status.choices,
        "summary": {
            "total": CustomUser.objects.filter(user_type=CustomUser.UserType.SUBSCRIBER).count(),
            "active": Subscription.objects.filter(
                status__in=[Subscription.Status.ACTIVE, Subscription.Status.TRIAL],
                start_date__lte=now,
                end_date__gte=now,
            ).count(),
            "expiring_soon": Subscription.objects.filter(
                status__in=[Subscription.Status.ACTIVE, Subscription.Status.TRIAL],
                end_date__gte=now,
                end_date__lte=now + timezone.timedelta(days=7),
            ).count(),
            "pending_payment": Subscription.objects.filter(status=Subscription.Status.PENDING).count(),
        },
    }
    return render(request, "superadmin/admin/subscribers_list.html", context)


@login_required
@admin_required
def superadmin_coupon_codes_list(request):
    now = timezone.now()

    coupons_qs = (
        PromoCode.objects.prefetch_related("applicable_plans")
        .annotate(applicable_plan_count=Count("applicable_plans", distinct=True))
        .order_by("-created_at")
    )

    q = request.GET.get("q", "").strip()
    state = request.GET.get("state", "").strip().upper()

    if q:
        coupons_qs = coupons_qs.filter(
            Q(code__icontains=q) | Q(description__icontains=q)
        )

    if state == "ACTIVE":
        coupons_qs = coupons_qs.filter(
            is_active=True,
            valid_from__lte=now,
        ).filter(Q(valid_until__isnull=True) | Q(valid_until__gte=now))
    elif state == "EXPIRED":
        coupons_qs = coupons_qs.filter(Q(valid_until__lt=now) | Q(is_active=False))
    elif state == "SCHEDULED":
        coupons_qs = coupons_qs.filter(valid_from__gt=now)

    paginator = Paginator(coupons_qs, 15)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        **_base_superadmin_context(),
        "page_obj": page_obj,
        "coupons": page_obj.object_list,
        "q": q,
        "state_filter": state,
        "summary": {
            "total": PromoCode.objects.count(),
            "active": PromoCode.objects.filter(is_active=True).count(),
            "total_uses": PromoCode.objects.aggregate(total=Sum("uses_count"))["total"] or 0,
            "expiring_soon": PromoCode.objects.filter(
                is_active=True,
                valid_until__isnull=False,
                valid_until__gte=now,
                valid_until__lte=now + timezone.timedelta(days=7),
            ).count(),
        },
    }
    return render(request, "superadmin/admin/coupon_codes.html", context)


@login_required
@admin_required
def admin_payout_history(request):
    payouts = PayoutRecord.objects.select_related("venue", "venue__partner").all()

    status = request.GET.get("status", "").strip()
    venue_id = request.GET.get("venue", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    if status:
        payouts = payouts.filter(status=status)
    if venue_id:
        payouts = payouts.filter(venue_id=venue_id)
    if date_from:
        payouts = payouts.filter(created_at__date__gte=date_from)
    if date_to:
        payouts = payouts.filter(created_at__date__lte=date_to)

    paginator = Paginator(payouts.order_by("-created_at"), 12)
    page_obj = paginator.get_page(request.GET.get("page"))

    history_stats = payouts.aggregate(
        total_batches=Count("id"),
        total_paid=Sum("total_net", filter=Q(status=PayoutRecord.Status.PAID)),
        total_pending=Sum(
            "total_net",
            filter=Q(
                status__in=[
                    PayoutRecord.Status.PENDING,
                    PayoutRecord.Status.APPROVED,
                    PayoutRecord.Status.FAILED,
                ]
            ),
        ),
    )

    context = {
        **_base_superadmin_context(),
        "page_obj": page_obj,
        "payouts": page_obj.object_list,
        "history_stats": history_stats,
        "status_filter": status,
        "venue_filter": venue_id,
        "date_from": date_from,
        "date_to": date_to,
        "venues": (
            PayoutRecord.objects.values("venue_id", "venue__name")
            .order_by("venue__name")
            .distinct()
        ),
        "status_choices": PayoutRecord.Status.choices,
    }
    return render(request, "superadmin/admin/history.html", context)


@login_required
@admin_required
def admin_payout_detail(request, payout_uuid):
    payout = get_object_or_404(
        PayoutRecord.objects.select_related(
            "venue",
            "venue__partner",
            "approved_by",
            "paid_by",
        ),
        payout_id=payout_uuid,
    )

    sales_qs = (
        payout.sales_records.select_related("booking", "booking__user", "venue")
        .order_by("-checked_in_at", "-created_at")
    )
    records_paginator = Paginator(sales_qs, 25)
    records_page = records_paginator.get_page(request.GET.get("records_page"))

    context = {
        **_base_superadmin_context(),
        "payout": payout,
        "sales_records": records_page,
        "records_page_obj": records_page,
        "approval_form": PayoutApprovalForm(),
        "payment_form": PayoutPaymentForm(),
    }
    return render(request, "superadmin/admin/detail.html", context)


@login_required
@admin_required
@require_POST
def approve_payout(request, payout_uuid):
    payout = get_object_or_404(PayoutRecord, payout_id=payout_uuid)

    form = PayoutApprovalForm(request.POST)
    if form.is_valid():
        try:
            payout.approve(request.user, form.cleaned_data.get("admin_notes", ""))
            messages.success(request, f"{payout.reference} approved successfully.")
        except ValueError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "Approval form is invalid.")

    return redirect("superadmin:detail", payout_uuid=payout_uuid)


@login_required
@admin_required
@require_POST
def mark_payout_paid(request, payout_uuid):
    payout = get_object_or_404(PayoutRecord, payout_id=payout_uuid)

    form = PayoutPaymentForm(request.POST)
    if form.is_valid():
        try:
            payout.mark_paid(
                request.user,
                transfer_reference=form.cleaned_data["transfer_reference"],
                notes=form.cleaned_data.get("transfer_notes", ""),
            )
            notify_venue_payout_completed(payout, request=request)
            messages.success(request, f"{payout.reference} marked as paid.")
        except ValueError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "Transfer reference is required.")

    return redirect("superadmin:detail", payout_uuid=payout_uuid)


@login_required
@admin_required
@require_POST
def fail_payout(request, payout_uuid):
    payout = get_object_or_404(PayoutRecord, payout_id=payout_uuid)

    try:
        payout.mark_failed(notes=request.POST.get("admin_notes", "").strip())
        messages.warning(request, f"{payout.reference} marked as failed.")
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("superadmin:detail", payout_uuid=payout_uuid)


@login_required
@admin_required
@require_POST
def cancel_payout(request, payout_uuid):
    payout = get_object_or_404(PayoutRecord, payout_id=payout_uuid)

    try:
        payout.cancel(notes=request.POST.get("admin_notes", "").strip())
        messages.warning(
            request,
            f"{payout.reference} cancelled and linked sales records released back to the unpaid pool.",
        )
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("superadmin:detail", payout_uuid=payout_uuid)
