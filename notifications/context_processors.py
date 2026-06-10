def notification_context(request):
    if not request.user.is_authenticated:
        return {'unread_notification_count': 0}
    try:
        from .models import Notification
        count = Notification.objects.filter(
            recipient=request.user, is_read=False
        ).count()
        return {'unread_notification_count': count}
    except Exception:
        return {'unread_notification_count': 0}