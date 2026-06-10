# Replace your entire urls.py with:
from django.urls import path
from . import views

app_name = "superadmin"


urlpatterns = [
    # Admin
    path("",                           views.admin_payout_dashboard,  name="dashboard"),

    path("venues/", views.superadmin_venues_list, name="venues"),
    path("partners/", views.superadmin_partners_list, name="partners"),
    path("subscribers/", views.superadmin_subscribers_list, name="subscribers"),
    path("coupon-codes/", views.superadmin_coupon_codes_list, name="coupon_codes"),

    path("history/",                   views.admin_payout_history,    name="history"),
    path("<uuid:payout_uuid>/",        views.admin_payout_detail,     name="detail"),
    path("<uuid:payout_uuid>/approve/",views.approve_payout,          name="approve"),
    path("<uuid:payout_uuid>/pay/",    views.mark_payout_paid,        name="pay"),
    path("<uuid:payout_uuid>/fail/",   views.fail_payout,             name="fail"),
    path("<uuid:payout_uuid>/cancel/", views.cancel_payout,           name="cancel"),
    path("auto-create/",              views.run_auto_create_payouts,  name="run_auto_create"),
    path('send-notification/', views.send_notification, name='send_notification'),
]