"""
notifications/forms.py

The form an admin fills to send a notification.
One form, lives in the superadmin dashboard.
"""

from django import forms
from .models import Notification


class SendNotificationForm(forms.Form):

    AUDIENCE_CHOICES = [
        ('ALL',         'All Users'),
        ('SUBSCRIBERS', 'Subscribers Only'),
        ('PARTNERS',    'Partners Only'),
    ]

    audience = forms.ChoiceField(
        choices=AUDIENCE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Send to',
    )
    title = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            'class':       'form-control',
            'placeholder': 'e.g. New venues added this week!',
        }),
    )
    body = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class':       'form-control',
            'rows':        3,
            'placeholder': 'Optional longer message...',
        }),
    )
    link = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.TextInput(attrs={
            'class':       'form-control',
            'placeholder': 'Optional link e.g. /store/',
        }),
        label='Link (optional)',
    )