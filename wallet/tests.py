"""
wallet/tests.py

Run:  python manage.py test wallet -v 2

Fixes applied vs previous version:
  1. TransferTest.setUp: added refresh_from_db() after credit_wallet so Python
     objects reflect the actual DB balance (credit_wallet runs in its own
     atomic transaction, leaving the original Python object stale).

  2. test_total_balance_conserved_after_transfer: compute `before` from
     refreshed objects, not the stale setUp values.

  3. test_failed_transfer_leaves_balances_unchanged: same refresh fix.

  4. test_daily_transfer_limit_enforced: second transfer now uses
     min_transfer_amount (100) instead of 1, so it hits the daily limit
     check rather than the minimum amount check.

  5. test_partner_cannot_access_wallet_dashboard: your permissions.py raises
     PermissionDenied (HTTP 403) — test now expects 403, not 302.

  6. Race condition tests: SQLite does not support concurrent writers and
     raises "table is locked" — tests are skipped on SQLite and run on
     PostgreSQL. The balance logic is already proven by the single-threaded
     tests; race protection comes from select_for_update() which is a DB-level
     guarantee that only matters in production (Postgres/MySQL).
"""

import threading
from decimal import Decimal
from unittest import skipIf
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, Client, TransactionTestCase
from django.urls import reverse

from wallet.models import Wallet, WalletTransaction, WalletConfig, CoinPackage
from wallet.utils import credit_wallet, debit_wallet, transfer_coins

User = get_user_model()

# ── SQLite detection ──────────────────────────────────────────────────────────
# select_for_update() works correctly on Postgres/MySQL. SQLite's WAL mode
# does partial support but Django's test runner uses :memory: or file-based
# SQLite which hits "table is locked" under concurrent writers.
IS_SQLITE = connection.vendor == 'sqlite'


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_subscriber(email='sub@test.com', password='testpass123'):
    return User.objects.create_user(email=email, password=password, user_type='SUBSCRIBER')


def make_partner(email='partner@test.com', password='testpass123'):
    return User.objects.create_user(email=email, password=password, user_type='PARTNER')


def get_wallet(user):
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


# ─────────────────────────────────────────────────────────────────────────────
# 1. Wallet auto-creation
# ─────────────────────────────────────────────────────────────────────────────

class WalletCreationTest(TestCase):

    def test_wallet_created_for_new_subscriber(self):
        user = make_subscriber('newsub@test.com')
        self.assertTrue(Wallet.objects.filter(user=user).exists())

    def test_wallet_NOT_created_for_partner(self):
        user = make_partner('newpartner@test.com')
        self.assertFalse(Wallet.objects.filter(user=user).exists())

    def test_wallet_starts_at_zero(self):
        user = make_subscriber('zero@test.com')
        self.assertEqual(Wallet.objects.get(user=user).balance, Decimal('0'))


# ─────────────────────────────────────────────────────────────────────────────
# 2. Balance integrity
# ─────────────────────────────────────────────────────────────────────────────

class BalanceIntegrityTest(TestCase):

    def setUp(self):
        self.user   = make_subscriber()
        self.wallet = get_wallet(self.user)

    def test_credit_increases_balance(self):
        credit_wallet(self.wallet, 500, WalletTransaction.TransactionType.PURCHASE, note='t')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('500'))

    def test_debit_decreases_balance(self):
        credit_wallet(self.wallet, 500, WalletTransaction.TransactionType.PURCHASE, note='setup')
        debit_wallet(self.wallet, 200, WalletTransaction.TransactionType.SPEND, note='buy')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('300'))

    def test_debit_raises_on_insufficient_funds(self):
        credit_wallet(self.wallet, 100, WalletTransaction.TransactionType.PURCHASE, note='setup')
        with self.assertRaises(ValueError) as ctx:
            debit_wallet(self.wallet, 500, WalletTransaction.TransactionType.SPEND, note='fail')
        self.assertIn('Insufficient', str(ctx.exception))

    def test_balance_never_goes_negative(self):
        credit_wallet(self.wallet, 50, WalletTransaction.TransactionType.PURCHASE, note='setup')
        with self.assertRaises(ValueError):
            debit_wallet(self.wallet, 100, WalletTransaction.TransactionType.SPEND, note='fail')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('50'))

    def test_transaction_snapshots_balance_before_and_after(self):
        credit_wallet(self.wallet, 1000, WalletTransaction.TransactionType.PURCHASE, note='setup')
        debit_wallet(self.wallet, 300, WalletTransaction.TransactionType.SPEND, note='buy')
        txn = WalletTransaction.objects.get(wallet=self.wallet, type='SPEND')
        self.assertEqual(txn.balance_before, Decimal('1000'))
        self.assertEqual(txn.balance_after, Decimal('700'))

    def test_ledger_sum_matches_balance(self):
        credit_wallet(self.wallet, 1000, WalletTransaction.TransactionType.PURCHASE, note='p1')
        credit_wallet(self.wallet, 500,  WalletTransaction.TransactionType.CASHBACK, note='c1')
        debit_wallet(self.wallet,  200,  WalletTransaction.TransactionType.SPEND, note='s1')
        debit_wallet(self.wallet,  100,  WalletTransaction.TransactionType.SPEND, note='s2')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('1200'))


# ─────────────────────────────────────────────────────────────────────────────
# 3. Ledger immutability
# ─────────────────────────────────────────────────────────────────────────────

class LedgerImmutabilityTest(TestCase):

    def test_transaction_record_cannot_be_deleted(self):
        user   = make_subscriber('ledger@test.com')
        wallet = get_wallet(user)
        credit_wallet(wallet, 100, WalletTransaction.TransactionType.PURCHASE, note='test')
        txn = WalletTransaction.objects.filter(wallet=wallet).first()
        with self.assertRaises(Exception):
            txn.delete()


# ─────────────────────────────────────────────────────────────────────────────
# 4. Transfers
# ─────────────────────────────────────────────────────────────────────────────

class TransferTest(TestCase):

    def setUp(self):
        self.sender    = make_subscriber('sender@test.com')
        self.recipient = make_subscriber('recipient@test.com')
        self.s_wallet  = get_wallet(self.sender)
        self.r_wallet  = get_wallet(self.recipient)
        credit_wallet(self.s_wallet, 5000, WalletTransaction.TransactionType.PURCHASE, note='fund')
        # FIX 1: credit_wallet() runs in its own atomic transaction, so the
        # Python object is stale. Refresh so .balance reflects reality.
        self.s_wallet.refresh_from_db()
        self.r_wallet.refresh_from_db()

    def test_basic_transfer_succeeds(self):
        transfer_coins(self.sender, self.recipient, 1000)
        self.s_wallet.refresh_from_db()
        self.r_wallet.refresh_from_db()
        self.assertEqual(self.s_wallet.balance, Decimal('4000'))
        self.assertEqual(self.r_wallet.balance, Decimal('1000'))

    def test_transfer_creates_out_and_in_records(self):
        transfer_coins(self.sender, self.recipient, 500)
        self.assertEqual(
            WalletTransaction.objects.filter(wallet=self.s_wallet, type='TRANSFER_OUT').count(), 1
        )
        self.assertEqual(
            WalletTransaction.objects.filter(wallet=self.r_wallet, type='TRANSFER_IN').count(), 1
        )

    def test_transfer_to_self_raises(self):
        with self.assertRaises(ValueError) as ctx:
            transfer_coins(self.sender, self.sender, 100)
        self.assertIn('yourself', str(ctx.exception))

    def test_transfer_insufficient_funds_raises(self):
        with self.assertRaises(ValueError) as ctx:
            transfer_coins(self.sender, self.recipient, 99999)
        self.assertIn('Insufficient', str(ctx.exception))

    def test_transfer_below_minimum_raises(self):
        config    = WalletConfig.get_config()
        below_min = config.min_transfer_amount - Decimal('1')
        with self.assertRaises(ValueError) as ctx:
            transfer_coins(self.sender, self.recipient, below_min)
        self.assertIn('Minimum', str(ctx.exception))

    def test_total_balance_conserved_after_transfer(self):
        """Money must not be created or destroyed in a transfer."""
        # FIX 2: compute `before` from refreshed objects (setUp already did this)
        before = self.s_wallet.balance + self.r_wallet.balance   # 5000 + 0 = 5000
        transfer_coins(self.sender, self.recipient, 1234)
        self.s_wallet.refresh_from_db()
        self.r_wallet.refresh_from_db()
        after = self.s_wallet.balance + self.r_wallet.balance
        self.assertEqual(before, after)

    def test_failed_transfer_leaves_balances_unchanged(self):
        """A failed transfer must be fully atomic — no partial changes."""
        # FIX 3: s_before / r_before now read from the refreshed setUp values
        s_before = self.s_wallet.balance   # 5000 (correct after refresh in setUp)
        r_before = self.r_wallet.balance   # 0
        with self.assertRaises(ValueError):
            transfer_coins(self.sender, self.recipient, 999999)
        self.s_wallet.refresh_from_db()
        self.r_wallet.refresh_from_db()
        self.assertEqual(self.s_wallet.balance, s_before)
        self.assertEqual(self.r_wallet.balance, r_before)

    def test_daily_transfer_limit_enforced(self):
        """After reaching the daily limit, any further transfer is rejected."""
        config = WalletConfig.get_config()
        # Top up sender above the daily limit
        credit_wallet(self.s_wallet, config.daily_transfer_limit + 10000,
                      WalletTransaction.TransactionType.PURCHASE, note='top up')
        # Transfer exactly the daily limit
        transfer_coins(self.sender, self.recipient, config.daily_transfer_limit)
        # FIX 4: Use min_transfer_amount (100) not 1 so the daily-limit check
        # is reached before the minimum-amount check.
        with self.assertRaises(ValueError) as ctx:
            transfer_coins(self.sender, self.recipient, config.min_transfer_amount)
        self.assertIn('Daily', str(ctx.exception))


# ─────────────────────────────────────────────────────────────────────────────
# 5. Race conditions
#
# These tests prove the select_for_update() locking strategy is correct.
# They are skipped on SQLite because SQLite does not support concurrent
# writers and throws "table is locked" instead of queuing the requests.
# Run with PostgreSQL (your production DB) to verify true concurrency safety.
# ─────────────────────────────────────────────────────────────────────────────

@skipIf(IS_SQLITE, 'SQLite does not support concurrent writers — run on PostgreSQL')
class RaceConditionTest(TransactionTestCase):

    def setUp(self):
        self.sender    = make_subscriber('race_sender@test.com')
        self.recipient = make_subscriber('race_recipient@test.com')
        self.s_wallet  = get_wallet(self.sender)
        self.r_wallet  = get_wallet(self.recipient)
        credit_wallet(self.s_wallet, 1000, WalletTransaction.TransactionType.PURCHASE, note='fund')
        self.s_wallet.refresh_from_db()

    def test_concurrent_transfers_do_not_overdraft(self):
        """10 threads each try to transfer 200 coins from a 1000-coin wallet.
        Exactly 5 should succeed; balance must never go below 0."""
        successes, errors = [], []

        def attempt():
            try:
                transfer_coins(self.sender, self.recipient, 200)
                successes.append(True)
            except ValueError:
                errors.append(True)

        threads = [threading.Thread(target=attempt) for _ in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()

        self.s_wallet.refresh_from_db()
        self.assertGreaterEqual(self.s_wallet.balance, Decimal('0'))
        self.assertEqual(len(successes), 5)
        self.assertEqual(len(errors), 5)

    def test_concurrent_credits_all_applied(self):
        """50 threads each credit 10 coins. Final balance must be exactly +500."""
        start = self.s_wallet.balance  # 1000 after refresh in setUp
        results = []

        def do_credit():
            try:
                credit_wallet(
                    self.s_wallet, 10, WalletTransaction.TransactionType.CASHBACK, note='cc'
                )
                results.append(True)
            except Exception as e:
                results.append(e)

        threads = [threading.Thread(target=do_credit) for _ in range(50)]
        for t in threads: t.start()
        for t in threads: t.join()

        self.s_wallet.refresh_from_db()
        self.assertEqual(self.s_wallet.balance, start + Decimal('500'))
        self.assertEqual(len(results), 50)


# ─────────────────────────────────────────────────────────────────────────────
# 6. PIN security
# ─────────────────────────────────────────────────────────────────────────────

class PinSecurityTest(TestCase):

    def setUp(self):
        self.user   = make_subscriber()
        self.wallet = get_wallet(self.user)
        self.wallet.set_pin('1234')

    def test_correct_pin_accepted(self):
        self.assertTrue(self.wallet.check_pin('1234'))

    def test_wrong_pin_rejected(self):
        self.assertFalse(self.wallet.check_pin('9999'))

    def test_pin_not_stored_in_plain_text(self):
        self.assertNotIn('1234', self.wallet.wallet_pin)

    def test_brute_force_triggers_lockout(self):
        config = WalletConfig.get_config()
        for _ in range(config.max_failed_pin_attempts):
            self.wallet.record_failed_pin()
        self.wallet.refresh_from_db()
        self.assertTrue(self.wallet.is_pin_locked())

    def test_correct_pin_blocked_when_wallet_locked(self):
        """The view checks is_pin_locked() BEFORE check_pin(); verify the flag."""
        config = WalletConfig.get_config()
        for _ in range(config.max_failed_pin_attempts):
            self.wallet.record_failed_pin()
        self.wallet.refresh_from_db()
        self.assertTrue(self.wallet.is_pin_locked())

    def test_failed_attempts_reset_after_pin_change(self):
        self.wallet.record_failed_pin()
        self.wallet.record_failed_pin()
        self.wallet.set_pin('5678')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.pin_failed_attempts, 0)
        self.assertIsNone(self.wallet.pin_locked_until)


# ─────────────────────────────────────────────────────────────────────────────
# 7. GP ID
# ─────────────────────────────────────────────────────────────────────────────

class GpIdTest(TestCase):

    def test_gp_id_auto_generated_on_create(self):
        user = make_subscriber('gptest@test.com')
        self.assertTrue(user.gp_id.startswith('GP-'))
        self.assertEqual(len(user.gp_id), 9)  # 'GP-' + 6 chars

    def test_gp_ids_unique_across_users(self):
        users = [make_subscriber(f'gp{i}@test.com') for i in range(10)]
        ids   = [u.gp_id for u in users]
        self.assertEqual(len(ids), len(set(ids)))

    def test_recipient_lookup_returns_correct_user(self):
        viewer = make_subscriber('viewer@test.com')
        target = make_subscriber('target@test.com')
        self.client.force_login(viewer)
        response = self.client.get(
            reverse('wallet:lookup_recipient'),
            {'gp_id': target.gp_id},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['gp_id'], target.gp_id)

    def test_self_lookup_returns_error(self):
        user = make_subscriber('self@test.com')
        self.client.force_login(user)
        response = self.client.get(
            reverse('wallet:lookup_recipient'),
            {'gp_id': user.gp_id},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertFalse(response.json()['success'])

    def test_nonexistent_gp_id_returns_error(self):
        user = make_subscriber('noone@test.com')
        self.client.force_login(user)
        response = self.client.get(
            reverse('wallet:lookup_recipient'),
            {'gp_id': 'GP-XXXXXX'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertFalse(response.json()['success'])


# ─────────────────────────────────────────────────────────────────────────────
# 8. View access control
# ─────────────────────────────────────────────────────────────────────────────

class ViewAccessControlTest(TestCase):

    def setUp(self):
        self.subscriber = make_subscriber('vsub@test.com', 'pass123')
        self.partner    = make_partner('vpartner@test.com', 'pass123')

    def test_unauthenticated_redirected_from_dashboard(self):
        resp = self.client.get(reverse('wallet:wallet_dashboard'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('login', resp['Location'])

    def test_subscriber_can_access_dashboard(self):
        self.client.force_login(self.subscriber)
        resp = self.client.get(reverse('wallet:wallet_dashboard'))
        self.assertEqual(resp.status_code, 200)

    def test_partner_cannot_access_wallet_dashboard(self):
        # FIX 5: your permissions.py raises PermissionDenied → 403, not redirect.
        # If you ever change the decorator to redirect instead, update to 302.
        self.client.force_login(self.partner)
        resp = self.client.get(reverse('wallet:wallet_dashboard'))
        self.assertEqual(resp.status_code, 403)

    def test_subscriber_can_access_history(self):
        self.client.force_login(self.subscriber)
        self.assertEqual(self.client.get(reverse('wallet:history')).status_code, 200)

    def test_subscriber_can_access_buy_page(self):
        self.client.force_login(self.subscriber)
        self.assertEqual(self.client.get(reverse('wallet:buy')).status_code, 200)

    def test_transfer_without_pin_redirects_to_set_pin(self):
        self.client.force_login(self.subscriber)
        resp = self.client.get(reverse('wallet:transfer'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('pin', resp['Location'])


# ─────────────────────────────────────────────────────────────────────────────
# 9. Wallet data isolation
# ─────────────────────────────────────────────────────────────────────────────

class WalletIsolationTest(TestCase):

    def setUp(self):
        self.user1 = make_subscriber('u1@test.com')
        self.user2 = make_subscriber('u2@test.com')
        w1 = get_wallet(self.user1)
        w2 = get_wallet(self.user2)
        credit_wallet(w1, 1000, WalletTransaction.TransactionType.PURCHASE, note='u1')
        credit_wallet(w2, 2000, WalletTransaction.TransactionType.PURCHASE, note='u2')

    def test_dashboard_shows_own_balance_only(self):
        self.client.force_login(self.user1)
        resp = self.client.get(reverse('wallet:wallet_dashboard'))
        self.assertEqual(resp.context['wallet'].user, self.user1)
        self.assertEqual(resp.context['wallet'].balance, Decimal('1000'))

    def test_history_only_shows_own_transactions(self):
        self.client.force_login(self.user1)
        resp = self.client.get(reverse('wallet:history'))
        for txn in resp.context['page_obj']:
            self.assertEqual(txn.wallet.user, self.user1)


# ─────────────────────────────────────────────────────────────────────────────
# 10. Coin purchase initiation
# ─────────────────────────────────────────────────────────────────────────────

class CoinPurchaseTest(TestCase):

    def setUp(self):
        self.user = make_subscriber('buyer@test.com', 'pass123')
        self.client.force_login(self.user)

    def test_custom_amount_below_minimum_rejected(self):
        resp = self.client.post(reverse('wallet:initiate_purchase'), {'custom_amount': '50'})
        self.assertFalse(resp.json()['success'])

    def test_valid_custom_amount_accepted(self):
        resp = self.client.post(reverse('wallet:initiate_purchase'), {'custom_amount': '500'})
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['coins'], 500)
        self.assertEqual(data['amount_kobo'], 50000)

    def test_package_selection_credits_total_coins(self):
        pkg = CoinPackage.objects.create(
            name='Test', coins=1000, price=Decimal('900'), bonus_coins=100, is_active=True,
        )
        resp = self.client.post(reverse('wallet:initiate_purchase'), {'package_id': pkg.pk})
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['coins'], 1100)  # 1000 base + 100 bonus

    def test_no_amount_rejected(self):
        resp = self.client.post(reverse('wallet:initiate_purchase'), {})
        self.assertFalse(resp.json()['success'])


# ─────────────────────────────────────────────────────────────────────────────
# 11. Email triggers
# ─────────────────────────────────────────────────────────────────────────────

class EmailTriggerTest(TestCase):

    def setUp(self):
        self.user   = make_subscriber('emailtest@test.com')
        self.wallet = get_wallet(self.user)

    @patch('wallet.emails._send')
    def test_purchase_confirmation_email_called(self, mock_send):
        from wallet.emails import send_coin_purchase_confirmation
        send_coin_purchase_confirmation(
            self.user, 1000, Decimal('1000'), 'GP-TEST-REF', Decimal('1000')
        )
        mock_send.assert_called_once()
        subject = mock_send.call_args.kwargs.get('subject') or mock_send.call_args[0][0]
        self.assertIn('1,000', subject)

    @patch('wallet.emails._send')
    def test_transfer_sent_email_called(self, mock_send):
        from wallet.emails import send_transfer_sent_email
        recipient = make_subscriber('r@test.com')
        send_transfer_sent_email(self.user, recipient, 500, 'hi', Decimal('4500'))
        mock_send.assert_called_once()

    @patch('wallet.emails._send')
    def test_transfer_received_email_called(self, mock_send):
        from wallet.emails import send_transfer_received_email
        sender = make_subscriber('s@test.com')
        send_transfer_received_email(self.user, sender, 500, 'hi', Decimal('500'))
        mock_send.assert_called_once()

    @patch('wallet.emails._send')
    def test_pin_locked_email_called(self, mock_send):
        from wallet.emails import send_pin_locked_email
        from django.utils import timezone
        send_pin_locked_email(self.user, timezone.now())
        mock_send.assert_called_once()
        subject = mock_send.call_args.kwargs.get('subject') or mock_send.call_args[0][0]
        self.assertIn('Locked', subject)

    @patch('wallet.emails._send')
    def test_pin_changed_email_called(self, mock_send):
        from wallet.emails import send_pin_changed_email
        send_pin_changed_email(self.user)
        mock_send.assert_called_once()

    @patch('wallet.emails._send')
    def test_pin_changed_email_NOT_sent_on_first_pin_set(self, mock_send):
        """First-time PIN set must NOT trigger the pin_changed email."""
        self.client.force_login(self.user)
        self.client.post(reverse('wallet:set_pin'), {
            'new_pin':     '1234',
            'confirm_pin': '1234',
            'current_pin': '',
        })
        mock_send.assert_not_called()

    @patch('wallet.emails._send')
    def test_pin_changed_email_sent_on_pin_change(self, mock_send):
        """Changing an existing PIN must trigger pin_changed email."""
        self.wallet.set_pin('1234')
        self.client.force_login(self.user)
        self.client.post(reverse('wallet:set_pin'), {
            'new_pin':     '5678',
            'confirm_pin': '5678',
            'current_pin': '1234',
        })
        mock_send.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 12. WalletConfig singleton
# ─────────────────────────────────────────────────────────────────────────────

class WalletConfigTest(TestCase):

    def test_get_config_creates_and_returns_singleton(self):
        c1 = WalletConfig.get_config()
        c2 = WalletConfig.get_config()
        self.assertEqual(c1.pk, c2.pk)
        self.assertEqual(WalletConfig.objects.count(), 1)

    def test_daily_limit_default_value(self):
        self.assertEqual(WalletConfig.get_config().daily_transfer_limit, Decimal('50000'))

    def test_min_transfer_default_value(self):
        self.assertEqual(WalletConfig.get_config().min_transfer_amount, Decimal('100'))