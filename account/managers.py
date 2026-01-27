from django.contrib.auth.base_user import BaseUserManager
from django.utils.translation import gettext_lazy as _


class CustomUserManager(BaseUserManager):
    """
    Custom user model manager where email is the unique identifier
    for authentication instead of usernames.
    """
    
    def create_user(self, email, password=None, **extra_fields):
        """
        Create and save a subscriber user with the given email and password.
        """
        if not email:
            raise ValueError(_('The Email field must be set'))
        
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
    
    def create_superuser(self, email, password=None, **extra_fields):
        """
        Create and save a SuperUser with the given email and password.
        """
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('is_verified', True)
        extra_fields.setdefault('user_type', 'ADMIN')
        
        if extra_fields.get('is_staff') is not True:
            raise ValueError(_('Superuser must have is_staff=True.'))
        if extra_fields.get('is_superuser') is not True:
            raise ValueError(_('Superuser must have is_superuser=True.'))
        
        return self.create_user(email, password, **extra_fields)
    
    def create_partner(self, email, password=None, **extra_fields):
        """
        Create and save a Partner user.
        """
        extra_fields.setdefault('user_type', 'PARTNER')
        extra_fields.setdefault('is_active', True)
        
        return self.create_user(email, password, **extra_fields)
    
    def get_subscribers(self):
        """Return only suscribers"""
        return self.filter(user_type='SUBSCRIBER')
    
    def get_partners(self):
        """Return only partner users"""
        return self.filter(user_type='PARTNER')
    
    def get_active_partners(self):
        """Return only active and approved partners"""
        return self.filter(
            user_type='PARTNER',
            is_active=True,
            partner_profile__status='APPROVED'
        )