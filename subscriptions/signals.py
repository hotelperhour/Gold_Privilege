from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils import timezone
from .models import Subscription, Payment


@receiver(post_save, sender=Subscription)
def send_subscription_confirmation(sender, instance, created, **kwargs):
    """
    Send HTML email when subscription is activated
    """
    if instance.status in ['ACTIVE', 'TRIAL']:
        subject = 'Welcome to Gold Privilege!'
        
        context = {
            'user': instance.user,
            'subscription': instance,
            'plan': instance.plan,
            'is_trial': instance.is_trial,
        }
        
        try:
            # Render HTML email template
            html_message = render_to_string(
                'subscriptions/emails/subscription_confirmation.html',
                context
            )
            plain_message = strip_tags(html_message)
            
            email = EmailMultiAlternatives(
                subject=subject,
                body=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[instance.user.email]
            )
            email.attach_alternative(html_message, "text/html")
            email.send(fail_silently=True)
            
        except Exception as e:
            # Log error in production (use proper logging)
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to send subscription confirmation: {e}")


@receiver(post_save, sender=Subscription)
def notify_subscription_cancellation(sender, instance, **kwargs):
    """
    Send HTML email when subscription is cancelled
    """
    if instance.status == 'CANCELLED' and instance.cancelled_at:
        subject = 'Subscription Cancelled - Gold Privilege'
        
        context = {
            'user': instance.user,
            'subscription': instance,
            'plan': instance.plan,
        }
        
        try:
            html_message = render_to_string(
                'subscriptions/emails/subscription_cancelled.html',
                context
            )
            plain_message = strip_tags(html_message)
            
            email = EmailMultiAlternatives(
                subject=subject,
                body=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[instance.user.email]
            )
            email.attach_alternative(html_message, "text/html")
            email.send(fail_silently=True)
            
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to send cancellation email: {e}")





@receiver(pre_save, sender=Subscription)
def check_subscription_expiry(sender, instance, **kwargs):
    """
    Automatically mark subscriptions as expired
    
    PRODUCTION NOTE: For 1000+ users, move this to a Celery task:
    - Run daily cron job to check expiring subscriptions
    - Send expiry warnings 7, 3, 1 days before expiry
    - Update statuses in bulk
    
    Current implementation is fine for < 1000 users
    """
    if instance.pk:  # Only for existing subscriptions
        now = timezone.now()
        
        # Check if subscription should be expired
        if instance.status in ['ACTIVE', 'TRIAL'] and now > instance.end_date:
            instance.status = Subscription.Status.EXPIRED
            
            # Send expiry notification
            try:
                subject = 'Your Subscription Has Expired - Gold Privilege'
                context = {
                    'user': instance.user,
                    'subscription': instance,
                    'plan': instance.plan,
                }
                
                html_message = render_to_string(
                    'subscriptions/emails/subscription_expired.html',
                    context
                )
                plain_message = strip_tags(html_message)
                
                email = EmailMultiAlternatives(
                    subject=subject,
                    body=plain_message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[instance.user.email]
                )
                email.attach_alternative(html_message, "text/html")
                email.send(fail_silently=True)
                
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to send expiry notification: {e}")


# PRODUCTION TIP: Add Celery tasks for scheduled operations
"""
# Example Celery tasks to add later (when scaling):

from celery import shared_task

@shared_task
def check_expiring_subscriptions():
    '''Run daily - check and notify expiring subscriptions'''
    from django.utils import timezone
    from datetime import timedelta
    
    # Find subscriptions expiring in 7 days
    seven_days = timezone.now() + timedelta(days=7)
    expiring_soon = Subscription.objects.filter(
        status__in=['ACTIVE', 'TRIAL'],
        end_date__date=seven_days.date(),
        auto_renew=False
    )
    
    for sub in expiring_soon:
        send_expiry_warning_email(sub, days=7)

@shared_task
def process_auto_renewals():
    '''Run daily - process subscriptions due for renewal'''
    from django.utils import timezone
    
    # Find subscriptions ending today with auto_renew=True
    today = timezone.now().date()
    to_renew = Subscription.objects.filter(
        end_date__date=today,
        auto_renew=True,
        status='ACTIVE'
    )
    
    for sub in to_renew:
        # Create new subscription period
        # Charge payment method
        # Send confirmation
        pass

@shared_task
def send_bulk_notifications(user_ids, template, context):
    '''Send notifications in batches to avoid email service rate limits'''
    from django.core.mail import EmailMultiAlternatives
    
    # Process in batches of 100
    batch_size = 100
    # ... implementation
"""