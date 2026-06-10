"""
notifications/models.py

Simple design: one row = one notification for one user.
Admin creates notifications manually via the admin panel or a form.
No auto-triggers. No signals. No utils importing everywhere.
"""

import uuid
from django.db import models
from django.utils import timezone
from django.conf import settings


class Notification(models.Model):

    class AudienceType(models.TextChoices):
        ALL         = 'ALL',         'All Users'
        SUBSCRIBERS = 'SUBSCRIBERS', 'Subscribers Only'
        PARTNERS    = 'PARTNERS',    'Partners Only'

    notification_id = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True
    )

    # Who receives it
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
    )

    # Content — admin types these
    title = models.CharField(max_length=255)
    body  = models.TextField(blank=True)
    link  = models.CharField(
        max_length=500, blank=True,
        help_text='Optional: relative URL to open when clicked e.g. /store/',
    )

    # State
    is_read  = models.BooleanField(default=False, db_index=True)
    read_at  = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['recipient', 'is_read']),
        ]
        verbose_name        = 'Notification'
        verbose_name_plural = 'Notifications'

    def __str__(self):
        return f'{self.title[:60]} → {self.recipient.email}'

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])