from django.contrib import admin
from django.utils import timezone
from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display    = ['title', 'recipient', 'is_read', 'created_at']
    list_filter     = ['is_read', 'created_at']
    search_fields   = ['title', 'body', 'recipient__email']
    readonly_fields = ['notification_id', 'created_at', 'read_at']
    ordering        = ['-created_at']
    actions         = ['mark_selected_read']
    list_per_page    = 50

    def mark_selected_read(self, request, queryset):
        queryset.update(is_read=True, read_at=timezone.now())
    mark_selected_read.short_description = 'Mark selected as read'