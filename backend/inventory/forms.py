from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from partners.models import Partner

from .models import MROItem, RawMaterial


class RawMaterialBaseForm(forms.Form):
    name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": "form-control"}))
    rm_id = forms.CharField(max_length=50, widget=forms.TextInput(attrs={"class": "form-control"}))
    code = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Optional; defaults to RM ID + Vendor Colour Code/Pantone"}
        ),
    )
    material_type = forms.ChoiceField(
        choices=RawMaterial.MaterialType.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    colour = forms.CharField(
        max_length=80,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Red"}),
    )
    colour_code = forms.CharField(
        max_length=30,
        required=False,
        label="Vendor Colour Code",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. RED"}),
    )
    pantone_number = forms.CharField(
        max_length=50,
        required=False,
        label="Pantone Number",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. PANTONE-186 C"}),
    )
    unit = forms.ChoiceField(choices=RawMaterial.Unit.choices, widget=forms.Select(attrs={"class": "form-select"}))
    cost_per_unit = forms.DecimalField(
        min_value=Decimal("0"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
    )
    vendor = forms.ModelChoiceField(queryset=Partner.objects.none(), widget=forms.Select(attrs={"class": "form-select"}))
    additional_vendors = forms.ModelMultipleChoiceField(
        queryset=Partner.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": "6"}),
        help_text="Optional: add additional suppliers for this material.",
    )
    reorder_level = forms.DecimalField(
        min_value=Decimal("0"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
    )

    def __init__(self, *args, material: RawMaterial | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.material = material
        supplier_queryset = Partner.objects.filter(
            partner_type__in=[Partner.PartnerType.SUPPLIER, Partner.PartnerType.BOTH]
        ).order_by("name")
        self.fields["vendor"].queryset = supplier_queryset
        self.fields["additional_vendors"].queryset = supplier_queryset
        self.fields["code"].help_text = "Optional. If left blank, system uses RM ID + Vendor Colour Code or Pantone Number."
        autocomplete_lists = {
            "name": "rmNameSuggestions",
            "rm_id": "rmIdSuggestions",
            "code": "rmCodeSuggestions",
            "colour": "rmColourSuggestions",
            "colour_code": "rmVendorColourCodeSuggestions",
            "pantone_number": "rmPantoneSuggestions",
            "cost_per_unit": "rmCostPerUnitSuggestions",
            "reorder_level": "rmReorderLevelSuggestions",
        }
        for field_name, datalist_id in autocomplete_lists.items():
            self.fields[field_name].widget.attrs["list"] = datalist_id

    def clean_code(self):
        return (self.cleaned_data.get("code") or "").strip().upper()

    def clean_rm_id(self):
        rm_id = self.cleaned_data["rm_id"].strip().upper()
        if not rm_id:
            raise ValidationError("Raw material ID is required.")
        return rm_id

    def clean_additional_vendors(self):
        additional_vendors = list(self.cleaned_data.get("additional_vendors") or [])
        primary_vendor = self.cleaned_data.get("vendor")
        if primary_vendor:
            return [vendor for vendor in additional_vendors if vendor.id != primary_vendor.id]
        return additional_vendors

    def clean_colour(self):
        return (self.cleaned_data.get("colour") or "").strip()

    def clean_colour_code(self):
        return (self.cleaned_data.get("colour_code") or "").strip().upper()

    def clean_pantone_number(self):
        return (self.cleaned_data.get("pantone_number") or "").strip().upper()

    def clean(self):
        cleaned = super().clean()
        material_type = cleaned.get("material_type")
        colour = (cleaned.get("colour") or "").strip()
        colour_code = (cleaned.get("colour_code") or "").strip().upper()
        pantone_number = (cleaned.get("pantone_number") or "").strip().upper()
        rm_id = (cleaned.get("rm_id") or "").strip().upper()
        code = (cleaned.get("code") or "").strip().upper()
        if material_type == RawMaterial.MaterialType.FABRIC and not colour:
            self.add_error("colour", "Colour is required when material type is Fabric.")

        if not colour_code and not pantone_number:
            self.add_error("colour_code", "Enter Vendor Colour Code or Pantone Number.")
            self.add_error("pantone_number", "Enter Vendor Colour Code or Pantone Number.")

        variant_identifier = colour_code or pantone_number
        resolved_code = code or (f"{rm_id}-{variant_identifier}" if rm_id and variant_identifier else "")
        if not resolved_code:
            self.add_error(
                "code",
                "Material code could not be resolved. Provide Code or valid RM ID with Vendor Colour Code/Pantone Number.",
            )
            return cleaned

        if self.material:
            duplicate_base = RawMaterial.objects.filter(rm_id__iexact=rm_id).exclude(pk=self.material.pk)
            if colour_code and duplicate_base.filter(colour_code__iexact=colour_code).exists():
                self.add_error("colour_code", "This RM ID and Vendor Colour Code combination already exists.")
            if pantone_number and duplicate_base.filter(pantone_number__iexact=pantone_number).exists():
                self.add_error("pantone_number", "This RM ID and Pantone Number combination already exists.")

        cleaned["code"] = resolved_code
        cleaned["rm_id"] = rm_id
        cleaned["colour_code"] = colour_code
        cleaned["pantone_number"] = pantone_number
        return cleaned


class RawMaterialCreateForm(RawMaterialBaseForm):
    invoice_number = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Optional"}),
        help_text="Optional. Captured on the opening stock transaction only.",
    )
    opening_stock = forms.DecimalField(
        min_value=Decimal("0"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["invoice_number"].widget.attrs["autocomplete"] = "off"
        self.fields["opening_stock"].widget.attrs["list"] = "rmOpeningStockSuggestions"


class RawMaterialUpdateForm(RawMaterialBaseForm):
    pass


class RawMaterialCSVUploadForm(forms.Form):
    csv_file = forms.FileField(widget=forms.ClearableFileInput(attrs={"class": "form-control"}))

    def clean_csv_file(self):
        csv_file = self.cleaned_data["csv_file"]
        if not csv_file.name.lower().endswith(".csv"):
            raise ValidationError("Upload a CSV file.")
        return csv_file


class StockAdjustmentForm(forms.Form):
    material_id = forms.IntegerField(widget=forms.HiddenInput())
    delta = forms.DecimalField(
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001", "placeholder": "+/- qty"}),
    )
    reason = forms.CharField(max_length=255, widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Reason"}))

    def clean_delta(self):
        delta = self.cleaned_data["delta"]
        if delta == 0:
            raise ValidationError("Adjustment cannot be zero.")
        return delta


class MROItemBaseForm(forms.Form):
    name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": "form-control"}))
    mro_id = forms.CharField(max_length=50, widget=forms.TextInput(attrs={"class": "form-control"}))
    code = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Optional; defaults to MRO ID"}),
    )
    item_type = forms.ChoiceField(
        choices=MROItem.ItemType.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    unit = forms.ChoiceField(choices=MROItem.Unit.choices, widget=forms.Select(attrs={"class": "form-select"}))
    cost_per_unit = forms.DecimalField(
        required=False,
        min_value=Decimal("0"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001", "placeholder": "Optional"}),
    )
    vendor = forms.ModelChoiceField(
        queryset=Partner.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    location = forms.CharField(
        max_length=120,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Optional storage location/bin"}),
    )
    reorder_level = forms.DecimalField(
        min_value=Decimal("0"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
    )

    def __init__(self, *args, item: MROItem | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.item = item
        supplier_queryset = Partner.objects.filter(
            partner_type__in=[Partner.PartnerType.SUPPLIER, Partner.PartnerType.BOTH]
        ).order_by("name")
        self.fields["vendor"].queryset = supplier_queryset
        self.fields["code"].help_text = "Optional. If left blank, system uses MRO ID."
        self.fields["cost_per_unit"].help_text = "Optional. Defaults to 0 if left blank."

    def clean_mro_id(self):
        mro_id = (self.cleaned_data.get("mro_id") or "").strip().upper()
        if not mro_id:
            raise ValidationError("MRO ID is required.")
        duplicate = MROItem.objects.filter(mro_id=mro_id)
        if self.item:
            duplicate = duplicate.exclude(pk=self.item.pk)
        if duplicate.exists():
            raise ValidationError("MRO ID already exists.")
        return mro_id

    def clean_code(self):
        return (self.cleaned_data.get("code") or "").strip().upper()

    def clean_location(self):
        return (self.cleaned_data.get("location") or "").strip()

    def clean(self):
        cleaned = super().clean()
        mro_id = (cleaned.get("mro_id") or "").strip().upper()
        code = (cleaned.get("code") or "").strip().upper()
        resolved_code = code or mro_id
        if not resolved_code:
            self.add_error("code", "Item code could not be resolved. Provide Code or valid MRO ID.")
            return cleaned
        cleaned["cost_per_unit"] = cleaned.get("cost_per_unit") or Decimal("0")
        cleaned["mro_id"] = mro_id
        cleaned["code"] = resolved_code
        return cleaned


class MROItemCreateForm(MROItemBaseForm):
    opening_stock = forms.DecimalField(
        min_value=Decimal("0"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
    )


class MROItemUpdateForm(MROItemBaseForm):
    pass


class MROStockAdjustmentForm(forms.Form):
    item_id = forms.IntegerField(widget=forms.HiddenInput())
    delta = forms.DecimalField(
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001", "placeholder": "+/- qty"}),
    )
    reason = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Reason"}),
    )

    def clean_delta(self):
        delta = self.cleaned_data["delta"]
        if delta == 0:
            raise ValidationError("Adjustment cannot be zero.")
        return delta
