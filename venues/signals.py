from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from .models import Venue, VenueReview
from account.models import CustomUser
import logging

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Venue)
def notify_venue_status_change(sender, instance, created, **kwargs):
    """
    Send email notifications when venue status changes
    """
    if created:
        # Notify partner about successful venue creation
        if instance.status == 'DRAFT':
            try:
                subject = 'Venue Created Successfully - Gold Privilege'
                context = {
                    'venue': instance,
                    'partner': instance.partner,
                }
                
                html_message = render_to_string(
                    'venues/emails/venue_created.html',
                    context
                )
                plain_message = strip_tags(html_message)
                
                email = EmailMultiAlternatives(
                    subject=subject,
                    body=plain_message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[instance.partner.user.email]
                )
                email.attach_alternative(html_message, "text/html")
                email.send(fail_silently=True)
                
            except Exception as e:
                logger.error(f"Failed to send venue creation email: {e}")
    
    else:
        # Check if status changed
        old_instance = Venue.objects.filter(pk=instance.pk).first()
        if old_instance and old_instance.status != instance.status:
            
            # Venue approved
            if instance.status == 'APPROVED':
                try:
                    subject = 'Venue Approved! - Gold Privilege'
                    context = {
                        'venue': instance,
                        'partner': instance.partner,
                    }
                    
                    html_message = render_to_string(
                        'venues/emails/venue_approved.html',
                        context
                    )
                    plain_message = strip_tags(html_message)
                    
                    email = EmailMultiAlternatives(
                        subject=subject,
                        body=plain_message,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        to=[instance.partner.user.email]
                    )
                    email.attach_alternative(html_message, "text/html")
                    email.send(fail_silently=True)
                    
                except Exception as e:
                    logger.error(f"Failed to send venue approval email: {e}")
            
            # Venue rejected
            elif instance.status == 'REJECTED':
                try:
                    subject = 'Venue Submission Update - Gold Privilege'
                    context = {
                        'venue': instance,
                        'partner': instance.partner,
                        'rejection_reason': instance.rejection_reason,
                    }
                    
                    html_message = render_to_string(
                        'venues/emails/venue_rejected.html',
                        context
                    )
                    plain_message = strip_tags(html_message)
                    
                    email = EmailMultiAlternatives(
                        subject=subject,
                        body=plain_message,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        to=[instance.partner.user.email]
                    )
                    email.attach_alternative(html_message, "text/html")
                    email.send(fail_silently=True)
                    
                except Exception as e:
                    logger.error(f"Failed to send venue rejection email: {e}")
            
            # Venue submitted for review
            elif instance.status == 'PENDING':
                # Notify partner
                try:
                    subject = 'Venue Submitted for Review - Gold Privilege'
                    context = {
                        'venue': instance,
                        'partner': instance.partner,
                    }
                    
                    html_message = render_to_string(
                        'venues/emails/venue_submitted_partner.html',
                        context
                    )
                    plain_message = strip_tags(html_message)
                    
                    email = EmailMultiAlternatives(
                        subject=subject,
                        body=plain_message,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        to=[instance.partner.user.email]
                    )
                    email.attach_alternative(html_message, "text/html")
                    email.send(fail_silently=True)
                    
                except Exception as e:
                    logger.error(f"Failed to send venue submission email to partner: {e}")
                
                # Notify admins
                try:
                    admin_emails = CustomUser.objects.filter(
                        is_staff=True,
                        is_active=True
                    ).values_list('email', flat=True)
                    
                    if admin_emails:
                        subject = 'New Venue Pending Approval - Gold Privilege'
                        context = {
                            'venue': instance,
                            'partner': instance.partner,
                        }
                        
                        html_message = render_to_string(
                            'venues/emails/venue_submitted_admin.html',
                            context
                        )
                        plain_message = strip_tags(html_message)
                        
                        email = EmailMultiAlternatives(
                            subject=subject,
                            body=plain_message,
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            to=list(admin_emails)
                        )
                        email.attach_alternative(html_message, "text/html")
                        email.send(fail_silently=True)
                        
                except Exception as e:
                    logger.error(f"Failed to send venue submission email to admins: {e}")



@receiver(post_delete, sender=Venue)
def cleanup_venue_images(sender, instance, **kwargs):
    """
    Clean up venue images when venue is deleted
    """
    import os
    from django.core.files.storage import default_storage
    
    # Delete cover image
    if instance.cover_image and default_storage.exists(instance.cover_image.name):
        try:
            default_storage.delete(instance.cover_image.name)
            logger.info(f"Deleted cover image for venue: {instance.name}")
        except Exception as e:
            logger.error(f"Failed to delete cover image: {e}")
    
    # Delete gallery images
    for image in instance.images.all():
        if image.image and default_storage.exists(image.image.name):
            try:
                default_storage.delete(image.image.name)
            except Exception as e:
                logger.error(f"Failed to delete gallery image: {e}")


# PRODUCTION TIP: Add Celery tasks for async processing
"""
For production with 1000+ venues, move email sending to Celery:

from celery import shared_task

@shared_task
def send_venue_status_email(venue_id, status):
    '''Send venue status change email asynchronously'''
    venue = Venue.objects.get(id=venue_id)
    # Email sending logic here
    pass

@shared_task
def send_review_notification(review_id):
    '''Send new review notification asynchronously'''
    review = VenueReview.objects.get(id=review_id)
    # Email sending logic here
    pass

Then in signals, replace email.send() with:
send_venue_status_email.delay(instance.id, instance.status)
"""