from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from .managers import CustomUserManager


NIGERIAN_STATES = [
    ('AB', 'Abia'),
    ('AD', 'Adamawa'),
    ('AK', 'Akwa Ibom'),
    ('AN', 'Anambra'),
    ('BA', 'Bauchi'),
    ('BY', 'Bayelsa'),
    ('BE', 'Benue'),
    ('BO', 'Borno'),
    ('CR', 'Cross River'),
    ('DE', 'Delta'),
    ('EB', 'Ebonyi'),
    ('ED', 'Edo'),
    ('EK', 'Ekiti'),
    ('EN', 'Enugu'),
    ('FC', 'FCT (Abuja)'),
    ('GO', 'Gombe'),
    ('IM', 'Imo'),
    ('JI', 'Jigawa'),
    ('KD', 'Kaduna'),
    ('KN', 'Kano'),
    ('KT', 'Katsina'),
    ('KE', 'Kebbi'),
    ('KO', 'Kogi'),
    ('KW', 'Kwara'),
    ('LA', 'Lagos'),
    ('NA', 'Nasarawa'),
    ('NI', 'Niger'),
    ('OG', 'Ogun'),
    ('ON', 'Ondo'),
    ('OS', 'Osun'),
    ('OY', 'Oyo'),
    ('PL', 'Plateau'),
    ('RI', 'Rivers'),
    ('SO', 'Sokoto'),
    ('TA', 'Taraba'),
    ('YO', 'Yobe'),
    ('ZA', 'Zamfara'),
]

COUNTRY_CHOICES = [
    ('NG', 'Nigeria')
]


class CustomUser(AbstractBaseUser, PermissionsMixin):
    """
    Custom user model - Base authentication for all users
    This is ONLY for authentication. Profile details are in separate models.
    """
    
    class UserType(models.TextChoices):
        SUBSCRIBER = 'SUBSCRIBER', _('Subscriber')
        PARTNER = 'PARTNER', _('Partner')
        ADMIN = 'ADMIN', _('Admin')
    
    # Core Authentication Fields (Required for all users)
    email = models.EmailField(_('email address'), unique=True)
    user_type = models.CharField(
        _('user type'),
        max_length=10,
        choices=UserType.choices,
        default=UserType.SUBSCRIBER,
    )
    
    # System Fields
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []  # Only email required for authentication
    
    objects = CustomUserManager()
    
    class Meta:
        verbose_name = _('user')
        verbose_name_plural = _('users')
        ordering = ['-date_joined']
    
    def __str__(self):
        # Show name if available, otherwise email
        if self.user_type == self.UserType.SUBSCRIBER and hasattr(self, 'profile'):
            return f"{self.profile.get_full_name()} ({self.email})"
        elif self.user_type == self.UserType.PARTNER and hasattr(self, 'partner_profile'):
            return f"{self.partner_profile.business_name} ({self.email})"
        return self.email
    
    def get_full_name(self):
        """Return full name based on user type"""
        if self.user_type == self.UserType.SUBSCRIBER and hasattr(self, 'profile'):
            return self.profile.get_full_name()
        elif self.user_type == self.UserType.PARTNER and hasattr(self, 'partner_profile'):
            return self.partner_profile.business_name
        return self.email
    
    def get_short_name(self):
        """Return short name"""
        if self.user_type == self.UserType.SUBSCRIBER and hasattr(self, 'profile'):
            return self.profile.first_name
        elif self.user_type == self.UserType.PARTNER and hasattr(self, 'partner_profile'):
            return self.partner_profile.business_name
        return self.email.split('@')[0]
    
    @property
    def is_partner(self):
        return self.user_type == self.UserType.PARTNER
    
    @property
    def is_subscriber(self):
        return self.user_type == self.UserType.SUBSCRIBER


class UserProfile(models.Model):
    """
    Profile for SUBSCRIBERS only
    Contains personal information for individual members
    """
    user = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='profile',
        limit_choices_to={'user_type': CustomUser.UserType.SUBSCRIBER}
    )
    
    # Personal Information
    first_name = models.CharField(_('first name'), max_length=150)
    last_name = models.CharField(_('last name'), max_length=150)
    phone_number = models.CharField(_('phone number'), max_length=20, blank=True)
    date_of_birth = models.DateField(_('date of birth'), null=True, blank=True)
    gender = models.CharField(
        _('gender'),
        max_length=10,
        choices=[('M', 'Male'), ('F', 'Female'), ('O', 'Other')],
        blank=True
    )
    profile_picture = models.ImageField(
        upload_to='profiles/subscribers/',
        blank=True,
        null=True
    )
    
    # Address Information
    address_line1 = models.CharField(_('address line 1'), max_length=255, blank=True)
    address_line2 = models.CharField(_('address line 2'), max_length=255, blank=True)
    city = models.CharField(_('city'), max_length=100, blank=True)
    state = models.CharField(_('state'), max_length=2, choices=NIGERIAN_STATES, default='LA', help_text="Select the state where you are located")
    country = models.CharField(_('country'), max_length=100, choices=COUNTRY_CHOICES, default='NG', help_text='Select the country where you are located')
    
    # Preferences
    receive_notifications = models.BooleanField(default=True)
    receive_marketing_emails = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('subscriber profile')
        verbose_name_plural = _('subscriber profiles')
    
    def __str__(self):
        return f"{self.get_full_name()} - {self.user.email}"
    
    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()


class PartnerProfile(models.Model):
    """
    Profile for PARTNERS only
    Contains business information for venue owners/managers
    NO personal names required - only business details
    """
    
    class PartnerStatus(models.TextChoices):
        PENDING = 'PENDING', _('Pending Approval')
        APPROVED = 'APPROVED', _('Approved')
        REJECTED = 'REJECTED', _('Rejected')
        SUSPENDED = 'SUSPENDED', _('Suspended')
    
    
    
    user = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='partner_profile',
        limit_choices_to={'user_type': CustomUser.UserType.PARTNER}
    )
    
    # Business Information (Core Fields)
    business_name = models.CharField(_('business name'), max_length=255)
    
    
    # Contact Person (Optional - for communication only)

    
    '''# Business Registration
    business_registration_number = models.CharField(
        _('CAC registration number'),
        max_length=50,
        blank=True,
        help_text='Corporate Affairs Commission (CAC) number'
    )
    tax_identification_number = models.CharField(
        _('TIN'),
        max_length=50,
        blank=True
    )'''
    

    # Bank Details (for payouts)
    bank_name = models.CharField(_('bank name'), max_length=100, blank=True, null=True, default='')
    account_number = models.CharField(_('account number'), max_length=20, blank=True, null=True, default='')
    account_name = models.CharField(_('account name'), max_length=255, blank=True, null=True, default='')
    
    # Approval Status
    status = models.CharField(
        _('status'),
        max_length=10,
        choices=PartnerStatus.choices,
        default=PartnerStatus.PENDING
    )
    approved_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_partners',
        limit_choices_to={'is_staff': True}
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(_('rejection reason'), blank=True)
    
    # Documents
    business_license = models.FileField(
        upload_to='partners/documents/',
        blank=True,
        null=True,
        help_text='Upload business license/CAC certificate'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('partner profile')
        verbose_name_plural = _('partner profiles')
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.business_name} ({self.get_status_display()})"
    
    def approve(self, approved_by_user):
        """Approve partner application"""
        self.status = self.PartnerStatus.APPROVED
        self.approved_by = approved_by_user
        self.approved_at = timezone.now()
        self.save()
    
    def reject(self, reason, rejected_by_user):
        """Reject partner application"""
        self.status = self.PartnerStatus.REJECTED
        self.rejection_reason = reason
        self.approved_by = rejected_by_user
        self.approved_at = timezone.now()
        self.save()
    
    @property
    def is_approved(self):
        return self.status == self.PartnerStatus.APPROVED