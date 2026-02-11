# venues/templatetags/venue_filters.py
from django import template
from ..models import VenueCategory

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Get item from dictionary by key"""
    if dictionary and key:
        return dictionary.get(key)
    return None

@register.filter
def get_category_display(code):
    """Get display name for category code"""
    try:
        return dict(VenueCategory.choices).get(code, code)
    except:
        return code