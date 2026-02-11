from django.urls import path
from . import views

app_name = 'venues'

urlpatterns = [
    # ==================== PUBLIC URLS ====================
    # Venue browsing
    path('', views.VenueListView.as_view(), name='list'),
    path('map-data/', views.venues_map_data, name='map_data'),
    path('<slug:slug>/', views.VenueDetailView.as_view(), name='detail'),

    
    # User actions
    path('<slug:slug>/favorite/', views.toggle_favorite, name='toggle_favorite'),
    path('<slug:slug>/review/', views.submit_review, name='submit_review'),
    path('/favorites/', views.my_favorites, name='my_favorites'),
    
    # AJAX endpoints
    path('api/search/', views.venue_search_autocomplete, name='search_autocomplete'),
    path('api/review/<int:review_id>/helpful/', views.mark_review_helpful, name='review_helpful'),
    
    # ==================== PARTNER URLS ====================
    path('partner/dashboard/', views.PartnerVenueListView.as_view(), name='partner_list'),
    path('partner/<slug:slug>/', views.PartnerVenueDetailView.as_view(), name='partner_detail'),
    path('partner/<slug:slug>/submit/', views.partner_submit_for_approval, name='partner_submit_for_approval'),
    path('partner/create/multistep/',views.MultiStepVenueCreateView.as_view(), name='multistep_create'),
    path('partner/create/success/<int:venue_id>/', views.VenueCreateSuccessView.as_view(), name='multistep_success'),
    path('partner/<slug:slug>/edit/multistep/',views.MultiStepVenueUpdateView.as_view(), name='multistep_update'),
    path('api/venue-image/<int:image_id>/delete/',views.delete_venue_image, name='delete_gallery_image'),
]