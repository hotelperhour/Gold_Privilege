"""
wallet/signals.py

Auto-creates a Wallet for every new SUBSCRIBER user on registration.
The wallet creation email is handled by the existing welcome_subscriber.html
email sent during registration — not here. See your account registration view
and update the welcome email context to include gp_id and wallet_url.
"""

import logging
from django.db.models.signals import post_save
from django.dispatch import receiver

from account.models import CustomUser
from .models import Wallet

logger = logging.getLogger(__name__)


@receiver(post_save, sender=CustomUser)
def create_user_wallet(sender, instance, created, **kwargs):
    """Create a Wallet for every new SUBSCRIBER."""
    if created and instance.user_type == CustomUser.UserType.SUBSCRIBER:
        try:
            wallet, wallet_created = Wallet.objects.get_or_create(user=instance)
            if wallet_created:
                logger.info(f'Wallet created for new subscriber: {instance.email}')
        except Exception as e:
            logger.error(f'Failed to create wallet for {instance.email}: {e}')


@receiver(post_save, sender=CustomUser)
def ensure_subscriber_wallet(sender, instance, created, **kwargs):
    """Safety net: ensure existing subscribers always have a wallet."""
    if not created and instance.user_type == CustomUser.UserType.SUBSCRIBER:
        Wallet.objects.get_or_create(user=instance)