"""
services/tests.py

Run with:
    python manage.py test services -v 2
    python manage.py test services.tests.ServiceModelTest   (single class)

Fixes vs original:
  1. Added IS_SQLITE detection + @skipIf on RaceConditionTest (SQLite
     cannot handle concurrent writers — same issue as wallet tests).
  2. ProcessDataRequestTest.test_data_success_deducts_gb is skipped
     because data API delivery is currently disabled in process_service_request
     (the elif cat == ServiceCategory.DATA branch is commented out).
     All other data tests (quota logic, pre-flight validation, GB deduction)
     still run — they don't touch the API delivery path.
"""

from decimal import Decimal
from datetime import date
import threading
import uuid
from unittest import skipIf
from unittest.mock import patch

from django.test import TestCase, Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db import connection

from services.models import (
    Service, ServiceCategory, DeliveryType, NetworkProvider,
    ServicePlanQuota, VoucherInventory, ServicePurchase, ServiceQuotaUsage
)
from services.utils import (
    check_service_quota, deduct_quota_atomic, refund_quota_atomic,
    assign_voucher_atomic, get_all_service_quotas, process_service_request
)
from subscriptions.models import SubscriptionPlan, Subscription

User = get_user_model()

# FIX 1: SQLite detection — same pattern as wallet tests
IS_SQLITE = connection.vendor == 'sqlite'


# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────

def make_user(email=None, password='testpass123'):
    email = email or f"user_{uuid.uuid4().hex[:6]}@test.com"
    return User.objects.create_user(
        email=email, password=password, user_type='SUBSCRIBER'
    )


def make_plan(name=None, price=10000):
    name = name or f'Plan-{uuid.uuid4().hex[:4]}'
    return SubscriptionPlan.objects.create(
        name=name,
        slug=f'plan-{uuid.uuid4().hex[:6]}',
        description='Test plan',
        price=price,
        billing_period='MONTHLY',
    )


def make_subscription(user, plan, status='ACTIVE'):
    now = timezone.now()
    return Subscription.objects.create(
        user=user, plan=plan,
        start_date=now,
        end_date=now + timezone.timedelta(days=30),
        status=status,
        price_paid=plan.price,
    )


def make_airtime_service(name='MTN Airtime'):
    return Service.objects.create(
        name=name,
        category=ServiceCategory.AIRTIME,
        delivery_type=DeliveryType.API_INSTANT,
        min_transaction_amount=Decimal('100'),
        max_transaction_amount=Decimal('5000'),
        is_active=True,
    )


def make_data_service(name='MTN Data'):
    return Service.objects.create(
        name=name,
        category=ServiceCategory.DATA,
        delivery_type=DeliveryType.API_INSTANT,
        min_data_gb=Decimal('0.5'),
        max_data_gb=Decimal('5.0'),
        is_active=True,
    )


def make_voucher_service(name='Uber Ride'):
    return Service.objects.create(
        name=name,
        category=ServiceCategory.RIDE_VOUCHER,
        delivery_type=DeliveryType.MANUAL_CODE,
        fixed_amounts=[5000, 10000],
        has_inventory=True,
        is_active=True,
    )


def make_voucher(service, amount=5000, status='AVAILABLE'):
    return VoucherInventory.objects.create(
        service=service,
        voucher_code=f"CODE-{uuid.uuid4().hex[:8].upper()}",
        amount=Decimal(str(amount)),
        cost_price=Decimal('4000'),
        status=status,
    )


def make_airtime_quota(plan, service, allowance=Decimal('10000')):
    return ServicePlanQuota.objects.create(
        plan=plan, service=service,
        monthly_allowance=allowance,
    )


def make_data_quota(plan, service, gb=Decimal('5.0')):
    return ServicePlanQuota.objects.create(
        plan=plan, service=service,
        monthly_data_gb=gb,
    )


def make_voucher_quota(plan, service, count=2):
    return ServicePlanQuota.objects.create(
        plan=plan, service=service,
        monthly_voucher_count=count,
    )


# ──────────────────────────────────────────────
# 1. MODEL TESTS
# ──────────────────────────────────────────────

class ServiceModelTest(TestCase):

    def test_service_str(self):
        s = make_airtime_service()
        self.assertIn('MTN Airtime', str(s))

    def test_get_icon_airtime(self):
        s = make_airtime_service()
        self.assertEqual(s.get_icon(), 'fa-mobile-alt')

    def test_get_icon_data(self):
        s = make_data_service()
        self.assertEqual(s.get_icon(), 'fa-wifi')

    def test_get_icon_custom(self):
        s = Service.objects.create(
            name='Custom', category='OTHER',
            delivery_type='API_INSTANT', icon='fa-star', is_active=True
        )
        self.assertEqual(s.get_icon(), 'fa-star')

    def test_get_unit_airtime(self):
        self.assertEqual(make_airtime_service().get_unit(), '₦')

    def test_get_unit_data(self):
        self.assertEqual(make_data_service().get_unit(), 'GB')

    def test_get_unit_voucher(self):
        self.assertEqual(make_voucher_service().get_unit(), 'vouchers')

    def test_plan_quota_str_airtime(self):
        plan = make_plan()
        svc  = make_airtime_service()
        q    = make_airtime_quota(plan, svc, Decimal('10000'))
        self.assertIn('₦10,000', str(q))

    def test_plan_quota_str_data(self):
        plan = make_plan()
        svc  = make_data_service()
        q    = make_data_quota(plan, svc, Decimal('5.0'))
        self.assertIn('5.0 GB', str(q))

    def test_plan_quota_unlimited_airtime(self):
        plan = make_plan()
        svc  = make_airtime_service()
        q    = ServicePlanQuota.objects.create(plan=plan, service=svc, monthly_allowance=None)
        self.assertTrue(q.is_unlimited())

    def test_plan_quota_unlimited_data(self):
        plan = make_plan()
        svc  = make_data_service()
        q    = ServicePlanQuota.objects.create(plan=plan, service=svc, monthly_data_gb=None)
        self.assertTrue(q.is_unlimited())

    def test_purchase_reference_generated(self):
        user = make_user()
        plan = make_plan()
        sub  = make_subscription(user, plan)
        svc  = make_airtime_service()
        make_airtime_quota(plan, svc)
        p = ServicePurchase.objects.create(
            user=user, service=svc, subscription=sub,
            amount=Decimal('500'), status='DELIVERED',
        )
        self.assertTrue(p.reference.startswith('GP-SVC-'))

    def test_voucher_is_available(self):
        svc = make_voucher_service()
        v   = make_voucher(svc)
        self.assertTrue(v.is_available())

    def test_voucher_not_available_if_expired(self):
        svc = make_voucher_service()
        v   = VoucherInventory.objects.create(
            service=svc, voucher_code='EXP-001',
            amount=Decimal('5000'), cost_price=Decimal('4000'),
            status='AVAILABLE', expires_at=date(2000, 1, 1),
        )
        self.assertFalse(v.is_available())


# ──────────────────────────────────────────────
# 2. AIRTIME QUOTA CHECK TESTS
# ──────────────────────────────────────────────

class AirtimeQuotaCheckTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.plan = make_plan()
        self.sub  = make_subscription(self.user, self.plan)
        self.svc  = make_airtime_service()

    def test_no_quota_on_plan_returns_not_allowed(self):
        allowed, remaining, msg, quota, mn, mx = check_service_quota(
            self.user, self.svc, self.sub
        )
        self.assertFalse(allowed)
        self.assertIn('not available', msg)

    def test_allowance_available(self):
        make_airtime_quota(self.plan, self.svc, Decimal('10000'))
        allowed, remaining, msg, _, mn, mx = check_service_quota(
            self.user, self.svc, self.sub
        )
        self.assertTrue(allowed)
        self.assertEqual(remaining, Decimal('10000'))
        self.assertEqual(mn, Decimal('100'))
        self.assertEqual(mx, Decimal('5000'))

    def test_allowance_partially_used(self):
        make_airtime_quota(self.plan, self.svc, Decimal('10000'))
        now = timezone.now()
        ServiceQuotaUsage.objects.create(
            user=self.user, service=self.svc, subscription=self.sub,
            period_year=now.year, period_month=now.month,
            amount_used=Decimal('3000'),
        )
        allowed, remaining, msg, _, mn, mx = check_service_quota(
            self.user, self.svc, self.sub
        )
        self.assertTrue(allowed)
        self.assertEqual(remaining, Decimal('7000'))
        # remaining (7000) > service max (5000), so service max wins
        self.assertEqual(mx, Decimal('5000'))

    def test_effective_max_capped_at_remaining(self):
        """When remaining < service max_transaction_amount, effective max = remaining."""
        make_airtime_quota(self.plan, self.svc, Decimal('10000'))
        now = timezone.now()
        ServiceQuotaUsage.objects.create(
            user=self.user, service=self.svc, subscription=self.sub,
            period_year=now.year, period_month=now.month,
            amount_used=Decimal('9500'),   # only ₦500 left
        )
        allowed, remaining, msg, _, mn, mx = check_service_quota(
            self.user, self.svc, self.sub
        )
        self.assertTrue(allowed)
        self.assertEqual(remaining, Decimal('500'))
        self.assertEqual(mx, Decimal('500'))   # capped at ₦500, not ₦5000

    def test_allowance_exhausted(self):
        make_airtime_quota(self.plan, self.svc, Decimal('5000'))
        now = timezone.now()
        ServiceQuotaUsage.objects.create(
            user=self.user, service=self.svc, subscription=self.sub,
            period_year=now.year, period_month=now.month,
            amount_used=Decimal('5000'),
        )
        allowed, remaining, msg, _, mn, mx = check_service_quota(
            self.user, self.svc, self.sub
        )
        self.assertFalse(allowed)
        self.assertEqual(remaining, Decimal('0'))

    def test_unlimited_always_allowed(self):
        ServicePlanQuota.objects.create(
            plan=self.plan, service=self.svc, monthly_allowance=None
        )
        allowed, remaining, msg, quota, mn, mx = check_service_quota(
            self.user, self.svc, self.sub
        )
        self.assertTrue(allowed)
        self.assertIsNone(remaining)
        self.assertTrue(quota.is_unlimited())


# ──────────────────────────────────────────────
# 3. DATA QUOTA CHECK TESTS  (GB-based)
# These test quota logic — they do NOT call the delivery API.
# ──────────────────────────────────────────────

class DataQuotaCheckTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.plan = make_plan()
        self.sub  = make_subscription(self.user, self.plan)
        self.svc  = make_data_service()

    def test_data_quota_available(self):
        make_data_quota(self.plan, self.svc, Decimal('5.0'))
        allowed, remaining, msg, _, mn, mx = check_service_quota(
            self.user, self.svc, self.sub
        )
        self.assertTrue(allowed)
        self.assertEqual(remaining, Decimal('5.0'))
        self.assertIn('GB', msg)

    def test_data_quota_partially_used(self):
        make_data_quota(self.plan, self.svc, Decimal('5.0'))
        now = timezone.now()
        ServiceQuotaUsage.objects.create(
            user=self.user, service=self.svc, subscription=self.sub,
            period_year=now.year, period_month=now.month,
            data_gb_used=Decimal('2.0'),
        )
        allowed, remaining, msg, _, mn, mx = check_service_quota(
            self.user, self.svc, self.sub
        )
        self.assertTrue(allowed)
        self.assertEqual(remaining, Decimal('3.0'))

    def test_data_quota_exhausted(self):
        make_data_quota(self.plan, self.svc, Decimal('5.0'))
        now = timezone.now()
        ServiceQuotaUsage.objects.create(
            user=self.user, service=self.svc, subscription=self.sub,
            period_year=now.year, period_month=now.month,
            data_gb_used=Decimal('5.0'),
        )
        allowed, remaining, msg, _, mn, mx = check_service_quota(
            self.user, self.svc, self.sub
        )
        self.assertFalse(allowed)
        self.assertIn('GB', msg)

    def test_effective_max_gb_capped_at_remaining(self):
        make_data_quota(self.plan, self.svc, Decimal('5.0'))
        now = timezone.now()
        ServiceQuotaUsage.objects.create(
            user=self.user, service=self.svc, subscription=self.sub,
            period_year=now.year, period_month=now.month,
            data_gb_used=Decimal('4.7'),   # only 0.3 GB left
        )
        allowed, remaining, msg, _, mn, mx = check_service_quota(
            self.user, self.svc, self.sub
        )
        self.assertTrue(allowed)
        self.assertEqual(remaining, Decimal('0.3'))
        self.assertEqual(mx, Decimal('0.3'))   # capped, not the service max of 5.0


# ──────────────────────────────────────────────
# 4. VOUCHER QUOTA CHECK TESTS  (count-based)
# ──────────────────────────────────────────────

class VoucherQuotaCheckTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.plan = make_plan()
        self.sub  = make_subscription(self.user, self.plan)
        self.svc  = make_voucher_service()

    def test_voucher_quota_available(self):
        make_voucher_quota(self.plan, self.svc, count=2)
        allowed, remaining, msg, _, mn, mx = check_service_quota(
            self.user, self.svc, self.sub
        )
        self.assertTrue(allowed)
        self.assertEqual(remaining, 2)

    def test_voucher_quota_exhausted(self):
        make_voucher_quota(self.plan, self.svc, count=1)
        now = timezone.now()
        ServiceQuotaUsage.objects.create(
            user=self.user, service=self.svc, subscription=self.sub,
            period_year=now.year, period_month=now.month,
            count_used=1,
        )
        allowed, remaining, msg, _, mn, mx = check_service_quota(
            self.user, self.svc, self.sub
        )
        self.assertFalse(allowed)
        self.assertEqual(remaining, 0)


# ──────────────────────────────────────────────
# 5. ATOMIC DEDUCTION TESTS
# ──────────────────────────────────────────────

class AtomicDeductionTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.plan = make_plan()
        self.sub  = make_subscription(self.user, self.plan)

    def test_airtime_deduction_increments_amount_used(self):
        svc = make_airtime_service()
        make_airtime_quota(self.plan, svc, Decimal('10000'))
        ok, msg = deduct_quota_atomic(self.user, svc, self.sub, Decimal('500'))
        self.assertTrue(ok)
        now   = timezone.now()
        usage = ServiceQuotaUsage.objects.get(
            user=self.user, service=svc,
            period_year=now.year, period_month=now.month,
        )
        self.assertEqual(usage.amount_used, Decimal('500'))

    def test_data_deduction_increments_data_gb_used(self):
        svc = make_data_service()
        make_data_quota(self.plan, svc, Decimal('5.0'))
        ok, msg = deduct_quota_atomic(self.user, svc, self.sub, Decimal('1.0'))
        self.assertTrue(ok)
        now   = timezone.now()
        usage = ServiceQuotaUsage.objects.get(
            user=self.user, service=svc,
            period_year=now.year, period_month=now.month,
        )
        self.assertEqual(usage.data_gb_used, Decimal('1.0'))

    def test_voucher_deduction_increments_count_used(self):
        svc = make_voucher_service()
        make_voucher_quota(self.plan, svc, count=2)
        ok, msg = deduct_quota_atomic(self.user, svc, self.sub, Decimal('5000'))
        self.assertTrue(ok)
        now   = timezone.now()
        usage = ServiceQuotaUsage.objects.get(
            user=self.user, service=svc,
            period_year=now.year, period_month=now.month,
        )
        self.assertEqual(usage.count_used, 1)

    def test_airtime_blocked_when_would_exceed(self):
        svc = make_airtime_service()
        make_airtime_quota(self.plan, svc, Decimal('1000'))
        now = timezone.now()
        ServiceQuotaUsage.objects.create(
            user=self.user, service=svc, subscription=self.sub,
            period_year=now.year, period_month=now.month,
            amount_used=Decimal('900'),
        )
        ok, msg = deduct_quota_atomic(self.user, svc, self.sub, Decimal('500'))
        self.assertFalse(ok)
        self.assertIn('exceed', msg.lower())

    def test_data_blocked_when_would_exceed_gb(self):
        svc = make_data_service()
        make_data_quota(self.plan, svc, Decimal('2.0'))
        now = timezone.now()
        ServiceQuotaUsage.objects.create(
            user=self.user, service=svc, subscription=self.sub,
            period_year=now.year, period_month=now.month,
            data_gb_used=Decimal('1.8'),
        )
        ok, msg = deduct_quota_atomic(self.user, svc, self.sub, Decimal('1.0'))
        self.assertFalse(ok)
        self.assertIn('GB', msg)

    def test_airtime_refund_decrements_amount_used(self):
        svc = make_airtime_service()
        make_airtime_quota(self.plan, svc, Decimal('10000'))
        deduct_quota_atomic(self.user, svc, self.sub, Decimal('500'))
        refund_quota_atomic(self.user, svc, self.sub, Decimal('500'))
        now   = timezone.now()
        usage = ServiceQuotaUsage.objects.get(
            user=self.user, service=svc,
            period_year=now.year, period_month=now.month,
        )
        self.assertEqual(usage.amount_used, Decimal('0'))

    def test_data_refund_decrements_data_gb_used(self):
        svc = make_data_service()
        make_data_quota(self.plan, svc, Decimal('5.0'))
        deduct_quota_atomic(self.user, svc, self.sub, Decimal('2.0'))
        refund_quota_atomic(self.user, svc, self.sub, Decimal('2.0'))
        now   = timezone.now()
        usage = ServiceQuotaUsage.objects.get(
            user=self.user, service=svc,
            period_year=now.year, period_month=now.month,
        )
        self.assertEqual(usage.data_gb_used, Decimal('0'))


# ──────────────────────────────────────────────
# 6. RACE CONDITION TEST
# FIX 2: @skipIf on SQLite — same reason as wallet tests.
# Run on PostgreSQL to verify true concurrency protection.
# ──────────────────────────────────────────────

@skipIf(IS_SQLITE, 'SQLite cannot handle concurrent writers — run on PostgreSQL')
class RaceConditionTest(TransactionTestCase):
    """Two concurrent requests, only ₦500 allowance left — exactly one should win."""

    def setUp(self):
        self.user = make_user()
        self.plan = make_plan()
        self.sub  = make_subscription(self.user, self.plan)
        self.svc  = make_airtime_service()
        ServicePlanQuota.objects.create(
            plan=self.plan, service=self.svc,
            monthly_allowance=Decimal('500'),
        )

    def test_only_one_concurrent_request_succeeds(self):
        results = []

        def attempt():
            ok, _ = deduct_quota_atomic(self.user, self.svc, self.sub, Decimal('500'))
            results.append(ok)

        t1 = threading.Thread(target=attempt)
        t2 = threading.Thread(target=attempt)
        t1.start(); t2.start()
        t1.join();  t2.join()

        successes = sum(1 for r in results if r)
        self.assertEqual(successes, 1,
            f"Expected exactly 1 success, got {successes}. Results: {results}")


# ──────────────────────────────────────────────
# 7. VOUCHER ASSIGNMENT TESTS
# ──────────────────────────────────────────────

class VoucherAssignmentTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.svc  = make_voucher_service()

    def test_assigns_available_voucher(self):
        make_voucher(self.svc, amount=5000)
        voucher, err = assign_voucher_atomic(self.user, self.svc, Decimal('5000'))
        self.assertIsNotNone(voucher)
        self.assertIsNone(err)
        self.assertEqual(voucher.status, 'ASSIGNED')
        self.assertEqual(voucher.assigned_to, self.user)

    def test_returns_none_when_out_of_stock(self):
        voucher, err = assign_voucher_atomic(self.user, self.svc, Decimal('5000'))
        self.assertIsNone(voucher)
        self.assertIn('available', err.lower())

    def test_does_not_assign_expired_voucher(self):
        VoucherInventory.objects.create(
            service=self.svc, voucher_code='EXP-999',
            amount=Decimal('5000'), cost_price=Decimal('4000'),
            status='AVAILABLE', expires_at=date(2000, 1, 1),
        )
        voucher, err = assign_voucher_atomic(self.user, self.svc, Decimal('5000'))
        self.assertIsNone(voucher)

    def test_does_not_reassign_already_assigned_voucher(self):
        make_voucher(self.svc, amount=5000, status='ASSIGNED')
        voucher, _ = assign_voucher_atomic(self.user, self.svc, Decimal('5000'))
        self.assertIsNone(voucher)


# ──────────────────────────────────────────────
# 8. AIRTIME process_service_request TESTS
# ──────────────────────────────────────────────

class ProcessAirtimeRequestTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.plan = make_plan()
        self.sub  = make_subscription(self.user, self.plan)
        self.svc  = make_airtime_service()
        make_airtime_quota(self.plan, self.svc, Decimal('10000'))

    @patch('services.utils.ReloadlyAPI.buy_airtime')
    def test_airtime_success(self, mock_buy):
        mock_buy.return_value = (True, {'status': 'SUCCESSFUL', 'transactionId': 'RL-123'})
        purchase, err = process_service_request(
            user=self.user, service=self.svc, subscription=self.sub,
            amount=Decimal('500'), phone='08012345678', network='mtn',
        )
        self.assertIsNotNone(purchase)
        self.assertIsNone(err)
        self.assertEqual(purchase.status, 'DELIVERED')
        now   = timezone.now()
        usage = ServiceQuotaUsage.objects.get(
            user=self.user, service=self.svc,
            period_year=now.year, period_month=now.month,
        )
        self.assertEqual(usage.amount_used, Decimal('500'))

    @patch('services.utils.ReloadlyAPI.buy_airtime')
    def test_airtime_failure_refunds_quota(self, mock_buy):
        mock_buy.return_value = (False, {'error': 'Reloadly wallet low'})
        purchase, err = process_service_request(
            user=self.user, service=self.svc, subscription=self.sub,
            amount=Decimal('500'), phone='08012345678', network='mtn',
        )
        self.assertIsNone(purchase)
        self.assertIsNotNone(err)
        now   = timezone.now()
        usage = ServiceQuotaUsage.objects.filter(
            user=self.user, service=self.svc,
            period_year=now.year, period_month=now.month,
        ).first()
        if usage:
            self.assertEqual(usage.amount_used, Decimal('0'))

    def test_below_min_blocked(self):
        purchase, err = process_service_request(
            user=self.user, service=self.svc, subscription=self.sub,
            amount=Decimal('50'),   # below min of ₦100
            phone='08012345678', network='mtn',
        )
        self.assertIsNone(purchase)
        self.assertIn('Minimum', err)

    def test_above_max_blocked(self):
        purchase, err = process_service_request(
            user=self.user, service=self.svc, subscription=self.sub,
            amount=Decimal('6000'),   # above service max of ₦5000
            phone='08012345678', network='mtn',
        )
        self.assertIsNone(purchase)
        self.assertIn('Maximum', err)

    def test_inactive_subscription_blocked(self):
        self.sub.status = 'CANCELLED'
        self.sub.save()
        purchase, err = process_service_request(
            user=self.user, service=self.svc, subscription=self.sub,
            amount=Decimal('500'), phone='08012345678', network='mtn',
        )
        self.assertIsNone(purchase)
        self.assertIn('not active', err.lower())


# ──────────────────────────────────────────────
# 9. DATA process_service_request TESTS
#
# note: Data API delivery is currently disabled in process_service_request
# (the elif cat == ServiceCategory.DATA branch is commented out).
# test_data_success_deducts_gb is skipped until data topup is re-enabled.
# test_data_below_min_gb_blocked still runs — it tests pre-flight validation
# which happens before any API call.
# ──────────────────────────────────────────────

class ProcessDataRequestTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.plan = make_plan()
        self.sub  = make_subscription(self.user, self.plan)
        self.svc  = make_data_service()
        make_data_quota(self.plan, self.svc, Decimal('5.0'))

    @skipIf(True, 'Data API delivery is currently disabled in process_service_request — re-enable when data topup is live')
    @patch('services.utils.ReloadlyAPI.buy_data')
    def test_data_success_deducts_gb(self, mock_buy):
        mock_buy.return_value = (True, {'status': 'SUCCESSFUL', 'transactionId': 'RL-DATA-1'})
        purchase, err = process_service_request(
            user=self.user, service=self.svc, subscription=self.sub,
            amount=Decimal('1500'),
            data_gb=Decimal('1.0'),
            phone='08012345678', network='mtn',
            variation_code='mtn-1gb-1500',
        )
        self.assertIsNotNone(purchase)
        self.assertIsNone(err)
        self.assertEqual(purchase.status, 'DELIVERED')
        now   = timezone.now()
        usage = ServiceQuotaUsage.objects.get(
            user=self.user, service=self.svc,
            period_year=now.year, period_month=now.month,
        )
        self.assertEqual(usage.data_gb_used, Decimal('1.0'))

    def test_data_below_min_gb_blocked(self):
        """Pre-flight validation — does not reach the API."""
        purchase, err = process_service_request(
            user=self.user, service=self.svc, subscription=self.sub,
            amount=Decimal('200'), data_gb=Decimal('0.1'),   # below 0.5 GB min
            phone='08012345678', network='mtn',
        )
        self.assertIsNone(purchase)
        self.assertIn('Minimum', err)


# ──────────────────────────────────────────────
# 9. VIEW TESTS
# ──────────────────────────────────────────────

class ServicesViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user   = make_user(email='viewer@test.com')
        self.plan   = make_plan()
        self.sub    = make_subscription(self.user, self.plan)
        self.svc    = make_airtime_service()
        make_airtime_quota(self.plan, self.svc, Decimal('10000'))
        self.client.force_login(self.user)

    def test_home_redirects_unauthenticated(self):
        c    = Client()
        resp = c.get(reverse('services:home'))
        self.assertIn(resp.status_code, [301, 302])

    def test_home_renders_for_subscriber(self):
        resp = self.client.get(reverse('services:home'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.svc.name)

    def test_home_shows_no_subscription_page_when_cancelled(self):
        self.sub.status = 'CANCELLED'
        self.sub.save()
        resp = self.client.get(reverse('services:home'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Subscribe')

    def test_detail_get(self):
        resp = self.client.get(
            reverse('services:detail', kwargs={'service_id': self.svc.id})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.svc.name)

    def test_history_page_loads(self):
        resp = self.client.get(reverse('services:history'))
        self.assertEqual(resp.status_code, 200)

    def test_ajax_quota_check_airtime(self):
        resp = self.client.get(
            reverse('services:ajax_quota', kwargs={'service_id': self.svc.id})
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['allowed'])
        # remaining is naira string
        self.assertEqual(data['remaining'], '10000.00')

    @patch('services.utils.ReloadlyAPI.buy_airtime')
    def test_detail_post_airtime_success_redirects_to_confirmation(self, mock_buy):
        mock_buy.return_value = (True, {'status': 'SUCCESSFUL', 'transactionId': 'RL-VIEW-1'})
        resp = self.client.post(
            reverse('services:detail', kwargs={'service_id': self.svc.id}),
            {'phone': '08012345678', 'network': 'mtn', 'amount': '500'},
            follow=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/services/confirmation/', resp['Location'])

    def test_detail_post_invalid_amount_redirects_with_error(self):
        resp = self.client.post(
            reverse('services:detail', kwargs={'service_id': self.svc.id}),
            {'phone': '08012345678', 'network': 'mtn', 'amount': 'abc'},
        )
        self.assertEqual(resp.status_code, 302)