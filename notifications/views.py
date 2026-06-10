"""
notifications/views.py
"""

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from django.utils import timezone
from django.template.loader import render_to_string

from .models import Notification

PAGE_SIZE = 10  # notifications loaded per batch


@login_required
def notification_panel(request):
    """AJAX: Returns the panel HTML fragment. First 10 only."""
    notifications = Notification.objects.filter(
        recipient=request.user
    ).order_by('-created_at')[:PAGE_SIZE]

    total = Notification.objects.filter(recipient=request.user).count()
    unread_count = Notification.objects.filter(
        recipient=request.user, is_read=False
    ).count()

    return render(request, 'notifications/partials/panel.html', {
        'notifications': notifications,
        'unread_count':  unread_count,
        'has_more':      total > PAGE_SIZE,
        'next_offset':   PAGE_SIZE,
    })


@login_required
def notification_load_more(request):
    """
    AJAX: Load next batch of notifications.
    Called by 'Load more' button at bottom of panel.
    Returns JSON with rendered HTML rows + has_more flag.

    Teaching note:
      offset-based pagination (OFFSET/LIMIT) is simpler than cursor-based
      for this use case. Notifications are append-only (never reordered),
      so OFFSET is safe — no rows will be skipped if new notifications arrive
      while the user is scrolling.
    """
    offset = int(request.GET.get('offset', PAGE_SIZE))
    qs = Notification.objects.filter(
        recipient=request.user
    ).order_by('-created_at')[offset: offset + PAGE_SIZE]

    notifications = list(qs)
    total = Notification.objects.filter(recipient=request.user).count()
    has_more = (offset + PAGE_SIZE) < total

    rows_html = render_to_string(
        'notifications/partials/panel_rows.html',
        {'notifications': notifications},
        request=request,
    )

    return JsonResponse({
        'html':        rows_html,
        'has_more':    has_more,
        'next_offset': offset + PAGE_SIZE,
    })


@login_required
@require_POST
def mark_read(request, notification_id):
    notif = get_object_or_404(
        Notification,
        notification_id=notification_id,
        recipient=request.user,
    )
    notif.mark_read()
    return JsonResponse({
        'success':      True,
        'unread_count': Notification.objects.filter(
            recipient=request.user, is_read=False
        ).count(),
    })


@login_required
@require_POST
def mark_all_read(request):
    Notification.objects.filter(
        recipient=request.user, is_read=False
    ).update(is_read=True, read_at=timezone.now())
    return JsonResponse({'success': True, 'unread_count': 0})


@login_required
def unread_count(request):
    count = Notification.objects.filter(
        recipient=request.user, is_read=False
    ).count()
    return JsonResponse({'unread_count': count})