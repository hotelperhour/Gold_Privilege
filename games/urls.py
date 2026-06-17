"""
games/urls.py

Three clean URLs:
  /games/spin/         → the wheel page
  /games/spin/action/  → POST-only spin endpoint (AJAX)
  /games/spin/history/ → full history page
"""

from django.urls import path
from . import views

app_name = 'games'

urlpatterns = [
    path('spin/',         views.spin_page,    name='spin'),
    path('spin/action/',  views.spin_action,  name='spin_action'),
    path('spin/history/', views.spin_history, name='spin_history'),
]