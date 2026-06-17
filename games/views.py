"""
games/views.py

Three views only:
  spin_page    → GET  — renders the wheel page
  spin_action  → POST — processes a spin (JSON response)
  spin_history → GET  — user's full spin history (paginated)

SECURITY:
  spin_action is POST-only, requires login, requires CSRF token.
  The outcome is never touched by the browser. All validation and
  prize selection happens here before the response is sent.
"""

import json
import logging
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from .models import SpinConfig, SpinRecord
from .utils import execute_spin, get_spin_page_context

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SPIN PAGE  (GET)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def spin_page(request):
    """
    Renders the standalone spin wheel page at /games/spin/.

    All subscribers can VIEW the page but only those with an active
    subscription can actually spin. The template shows an "upgrade" prompt
    to non-active subscribers.

    Teaching note:
      We call get_spin_page_context() which does all the DB queries in one
      place. The view itself has no business logic — it just passes data to
      the template. This keeps views thin and testable.
    """
    context = get_spin_page_context(request.user)
    return render(request, 'games/spin.html', context)


# ─────────────────────────────────────────────────────────────────────────────
# SPIN ACTION  (POST → JSON)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def spin_action(request):
    """
    The only endpoint that actually executes a spin.

    Returns JSON only — the wheel animation reads this.

    Security checklist for this view:
      ✓ @login_required         — must be authenticated
      ✓ @require_POST           — GET requests are rejected (405)
      ✓ CSRF enforced by Django — cross-site POST requests are rejected
      ✓ Daily limit checked     — inside execute_spin(), inside atomic block
      ✓ Cooldown checked        — inside execute_spin()
      ✓ Outcome decided server  — execute_spin() calls pick_prize() here
      ✓ Wallet credited server  — credit_wallet() called inside atomic block
      ✓ IP logged               — for fraud detection

    Teaching note on @require_POST:
      This decorator immediately returns HTTP 405 Method Not Allowed for
      any non-POST request. It means a curious user can't just visit the
      URL in their browser and trigger a spin. They must submit the form
      (which requires a valid CSRF token from the spin page).
    """
    # Get client IP — respects X-Forwarded-For from reverse proxies (ngrok, etc.)
    ip_address = (
        request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
        or request.META.get('REMOTE_ADDR')
    )

    try:
        result = execute_spin(user=request.user, ip_address=ip_address)
    except Exception as e:
        logger.error(
            f'spin_action: Unhandled error for {request.user.email}: {e}',
            exc_info=True,
        )
        return JsonResponse({
            'success': False,
            'reason': 'An unexpected error occurred. Please try again.',
        }, status=500)

    status_code = 200 if result.get('success') else 400
    return JsonResponse(result, status=status_code)


# ─────────────────────────────────────────────────────────────────────────────
# SPIN HISTORY  (GET — full paginated history)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def spin_history(request):
    """
    Shows the user's complete spin history, paginated.
    Separate from the spin page so the main wheel page stays fast.
    """
    records_qs = (
        SpinRecord.objects
        .filter(user=request.user)
        .select_related('prize')
        .order_by('-spun_at')
    )

    paginator = Paginator(records_qs, 20)
    page_obj  = paginator.get_page(request.GET.get('page'))

    from django.db.models import Sum, Count
    from django.db.models import Q as DQ
    lifetime = SpinRecord.objects.filter(user=request.user).aggregate(
        total_spins = Count('id'),
        total_coins = Sum('coins_awarded'),
        total_wins  = Count('id', filter=DQ(coins_awarded__gt=0)),
    )

    return render(request, 'games/spin_history.html', {
        'page_obj':        page_obj,
        'records':         page_obj.object_list,
        'is_paginated':    page_obj.has_other_pages(),
        'lifetime_spins':  lifetime['total_spins'] or 0,
        'lifetime_coins':  lifetime['total_coins'] or 0,
        'lifetime_wins':   lifetime['total_wins']  or 0,
    })