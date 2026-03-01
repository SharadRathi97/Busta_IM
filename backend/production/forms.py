from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from inventory.models import RawMaterial

from .models import BOMItem, FinishedProduct, ProductionOrder


def _component_value_for_item(item: BOMItem) -> str:
    if item.material_id:
        return f"raw:{item.material_id}"
    if item.part_id:
        return f"part:{item.part_id}"
    return ""


def resolve_bom_component(component_value: str) -> tuple[RawMaterial | None, FinishedProduct | None]:
    raw_value = (component_value or "").strip()
    if ":" not in raw_value:
        raise ValidationError("Select a valid BOM component.")

    prefix, pk_raw = raw_value.split(":", 1)
    if not pk_raw.isdigit():
        raise ValidationError("Select a valid BOM component.")

    pk = int(pk_raw)
    if prefix == "raw":
        material = RawMaterial.objects.filter(pk=pk).first()
        if not material:
            raise ValidationError("Selected raw material is no longer available.")
        return material, None

    if prefix == "part":
        part = FinishedProduct.objects.filter(pk=pk, item_type=FinishedProduct.ItemType.PART).first()
        if not part:
            raise ValidationError("Selected part is no longer available.")
        return None, part

    raise ValidationError("Select a valid BOM component.")


def build_bom_component_choices(
    *,
    target_product: FinishedProduct | None = None,
    exclude_bom_item_id: int | None = None,
) -> list[tuple[str, str]]:
    used_material_ids: set[int] = set()
    used_part_ids: set[int] = set()
    if target_product:
        existing = BOMItem.objects.filter(product=target_product)
        if exclude_bom_item_id:
            existing = existing.exclude(pk=exclude_bom_item_id)
        used_material_ids = set(existing.filter(material__isnull=False).values_list("material_id", flat=True))
        used_part_ids = set(existing.filter(part__isnull=False).values_list("part_id", flat=True))

    material_qs = RawMaterial.objects.order_by("name")
    if used_material_ids:
        material_qs = material_qs.exclude(id__in=used_material_ids)

    choices: list[tuple[str, str]] = [
        (f"raw:{material.id}", f"Raw Material - {material.name} ({material.code})")
        for material in material_qs
    ]

    include_parts = target_product is None or target_product.item_type == FinishedProduct.ItemType.FINISHED
    if include_parts:
        part_qs = FinishedProduct.objects.filter(item_type=FinishedProduct.ItemType.PART).order_by("name")
        if target_product:
            part_qs = part_qs.exclude(id=target_product.id)
        if used_part_ids:
            part_qs = part_qs.exclude(id__in=used_part_ids)
        choices.extend(
            (
                f"part:{part.id}",
                f"Part - {part.name}{f' [{part.colour}]' if part.colour else ''} ({part.sku})",
            )
            for part in part_qs
        )

    return choices


class FinishedProductForm(forms.ModelForm):
    class Meta:
        model = FinishedProduct
        fields = ["name", "sku", "colour", "item_type"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "sku": forms.TextInput(attrs={"class": "form-control"}),
            "colour": forms.TextInput(attrs={"class": "form-control"}),
            "item_type": forms.HiddenInput(),
        }

    def clean_sku(self):
        return self.cleaned_data["sku"].upper()

    def clean(self):
        cleaned_data = super().clean()
        item_type = cleaned_data.get("item_type")
        colour = (cleaned_data.get("colour") or "").strip()
        if item_type == FinishedProduct.ItemType.PART and not colour:
            self.add_error("colour", "Colour is required for parts.")
        cleaned_data["colour"] = colour
        return cleaned_data


class BOMItemForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=FinishedProduct.objects.order_by("item_type", "name"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    component = forms.CharField(widget=forms.Select(attrs={"class": "form-select"}))
    qty_per_unit = forms.DecimalField(
        min_value=Decimal("0.001"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001", "min": "0.001"}),
    )

    def __init__(self, *args, **kwargs):
        target_product = kwargs.pop("target_product", None)
        super().__init__(*args, **kwargs)
        component_choices = build_bom_component_choices(target_product=target_product)
        self.fields["component"].widget.choices = [("", "Select component")] + component_choices

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get("product")
        component_value = cleaned_data.get("component")
        if not product or not component_value:
            return cleaned_data

        material, part = resolve_bom_component(component_value)
        if part and part.id == product.id:
            raise ValidationError("A part cannot include itself as a BOM component.")

        duplicate_qs = BOMItem.objects.filter(product=product)
        if material and duplicate_qs.filter(material=material).exists():
            raise ValidationError("This BOM mapping already exists.")
        if part and duplicate_qs.filter(part=part).exists():
            raise ValidationError("This BOM mapping already exists.")

        cleaned_data["material"] = material
        cleaned_data["part"] = part
        return cleaned_data


class BOMItemUpdateForm(forms.Form):
    component = forms.CharField(widget=forms.Select(attrs={"class": "form-select form-select-sm"}))
    qty_per_unit = forms.DecimalField(
        min_value=Decimal("0.001"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.001", "min": "0.001"}),
    )

    def __init__(self, *args, instance: BOMItem, **kwargs):
        self.instance = instance
        super().__init__(*args, **kwargs)
        component_choices = build_bom_component_choices(
            target_product=instance.product,
            exclude_bom_item_id=instance.id,
        )
        self.fields["component"].widget.choices = [("", "Select component")] + component_choices
        if not self.is_bound:
            self.initial["component"] = _component_value_for_item(instance)
            self.initial["qty_per_unit"] = instance.qty_per_unit

    def clean(self):
        cleaned_data = super().clean()
        component_value = cleaned_data.get("component")
        if not component_value:
            return cleaned_data

        material, part = resolve_bom_component(component_value)
        product = self.instance.product
        if part and part.id == product.id:
            raise ValidationError("A part cannot include itself as a BOM component.")

        duplicate_qs = BOMItem.objects.filter(product=product).exclude(pk=self.instance.id)
        if material and duplicate_qs.filter(material=material).exists():
            raise ValidationError("This BOM mapping already exists for the selected item.")
        if part and duplicate_qs.filter(part=part).exists():
            raise ValidationError("This BOM mapping already exists for the selected item.")

        cleaned_data["material"] = material
        cleaned_data["part"] = part
        return cleaned_data


class BOMCSVUploadForm(forms.Form):
    csv_file = forms.FileField(widget=forms.ClearableFileInput(attrs={"class": "form-control"}))

    def clean_csv_file(self):
        csv_file = self.cleaned_data["csv_file"]
        if not csv_file.name.lower().endswith(".csv"):
            raise forms.ValidationError("Upload a CSV file.")
        return csv_file


class ProductionOrderCreateForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=FinishedProduct.objects.order_by("item_type", "name"),
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
