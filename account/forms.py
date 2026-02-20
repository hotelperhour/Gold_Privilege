from django import forms
from django.contrib.auth.forms import (
    UserCreationForm, 
    UserChangeForm, 
    AuthenticationForm,
    PasswordResetForm,
    SetPasswordForm
)
from django.utils.translation import gettext_lazy as _
from .models import CustomUser, UserProfile, PartnerProfile
import os


class CustomUserCreationForm(UserCreationForm):
    """Base user creation form - Only email for authentication"""
    
    class Meta:
        model = CustomUser
        fields = ('email',)  # Only email - no personal fields in CustomUser
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'


class CustomUserChangeForm(UserChangeForm):
    """Base user change form - Only email for authentication"""
    
    class Meta:
        model = CustomUser
        fields = ('email',)  # Only email - no personal fields in CustomUser


class UserRegistrationForm(forms.ModelForm):
    """Registration form for subscribers"""
    
    # Personal Information (for UserProfile)
    first_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        label=_('First Name')
    )
    last_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        label=_('Last Name')
    )
    phone_number = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        label=_('Phone Number'),
        required=False
    )
    
    # Password fields
    password1 = forms.CharField(
        label=_('Password'),
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
        help_text=_('Password must be at least 8 characters')
    )
    password2 = forms.CharField(
        label=_('Confirm Password'),
        widget=forms.PasswordInput(attrs={'class': 'form-control'})
    )
    terms_accepted = forms.BooleanField(
        required=True,
        label=_('I agree to the Terms and Conditions')
    )
    
    class Meta:
        model = CustomUser
        fields = ('email',)  # Only email for CustomUser
        widgets = {
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
        }
    

    def clean_email(self):
        email = self.cleaned_data.get('email').lower()
        if CustomUser.objects.filter(email=email).exists():
            raise forms.ValidationError(_("A user with this email already exists."))
        return email

    def clean_password2(self):
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError(_("Passwords don't match"))
        
        if len(password1) < 8:
            raise forms.ValidationError(_("Password must be at least 8 characters"))
        
        return password2
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        user.user_type = CustomUser.UserType.SUBSCRIBER

        if commit:
            user.save()
            # Use get_or_create to safely handle any profile already created by signals
            profile, created = UserProfile.objects.get_or_create(user=user)
            # Update profile fields with the form data
            profile.first_name = self.cleaned_data['first_name']
            profile.last_name = self.cleaned_data['last_name']
            profile.phone_number = self.cleaned_data.get('phone_number', '')
            profile.save()

        return user


class PartnerRegistrationForm(forms.ModelForm):
    """Registration form for partners - Business details only"""
    
    password1 = forms.CharField(
        label=_('Password'),
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
        help_text=_('Password must be at least 8 characters')
    )
    
    password2 = forms.CharField(
        label=_('Confirm Password'),
        widget=forms.PasswordInput(attrs={'class': 'form-control'})
    )
    
    # Business Information (for PartnerProfile)
    business_name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        label=_('Business Name')
    )
    
    
    
    terms_accepted = forms.BooleanField(
        required=True,
        label=_('I agree to the Partner Terms and Conditions')
    )
    
    class Meta:
        model = CustomUser
        fields = ('email',)  # Only email for login
        widgets = {
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'Business/Admin Email'
            }),
        }
    
    def clean_email(self):
        email = self.cleaned_data.get('email').lower()
        if CustomUser.objects.filter(email=email).exists():
            raise forms.ValidationError(_("A user with this email already exists."))
        return email
    
    def clean_password2(self):
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError(_("Passwords don't match"))
        
        if len(password1) < 8:
            raise forms.ValidationError(_("Password must be at least 8 characters"))
        
        return password2
    
    def save(self, commit=True):
        """
        Override save to handle both user and partner profile creation
        """
        # Create the user
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        user.user_type = CustomUser.UserType.PARTNER
        
        if commit:
            user.save()
            
            # Create partner profile
            partner_profile_data = {
                'user': user,
                'business_name': self.cleaned_data['business_name'],
                
            }
            
            PartnerProfile.objects.create(**partner_profile_data)
        
        return user
    
# Add this to your forms.py after the existing forms

class PartnerProfileUpdateForm(forms.ModelForm):
    """Form for partners to update their business information"""
    
    email = forms.EmailField(
        label=_('Business Email'),
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'readonly': 'readonly',  # Email cannot be changed here
        }),
        disabled=True,
    )
    
    business_name = forms.CharField(
        max_length=255,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your business name'
        }),
        label=_('Business Name')
    )
    
    # Bank Details
    bank_name = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g., First Bank, Access Bank'
        }),
        label=_('Bank Name')
    )
    
    account_number = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '1234567890'
        }),
        label=_('Account Number')
    )
    
    account_name = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Account holder name'
        }),
        label=_('Account Name')
    )
    
    # Business License Upload
    business_license = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={
            'class': 'form-control',
            'accept': '.pdf,.jpg,.jpeg,.png'
        }),
        label=_('Business License/CAC Certificate'),
        help_text=_('Upload PDF, JPG, or PNG (Max 5MB)')
    )
    
    class Meta:
        model = PartnerProfile
        fields = ['business_name', 'bank_name', 'account_number', 'account_name', 'business_license']
        exclude = ['user', 'status', 'approved_by', 'approved_at', 'rejection_reason']
    
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        if self.user and hasattr(self.user, 'partner_profile'):
            # Pre-fill email from user object
            self.fields['email'].initial = self.user.email
    
    def clean_account_number(self):
        account_number = self.cleaned_data.get('account_number')
        if account_number and not account_number.isdigit():
            raise forms.ValidationError(_("Account number must contain only digits"))
        return account_number
    
    def clean_business_license(self):
        business_license = self.cleaned_data.get('business_license')
        if business_license:
            # Check file size (5MB limit)
            if business_license.size > 5 * 1024 * 1024:  # 5MB
                raise forms.ValidationError(_("File size must be under 5MB"))
            
            # Check file extension
            allowed_extensions = ['.pdf', '.jpg', '.jpeg', '.png']
            ext = os.path.splitext(business_license.name)[1].lower()
            if ext not in allowed_extensions:
                raise forms.ValidationError(_("Only PDF, JPG, and PNG files are allowed"))
        
        return business_license


class PartnerContactUpdateForm(forms.ModelForm):
    """Form for partners to update contact information (if needed later)"""
    
    class Meta:
        model = PartnerProfile
        fields = []  # Add contact fields if you add them later
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

class CustomLoginForm(AuthenticationForm):
    """Custom login form"""
    
    username = forms.EmailField(
        label=_('Email'),
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your email'
        })
    )
    password = forms.CharField(
        label=_('Password'),
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your password'
        })
    )
    
    remember_me = forms.BooleanField(
        required=False,
        initial=False,
        label=_('Remember me')
    )


class CustomPasswordResetForm(PasswordResetForm):
    """Custom password reset form"""
    
    email = forms.EmailField(
        label=_('Email'),
        max_length=254,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your email address'
        })
    )


class CustomSetPasswordForm(SetPasswordForm):
    """Custom set password form"""
    
    new_password1 = forms.CharField(
        label=_('New Password'),
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter new password'
        }),
        help_text=_('Password must be at least 8 characters')
    )
    
    new_password2 = forms.CharField(
        label=_('Confirm New Password'),
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Confirm new password'
        })
    )

class UserProfileUpdateForm(forms.ModelForm):
    """Form for subscribers to update their profile"""
    
    email = forms.EmailField(
        label=_('Email'),
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'readonly': 'readonly',
        }),
        disabled=True,
    )
    
    first_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your first name'
        }),
        label=_('First Name')
    )
    
    last_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your last name'
        }),
        label=_('Last Name')
    )
    
    phone_number = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g., +234 800 000 0000'
        }),
        label=_('Phone Number')
    )
    
    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'class': 'form-control',
            'type': 'date'
        }),
        label=_('Date of Birth')
    )
    
    gender = forms.ChoiceField(
        choices=[('', 'Select Gender'), ('M', 'Male'), ('F', 'Female'), ('O', 'Other')],
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-select'
        }),
        label=_('Gender')
    )
    
    profile_picture = forms.ImageField(
        required=False,
        widget=forms.ClearableFileInput(attrs={
            'class': 'form-control',
            'accept': 'image/*'
        }),
        label=_('Profile Picture')
    )
    
    # Address Information
    address_line1 = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Street address, P.O. Box'
        }),
        label=_('Address Line 1')
    )
    
    address_line2 = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Apartment, suite, unit, building, floor, etc.'
        }),
        label=_('Address Line 2')
    )
    
    city = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'City'
        }),
        label=_('City')
    )
    
    state = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'State'
        }),
        label=_('State')
    )
    
    # Preferences
    receive_notifications = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        }),
        label=_('Receive notifications')
    )
    
    receive_marketing_emails = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        }),
        label=_('Receive marketing emails')
    )
    
    class Meta:
        model = UserProfile
        fields = [
            'first_name', 'last_name', 'phone_number', 'date_of_birth', 
            'gender', 'profile_picture', 'address_line1', 'address_line2', 
            'city', 'state', 'country', 'receive_notifications', 
            'receive_marketing_emails'
        ]
    
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        if self.user:
            self.fields['email'].initial = self.user.email
        
        # Set default country to Nigeria
        self.fields['country'].widget = forms.HiddenInput()
        if not self.instance.pk or not self.instance.country:
            self.instance.country = 'Nigeria'
    
    def clean_profile_picture(self):
        profile_picture = self.cleaned_data.get('profile_picture')
        if profile_picture:
            # Check file size (2MB limit)
            if profile_picture.size > 2 * 1024 * 1024:
                raise forms.ValidationError(_("Profile picture must be under 2MB"))
            
            # Check file extension
            allowed_extensions = ['.jpg', '.jpeg', '.png', '.gif']
            ext = os.path.splitext(profile_picture.name)[1].lower()
            if ext not in allowed_extensions:
                raise forms.ValidationError(_("Only JPG, PNG, and GIF files are allowed"))
        
        return profile_picture