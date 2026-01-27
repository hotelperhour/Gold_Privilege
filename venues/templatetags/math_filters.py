from django import template

register = template.Library()

@register.filter
def mul(value, arg):
    """
    Multiplies the value by the argument.
    """
    try:
        return float(value) * float(arg)
    except (ValueError, TypeError):
        return ''
    
@register.filter
def div(value, arg):
    try:
        return value / arg
    except Exception:
        return 0
