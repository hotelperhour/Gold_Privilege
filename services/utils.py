"""
services/utils.py

QUOTA TYPES:
  Airtime  → naira value tracked in ServiceQuotaUsage.amount_used
  Data     → GB tracked in ServiceQuotaUsage.data_gb_used
  Vouchers → count tracked in ServiceQuotaUsage.count_used

check_service_quota() returns 6 values:
  (allowed, remaining, quota_msg, plan_quota, min_limit, max_limit)
  - For airtime:  min/max are Decimal naira
  - For data:     min/max are Decimal GB
  - For vouchers: min/max are None

API: Reloadly (sandbox + live)
Settings needed:
  RELOADLY_CLIENT_ID
  RELOADLY_CLIENT_SECRET
  RELOADLY_SANDBOX = True/False
"""

import logging
import requests
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.conf import settings

from .models import (
    Service, DeliveryType, ServiceCategory,
    ServicePlanQuota, ServiceQuotaUsage,
    ServicePurchase, VoucherInventory,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# RELOADLY API
# ─────────────────────────────────────────────────────

class ReloadlyAPI:
    """
    Wrapper around Reloadly Airtime + Data API.

    Sandbox:  RELOADLY_SANDBOX = True
    Live:     RELOADLY_SANDBOX = False

    Nigerian operator IDs (default, override via RELOADLY_OPERATOR_IDS in settings):
      MTN Nigeria    → 341
      Glo Nigeria    → 342
      Airtel Nigeria → 343
      9mobile        → 344
    """

    AUTH_URL         = "https://auth.reloadly.com/oauth/token"
    SANDBOX_BASE     = "https://topups-sandbox.reloadly.com"
    SANDBOX_AUDIENCE = "https://topups-sandbox.reloadly.com"
    LIVE_BASE        = "https://topups.reloadly.com"
    LIVE_AUDIENCE    = "https://topups.reloadly.com"

    DEFAULT_OPERATOR_IDS = {
        'mtn':      341,
        'glo':      342,
        'airtel':   343,
        'etisalat': 344,
    }

    def __init__(self):
        self.client_id     = getattr(settings, 'RELOADLY_CLIENT_ID', '')
        self.client_secret = getattr(settings, 'RELOADLY_CLIENT_SECRET', '')
        self.sandbox       = getattr(settings, 'RELOADLY_SANDBOX', True)
        self.base_url      = self.SANDBOX_BASE if self.sandbox else self.LIVE_BASE
        self.audience      = self.SANDBOX_AUDIENCE if self.sandbox else self.LIVE_AUDIENCE
        self.operator_ids  = getattr(settings, 'RELOADLY_OPERATOR_IDS', self.DEFAULT_OPERATOR_IDS)
        self._access_token = None

    def get_access_token(self):
        """Fetch bearer token. Test tokens expire in 24h, live in 60 days."""
        try:
            resp = requests.post(
                self.AUTH_URL,
                json={
                    "client_id":     self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type":    "client_credentials",
                    "audience":      self.audience,
                },
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            data  = resp.json()
            token = data.get("access_token")
            if not token:
                logger.error("Reloadly auth failed: %s", data)
                return None
            self._access_token = token
            return token
        except Exception as exc:
            logger.error("Reloadly auth exception: %s", exc, exc_info=True)
            return None

    def _headers(self):
        if not self._access_token:
            self.get_access_token()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type":  "application/json",
            "Accept":        "application/com.reloadly.topups-v1+json",
        }

    def buy_airtime(self, network: str, phone: str, amount: Decimal,
                    purchase_reference: str, country_code: str = "NG"):
        """
        Send airtime. amount is in naira (local currency).
        useLocalAmount=True tells Reloadly the amount is in the operator's local currency.
        """
        operator_id = self.operator_ids.get(network.lower())
        if not operator_id:
            return False, {'error': f'Unknown network: {network}'}

        payload = {
            "operatorId":       operator_id,
            "amount":           float(amount),
            "useLocalAmount":   True,
            "customIdentifier": purchase_reference,
            "recipientPhone": {
                "countryCode": country_code,
                "number":      self._to_international(phone, country_code),
            },
            "senderPhone": {
                "countryCode": "NG",
                "number":      "2340000000000",
            },
        }
        return self._post("/topups", payload)

    def get_data_bundles(self, network: str):
        """
        Fetch available data bundle plans for a network from Reloadly.
        Returns list of bundles with their variation codes and prices.
        """
        operator_id = self.operator_ids.get(network.lower())
        if not operator_id:
            return False, {'error': f'Unknown network: {network}'}

        if not self._access_token:
            self.get_access_token()
        try:
            resp = requests.get(
                f"{self.base_url}/operators/{operator_id}/bundles",
                headers=self._headers(),
                timeout=15,
            )
            data = resp.json()
            return True, data
        except Exception as exc:
            logger.error("Reloadly get_data_bundles error: %s", exc, exc_info=True)
            return False, {'error': str(exc)}

    def buy_data(self, network: str, phone: str, variation_code: str,
                 amount: Decimal, data_gb: Decimal,
                 purchase_reference: str, country_code: str = "NG"):
        """
        Purchase a data bundle via Reloadly.
        variation_code = Reloadly bundle ID for the specific GB package.
        amount = naira cost of that bundle.
        """
        operator_id = self.operator_ids.get(network.lower())
        if not operator_id:
            return False, {'error': f'Unknown network: {network}'}

        payload = {
            "operatorId":       operator_id,
            "amount":           float(amount),
            "useLocalAmount":   True,
            "customIdentifier": purchase_reference,
            "recipientPhone": {
                "countryCode": country_code,
                "number":      self._to_international(phone, country_code),
            },
            "senderPhone": {
                "countryCode": "NG",
                "number":      "2340000000000",
            },
        }
        return self._post("/topups", payload)

    def verify_transaction(self, transaction_id: int):
        if not self._access_token:
            self.get_access_token()
        try:
            resp = requests.get(
                f"{self.base_url}/topups/{transaction_id}/status",
                headers=self._headers(),
                timeout=15,
            )
            data = resp.json()
            return data.get("status") == "SUCCESSFUL", data
        except Exception as exc:
            logger.error("Reloadly verify error: %s", exc, exc_info=True)
            return False, {'error': str(exc)}

    def _post(self, endpoint: str, payload: dict):
        try:
            resp = requests.post(
                f"{self.base_url}{endpoint}",
                json=payload,
                headers=self._headers(),
                timeout=30,
            )
            data    = resp.json()
            success = data.get("status") == "SUCCESSFUL"
            logger.info("Reloadly %s → status=%s", endpoint, data.get("status"))
            return success, data
        except requests.Timeout:
            logger.error("Reloadly timeout on %s", endpoint)
            return False, {'error': 'Reloadly request timed out. Please retry.'}
        except Exception as exc:
            logger.error("Reloadly error on %s: %s", endpoint, exc, exc_info=True)
            return False, {'error': str(exc)}

    @staticmethod
    def _to_international(phone: str, country_code: str = "NG") -> str:
        phone = phone.strip().replace(" ", "").replace("-", "")
        if phone.startswith("0") and country_code == "NG":
            return "234" + phone[1:]
        if phone.startswith("+"):
            return phone[1:]
        return phone


# ─────────────────────────────────────────────────────
# QUOTA CHECK
# ─────────────────────────────────────────────────────

def check_service_quota(user, service, subscription):
    """
    Check quota and return per-transaction limits.

    Returns:
        (allowed, remaining, quota_msg, plan_quota, min_limit, max_limit)

    Units:
        AIRTIME  → remaining = Decimal naira,  min/max = Decimal naira
        DATA     → remaining = Decimal GB,     min/max = Decimal GB
        VOUCHERS → remaining = int count,      min/max = None
    """
    try:
        plan_quota = ServicePlanQuota.objects.get(
            plan=subscription.plan, service=service
        )
    except ServicePlanQuota.DoesNotExist:
        return (
            False, 0,
            f"'{service.name}' is not available on your {subscription.plan.name} plan.",
            None, None, None
        )

    now = timezone.now()
    cat = service.category

    # ── Vouchers: count-based ──────────────────────────────────────
    if service.delivery_type == DeliveryType.MANUAL_CODE:
        if plan_quota.monthly_voucher_count is None:
            return True, None, "Unlimited vouchers", plan_quota, None, None

        usage     = _get_usage(user, service, now)
        used      = usage.count_used if usage else 0
        remaining = plan_quota.monthly_voucher_count - used

        if remaining <= 0:
            return (
                False, 0,
                f"You've used all {plan_quota.monthly_voucher_count} "
                f"{service.name} voucher(s) for this month.",
                plan_quota, None, None
            )
        return (True, remaining, f"{remaining} voucher(s) remaining this month.",
                plan_quota, None, None)

    # ── Data: GB-based ─────────────────────────────────────────────
    if cat == ServiceCategory.DATA:
        if plan_quota.monthly_data_gb is None:
            return (True, None, "Unlimited data", plan_quota,
                    service.min_data_gb, service.max_data_gb)

        usage     = _get_usage(user, service, now)
        used_gb   = usage.data_gb_used if usage else Decimal('0')
        remaining = plan_quota.monthly_data_gb - used_gb

        if remaining <= 0:
            return (
                False, Decimal('0'),
                f"You've used your full {plan_quota.monthly_data_gb} GB "
                f"data allowance for this month.",
                plan_quota, service.min_data_gb, service.max_data_gb
            )

        svc_max_gb     = service.max_data_gb
        effective_max  = min(svc_max_gb, remaining) if svc_max_gb else remaining

        return (
            True, remaining,
            f"{remaining:.2f} GB remaining this month.",
            plan_quota, service.min_data_gb, effective_max
        )

    # ── Airtime: naira-based ───────────────────────────────────────
    if plan_quota.monthly_allowance is None:
        return (True, None, "Unlimited access", plan_quota,
                service.min_transaction_amount, service.max_transaction_amount)

    usage     = _get_usage(user, service, now)
    spent     = usage.amount_used if usage else Decimal('0')
    remaining = plan_quota.monthly_allowance - spent

    if remaining <= 0:
        return (
            False, Decimal('0'),
            f"You've used your full ₦{plan_quota.monthly_allowance:,.0f} "
            f"{service.name} allowance for this month.",
            plan_quota, service.min_transaction_amount, service.max_transaction_amount
        )

    svc_max       = service.max_transaction_amount
    effective_max = min(svc_max, remaining) if svc_max else remaining

    return (
        True, remaining,
        f"₦{remaining:,.0f} remaining this month.",
        plan_quota, service.min_transaction_amount, effective_max
    )


def _get_usage(user, service, now):
    return ServiceQuotaUsage.objects.filter(
        user=user, service=service,
        period_year=now.year, period_month=now.month,
    ).first()


def get_all_service_quotas(user, subscription):
    """Quota summary for every service on the user's plan (for the home page)."""
    now     = timezone.now()
    results = []

    for plan_quota in ServicePlanQuota.objects.filter(
        plan=subscription.plan, service__is_active=True
    ).select_related('service'):
        service = plan_quota.service
        usage   = _get_usage(user, service, now)
        cat     = service.category

        if service.delivery_type == DeliveryType.MANUAL_CODE:
            used      = usage.count_used if usage else 0
            unlimited = plan_quota.monthly_voucher_count is None
            limit     = plan_quota.monthly_voucher_count
            remaining = None if unlimited else max(0, limit - used)
            percentage    = int((used / limit) * 100) if limit else 0
            display_used  = str(used)
            display_limit = str(limit) if limit else '∞'
            unit          = 'vouchers'

        elif cat == ServiceCategory.DATA:
            used_gb   = usage.data_gb_used if usage else Decimal('0')
            unlimited = plan_quota.monthly_data_gb is None
            limit     = plan_quota.monthly_data_gb
            remaining = None if unlimited else max(Decimal('0'), limit - used_gb)
            percentage    = int((used_gb / limit) * 100) if limit else 0
            display_used  = f"{used_gb:.1f} GB"
            display_limit = f"{limit:.1f} GB" if limit else '∞'
            unit          = 'GB'
            used          = used_gb

        else:  # AIRTIME
            spent     = usage.amount_used if usage else Decimal('0')
            unlimited = plan_quota.monthly_allowance is None
            limit     = plan_quota.monthly_allowance
            remaining = None if unlimited else max(Decimal('0'), limit - spent)
            percentage    = int((spent / limit) * 100) if limit else 0
            display_used  = f"₦{spent:,.0f}"
            display_limit = f"₦{limit:,.0f}" if limit else '∞'
            unit          = '₦'
            used          = spent

        results.append({
            'service':       service,
            'plan_quota':    plan_quota,
            'used':          used,
            'limit':         limit,
            'remaining':     remaining,
            'unlimited':     unlimited,
            'percentage':    min(percentage, 100),
            'unit':          unit,
            'display_used':  display_used,
            'display_limit': display_limit,
            'min_limit':     service.min_data_gb if cat == ServiceCategory.DATA else service.min_transaction_amount,
            'max_limit':     service.max_data_gb if cat == ServiceCategory.DATA else service.max_transaction_amount,
        })

    return results


# ─────────────────────────────────────────────────────
# ATOMIC DEDUCTION
# ─────────────────────────────────────────────────────

def deduct_quota_atomic(user, service, subscription, amount):
    """
    Atomically deduct quota.
    amount = naira for airtime, GB (Decimal) for data, ignored (1) for vouchers.
    """
    try:
        with transaction.atomic():
            try:
                plan_quota = ServicePlanQuota.objects.get(
                    plan=subscription.plan, service=service
                )
            except ServicePlanQuota.DoesNotExist:
                return False, f"'{service.name}' is not on your plan."

            now = timezone.now()
            defaults = {
                'subscription': subscription,
                'amount_used':  Decimal('0'),
                'data_gb_used': Decimal('0'),
                'count_used':   0,
            }
            cat = service.category

            if service.delivery_type == DeliveryType.MANUAL_CODE:
                if plan_quota.monthly_voucher_count is None:
                    return True, "Quota deducted (unlimited)."
                usage, _ = ServiceQuotaUsage.objects.select_for_update().get_or_create(
                    user=user, service=service,
                    period_year=now.year, period_month=now.month,
                    defaults=defaults,
                )
                if usage.count_used >= plan_quota.monthly_voucher_count:
                    return False, f"Monthly voucher quota exhausted for {service.name}."
                usage.count_used  += 1
                usage.last_used_at = now
                usage.save(update_fields=['count_used', 'last_used_at', 'updated_at'])

            elif cat == ServiceCategory.DATA:
                if plan_quota.monthly_data_gb is None:
                    return True, "Quota deducted (unlimited)."
                usage, _ = ServiceQuotaUsage.objects.select_for_update().get_or_create(
                    user=user, service=service,
                    period_year=now.year, period_month=now.month,
                    defaults=defaults,
                )
                if usage.data_gb_used + amount > plan_quota.monthly_data_gb:
                    remaining = plan_quota.monthly_data_gb - usage.data_gb_used
                    return (
                        False,
                        f"This bundle of {amount} GB would exceed your monthly data. "
                        f"You have {remaining:.2f} GB remaining."
                    )
                usage.data_gb_used += amount
                usage.last_used_at  = now
                usage.save(update_fields=['data_gb_used', 'last_used_at', 'updated_at'])

            else:  # AIRTIME
                if plan_quota.monthly_allowance is None:
                    return True, "Quota deducted (unlimited)."
                usage, _ = ServiceQuotaUsage.objects.select_for_update().get_or_create(
                    user=user, service=service,
                    period_year=now.year, period_month=now.month,
                    defaults=defaults,
                )
                if usage.amount_used + amount > plan_quota.monthly_allowance:
                    remaining = plan_quota.monthly_allowance - usage.amount_used
                    return (
                        False,
                        f"This top-up of ₦{amount:,.0f} would exceed your monthly allowance. "
                        f"You have ₦{remaining:,.0f} remaining."
                    )
                usage.amount_used  += amount
                usage.last_used_at  = now
                usage.save(update_fields=['amount_used', 'last_used_at', 'updated_at'])

        return True, "Quota deducted."

    except Exception as exc:
        logger.error("deduct_quota_atomic failed user=%s service=%s: %s",
                     user.email, service.name, exc, exc_info=True)
        return False, "An error occurred while processing your request. Please try again."


def refund_quota_atomic(user, service, subscription, amount):
    """Refund quota on delivery failure. amount = naira for airtime, GB for data."""
    try:
        with transaction.atomic():
            now   = timezone.now()
            usage = ServiceQuotaUsage.objects.select_for_update().filter(
                user=user, service=service,
                period_year=now.year, period_month=now.month,
            ).first()
            if not usage:
                return

            cat = service.category
            if service.delivery_type == DeliveryType.MANUAL_CODE:
                usage.count_used = max(0, usage.count_used - 1)
                usage.save(update_fields=['count_used', 'updated_at'])
            elif cat == ServiceCategory.DATA:
                usage.data_gb_used = max(Decimal('0'), usage.data_gb_used - amount)
                usage.save(update_fields=['data_gb_used', 'updated_at'])
            else:
                usage.amount_used = max(Decimal('0'), usage.amount_used - amount)
                usage.save(update_fields=['amount_used', 'updated_at'])

    except Exception as exc:
        logger.error("refund_quota_atomic failed user=%s service=%s: %s",
                     user.email, service.name, exc, exc_info=True)


# ─────────────────────────────────────────────────────
# VOUCHER ASSIGNMENT
# ─────────────────────────────────────────────────────

def assign_voucher_atomic(user, service, amount):
    """Atomically assign one available voucher. skip_locked=True prevents deadlocks."""
    try:
        with transaction.atomic():
            voucher = (
                VoucherInventory.objects
                .select_for_update(skip_locked=True)
                .filter(service=service, status=VoucherInventory.VoucherStatus.AVAILABLE, amount=amount)
                .exclude(expires_at__lt=timezone.now().date())
                .first()
            )
            if not voucher:
                return None, (
                    f"No {service.name} vouchers of ₦{amount:,.0f} are available right now. "
                    "Please contact support or try again later."
                )
            voucher.status      = VoucherInventory.VoucherStatus.ASSIGNED
            voucher.assigned_to = user
            voucher.assigned_at = timezone.now()
            voucher.save(update_fields=['status', 'assigned_to', 'assigned_at'])
        return voucher, None
    except Exception as exc:
        logger.error("assign_voucher_atomic failed user=%s service=%s: %s",
                     user.email, service.name, exc, exc_info=True)
        return None, "An error occurred assigning your voucher. Please try again."


# ─────────────────────────────────────────────────────
# MAIN ORCHESTRATOR
# ─────────────────────────────────────────────────────

def process_service_request(user, service, subscription, amount,
                             phone=None, network=None,
                             variation_code=None, data_gb=None):
    """
    Full pipeline:
      1. Active subscription check
      2. Pre-flight quota check
      3. Per-transaction limit validation
      4. Atomic quota deduction
      5. Deliver (Reloadly or voucher)
      6. On failure → refund + mark FAILED

    For data: amount = naira cost, data_gb = Decimal GB of the bundle.
    The quota deduction uses data_gb (GB), not naira.
    """
    if subscription.status not in ('ACTIVE', 'TRIAL'):
        return None, "Your subscription is not active."

    allowed, remaining, msg, plan_quota, min_limit, max_limit = check_service_quota(
        user, service, subscription
    )
    if not allowed:
        return None, msg

    cat = service.category

    # Per-transaction limit validation
    # For data: amount = GB to buy; for airtime: amount = naira
    check_value = data_gb if cat == ServiceCategory.DATA else amount
    unit_label  = 'GB' if cat == ServiceCategory.DATA else '₦'

    if min_limit and check_value < min_limit:
        return None, f"Minimum is {min_limit} {unit_label} per transaction."

    if max_limit and check_value > max_limit:
        if remaining is not None and check_value > remaining:
            return None, (
                f"This would exceed your monthly balance. "
                f"You have {remaining} {unit_label} remaining."
            )
        return None, f"Maximum is {max_limit} {unit_label} per transaction."

    # Deduct quota (GB for data, naira for airtime, 1 for vouchers)
    deduct_amount = data_gb if cat == ServiceCategory.DATA else amount
    ok, err = deduct_quota_atomic(user, service, subscription, deduct_amount)
    if not ok:
        return None, err

    purchase = ServicePurchase.objects.create(
        user             = user,
        service          = service,
        subscription     = subscription,
        amount           = amount,
        data_gb          = data_gb,
        variation_code   = variation_code or '',
        recipient_phone  = phone or '',
        network_provider = network or '',
        status           = ServicePurchase.PurchaseStatus.PROCESSING,
        used_quota       = True,
    )

    if service.delivery_type == DeliveryType.API_INSTANT:
        api = ReloadlyAPI()

        if cat == ServiceCategory.AIRTIME:
            success, resp = api.buy_airtime(
                network=network, phone=phone,
                amount=amount, purchase_reference=purchase.reference,
            )
        # elif cat == ServiceCategory.DATA:
        #     success, resp = api.buy_data(
        #         network=network, phone=phone,
        #         variation_code=variation_code or '',
        #         amount=amount, data_gb=data_gb,
        #         purchase_reference=purchase.reference,
        #     )
        else:
            resp, success = {'error': 'Unknown API service category.'}, False

        if success:
            purchase.status             = ServicePurchase.PurchaseStatus.DELIVERED
            purchase.api_response       = resp
            purchase.api_transaction_id = str(resp.get('transactionId', ''))
            purchase.delivered_at       = timezone.now()
            purchase.save()
            return purchase, None
        else:
            purchase.status       = ServicePurchase.PurchaseStatus.FAILED
            purchase.api_response = resp
            purchase.save()
            refund_quota_atomic(user, service, subscription, deduct_amount)
            return None, resp.get('error', 'Delivery failed. Your quota has been refunded.')

    elif service.delivery_type == DeliveryType.MANUAL_CODE:
        voucher, err = assign_voucher_atomic(user, service, amount)
        if voucher:
            purchase.voucher      = voucher
            purchase.status       = ServicePurchase.PurchaseStatus.DELIVERED
            purchase.delivered_at = timezone.now()
            purchase.save()
            return purchase, None
        else:
            purchase.status = ServicePurchase.PurchaseStatus.FAILED
            purchase.save()
            refund_quota_atomic(user, service, subscription, deduct_amount)
            return None, err

    else:
        purchase.status = ServicePurchase.PurchaseStatus.FAILED
        purchase.save()
        refund_quota_atomic(user, service, subscription, deduct_amount)
        return None, "Unsupported delivery type."