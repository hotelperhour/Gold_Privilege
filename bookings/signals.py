from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.urls import reverse
import logging

from .models import Booking, BookingStatus, BookingActivity

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Booking)
def send_booking_confirmation_emails(sender, instance, created, **kwargs):
    """
    Send confirmation emails when booking is created
    - Email to member with booking details and QR code
    - Email to partner (venue owner) about incoming booking
    """
    if created and instance.status == BookingStatus.CONFIRMED:
        # 1. Send to Member
        try:
            subject = f'Booking Confirmed - {instance.venue.name}'
            
            context = {
                'booking': instance,
                'user': instance.user,
                'venue': instance.venue,
                'qr_data': instance.get_qr_code_data(),
            }
            
            html_message = render_to_string(
                'bookings/emails/booking_confirmed_member.html',
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
            email.send(fail_silently=False)
            
            logger.info(f"Booking confirmation sent to member: {instance.booking_reference}")
        
        except Exception as e:
            logger.error(f"Failed to send booking confirmation to member: {e}")
        
        # 2. Send to Partner
        try:
            partner_email = instance.venue.partner.user.email
            subject = f'New Booking - {instance.venue.name}'
            
            context = {
                'booking': instance,
                'venue': instance.venue,
                'partner': instance.venue.partner,
                'member_name': instance.user.get_full_name(),
            }
            
            html_message = render_to_string(
                'bookings/emails/booking_confirmed_partner.html',
                context
            )
            plain_message = strip_tags(html_message)
            
            email = EmailMultiAlternatives(
                subject=subject,
                body=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[partner_email]
            )
            email.attach_alternative(html_message, "text/html")
            email.send(fail_silently=False)
            
            logger.info(f"Booking notification sent to partner: {instance.booking_reference}")
        
        except Exception as e:
            logger.error(f"Failed to send booking notification to partner: {e}")


@receiver(pre_save, sender=Booking)
def track_status_changes(sender, instance, **kwargs):
    """
    Track status changes and send appropriate emails
    """
    if not instance.pk:
        return  # New booking, handled by post_save
    
    try:
        old_instance = Booking.objects.get(pk=instance.pk)
        
        # Status changed
        if old_instance.status != instance.status:
            
            # Checked In
            if instance.status == BookingStatus.CHECKED_IN:
                send_check_in_email(instance)
            
            # Cancelled
            elif instance.status == BookingStatus.CANCELLED:
                send_cancellation_emails(instance, old_instance)
            
            # Completed
            elif instance.status == BookingStatus.COMPLETED:
                # Optional: Send completion/thank you email
                pass
    
    except Booking.DoesNotExist:
        pass


def send_check_in_email(booking):
    """
    Send email when member checks in at venue
    """
    try:
        subject = f'Checked In - {booking.venue.name}'
        
        context = {
            'booking': booking,
            'user': booking.user,
            'venue': booking.venue,
        }
        
        html_message = render_to_string(
            'bookings/emails/booking_checked_in.html',
            context
        )
        plain_message = strip_tags(html_message)
        
        email = EmailMultiAlternatives(
            subject=subject,
            body=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[booking.user.email]
        )
        email.attach_alternative(html_message, "text/html")
        email.send(fail_silently=False)
        
        logger.info(f"Check-in confirmation sent: {booking.booking_reference}")
    
    except Exception as e:
        logger.error(f"Failed to send check-in email: {e}")


def send_cancellation_emails(booking, old_booking):
    """
    Send cancellation emails to member and partner
    """
    # 1. Email to Member
    try:
        subject = f'Booking Cancelled - {booking.venue.name}'
        
        context = {
            'booking': booking,
            'user': booking.user,
            'venue': booking.venue,
            'cancellation_reason': booking.cancellation_reason,
        }
        
        html_message = render_to_string(
            'bookings/emails/booking_cancelled_member.html',
            context
        )
        plain_message = strip_tags(html_message)
        
        email = EmailMultiAlternatives(
            subject=subject,
            body=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[booking.user.email]
        )
        email.attach_alternative(html_message, "text/html")
        email.send(fail_silently=False)
        
        logger.info(f"Cancellation email sent to member: {booking.booking_reference}")
    
    except Exception as e:
        logger.error(f"Failed to send cancellation email to member: {e}")
    
    # 2. Email to Partner
    try:
        partner_email = booking.venue.partner.user.email
        subject = f'Booking Cancelled - {booking.venue.name}'
        
        context = {
            'booking': booking,
            'venue': booking.venue,
            'partner': booking.venue.partner,
            'member_name': booking.user.get_full_name(),
        }
        
        html_message = render_to_string(
            'bookings/emails/booking_cancelled_partner.html',
            context
        )
        plain_message = strip_tags(html_message)
        
        email = EmailMultiAlternatives(
            subject=subject,
            body=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[partner_email]
        )
        email.attach_alternative(html_message, "text/html")
        email.send(fail_silently=False)
        
        logger.info(f"Cancellation email sent to partner: {booking.booking_reference}")
    
    except Exception as e:
        logger.error(f"Failed to send cancellation email to partner: {e}")


@receiver(post_save, sender=Booking)
def create_booking_activity_log(sender, instance, created, **kwargs):
    """
    Create activity log entry for audit trail
    """
    if created:
        BookingActivity.objects.create(
            booking=instance,
            action='CREATED',
            performed_by=instance.user,
            notes=f'Booking created for {instance.visit_date}'
        )


# PRODUCTION TIP: Move to Celery for async processing
"""
For production with high volume, move email sending to Celery:

from celery import shared_task

@shared_task
def send_booking_confirmation_async(booking_id):
    '''Send booking confirmation email asynchronously'''
    booking = Booking.objects.get(id=booking_id)
    # Email sending logic
    pass

@shared_task
def send_check_in_notification_async(booking_id):
    '''Send check-in notification asynchronously'''
    booking = Booking.objects.get(id=booking_id)
    # Email sending logic
    pass

Then in signals, replace direct email sending with:
send_booking_confirmation_async.delay(instance.id)
"""