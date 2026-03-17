## services/templatetags/services_tags.py
##
## Usage in template:
##   {% load services_tags %}
##   {{ voucher_stock|get_item:amt }}

from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """
    Return dictionary[key] safely.
    Usage: {{ my_dict|get_item:key_variable }}
    """
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None