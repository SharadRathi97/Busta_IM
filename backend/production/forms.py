from decimal import Decimal

from django import forms

from inventory.models import RawMaterial

from .models import BOMItem, FinishedProduct, ProductionOrder


class FinishedProductForm(forms.ModelForm):
    class Meta:
        model = FinishedProduct
        fields = ["name", "sku"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "sku": forms.TextInput(attrs={"class": "form-control"}),
        }

    def clean_sku(self):
        return self.cleaned_data["sku"].upper()


class BOMItemForm(forms.ModelForm):
    class Meta:
        model = BOMItem
        fields = ["product", "material", "qty_per_unit"]
        widgets = {
            "product": forms.Select(attrs={"class": "form-select"}),
            "material": forms.Select(attrs={"class": "form-select"}),
            "qty_per_unit": forms.NumberInput(attrs={"class": "form-control", "step": "0.001", "min": "0.001"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = RawMaterial.objects.order_by("name")


class BOMItemUpdateForm(forms.ModelForm):
    class Meta:
        model = BOMItem
        fields = ["material", "qty_per_unit"]
        widgets = {
            "material": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "qty_per_unit": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.001", "min": "0.001"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = RawMaterial.objects.order_by("name")


class BOMCSVUploadForm(forms.Form):
    csv_file = forms.FileField(widget=forms.ClearableFileInput(attrs={"class": "form-control"}))

    def clean_csv_file(self):
        csv_file = self.cleaned_data["csv_file"]
        if not csv_file.name.lower().endswith(".csv"):
            raise forms.ValidationError("Upload a CSV file.")
        return csv_file


class ProductionOrderCreateForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=FinishedProduct.objects.order_by("name"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    quantity = forms.IntegerField(min_value=1, widget=forms.NumberInput(attrs={"class": "form-control", "min": "1"}))
    notes = forms.CharField(required=False, max_length=255, widget=forms.TextInput(attrs={"class": "form-control"}))


class ProductionStatusForm(forms.Form):
    order_id = forms.IntegerField(widget=forms.HiddenInput())
    status = forms.ChoiceField(choices=ProductionOrder.Status.choices, widget=forms.Select(attrs={"class": "form-select"}))
    produced_qty = forms.DecimalField(
        required=False,
        min_value=Decimal("0.001"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.001", "min": "0.001"}),
    )
    scrap_qty = forms.DecimalField(
        required=False,
        min_value=Decimal("0"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.001", "min": "0"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get("status")
        produced_qty = cleaned_data.get("produced_qty")
        scrap_qty = cleaned_data.get("scrap_qty")

        if status == ProductionOrder.Status.COMPLETED and produced_qty is None:
            self.add_error("produced_qty", "Produced quantity is required when completing an order.")

        if status != ProductionOrder.Status.COMPLETED:
            cleaned_data["produced_qty"] = None
            cleaned_data["scrap_qty"] = Decimal("0")
        else:
            cleaned_data["scrap_qty"] = scrap_qty if scrap_qty is not None else Decimal("0")

        return cleaned_data
