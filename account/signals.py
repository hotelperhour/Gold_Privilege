from django.db.models.signals import post_save
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


@receiver(post_save, sender=PartnerProfile)
def notify_partner_status_change(sender, instance, created, **kwargs):
    """
    Send email notification when partner status changes
    """
    if not created and instance.status in [
        PartnerProfile.PartnerStatus.APPROVED,
        PartnerProfile.PartnerStatus.REJECTED
    ]:
        if instance.status == PartnerProfile.PartnerStatus.APPROVED:
            subject = 'Gold Privilege - Your Partnership Application Approved!'
            message = f"""
            Dear {instance.user.get_full_name()},
            
            Congratulations! Your partnership application for {instance.business_name} 
            has been approved.
            
            You can now log in to your partner dashboard and start managing your 
            venue listings and offers.
            
            Best regards,
            Gold Privilege Team
            """
        else:  # REJECTED
            subject = 'Gold Privilege - Partnership Application Update'
            message = f"""
            Dear {instance.user.get_full_name()},
            
            Thank you for your interest in partnering with Gold Privilege.
            
            After careful review, we regret to inform you that we are unable to 
            approve your partnership application at this time.
            
            Reason: {instance.rejection_reason}
            
            If you have any questions or would like to reapply in the future, 
            please contact us.
            
            Best regards,
            Gold Privilege Team
            """
        
        # Send email (in production, use celery for async)
        try:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[instance.user.email],
                fail_silently=True,
            )
        except Exception as e:
            print(f"Failed to send email: {e}")