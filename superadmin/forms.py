from django import forms

from .models import PayoutConfig


class PayoutConfigForm(forms.ModelForm):
    class Meta:
        model = PayoutConfig
        fields = [
            "apply_commission_to_store",
            "store_commission_rate",
            "apply_commission_to_subscription",
            "subscription_commission_rate",
            "payout_delay_hours",
            "minimum_payout_amount",
        ]
        widgets = {
            "store_commission_rate": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "subscription_commission_rate": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "payout_delay_hours": forms.NumberInput(attrs={"min": "1"}),
            "minimum_payout_amount": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }

    def clean_store_commission_rate(self):
        value = self.cleaned_data["store_commission_rate"]
        if value < 0 or value > 100:
            raise forms.ValidationError("Commission rate must be between 0 and 100.")
        return value

    def clean_subscription_commission_rate(self):
        value = self.cleaned_data["subscription_commission_rate"]
        if value < 0 or value > 100:
            raise forms.ValidationError("Commission rate must be between 0 and 100.")
        return value


class PayoutApprovalForm(forms.Form):
    admin_notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3, "maxlength": "2000"}))


class PayoutPaymentForm(forms.Form):
    transfer_reference = forms.CharField(
        max_length=150,
        min_length=3,
        strip=True,
    )
    transfer_notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3, "maxlength": "2000"}))

    def clean_transfer_reference(self):
        value = self.cleaned_data["transfer_reference"].strip()
        if not value:
            raise forms.ValidationError("Transfer reference cannot be blank.")
        return value
