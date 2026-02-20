from .models import Subscription

def active_subscription(request):
    """
    Context processor to inject active subscription into all templates
    """
    active_sub = None
    
    if request.user.is_authenticated and request.user.user_type == 'SUBSCRIBER':
        active_sub = Subscription.objects.filter(
            user=request.user,
            status__in=['ACTIVE', 'TRIAL']
        ).select_related('plan').first()
    
    return {
        'active_subscription': active_sub
    }
