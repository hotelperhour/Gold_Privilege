from django.urls import path
from . import views, webhooks


app_name = 'subscriptions'

urlpatterns = [
    # Plan listing and details
    path('plans/', views.PlanListView.as_view(), name='plans_list'),
    path('plans/<slug:slug>/', views.PlanDetailView.as_view(), name='plan_detail'),
    
    # Subscription management
    path('subscribe/<slug:slug>/', views.subscribe_to_plan, name='subscribe'),
    path('my-subscription/', views.my_subscription, name='my_subscription'),
    path('cancel/<uuid:subscription_id>/', views.cancel_subscription, name='cancel_subscription'),
    
    # Payment
    path('payment/<uuid:payment_id>/', views.payment_page, name='payment'),
    path('success/', views.SubscriptionSuccessView.as_view(), name='success'),
    path('payment/callback/', views.payment_callback, name='payment_callback'),  
    path('webhook/paystack/', webhooks.paystack_webhook, name='paystack_webhook'),
    
    # AJAX endpoints
    path('api/validate-promo/', views.validate_promo_code, name='validate_promo'),
]