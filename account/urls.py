from django.urls import path
from . import views

app_name = 'account'

urlpatterns = [
    # home
    path('', views.home, name='home'),
    path('about/', views.about, name='about'),
    # Authentication
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    
    # Registration
    #path('register/', views.UserRegisterView.as_view(), name='register_user'),
    #path('register/partner/', views.PartnerRegisterView.as_view(), name='register_partner'),
    # Unified Registration (Both forms on same page)
    path('register/', views.UnifiedRegistrationView.as_view(), name='register_user'),
    path('register/partner/', views.UnifiedRegistrationView.as_view(), name='register_partner'),
    
    # Email Activation
    path('activation-sent/', views.activation_sent, name='activation_sent'),
    path('activate/<uidb64>/<token>/', views.activate, name='activate'),
    
    # Password Reset
    path('password-reset/', views.CustomPasswordResetView.as_view(), name='password_reset'),
    path('password-reset/done/', views.CustomPasswordResetDoneView.as_view(), name='password_reset_done'),
    path('password-reset-confirm/<uidb64>/<token>/', views.CustomPasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('password-reset-complete/', views.CustomPasswordResetCompleteView.as_view(), name='password_reset_complete'),
    
    # Dashboards
    path('dashboard/', views.dashboard, name='dashboard'),
    path('user/dashboard/', views.UserDashboardView.as_view(), name='user_dashboard'),
    path('partner/dashboard/', views.PartnerDashboardView.as_view(), name='partner_dashboard'),
    path('partner/pending/', views.PartnerPendingView.as_view(), name='partner_pending'),
    
    # Profile
    path('profile/', views.profile_view, name='profile'),
    path('partner/profile/update/', views.PartnerProfileUpdateView.as_view(), name='partner_profile_update'),
]