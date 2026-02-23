from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Access dict by key in template"""
    return dictionary.get(key, {})