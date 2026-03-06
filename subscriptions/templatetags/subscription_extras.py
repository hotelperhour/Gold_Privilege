from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Access dict by key in template"""
    return dictionary.get(key, {})

@register.filter
def index(list_obj, i):
    """
    Get item at index from list
    Usage: {{ my_list|index:2 }}
    """
    try:
        return list_obj[int(i)]
    except (IndexError, TypeError, ValueError):
        return None
@register.filter
def get_item_at_index(list_obj, index):
    """
    Get item at specific index from list
    Usage: {{ plans|get_item_at_index:2 }}
    """
    try:
        return list_obj[int(index)]
    except (IndexError, TypeError, ValueError, AttributeError):
        return None

@register.filter
def add(value, arg):
    """
    Add two values
    Usage: {{ value|add:5 }}
    """
    try:
        return int(value) + int(arg)
    except (ValueError, TypeError):
        return value


@register.filter
def get_slice(list_obj, arg):
    """
    Slice a list
    Usage: {{ my_list|get_slice:":3" }}
    """
    try:
        bits = []
        for x in arg.split(':'):
            if len(x) == 0:
                bits.append(None)
            else:
                bits.append(int(x))
        return list_obj[slice(*bits)]
    except (ValueError, TypeError):
        return list_obj


@register.filter
def last(list_obj):
    """
    Get last item from list
    Usage: {{ my_list|last }}
    """
    try:
        return list_obj[-1]
    except (IndexError, TypeError):
        return None