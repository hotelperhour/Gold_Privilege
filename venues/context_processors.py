# venues/context_processors.py
from django.conf import settings
from django.db.models import Q, Sum


def mapbox_settings(request):
    return {
        'MAPBOX_ACCESS_TOKEN': settings.MAPBOX_ACCESS_TOKEN,
        'DEFAULT_MAP_CENTER': [3.3792, 6.5244],  # Lagos coordinates
        'DEFAULT_MAP_ZOOM': 11,
    }

def partner_payout_context(request):
    if not request.user.is_authenticated or not getattr(request.user, 'is_partner', False):
        return {'partner_pending_payout_balance': None}

    try:
        partner_profile = request.user.partner_profile
    except Exception:
        return {'partner_pending_payout_balance': None}

    from superadmin.models import SalesRecord, PayoutRecord

    pending_balance = (
        SalesRecord.objects.filter(venue__partner=partner_profile)
        .filter(
            Q(payout_record__isnull=True) |
            Q(
                payout_record__status__in=[
                    PayoutRecord.Status.PENDING,
                    PayoutRecord.Status.APPROVED,
                    PayoutRecord.Status.FAILED,
                ]
            )
        )
        .aggregate(total=Sum("net_amount"))["total"] or 0
    )

    return {'partner_pending_payout_balance': pending_balance}
