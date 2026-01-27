from django import forms
from django.forms import inlineformset_factory
from django.core.exceptions import ValidationError
from .models import Venue, VenueImage, VenueReview


class VenueForm(forms.ModelForm):
    """Form for creating/editing venues"""
    
    class Meta:
        model = Venue
        fields = [
            'name', 'category', 'tagline', 'description',
            'phone', 'email', 'website',
            'address', 'city', 'state', 'suburb', 'postal_code',
            'latitude', 'longitude',
              'capacity',
            'amenities', 'cover_image',
            'opening_time', 'closing_time', 'open_24_hours',
            
            'meta_description',
            'slug',
        ]
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter venue name'
            }),
            'category': forms.Select(attrs={
                'class': 'form-select'
            }),
            'tagline': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., "Lagos\' Premier Rooftop Bar"'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 6,
                'placeholder': 'Describe your venue...'
            }),
            'phone': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '+234 XXX XXX XXXX'
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'venue@example.com'
            }),
            'website': forms.URLInput(attrs={
                'class': 'form-control',
                'placeholder': 'https://yourwebsite.com'
            }),
            'address': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Street address'
            }),
            'city': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'City'
            }),
            'state': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'State'
            }),
            'suburb': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Suburb/Area (optional)'
            }),
            'postal_code': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Postal code (optional)'
            }),
            'latitude': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., 6.5244',
                'step': 'any'
            }),
            'longitude': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., 3.3792',
                'step': 'any'
            }),
            'capacity': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Maximum capacity (optional)'
            }),
            'amenities': forms.CheckboxSelectMultiple(),
            'cover_image': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': 'image/*'
            }),
            'opening_time': forms.TimeInput(attrs={
                'class': 'form-control',
                'type': 'time'
            }),
            'closing_time': forms.TimeInput(attrs={
                'class': 'form-control',
                'type': 'time'
            }),
            'open_24_hours': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            
            
            'meta_description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'maxlength': 160,
                'placeholder': 'SEO description (max 160 chars)'
            }),

            # ... your existing widgets ...
            'slug': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Auto-generated from name (editable)'
    }),
            
        }
    
    def clean(self):
        cleaned_data = super().clean()
        
        # Validate operating hours
        open_24 = cleaned_data.get('open_24_hours')
        opening = cleaned_data.get('opening_time')
        closing = cleaned_data.get('closing_time')
        
        if not open_24:
            if not opening or not closing:
                raise ValidationError(
                    'Please provide opening and closing times, or mark as open 24 hours.'
                )
        
        # Validate coordinates if provided
        lat = cleaned_data.get('latitude')
        lon = cleaned_data.get('longitude')
        
        if (lat and not lon) or (lon and not lat):
            raise ValidationError(
                'Please provide both latitude and longitude, or leave both empty.'
            )
        
        return cleaned_data
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Make certain fields required
        self.fields['name'].required = True
        self.fields['category'].required = True
        self.fields['description'].required = True
        self.fields['phone'].required = True
        self.fields['address'].required = True
        self.fields['city'].required = True
        self.fields['state'].required = True
        self.fields['cover_image'].required = True
        
        # Add help text
        self.fields['latitude'].help_text = 'Optional: For map display'
        self.fields['longitude'].help_text = 'Optional: For map display'
        

class VenueImageForm(forms.ModelForm):
    """Form for venue gallery images"""
    
    class Meta:
        model = VenueImage
        fields = ['image', 'caption', 'alt_text', 'display_order', 'is_featured']
        widgets = {
            'image': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': 'image/*'
            }),
            'caption': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Image caption (optional)'
            }),
            'alt_text': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Alt text for accessibility'
            }),
            'display_order': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 0
            }),
            'is_featured': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
        }


# Formset for multiple venue images
VenueImageFormSet = inlineformset_factory(
    Venue,
    VenueImage,
    form=VenueImageForm,
    extra=3,
    max_num=10,
    can_delete=True,
    validate_max=True
)


class VenueReviewForm(forms.ModelForm):
    """Form for submitting venue reviews"""
    
    class Meta:
        model = VenueReview
        fields = ['rating', 'title', 'review_text']
        widgets = {
            'rating': forms.RadioSelect(
                choices=[(i, f'{i} Star{"s" if i > 1 else ""}') for i in range(1, 6)]
            ),
            'title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Summarize your experience (optional)'
            }),
            'review_text': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 5,
                'placeholder': 'Share your experience at this venue...',
                'required': True
            }),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['rating'].required = True
        self.fields['review_text'].required = True
        self.fields['title'].required = False


class VenueSearchForm(forms.Form):
    """Advanced search form for venues"""
    
    q = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Search venues, locations...',
            'autocomplete': 'off'
        })
    )
    
    category = forms.ChoiceField(
        required=False,
        choices=[('', 'All Categories')],
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )
    
    city = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'City'
        })
    )
    
    
    
    sort = forms.ChoiceField(
        required=False,
        choices=[
            ('rating', 'Highest Rated'),
            ('popular', 'Most Popular'),
            ('newest', 'Newest'),
            ('name', 'Name (A-Z)'),
        ],
        initial='rating',
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Dynamically populate category choices
        from .models import VenueCategory
        self.fields['category'].choices = [('', 'All Categories')] + [
            (code, label) for code, label in VenueCategory.choices
        ]
        
       