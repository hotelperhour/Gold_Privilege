from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.core.mail import send_mail
from django.conf import settings
from .models import CustomUser, UserProfile, PartnerProfile


@receiver(post_save, sender=CustomUser)
def create_user_profile(sender, instance, created, **kwargs):
    """
    Automatically create profile when user is created
    """
    if created:
        if instance.user_type == CustomUser.UserType.SUBSCRIBER:
            UserProfile.objects.get_or_create(user=instance)
        elif instance.user_type == CustomUser.UserType.PARTNER:
            # Partner profile should be created manually during registration
            # with business details
            pass


# Store old status before save
@receiver(pre_save, sender=PartnerProfile)
def store_old_status(sender, instance, **kwargs):
    """Store the old status before saving"""
    if instance.pk:  # Only for existing objects
        try:
            old_instance = PartnerProfile.objects.get(pk=instance.pk)
            instance._old_status = old_instance.status
        except PartnerProfile.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None


@receiver(post_save, sender=PartnerProfile)
def notify_partner_status_change(sender, instance, created, **kwargs):
    """
    Send email notification ONLY when partner status changes
    """
    # Don't send on creation
    if created:
        return
    
    # Check if status actually changed
    old_status = getattr(instance, '_old_status', None)
    
    if old_status is None or old_status == instance.status:
        return  # Status didn't change
    
    # Only send email for approved or rejected statuses
    if instance.status not in ['APPROVED', 'REJECTED']:
        return
    
    # Prepare email content
    if instance.status == 'APPROVED':
        subject = 'Gold Privilege - Your Partnership Application Approved!'
        message = f"""
Dear {instance.business_name},

Congratulations! Your partnership application has been approved.

You can now log in to your partner dashboard and start managing your 
venue listings and offers.

Login here: https://goldprivilege.com/login

Best regards,
Gold Privilege Team
        """
    else:  # REJECTED
        subject = 'Gold Privilege - Partnership Application Update'
        message = f"""
Dear {instance.business_name},

Thank you for your interest in partnering with Gold Privilege.

After careful review, we are unable to approve your partnership 
application at this time.

Reason: {instance.rejection_reason or 'Please contact support for details'}

If you have any questions, please contact us at support@goldprivilege.com

Best regards,
Gold Privilege Team
        """
    
    # Send email
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[instance.user.email],
            fail_silently=False,  # Set to False to see errors
        )
        print(f"✅ Email sent to {instance.user.email} - Status: {instance.status}")
    except Exception as e:
        print(f"❌ Failed to send email to {instance.user.email}: {e}")