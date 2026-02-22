from django import forms

from .models import Partner


class PartnerForm(forms.ModelForm):
    class Meta:
        model = Partner
        fields = [
            "vendor_id",
            "name",
            "partner_type",
            "gst_number",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "pincode",
            "contact_person",
            "phone",
            "email",
        ]
        widgets = {
            "vendor_id": forms.TextInput(attrs={"class": "form-control", "style": "text-transform:uppercase"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "partner_type": forms.Select(attrs={"class": "form-select"}),
            "gst_number": forms.TextInput(attrs={"class": "form-control", "style": "text-transform:uppercase"}),
            "address_line1": forms.TextInput(attrs={"class": "form-control"}),
            "address_line2": forms.TextInput(attrs={"class": "form-control"}),
            "city": forms.TextInput(attrs={"class": "form-control"}),
            "state": forms.TextInput(attrs={"class": "form-control"}),
            "pincode": forms.TextInput(attrs={"class": "form-control"}),
            "contact_person": forms.TextInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
        }

    def clean_gst_number(self):
        return self.cleaned_data["gst_number"].upper()

    def clean_vendor_id(self):
        return self.cleaned_data["vendor_id"].strip().upper()


class PartnerCSVUploadForm(forms.Form):
    csv_file = forms.FileField(widget=forms.ClearableFileInput(attrs={"class": "form-control"}))

    def clean_csv_file(self):
        csv_file = self.cleaned_data["csv_file"]
        if not csv_file.name.lower().endswith(".csv"):
            raise forms.ValidationError("Upload a CSV file.")
        return csv_file
