from django.urls import path
from . import views

app_name = 'bookings'

urlpatterns = [
    # ==================== MEMBER URLS ====================
    
    path('', views.booking_list, name='list'),
    path('create/', views.booking_create, name='create'),
    path('create/<slug:venue_slug>/', views.booking_create, name='create_for_venue'),
    
    # AJAX Endpoints (must come before detail to avoid conflicts)
    path('api/check-quota/', views.check_booking_quota, name='check_quota'),
    path('api/available-dates/<int:venue_id>/', views.get_available_dates, name='available_dates'),
    
    # ==================== PARTNER URLS ====================

    path('partner/dashboard/', views.PartnerBookingsListView.as_view(), name='partner_list'),
    path('partner/check-in/', views.partner_check_in, name='partner_check_in'),
    path('partner/api/quick-check-in/', views.quick_check_in, name='quick_check_in'),
    path('partner/<str:booking_reference>/', views.partner_booking_detail, name='partner_detail'),
    
    # ==================== GENERIC DETAIL (MUST BE LAST!) ====================
    
    path('<str:booking_reference>/cancel/', views.booking_cancel, name='cancel'),
    path('<str:booking_reference>/', views.booking_detail, name='detail'),
]