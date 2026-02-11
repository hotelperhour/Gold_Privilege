# venues/context_processors.py
from django.conf import settings

def mapbox_settings(request):
    return {
        'MAPBOX_ACCESS_TOKEN': settings.MAPBOX_ACCESS_TOKEN,
        'DEFAULT_MAP_CENTER': [3.3792, 6.5244],  # Lagos coordinates
        'DEFAULT_MAP_ZOOM': 11,
    }