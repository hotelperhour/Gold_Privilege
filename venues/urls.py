from django.urls import path
from . import views

app_name = 'venues'

urlpatterns = [
    # ==================== PUBLIC URLS ====================
    # Venue browsing
    path('', views.VenueListView.as_view(), name='list'),
    path('<slug:slug>/', views.VenueDetailView.as_view(), name='detail'),
    
    # User actions
    path('<slug:slug>/favorite/', views.toggle_favorite, name='toggle_favorite'),
    path('<slug:slug>/review/', views.submit_review, name='submit_review'),
    path('user/favorites/', views.my_favorites, name='my_favorites'),
    
    # AJAX endpoints
    path('api/search/', views.venue_search_autocomplete, name='search_autocomplete'),
    path('api/review/<int:review_id>/helpful/', views.mark_review_helpful, name='review_helpful'),
    
    # ==================== PARTNER URLS ====================
    path('partner/dashboard/', views.PartnerVenueListView.as_view(), name='partner_list'),
    path('partner/create/', views.PartnerVenueCreateView.as_view(), name='partner_create'),
    path('partner/<slug:slug>/', views.PartnerVenueDetailView.as_view(), name='partner_detail'),
    path('partner/<slug:slug>/edit/', views.PartnerVenueUpdateView.as_view(), name='partner_edit'),
    # urls.py
    path('partner/<slug:slug>/submit/', views.partner_submit_for_approval, name='partner_submit_for_approval'),
]