# ════════════════════════════════════════════════════════════════════════════
# FILE: discount_store/urls.py
# ════════════════════════════════════════════════════════════════════════════

from django.urls import path
from . import views

app_name = 'discount_store'

urlpatterns = [
    # Browse
    path('', views.store_home, name='store_home'),
    path('checkout/<int:product_id>/',views.store_checkout,name='store_checkout'),
    path('product/<int:product_id>/', views.store_product_detail, name='product_detail'),

    # Card payment
    path('pay/card/<int:product_id>/',views.initiate_card_payment, name='initiate_card_payment'),
    path('pay/callback/', views.card_payment_callback, name='card_payment_callback'),

    # Coin payment
    path('pay/coins/<int:product_id>/',views.pay_with_coins,name='pay_with_coins'),

    # Post-payment
    #path('order/<uuid:order_id>/', views.order_confirmation, name='order_confirmation'),
    #path('orders/', views.my_orders, name='my_orders'),
    path('orders/<uuid:order_id>/cancel/', views.cancel_order, name='cancel_order'),
]




