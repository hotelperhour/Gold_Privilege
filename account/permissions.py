from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.shortcuts import redirect

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
    
    def handle_no_permission(self):
        """Override to provide friendly redirect for pending partners"""
        if self.request.user.is_authenticated and self.request.user.user_type == 'PARTNER':
            # Partner exists but not approved - redirect to pending page
            messages.warning(
                self.request,
                'Your partnership application is still pending approval. '
                'You will gain full access once approved.'
            )
            return redirect('account:partner_pending')
        
        # Not a partner at all - default behavior
        return super().handle_no_permission()


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
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())
        
        if request.user.user_type != 'PARTNER':
            messages.error(request, "This page is only for partners")
            return redirect('account:login')
        
        try:
            if not request.user.partner_profile.is_approved:
                messages.warning(
                    request,
                    'Your partnership application is still pending approval. '
                    'You will gain full access once approved.'
                )
                return redirect('account:partner_pending')
        except AttributeError:
            messages.error(request, "Partner profile not found")
            return redirect('account:login')
        
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