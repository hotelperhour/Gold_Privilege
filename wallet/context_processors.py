"""
wallet/context_processors.py

Injects wallet_balance and wallet_obj into every template automatically.

Add to settings.py → TEMPLATES → context_processors:
    'wallet.context_processors.wallet_context'
"""

from .models import Wallet


def wallet_context(request):
    """
    Makes wallet_balance available in all templates.
    Used by the navbar reward pill and the subscriber sidebar.
    Returns None values for non-subscribers and unauthenticated users.
    """
    if not request.user.is_authenticated:
        return {'wallet_balance': None, 'wallet_obj': None}

    if getattr(request.user, 'user_type', None) != 'SUBSCRIBER':
        return {'wallet_balance': None, 'wallet_obj': None}

    try:
        wallet = request.user.wallet
        return {
            'wallet_balance': wallet.balance,
            'wallet_obj': wallet,
        }
    except Wallet.DoesNotExist:
        # Create on the fly as a safety net (signal should have done this already)
        wallet = Wallet.objects.create(user=request.user)
        return {
            'wallet_balance': wallet.balance,
            'wallet_obj': wallet,
        }