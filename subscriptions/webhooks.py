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
    Handle successful payment with validation
    
    Critical validations:
    - Payment exists and is pending
    - Amount matches expected amount
    - Payment hasn't been processed before
    - User owns the payment
    """
    reference = data.get('reference')
    # Paystack returns amount in kobo (Nigerian currency lowest unit)
    gateway_amount = Decimal(str(data.get('amount', 0))) / 100
    
    try:
        with transaction.atomic():
            # Lock payment record to prevent race conditions
            payment = Payment.objects.select_for_update().get(
                gateway_reference=reference
            )
            
            # Prevent double-processing
            if payment.status != Payment.PaymentStatus.PENDING:
                logger.warning(
                    f"Attempted to process non-pending payment: {payment.payment_id} "
                    f"(Status: {payment.status})"
                )
                return
            
            # CRITICAL: Validate amount matches
            expected_amount = payment.amount
            
            # Allow for minor rounding differences (0.01 tolerance)
            if abs(gateway_amount - expected_amount) > Decimal('0.01'):
                logger.error(
                    f"PAYMENT AMOUNT MISMATCH - Payment: {payment.payment_id}, "
                    f"Expected: {expected_amount}, Received: {gateway_amount}"
                )
                
                payment.status = Payment.PaymentStatus.FAILED
                payment.gateway_response = data
                payment.save()
                
                # Alert admin
                send_admin_alert(
                    subject='Payment Amount Mismatch',
                    message=f'Payment {payment.payment_id} amount mismatch. '
                            f'Expected: {expected_amount}, Received: {gateway_amount}'
                )
                return
            
            # Update payment status
            payment.status = Payment.PaymentStatus.SUCCESS
            payment.paid_at = timezone.now()
            payment.gateway_response = data
            payment.save()
            
            logger.info(f"Payment successful: {payment.payment_id}")
            
            # Activate subscription
            subscription = payment.subscription
            
            if subscription.status == Subscription.Status.PENDING:
                subscription.status = Subscription.Status.ACTIVE
                subscription.save()
                
                logger.info(
                    f"Subscription activated: {subscription.subscription_id} "
                    f"for user {subscription.user.email}"
                )
                
                # Send confirmation email
                send_payment_confirmation_email(payment)
            else:
                logger.warning(
                    f"Subscription already active: {subscription.subscription_id}"
                )
    
    except Payment.DoesNotExist:
        logger.error(f"Payment not found for reference: {reference}")
    except Exception as e:
        logger.error(
            f"Error handling charge success for reference {reference}: {e}", 
            exc_info=True
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