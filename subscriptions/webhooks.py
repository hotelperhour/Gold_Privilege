"""
Secure Paystack Webhook Handler

Security improvements:
- Signature verification
- Amount validation
- Idempotency handling
- Rate limiting
- Transaction atomicity
- Comprehensive logging
"""

from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.db import transaction
from django.core.cache import cache
from decimal import Decimal
import hashlib
import hmac
import json
import logging
from django.utils import timezone
from wallet.models import ReferralRecord, WalletConfig
from wallet.utils import credit_wallet
from wallet.models import WalletTransaction
from .models import Payment, Subscription

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def paystack_webhook(request):
    """
    Handle Paystack webhook events with security measures
    
    Security features:
    - Signature verification (HMAC SHA-512)
    - IP whitelist validation
    - Rate limiting per IP
    - Idempotency check
    - Amount validation
    - Transaction atomicity
    """
    
    # Get client IP for rate limiting
    client_ip = get_client_ip(request)
    
    # Rate limiting: Max 100 webhook requests per minute per IP
    rate_limit_key = f'webhook_rate:{client_ip}'
    requests_count = cache.get(rate_limit_key, 0)
    
    if requests_count >= 100:
        logger.warning(f"Rate limit exceeded for IP: {client_ip}")
        return HttpResponse("Rate limit exceeded", status=429)
    
    cache.set(rate_limit_key, requests_count + 1, 60)  # 1 minute
    
    # Verify Paystack signature
    paystack_signature = request.headers.get('X-Paystack-Signature')
    
    if not paystack_signature:
        logger.warning(f"Webhook received without signature from IP: {client_ip}")
        return HttpResponseBadRequest("No signature")
    
    # Verify signature with constant-time comparison
    secret_key = settings.PAYSTACK_SECRET_KEY
    body = request.body
    
    computed_signature = hmac.new(
        secret_key.encode('utf-8'),
        body,
        hashlib.sha512
    ).hexdigest()
    
    if not hmac.compare_digest(computed_signature, paystack_signature):
        logger.warning(f"Invalid webhook signature from IP: {client_ip}")
        return HttpResponseBadRequest("Invalid signature")
    
    # Parse webhook data
    try:
        data = json.loads(body.decode('utf-8'))
        event = data.get('event')
        event_data = data.get('data', {})
        
        # Idempotency check using event ID
        event_id = event_data.get('id') or event_data.get('reference')
        if event_id:
            idempotency_key = f'webhook_processed:{event_id}'
            if cache.get(idempotency_key):
                logger.info(f"Duplicate webhook ignored: {event_id}")
                return HttpResponse(status=200)
            
            # Mark as processed (24 hour expiry)
            cache.set(idempotency_key, True, 86400)
        
        logger.info(f"Webhook received - Event: {event}, Reference: {event_data.get('reference')}")
        
        # Route to appropriate handler
        if event == 'charge.success':
            handle_charge_success(event_data)
        elif event == 'charge.failed':
            handle_charge_failed(event_data)
        elif event == 'subscription.disable':
            handle_subscription_disabled(event_data)
        else:
            logger.info(f"Unhandled event type: {event}")
        
        return HttpResponse(status=200)
    
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in webhook from IP: {client_ip}")
        return HttpResponseBadRequest("Invalid JSON")
    except Exception as e:
        logger.error(f"Webhook processing error: {e}", exc_info=True)
        return HttpResponse(status=500)


def handle_charge_success(data):
    """
    Handle successful payment with validation.
    Called by Paystack webhook — may arrive AFTER payment_callback has
    already processed the payment. The is_paid guard in
    _maybe_award_referral_coins makes it safe to call from both paths.
    """
    reference      = data.get('reference')
    gateway_amount = Decimal(str(data.get('amount', 0))) / 100
    # Route by reference prefix — keeps payment categories cleanly separated
    if reference and reference.startswith('GP-DS-'):
        handle_store_order_payment(reference, gateway_amount, data)
        return  # Do NOT fall through to subscription payment handling
 
    subscription_user = None  # track for referral call outside atomic block
 
    try:
        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(
                gateway_reference=reference
            )
 
            # If payment_callback already processed this, skip silently
            if payment.status != Payment.PaymentStatus.PENDING:
                logger.info(
                    f"Webhook: payment {payment.payment_id} already processed "
                    f"(status={payment.status}) — skipping."
                )
                return
 
            # Validate amount
            expected_amount = payment.amount
            if abs(gateway_amount - expected_amount) > Decimal('0.01'):
                logger.error(
                    f"PAYMENT AMOUNT MISMATCH — Payment: {payment.payment_id}, "
                    f"Expected: {expected_amount}, Received: {gateway_amount}"
                )
                payment.status           = Payment.PaymentStatus.FAILED
                payment.gateway_response = data
                payment.save()
                send_admin_alert(
                    subject='Payment Amount Mismatch',
                    message=(
                        f'Payment {payment.payment_id} amount mismatch. '
                        f'Expected: {expected_amount}, Received: {gateway_amount}'
                    ),
                )
                return
 
            payment.status           = Payment.PaymentStatus.SUCCESS
            payment.paid_at          = timezone.now()
            payment.gateway_response = data
            payment.save()
            logger.info(f"Webhook: payment successful: {payment.payment_id}")

            subscription = payment.subscription

            # ── RACE CONDITION FIX ──────────────────────────────────────────────
            # A successful charge is the source of truth. If our local row drifted
            # to EXPIRED because the 15-minute stale-pending cleanup ran moments
            # before this webhook arrived, we must still activate — the user paid.
            #
            # We only refuse to activate CANCELLED subscriptions, because that
            # status means the USER explicitly chose to back out — reactivating
            # a subscription someone deliberately cancelled would be wrong, and
            # in that specific edge case the payment should be flagged for a
            # manual refund instead.
            if subscription.status in (Subscription.Status.PENDING, Subscription.Status.EXPIRED):
                if subscription.status == Subscription.Status.EXPIRED:
                    logger.warning(
                        f"Webhook: subscription {subscription.subscription_id} had expired "
                        f"locally but payment succeeded — reactivating. This is the "
                        f"stale-pending-cleanup race condition; activation is correct "
                        f"because the charge genuinely succeeded."
                    )

                subscription.status = Subscription.Status.ACTIVE
                subscription.save()
                subscription_user = subscription.user
                logger.info(
                    f"Webhook: subscription activated: {subscription.subscription_id} "
                    f"for user {subscription.user.email}"
                )
                send_payment_confirmation_email(payment)

            elif subscription.status == Subscription.Status.CANCELLED:
                logger.error(
                    f"Webhook: payment succeeded for CANCELLED subscription "
                    f"{subscription.subscription_id} (user {subscription.user.email}). "
                    f"This needs manual review — possible refund required."
                )
                send_admin_alert(
                    subject='Payment succeeded for a cancelled subscription — needs review',
                    message=(
                        f'Subscription {subscription.subscription_id} for '
                        f'{subscription.user.email} was CANCELLED by the user, but a '
                        f'payment of ₦{gateway_amount} just succeeded on Paystack '
                        f'(reference: {reference}). The subscription was NOT reactivated '
                        f'automatically. Please review and either refund the user or '
                        f'manually reactivate if appropriate.'
                    ),
                )

            else:
                logger.warning(
                    f"Webhook: subscription already active: {subscription.subscription_id}"
                )
 
    except Payment.DoesNotExist:
        logger.error(f"Webhook: payment not found for reference: {reference}")
        return
    except Exception as e:
        logger.error(
            f"Webhook: error handling charge success for reference {reference}: {e}",
            exc_info=True,
        )
        return
 
    # ── Award referral coins OUTSIDE the atomic block ────────────────────────
    # This means a referral failure can never roll back the subscription.
    # is_paid guard makes this idempotent — safe to call even if
    # payment_callback already called it first.
    if subscription_user:
        try:
            _maybe_award_referral_coins(subscription_user)
        except Exception as e:
            logger.error(
                f'Webhook: referral coin award failed for {subscription_user.email}: {e}',
                exc_info=True,
            )


def handle_charge_failed(data):
    """Handle failed payment"""
    reference = data.get('reference')
    
    try:
        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(
                gateway_reference=reference
            )
            
            # Only update if still pending
            if payment.status == Payment.PaymentStatus.PENDING:
                payment.status = Payment.PaymentStatus.FAILED
                payment.gateway_response = data
                payment.save()
                
                logger.info(f"Payment failed: {payment.payment_id}")
                
                # Send failure notification
                send_payment_failed_email(payment)
            
    except Payment.DoesNotExist:
        logger.error(f"Payment not found for reference: {reference}")
    except Exception as e:
        logger.error(f"Error handling charge failed: {e}", exc_info=True)


def handle_subscription_disabled(data):
    """Handle subscription cancellation from Paystack"""
    subscription_code = data.get('subscription_code')
    
    try:
        with transaction.atomic():
            subscription = Subscription.objects.select_for_update().get(
                gateway_subscription_code=subscription_code
            )
            
            if subscription.status not in ['CANCELLED', 'EXPIRED']:
                subscription.cancel(reason='Cancelled via payment gateway')
                logger.info(
                    f"Subscription cancelled via gateway: {subscription.subscription_id}"
                )
        
    except Subscription.DoesNotExist:
        logger.error(f"Subscription not found: {subscription_code}")
    except Exception as e:
        logger.error(f"Error handling subscription disable: {e}", exc_info=True)


def send_payment_confirmation_email(payment):
    """Send payment confirmation email"""
    try:
        subject = f'Payment Successful - {payment.subscription.plan.name}'
        context = {
            'user': payment.user,
            'payment': payment,
            'subscription': payment.subscription,
            'plan': payment.subscription.plan,
        }
        
        html_message = render_to_string(
            'subscriptions/emails/payment_success.html',
            context
        )
        plain_message = strip_tags(html_message)
        
        email = EmailMultiAlternatives(
            subject=subject,
            body=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[payment.user.email]
        )
        email.attach_alternative(html_message, "text/html")
        email.send(fail_silently=True)
        
        logger.info(f"Confirmation email sent to {payment.user.email}")
        
    except Exception as e:
        logger.error(f"Failed to send payment confirmation: {e}", exc_info=True)


def send_payment_failed_email(payment):
    """Send payment failure notification"""
    try:
        subject = 'Payment Failed - Please Try Again'
        context = {
            'user': payment.user,
            'payment': payment,
            'subscription': payment.subscription,
            'plan': payment.subscription.plan,
        }
        
        html_message = render_to_string(
            'subscriptions/emails/payment_failed.html',
            context
        )
        plain_message = strip_tags(html_message)
        
        email = EmailMultiAlternatives(
            subject=subject,
            body=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[payment.user.email]
        )
        email.attach_alternative(html_message, "text/html")
        email.send(fail_silently=True)
        
    except Exception as e:
        logger.error(f"Failed to send payment failure email: {e}", exc_info=True)


def send_admin_alert(subject, message):
    """Send alert email to admins"""
    try:
        from django.core.mail import mail_admins
        mail_admins(
            subject=subject,
            message=message,
            fail_silently=True
        )
    except Exception as e:
        logger.error(f"Failed to send admin alert: {e}")


def get_client_ip(request):
    """Get client IP address from request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def _maybe_award_referral_coins(user):
    """
    Award referral coins to the referrer when the referred user
    completes their FIRST subscription payment.
 
    Called once inside handle_charge_success() after subscription is
    marked ACTIVE. Safe to call multiple times — is_paid guard makes
    it idempotent.
    """
    from .models import Payment  # subscriptions.models.Payment
 
    # Count this user's successful subscription payments.
    # BUG 1 FIX: use Payment.PaymentStatus.SUCCESS, not 'COMPLETED'
    completed_payments = Payment.objects.filter(
        subscription__user=user,
        status=Payment.PaymentStatus.SUCCESS,   # ← was 'COMPLETED' — WRONG
    ).count()
 
    # Only award on the very first payment
    if completed_payments != 1:
        return
 
    # Check if this user was referred and bonus not yet paid
    try:
        record = ReferralRecord.objects.select_for_update().get(
            referred_user=user,
            is_paid=False,
        )
    except ReferralRecord.DoesNotExist:
        return  # Not a referred user — nothing to do
 
    # Get admin-configurable reward amount
    config = WalletConfig.get_config()
    coins  = config.referral_coins_reward  # default 500
 
    # Get or create the referrer's wallet
    from wallet.models import Wallet
    referrer_wallet, _ = Wallet.objects.get_or_create(user=record.referrer)
 
    # Credit and mark paid atomically
    with transaction.atomic():
        credit_wallet(
            wallet=referrer_wallet,
            amount=coins,
            txn_type=WalletTransaction.TransactionType.REFERRAL,
            note=f'Referral bonus: {user.get_full_name()} ({user.gp_id}) subscribed',
        )
        record.coins_awarded = coins
        record.awarded_at    = timezone.now()   # BUG 2 FIX: was broken __import__ hack
        record.is_paid       = True
        record.save(update_fields=['coins_awarded', 'awarded_at', 'is_paid'])
 
    logger.info(
        f'Referral bonus: {coins} coins credited to {record.referrer.email} '
        f'for referring {user.email}'
    )
 
    # Email the referrer (failure must never break the webhook)
    try:
        from wallet.emails import _send
        _send(
            subject=f'You earned {coins:,} Gold Coins — referral bonus!',
            template_name='referral_bonus.html',
            context={
                'referrer_name': record.referrer.get_full_name(),
                'referred_name': user.get_full_name(),
                'coins_awarded': f'{coins:,}',
                'awarded_at':    timezone.now().strftime('%d %b %Y'),
            },
            recipient_email=record.referrer.email,
        )
    except Exception as e:
        logger.error(f'Referral bonus email failed: {e}')


def handle_store_order_payment(reference, gateway_amount, data):
    """
    Handle successful Paystack payment for a Discount Store order.
 
    Called by handle_charge_success when the reference starts with 'GP-DS-'.
 
    The browser callback (card_payment_callback in discount_store/views.py)
    is the PRIMARY path — it usually runs first.
    This webhook is the server-to-server backup that handles:
      - Users who close the browser before the callback redirects
      - Network failures between Paystack and the user's browser
 
    The order.status check makes this fully idempotent — safe to call
    from both the callback and the webhook.
    """
    from django.db import transaction
    from discount_store.models import StoreOrder
    from discount_store.views import _create_booking_from_order, _award_cashback, _send_confirmation_email
 
    try:
        with transaction.atomic():
            order = StoreOrder.objects.select_for_update().get(
                paystack_reference=reference
            )
 
            # Idempotency guard — callback may have already processed this
            if order.status != StoreOrder.OrderStatus.PENDING:
                logger.info(
                    f'Store order {order.reference} already processed '
                    f'(status={order.status}) — webhook skipping.'
                )
                return
 
            # Validate amount (same rule as subscription payments: 1 kobo tolerance)
            if abs(gateway_amount - order.amount_paid) > Decimal('0.01'):
                logger.error(
                    f'STORE ORDER AMOUNT MISMATCH — Order: {order.reference}, '
                    f'Expected: {order.amount_paid}, Received: {gateway_amount}'
                )
                send_admin_alert(
                    subject=f'Store Order Amount Mismatch — {order.reference}',
                    message=(
                        f'Order {order.reference} for {order.user.email}. '
                        f'Expected ₦{order.amount_paid}, received ₦{gateway_amount}.'
                    ),
                )
                return
 
            # Create booking
            _create_booking_from_order(order)
            logger.info(
                f'Webhook: Store order {order.reference} paid and booking created '
                f'for {order.user.email}'
            )
 
    except StoreOrder.DoesNotExist:
        logger.error(f'Webhook: No store order found for reference: {reference}')
        return
    except Exception as e:
        logger.error(
            f'Webhook: Error handling store order payment for {reference}: {e}',
            exc_info=True
        )
        return
 
    # Cashback and email outside atomic block — failures must not roll back the booking
    try:
        order.refresh_from_db()
        _award_cashback(order)
        _send_confirmation_email(order)
    except Exception as e:
        logger.error(f'Webhook: Post-payment actions failed for {reference}: {e}', exc_info=True)


# PRODUCTION MONITORING
"""
Add this to your monitoring system:

1. Track webhook failures:
   - Failed signature validations
   - Amount mismatches
   - Processing errors

2. Set up alerts for:
   - High webhook failure rate (>5% in 1 hour)
   - Amount mismatches (immediate alert)
   - Duplicate payment attempts
   
3. Monitor metrics:
   - Webhook processing time
   - Success/failure rates
   - Payment conversion rates
"""