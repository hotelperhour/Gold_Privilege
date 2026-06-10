from django.urls import path
from . import views

app_name = 'notifications'

urlpatterns = [
    path('panel/', views.notification_panel, name='panel'),
    path('load-more/', views.notification_load_more, name='load_more'),
    path('mark-read/<uuid:notification_id>/', views.mark_read, name='mark_read'),
    path('mark-all-read/', views.mark_all_read, name='mark_all_read'),
    path('unread-count/', views.unread_count, name='unread_count'),
]