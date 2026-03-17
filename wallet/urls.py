from django.urls import path
from . import views

app_name = 'wallet'

urlpatterns = [
    path('',                  views.wallet_dashboard,      name='wallet_dashboard'),
    path('buy/',              views.buy_coins,              name='buy'),
    path('buy/initiate/',     views.initiate_coin_purchase, name='initiate_purchase'),
    path('buy/callback/',     views.coin_purchase_callback, name='purchase_callback'),
    path('transfer/',         views.transfer_coins_view,    name='transfer'),
    path('history/',          views.wallet_history,         name='history'),
    path('pin/',              views.set_pin,                name='set_pin'),
    path('lookup-recipient/', views.lookup_recipient,       name='lookup_recipient'),
]