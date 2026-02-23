
from django.utils import timezone
from .models import FeatureUsage, PlanFeatureAssignment
from .models import Subscription

def get_or_create_feature_usage(subscription, feature):
    """
    Get or create FeatureUsage for current period.
    
    Returns: (FeatureUsage instance, created: bool)
    """
    now = timezone.now()
    year = now.year
    month = now.month
    
    usage, created = FeatureUsage.objects.get_or_create(
        subscription=subscription,
        feature=feature,
        period_year=year,
        period_month=month,
        defaults={'used_count': 0}
    )
    
    return usage, created


def can_use_feature(subscription, feature):
    """
    Check if user can use a specific feature.
    
    Returns: (can_use: bool, remaining: int, message: str)
    """
    # Check if feature is in the plan
    try:
        assignment = PlanFeatureAssignment.objects.get(
            plan=subscription.plan,
            feature=feature
        )
    except PlanFeatureAssignment.DoesNotExist:
        return False, 0, f"{feature.name} is not included in your {subscription.plan.name} plan"
    
    # Get current usage
    usage, _ = get_or_create_feature_usage(subscription, feature)
    
    limit = assignment.usage_limit
    remaining = limit - usage.used_count
    
    if remaining <= 0:
        return False, 0, f"You've used all {limit} {feature.name} visits this month"
    
    return True, remaining, f"{remaining} {feature.name} visit(s) remaining"


def increment_feature_usage(subscription, feature):
    """
    Increment usage count for a feature.
    Called when booking is created.
    """
    usage, _ = get_or_create_feature_usage(subscription, feature)
    usage.increment()
    return usage


def decrement_feature_usage(subscription, feature):
    """
    Decrement usage count for a feature.
    Called when booking is cancelled.
    """
    usage, _ = get_or_create_feature_usage(subscription, feature)
    usage.decrement()
    return usage


def get_all_feature_usage(subscription):
    """
    Get usage stats for all features in the plan.
    
    Returns: dict like:
    {
        'Gym Access': {'used': 5, 'limit': 8, 'remaining': 3},
        'Buffet': {'used': 2, 'limit': 5, 'remaining': 3},
    }
    """
    now = timezone.now()
    year, month = now.year, now.month
    
    stats = {}
    
    # Get all features in the plan
    for assignment in subscription.plan.feature_assignments.all():
        feature = assignment.feature
        
        # Get usage for current period
        usage = FeatureUsage.objects.filter(
            subscription=subscription,
            feature=feature,
            period_year=year,
            period_month=month
        ).first()
        
        used = usage.used_count if usage else 0
        limit = assignment.usage_limit
        remaining = max(0, limit - used)
        
        stats[feature.name] = {
            'feature_id': feature.id,
            'feature': feature,
            'used': used,
            'limit': limit,
            'remaining': remaining,
            'percentage': int((used / limit) * 100) if limit > 0 else 0
        }
    
    return stats

def get_subscription_state(user):
    """
    Determine user's subscription state with precision.
    
    Returns: dict with keys:
        - has_active: bool (currently active)
        - has_expired: bool (had one before, now expired)
        - has_pending: bool (payment pending)
        - never_subscribed: bool (never had one)
        - subscription: Subscription object or None
        - state: 'active' | 'expired' | 'pending' | 'never'
    """
    # Check for active subscription
    active = Subscription.objects.filter(
        user=user,
        status__in=['ACTIVE', 'TRIAL'],
        end_date__gte=timezone.now().date()
    ).first()
    
    if active:
        return {
            'has_active': True,
            'has_expired': False,
            'has_pending': False,
            'never_subscribed': False,
            'subscription': active,
            'state': 'active'
        }
    
    # Check for pending subscription
    pending = Subscription.objects.filter(
        user=user,
        status='PENDING'
    ).first()
    
    if pending:
        return {
            'has_active': False,
            'has_expired': False,
            'has_pending': True,
            'never_subscribed': False,
            'subscription': pending,
            'state': 'pending'
        }
    
    # Check for expired subscription
    expired = Subscription.objects.filter(
        user=user,
        status__in=['EXPIRED', 'CANCELLED']
    ).first()
    
    if expired:
        return {
            'has_active': False,
            'has_expired': True,
            'has_pending': False,
            'never_subscribed': False,
            'subscription': expired,
            'state': 'expired'
        }
    
    # Never subscribed
    return {
        'has_active': False,
        'has_expired': False,
        'has_pending': False,
        'never_subscribed': True,
        'subscription': None,
        'state': 'never'
    }


def can_subscribe_to_plan(user, new_plan):
    """
    Check if user can subscribe to a plan.
    
    Returns: (can_subscribe: bool, reason: str, action: str)
    
    Actions: 'subscribe' | 'renew' | 'upgrade' | 'blocked'
    """
    state = get_subscription_state(user)
    
    # Active subscription
    if state['has_active']:
        current_plan = state['subscription'].plan
        
        if current_plan == new_plan:
            return False, "You're already on this plan", 'blocked'
        
        # Allow upgrade to higher-tier plan
        if new_plan.price > current_plan.price:
            return True, "Upgrade to this plan", 'upgrade'
        
        return False, "Cancel your current plan first to switch", 'blocked'
    
    # Pending payment
    if state['has_pending']:
        return False, "Complete your pending payment first", 'blocked'
    
    # Expired or never subscribed
    if state['has_expired']:
        return True, "Renew your membership", 'renew'
    
    return True, "Subscribe now", 'subscribe'