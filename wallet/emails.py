"""
wallet/emails.py

Thin senders — all email content lives in templates/wallet/emails/*.html.
Call these functions from views.py. Never put email content here.
"""

import logging
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def _send(subject, template_name, context, recipient_email):
    """Render an HTML template and send it. Falls back silently on error."""
    try:
        html_message = render_to_string(f'wallet/emails/{template_name}', context)
        # Plain text fallback — strip all HTML tags crudely
        plain_message = ' '.join(html_message.split())
        send_mail(
            subject=subject,
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient_email],
            html_message=html_message,
            fail_silently=False,
        )
    except Exception as e:
        logger.error(f'Wallet email [{template_name}] failed to {recipient_email}: {e}')


def _wallet_url(request=None):
    """Build the absolute wallet URL."""
    if request:
        return request.build_absolute_uri('/wallet/')
    base = getattr(settings, 'SITE_URL', 'https://goldprivilege.com')
    return f'{base}/wallet/'


# ─────────────────────────────────────────────────────────────────────────────

def send_coin_purchase_confirmation(user, coins_credited, amount_paid, paystack_reference,
                                    new_balance, request=None):
    _send(
        subject=f'{int(coins_credited):,} Gold Coins Added to Your Wallet',
        template_name='coin_purchase_confirmation.html',
        context={
            'user_name':          user.get_full_name(),
            'coins_credited':     f'{int(coins_credited):,}',
            'amount_paid':        f'{amount_paid:,.2f}',
            'new_balance':        f'{int(new_balance):,}',
            'paystack_reference': paystack_reference,
            'transaction_date':   timezone.now().strftime('%d %b %Y, %I:%M %p'),
            'wallet_url':         _wallet_url(request),
        },
        recipient_email=user.email,
    )


def send_transfer_sent_email(sender, recipient, amount, note, new_balance, request=None):
    _send(
        subject=f'You Sent {int(amount):,} Gold Coins to {recipient.get_full_name()}',
        template_name='transfer_sent.html',
        context={
            'sender_name':      sender.get_full_name(),
            'recipient_name':   recipient.get_full_name(),
            'recipient_gp_id':  recipient.gp_id,
            'amount':           f'{int(amount):,}',
            'new_balance':      f'{int(new_balance):,}',
            'note':             note,
            'transaction_date': timezone.now().strftime('%d %b %Y, %I:%M %p'),
            'wallet_url':       _wallet_url(request),
        },
        recipient_email=sender.email,
    )


def send_transfer_received_email(recipient, sender, amount, note, new_balance, request=None):
    _send(
        subject=f'You Received {int(amount):,} Gold Coins from {sender.get_full_name()}',
        template_name='transfer_received.html',
        context={
            'recipient_name':   recipient.get_full_name(),
            'sender_name':      sender.get_full_name(),
            'sender_gp_id':     sender.gp_id,
            'amount':           f'{int(amount):,}',
            'new_balance':      f'{int(new_balance):,}',
            'note':             note,
            'transaction_date': timezone.now().strftime('%d %b %Y, %I:%M %p'),
            'wallet_url':       _wallet_url(request),
        },
        recipient_email=recipient.email,
    )


def send_pin_locked_email(user, locked_until):
    _send(
        subject='Your Gold Privilege Wallet Has Been Temporarily Locked',
        template_name='pin_locked.html',
        context={
            'user_name':    user.get_full_name(),
            'locked_at':    timezone.now().strftime('%d %b %Y, %I:%M %p'),
            'locked_until': locked_until.strftime('%d %b %Y at %I:%M %p'),
        },
        recipient_email=user.email,
    )


def send_pin_changed_email(user):
    _send(
        subject='Your Wallet PIN Has Been Changed',
        template_name='pin_changed.html',
        context={
            'user_name':  user.get_full_name(),
            'gp_id':      user.gp_id,
            'changed_at': timezone.now().strftime('%d %b %Y, %I:%M %p'),
        },
        recipient_email=user.email,
    )