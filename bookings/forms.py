from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import date, timedelta

from .models import Booking


class BookingCreateForm(forms.ModelForm):
    """
    Form for creating new bookings
    Only shows fields user needs to fill
    """
    
    class Meta:
        model = Booking
        fields = ['venue', 'visit_date', 'guests_count', 'special_requests']
        widgets = {
            'venue': forms.Select(attrs={
                'class': 'form-select',
            }),
            'visit_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
                'min': date.today().isoformat(),
            }),
            'guests_count': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 1,
                'max': 20,
                'value': 1
            }),
            'special_requests': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Any special requests? (dietary restrictions, occasion, etc.)'
            }),
        }
    
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        
        # Filter venues to only approved ones
        from venues.models import Venue
        self.fields['venue'].queryset = Venue.objects.filter(
            status='APPROVED'
        ).order_by('name')
        
        # Make fields required
        self.fields['venue'].required = True
        self.fields['visit_date'].required = True
        self.fields['guests_count'].required = True
        self.fields['special_requests'].required = False
    
    def clean_visit_date(self):
        """Validate visit date"""
        visit_date = self.cleaned_data.get('visit_date')
        
        if visit_date and visit_date < date.today():
            raise ValidationError('Cannot book visits in the past')
        
        # Optional: Limit how far in advance bookings can be made
        max_advance_days = 90  # 3 months
        if visit_date and visit_date > date.today() + timedelta(days=max_advance_days):
            raise ValidationError(f'Cannot book more than {max_advance_days} days in advance')
        
        return visit_date
    
    def clean(self):
        """Additional validation"""
        cleaned_data = super().clean()
        
        # Check if user has active subscription
        if self.user:
            from subscriptions.models import Subscription
            active_sub = Subscription.objects.filter(
                user=self.user,
                status__in=['ACTIVE', 'TRIAL'],
                end_date__gte=timezone.now()
            ).first()
            
            if not active_sub:
                raise ValidationError(
                    'You need an active subscription to book venues. '
                    'Please subscribe or renew your membership.'
                )
            
            # Check booking quota
            can_book, remaining, message = Booking.check_booking_available(
                self.user, 
                active_sub
            )
            
            if not can_book:
                raise ValidationError(message)
        
        return cleaned_data


class BookingCancelForm(forms.Form):
    """
    Form for cancelling bookings
    """
    cancellation_reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'Why are you cancelling? (optional)'
        }),
        label='Reason for cancellation'
    )


class VenueCheckInForm(forms.Form):
    """
    Form for venue staff to check in bookings
    """
    booking_reference = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter booking reference (e.g., GP-BKABCD12)',
            'style': 'text-transform: uppercase;'
        }),
        label='Booking Reference'
    )
    check_in_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 2,
            'placeholder': 'Any notes? (optional)'
        }),
        label='Check-in Notes'
    )
    
    def clean_booking_reference(self):
        """Validate and fetch booking"""
        reference = self.cleaned_data.get('booking_reference', '').upper().strip()
        
        try:
            booking = Booking.objects.get(booking_reference=reference)
            
            # Validate booking can be checked in
            if not booking.can_check_in():
                if booking.status != 'CONFIRMED':
                    raise ValidationError(
                        f'This booking is {booking.get_status_display()}. '
                        'Only confirmed bookings can be checked in.'
                    )
                elif booking.visit_date != date.today():
                    raise ValidationError(
                        f'This booking is for {booking.visit_date}. '
                        'Bookings can only be checked in on the visit date.'
                    )
            
            self.cleaned_data['booking'] = booking
            
        except Booking.DoesNotExist:
            raise ValidationError('Invalid booking reference. Please check and try again.')
        
        return reference