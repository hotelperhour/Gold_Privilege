"""
services/urls.py

Mount in your root urls.py:
    path('services/', include('services.urls', namespace='services')),
"""

from django.urls import path
from . import views

app_name = 'services'

urlpatterns = [
    # Main services dashboard
    path('', views.services_home, name='home'),

    # Individual service form
    path('<int:service_id>/', views.service_detail, name='detail'),

    # Confirmation after purchase
    path('confirmation/<uuid:purchase_id>/', views.service_confirmation, name='confirmation'),

    # Purchase history
    path('history/', views.purchase_history, name='history'),

    # AJAX quota check (called by JS)
    path('ajax/quota/<int:service_id>/', views.ajax_check_quota, name='ajax_quota'),
]