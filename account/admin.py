from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _
from django.utils.html import format_html
from .models import CustomUser, UserProfile, PartnerProfile
from .forms import CustomUserCreationForm, CustomUserChangeForm


# ==================== INLINE ADMINS ====================

class UserProfileInline(admin.StackedInline):
    """
    Inline admin for UserProfile
    This allows editing subscriber profile directly in the User admin
    """
    model = UserProfile
    can_delete = False
    verbose_name = _('Subscriber Profile')
    verbose_name_plural = _('Subscriber Profile')
    fk_name = 'user'
    
    fieldsets = (
        (_('Personal Information'), {
            'fields': ('first_name', 'last_name', 'phone_number', 'date_of_birth', 'gender', 'profile_picture')
        }),
        (_('Address'), {
            'fields': ('address_line1', 'address_line2', 'city', 'state', 'country'),
            'classes': ('collapse',)
        }),
        (_('Preferences'), {
            'fields': ('receive_notifications', 'receive_marketing_emails'),
            'classes': ('collapse',)
        }),
    )
    
    def get_queryset(self, request):
        """Only show for subscriber user types"""
        qs = super().get_queryset(request)
        return qs.select_related('user')


class PartnerProfileInline(admin.StackedInline):
    """
    Inline admin for PartnerProfile
    This allows editing partner business details directly in the User admin
    """
    model = PartnerProfile
    can_delete = False
    verbose_name = _('Partner Business Profile')
    verbose_name_plural = _('Partner Business Profile')
    fk_name = 'user'
    
    fieldsets = (
        (_('Business Information'), {
            'fields': ('business_name',)
        }),
        
        #(_('Registration Details'), {
           # 'fields': ('business_registration_number', 'tax_identification_number'),
            #'classes': ('collapse',)
       # }),
        (_('Banking Details'), {
            'fields': ('bank_name', 'account_number', 'account_name'),
            'classes': ('collapse',)
        }),
        (_('Approval Status'), {
            'fields': ('status', 'approved_by', 'approved_at', 'rejection_reason')
        }),
        (_('Documents'), {
            'fields': ('business_license',),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ('approved_at',)
    
    def get_queryset(self, request):
        """Only show for partner user types"""
        qs = super().get_queryset(request)
        return qs.select_related('user')


# ==================== MAIN USER ADMIN ====================

@admin.register(CustomUser)
class CustomUserAdmin(BaseUserAdmin):
    """
    Custom User Admin with INLINE profiles
    This creates a unified interface for managing users and their profiles
    """
    
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = CustomUser
    
    list_display = (
        'email', 'display_name', 'user_type',
        'is_active', 'is_verified', 'date_joined'
    )
    list_filter = (
        'user_type', 'is_staff', 'is_active',
        'is_verified', 'date_joined'
    )
    
    # Fieldsets for editing existing users
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        (_('User Type'), {'fields': ('user_type',)}),
        (_('Permissions'), {
            'fields': ('is_active', 'is_verified', 'is_staff', 'is_superuser',
                      'groups', 'user_permissions'),
        }),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )
    
    # Fieldsets for adding new users
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'email', 'user_type', 'password1', 'password2',
                'is_staff', 'is_active', 'is_verified'
            ),
        }),
    )
    
    search_fields = ('email',)
    ordering = ('-date_joined',)
    filter_horizontal = ('groups', 'user_permissions',)
    
    def display_name(self, obj):
        """Show the user's display name"""
        return obj.get_full_name()
    display_name.short_description = _('Name')
    display_name.admin_order_field = 'email'
    
    def get_inline_instances(self, request, obj=None):
        """
        Dynamically show the correct inline based on user_type
        - Show UserProfileInline for SUBSCRIBER users
        - Show PartnerProfileInline for PARTNER users
        - Show nothing for ADMIN users
        """
        if not obj:
            return []
        
        inlines = []
        
        if obj.user_type == CustomUser.UserType.SUBSCRIBER:
            # Create UserProfile if it doesn't exist
            UserProfile.objects.get_or_create(user=obj)
            inlines.append(UserProfileInline(self.model, self.admin_site))
        
        elif obj.user_type == CustomUser.UserType.PARTNER:
            # Only show inline if PartnerProfile exists (created during registration)
            if hasattr(obj, 'partner_profile'):
                inlines.append(PartnerProfileInline(self.model, self.admin_site))
        
        return inlines
    
    def get_queryset(self, request):
        """Optimize queries"""
        qs = super().get_queryset(request)
        return qs.select_related('profile', 'partner_profile')
    
    def save_model(self, request, obj, form, change):
        """
        Auto-create profile for subscribers when created in admin
        """
        super().save_model(request, obj, form, change)
        
        # Auto-create UserProfile for subscribers
        if obj.user_type == CustomUser.UserType.SUBSCRIBER:
            UserProfile.objects.get_or_create(user=obj)


# ==================== SEPARATE PROFILE ADMINS (For advanced filtering) ====================

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """
    Standalone admin for subscriber profiles
    Use this for advanced searching/filtering of subscribers
    """
    
    list_display = (
        'get_email', 'get_full_name', 'phone_number', 
        'city', 'state', 'created_at'
    )
    list_filter = ('gender', 'state', 'country', 'receive_notifications', 'created_at')
    search_fields = (
        'user__email', 'first_name', 'last_name', 
        'phone_number', 'city'
    )
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        (_('User Account'), {
            'fields': ('user',)
        }),
        (_('Personal Information'), {
            'fields': ('first_name', 'last_name', 'phone_number', 'date_of_birth', 'gender', 'profile_picture')
        }),
        (_('Address'), {
            'fields': ('address_line1', 'address_line2', 'city', 'state', 'country')
        }),
        (_('Preferences'), {
            'fields': ('receive_notifications', 'receive_marketing_emails')
        }),
        (_('Timestamps'), {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_email(self, obj):
        return obj.user.email
    get_email.short_description = _('Email')
    get_email.admin_order_field = 'user__email'
    
    def get_full_name(self, obj):
        return obj.get_full_name()
    get_full_name.short_description = _('Full Name')


@admin.register(PartnerProfile)
class PartnerProfileAdmin(admin.ModelAdmin):
    """
    Standalone admin for partner profiles
    Use this for managing partner applications and approvals
    """
    
    list_display = (
        'business_name', 'get_email', 
        'status_badge',  'created_at'
    )
    list_filter = ('status',  'created_at')
    search_fields = (
        'business_name', 'user__email', 
    )
    readonly_fields = ('created_at', 'updated_at', 'approved_at')
    
    fieldsets = (
        (_('User Account'), {
            'fields': ('user',)
        }),
        (_('Business Information'), {
            'fields': ('business_name',)
        }),
        
        #(_('Registration Details'), {
           # 'fields': ('business_registration_number', 'tax_identification_number'),
            #'classes': ('collapse',)
        #}),
        (_('Banking Details'), {
            'fields': ('bank_name', 'account_number', 'account_name'),
            'classes': ('collapse',)
        }),
        (_('Approval Status'), {
            'fields': ('status', 'approved_by', 'approved_at', 'rejection_reason')
        }),
        (_('Documents'), {
            'fields': ('business_license',)
        }),
        (_('Timestamps'), {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['approve_partners', 'reject_partners', 'suspend_partners']
    
    def get_email(self, obj):
        return obj.user.email
    get_email.short_description = _('Email')
    get_email.admin_order_field = 'user__email'
    
    def status_badge(self, obj):
        """Display status as colored badge"""
        colors = {
            'PENDING': '#ffc107',
            'APPROVED': '#28a745',
            'REJECTED': '#dc3545',
            'SUSPENDED': '#6c757d',
        }
        color = colors.get(obj.status, '#6c757d')
        return format_html(
            '<span style="background-color: {}; color: white; '
            'padding: 5px 12px; border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = _('Status')
    
    def approve_partners(self, request, queryset):
        """Bulk approve partners"""
        count = 0
        for partner in queryset.filter(status=PartnerProfile.PartnerStatus.PENDING):
            partner.approve(request.user)
            count += 1
        
        self.message_user(
            request,
            f'{count} partner(s) have been approved successfully.'
        )
    approve_partners.short_description = _('✓ Approve selected partners')
    
    def reject_partners(self, request, queryset):
        """Bulk reject partners"""
        count = 0
        for partner in queryset.filter(status=PartnerProfile.PartnerStatus.PENDING):
            partner.reject('Rejected by admin', request.user)
            count += 1
        
        self.message_user(
            request,
            f'{count} partner(s) have been rejected.'
        )
    reject_partners.short_description = _('✗ Reject selected partners')
    
    def suspend_partners(self, request, queryset):
        """Bulk suspend partners"""
        updated = queryset.update(status=PartnerProfile.PartnerStatus.SUSPENDED)
        self.message_user(
            request,
            f'{updated} partner(s) have been suspended.'
        )
    suspend_partners.short_description = _('⊘ Suspend selected partners')