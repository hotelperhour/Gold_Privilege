from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from functools import wraps


class IsSubscriberUserMixin(UserPassesTestMixin):
    """Mixin to check if user is a subscriber """
    
    def test_func(self):
        return (
            self.request.user.is_authenticated and
            self.request.user.user_type == 'SUBSCRIBER'
        )


class IsPartnerMixin(UserPassesTestMixin):
    """Mixin to check if user is a partner"""
    
    def test_func(self):
        return (
            self.request.user.is_authenticated and
            self.request.user.user_type == 'PARTNER'
        )


class IsApprovedPartnerMixin(UserPassesTestMixin):
    """Mixin to check if user is an approved partner"""
    
    def test_func(self):
        if not self.request.user.is_authenticated:
            return False
        
        if self.request.user.user_type != 'PARTNER':
            return False
        
        try:
            return (
                hasattr(self.request.user, 'partner_profile') and
                self.request.user.partner_profile.is_approved
            )
        except:
            return False


class IsAdminUserMixin(UserPassesTestMixin):
    """Mixin to check if user is admin/staff"""
    
    def test_func(self):
        return (
            self.request.user.is_authenticated and
            (self.request.user.is_staff or self.request.user.user_type == 'ADMIN')
        )


# Decorator versions for function-based views
def subscriber_required(view_func):
    """Decorator to require subscriber"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            raise PermissionDenied("You must be logged in")
        
        if request.user.user_type != 'SUBSCRIBER':
            raise PermissionDenied("This page is only for subscriber")
        
        return view_func(request, *args, **kwargs)
    return wrapper


def partner_required(view_func):
    """Decorator to require partner user"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            raise PermissionDenied("You must be logged in")
        
        if request.user.user_type != 'PARTNER':
            raise PermissionDenied("This page is only for partners")
        
        return view_func(request, *args, **kwargs)
    return wrapper


def approved_partner_required(view_func):
    """Decorator to require approved partner"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            raise PermissionDenied("You must be logged in")
        
        if request.user.user_type != 'PARTNER':
            raise PermissionDenied("This page is only for partners")
        
        try:
            if not request.user.partner_profile.is_approved:
                raise PermissionDenied(
                    "Your partnership application is pending approval"
                )
        except AttributeError:
            raise PermissionDenied("Partner profile not found")
        
        return view_func(request, *args, **kwargs)
    return wrapper


def admin_required(view_func):
    """Decorator to require admin user"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            raise PermissionDenied("You must be logged in")
        
        if not (request.user.is_staff or request.user.user_type == 'ADMIN'):
            raise PermissionDenied("This page is only for administrators")
        
        return view_func(request, *args, **kwargs)
    return wrapper