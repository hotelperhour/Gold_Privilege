from django import forms
from django.core.validators import RegexValidator


class SetPinForm(forms.Form):
    """Set or change the wallet PIN."""

    new_pin = forms.CharField(
        min_length=4, max_length=6,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control pin-input',
            'placeholder': '••••',
            'inputmode': 'numeric',
            'maxlength': '6',
            'autocomplete': 'new-password',
        }),
        validators=[RegexValidator(r'^\d{4,6}$', 'PIN must be 4–6 digits.')],
        label='New PIN',
    )
    confirm_pin = forms.CharField(
        min_length=4, max_length=6,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control pin-input',
            'placeholder': '••••',
            'inputmode': 'numeric',
            'maxlength': '6',
            'autocomplete': 'new-password',
        }),
        label='Confirm PIN',
    )
    current_pin = forms.CharField(
        required=False, min_length=4, max_length=6,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control pin-input',
            'placeholder': '••••',
            'inputmode': 'numeric',
            'maxlength': '6',
        }),
        label='Current PIN',
        help_text='Required only when changing an existing PIN.',
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('new_pin') and cleaned.get('confirm_pin'):
            if cleaned['new_pin'] != cleaned['confirm_pin']:
                raise forms.ValidationError('PINs do not match.')
        return cleaned


class TransferCoinsForm(forms.Form):
    """Transfer Gold Coins to another subscriber by GP ID."""

    gp_id = forms.CharField(
        max_length=10,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. GP-A3X9K2',
            'autocomplete': 'off',
            'style': 'text-transform: uppercase;',
        }),
        label='Recipient GP ID',
    )
    amount = forms.DecimalField(
        min_value=1, max_digits=12, decimal_places=2,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': '0',
            'min': '1',
            'step': '1',
        }),
        label='Amount (coins)',
    )
    note = forms.CharField(
        required=False, max_length=200,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Optional message...',
        }),
        label='Message (optional)',
    )
    pin = forms.CharField(
        min_length=4, max_length=6,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control pin-input',
            'placeholder': '••••',
            'inputmode': 'numeric',
            'maxlength': '6',
            'autocomplete': 'off',
        }),
        validators=[RegexValidator(r'^\d{4,6}$', 'PIN must be 4–6 digits.')],
        label='Wallet PIN',
    )

    def clean_gp_id(self):
        return self.cleaned_data['gp_id'].strip().upper()


class BuyCoinsForm(forms.Form):
    """Initiate a coin purchase — package or custom amount."""

    package_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    custom_amount = forms.DecimalField(
        required=False, min_value=100, max_digits=10, decimal_places=2,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter amount in ₦',
            'min': '100',
            'step': '50',
        }),
        label='Custom Amount (₦)',
    )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('package_id') and not cleaned.get('custom_amount'):
            raise forms.ValidationError('Select a package or enter a custom amount.')
        return cleaned


class WalletHistoryFilterForm(forms.Form):
    """Filters on the transaction history page."""

    TYPE_CHOICES = [('', 'All Types')] + [
        ('PURCHASE',      'Coin Purchase'),
        ('SPEND',         'Store Purchase'),
        ('CASHBACK',      'Cashback'),
        ('REFERRAL',      'Referral Bonus'),
        ('TRANSFER_IN',   'Received'),
        ('TRANSFER_OUT',  'Sent'),
        ('MONTHLY_BONUS', 'Monthly Bonus'),
        ('REFUND',        'Refund'),
        ('ADMIN_CREDIT',  'Admin Credit'),
        ('ADMIN_DEBIT',   'Admin Debit'),
    ]

    txn_type = forms.ChoiceField(
        choices=TYPE_CHOICES, required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Type',
    )
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        label='From',
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        label='To',
    )