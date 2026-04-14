from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from inventory.models import RawMaterial

from .models import (
    BOMItem,
    FINISHED_PRODUCT_IMAGE_SIZE,
    FinishedProduct,
    Marker,
    MarkerOutput,
    ProductionOrder,
)


def _raw_material_base_label(material: RawMaterial) -> str:
    identifier = material.rm_id or material.code
    return f"Raw Material - {material.name} ({identifier})"


def _raw_material_variant_label(material: RawMaterial) -> str:
    return material.variant_display or material.code or material.rm_id or "Default"


def _raw_material_choice_label(material: RawMaterial) -> str:
    return f"{_raw_material_base_label(material)} - {_raw_material_variant_label(material)}"


def _part_choice_label(part: FinishedProduct) -> str:
    return f"Part - {part.name}{f' [{part.colour}]' if part.colour else ''} ({part.sku})"


def marker_material_queryset():
    return RawMaterial.objects.filter(
        Q(material_type__in=[RawMaterial.MaterialType.FABRIC, RawMaterial.MaterialType.MESH])
        | Q(name__icontains="foam")
        | Q(name__icontains="air mesh")
        | Q(name__icontains="airmesh")
    ).order_by("name", "rm_id", "colour", "colour_code", "pantone_number", "id")


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
        (f"raw:{material.id}", _raw_material_choice_label(material))
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
                _part_choice_label(part),
            )
            for part in part_qs
        )

    return choices


def build_bom_component_catalog(
    *,
    target_product: FinishedProduct | None = None,
    exclude_bom_item_id: int | None = None,
) -> list[dict[str, object]]:
    used_material_ids: set[int] = set()
    used_part_ids: set[int] = set()
    if target_product:
        existing = BOMItem.objects.filter(product=target_product)
        if exclude_bom_item_id:
            existing = existing.exclude(pk=exclude_bom_item_id)
        used_material_ids = set(existing.filter(material__isnull=False).values_list("material_id", flat=True))
        used_part_ids = set(existing.filter(part__isnull=False).values_list("part_id", flat=True))

    material_qs = RawMaterial.objects.order_by("name", "rm_id", "colour", "colour_code", "pantone_number", "id")
    if used_material_ids:
        material_qs = material_qs.exclude(id__in=used_material_ids)

    grouped_materials: dict[tuple[str, str], dict[str, object]] = {}
    ordered_material_keys: list[tuple[str, str]] = []
    for material in material_qs:
        group_key = (material.rm_id, material.name)
        if group_key not in grouped_materials:
            grouped_materials[group_key] = {
                "label": _raw_material_base_label(material),
                "kind": "raw_material",
                "variants": [],
            }
            ordered_material_keys.append(group_key)
        grouped_materials[group_key]["variants"].append(
            {
                "value": f"raw:{material.id}",
                "label": _raw_material_variant_label(material),
            }
        )

    catalog: list[dict[str, object]] = []
    for group_index, group_key in enumerate(ordered_material_keys):
        group_data = grouped_materials[group_key]
        catalog.append(
            {
                "value": f"raw-group:{group_index}",
                "label": group_data["label"],
                "kind": group_data["kind"],
                "variants": group_data["variants"],
            }
        )

    include_parts = target_product is None or target_product.item_type == FinishedProduct.ItemType.FINISHED
    if include_parts:
        part_qs = FinishedProduct.objects.filter(item_type=FinishedProduct.ItemType.PART).order_by("name")
        if target_product:
            part_qs = part_qs.exclude(id=target_product.id)
        if used_part_ids:
            part_qs = part_qs.exclude(id__in=used_part_ids)
        catalog.extend(
            {
                "value": f"part:{part.id}",
                "label": _part_choice_label(part),
                "kind": "part",
                "variants": [],
            }
            for part in part_qs
        )

    return catalog


class FinishedProductForm(forms.ModelForm):
    product_image = forms.ImageField(
        required=False,
        help_text=f"Optional. Uploaded image will be resized to {FINISHED_PRODUCT_IMAGE_SIZE[0]} x {FINISHED_PRODUCT_IMAGE_SIZE[1]} px.",
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
    )

    class Meta:
        model = FinishedProduct
        fields = ["name", "sku", "product_image", "colour", "item_type"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "sku": forms.TextInput(attrs={"class": "form-control"}),
            "colour": forms.TextInput(attrs={"class": "form-control"}),
            "item_type": forms.HiddenInput(),
        }

    def clean_sku(self):
        return self.cleaned_data["sku"].upper()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        prefixed_item_type_name = self.add_prefix("item_type")
        item_type = (
            self.data.get(prefixed_item_type_name)
            if self.is_bound
            else self.initial.get("item_type")
        )
        if item_type == FinishedProduct.ItemType.PART:
            self.fields["sku"].widget.attrs.update(
                {
                    "list": "finishedProductSkuSuggestions",
                    "autocomplete": "off",
                    "placeholder": "Type finished product SKU",
                }
            )

    def clean(self):
        cleaned_data = super().clean()
        item_type = cleaned_data.get("item_type")
        sku = (cleaned_data.get("sku") or "").strip().upper()
        colour = (cleaned_data.get("colour") or "").strip()
        if item_type == FinishedProduct.ItemType.FINISHED and sku:
            duplicate_qs = FinishedProduct.objects.filter(
                item_type=FinishedProduct.ItemType.FINISHED,
                sku__iexact=sku,
            )
            if self.instance.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)
            if duplicate_qs.exists():
                self.add_error("sku", "A finished product with this SKU already exists.")
        if item_type == FinishedProduct.ItemType.PART and not colour:
            self.add_error("colour", "Colour is required for parts.")
        if item_type == FinishedProduct.ItemType.PART and sku:
            finished_product_exists = FinishedProduct.objects.filter(
                item_type=FinishedProduct.ItemType.FINISHED,
                sku__iexact=sku,
            ).exists()
            if not finished_product_exists:
                self.add_error("sku", "Select an existing finished product SKU.")
        cleaned_data["sku"] = sku
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


class MarkerForm(forms.ModelForm):
    class Meta:
        model = Marker
        fields = ["marker_id", "material", "colour", "sku_id", "length_per_layer", "sets_per_layer"]
        labels = {
            "marker_id": "Marker ID",
            "sku_id": "Finished Product SKU",
            "length_per_layer": "Length / Layer",
            "sets_per_layer": "Sets / Layer",
        }
        widgets = {
            "marker_id": forms.TextInput(attrs={"class": "form-control"}),
            "material": forms.Select(attrs={"class": "form-select"}),
            "colour": forms.TextInput(attrs={"class": "form-control"}),
            "sku_id": forms.TextInput(attrs={"class": "form-control"}),
            "length_per_layer": forms.NumberInput(attrs={"class": "form-control", "step": "0.001", "min": "0.001"}),
            "sets_per_layer": forms.NumberInput(attrs={"class": "form-control", "step": "0.001", "min": "0.001"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = marker_material_queryset()
        self.fields["material"].label_from_instance = _raw_material_choice_label
        self.fields["sku_id"].required = True
        self.fields["sku_id"].widget.attrs.update(
            {
                "list": "finishedProductSkuSuggestions",
                "autocomplete": "off",
                "placeholder": "Type finished product SKU",
            }
        )

    def clean_marker_id(self):
        return self.cleaned_data["marker_id"].strip().upper()

    def clean_sku_id(self):
        sku_id = (self.cleaned_data.get("sku_id") or "").strip().upper()
        if not sku_id:
            raise ValidationError("SKU ID is required.")
        if not FinishedProduct.objects.filter(
            item_type=FinishedProduct.ItemType.FINISHED,
            sku__iexact=sku_id,
        ).exists():
            raise ValidationError("Select an existing finished product SKU.")
        return sku_id

    def clean_colour(self):
        return (self.cleaned_data.get("colour") or "").strip()


class MarkerOutputForm(forms.ModelForm):
    class Meta:
        model = MarkerOutput
        fields = ["part", "quantity_per_set"]
        labels = {
            "quantity_per_set": "Qty / Set",
        }
        widgets = {
            "part": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "quantity_per_set": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "step": "0.001", "min": "0.001"}
            ),
        }

    def __init__(self, *args, marker: Marker, **kwargs):
        self.marker = marker
        super().__init__(*args, **kwargs)
        used_part_ids = MarkerOutput.objects.filter(marker=marker).values_list("part_id", flat=True)
        self.fields["part"].queryset = (
            FinishedProduct.objects.filter(item_type=FinishedProduct.ItemType.PART)
            .exclude(id__in=used_part_ids)
            .order_by("name", "colour", "sku")
        )
        self.fields["part"].label_from_instance = _part_choice_label

    def clean_part(self):
        part = self.cleaned_data["part"]
        if part.item_type != FinishedProduct.ItemType.PART:
            raise ValidationError("Marker outputs must be parts.")
        if MarkerOutput.objects.filter(marker=self.marker, part=part).exists():
            raise ValidationError("This part is already mapped to the marker.")
        return part

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.marker = self.marker
        if commit:
            instance.save()
        return instance


class ProductionOrderCreateForm(forms.Form):
    order_type = forms.ChoiceField(
        choices=ProductionOrder.TargetType.choices,
        initial=ProductionOrder.TargetType.FINISHED_PRODUCT,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    product = forms.ModelChoiceField(
        queryset=FinishedProduct.objects.filter(item_type=FinishedProduct.ItemType.FINISHED).order_by("name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    marker = forms.ModelChoiceField(
        queryset=Marker.objects.order_by("marker_id"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    quantity = forms.IntegerField(
        label="Quantity / Sets",
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
    )
    notes = forms.CharField(required=False, max_length=255, widget=forms.TextInput(attrs={"class": "form-control"}))

    def clean(self):
        cleaned_data = super().clean()
        order_type = cleaned_data.get("order_type") or ProductionOrder.TargetType.FINISHED_PRODUCT
        product = cleaned_data.get("product")
        marker = cleaned_data.get("marker")

        if order_type == ProductionOrder.TargetType.FINISHED_PRODUCT:
            if not product:
                self.add_error("product", "Select a finished product.")
            cleaned_data["marker"] = None
            return cleaned_data

        if order_type == ProductionOrder.TargetType.MARKER:
            if not marker:
                self.add_error("marker", "Select a marker.")
            cleaned_data["product"] = None
            return cleaned_data

        raise ValidationError("Select a valid production order type.")


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


class PartProductionCompletionForm(forms.Form):
    order_id = forms.IntegerField(widget=forms.HiddenInput())
    actual_length = forms.DecimalField(
        min_value=Decimal("0.001"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.001", "min": "0.001"}),
    )
    actual_layers = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "min": "1"}),
    )
    actual_sets_per_layer = forms.DecimalField(
        min_value=Decimal("0.001"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.001", "min": "0.001"}),
    )
    total_sets = forms.DecimalField(
        min_value=Decimal("0.001"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.001", "min": "0.001"}),
    )
    actual_fabric_issued = forms.DecimalField(
        min_value=Decimal("0.001"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.001", "min": "0.001"}),
    )
    good_sets = forms.DecimalField(
        min_value=Decimal("0"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.001", "min": "0"}),
    )
    rejected_sets = forms.DecimalField(
        min_value=Decimal("0"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.001", "min": "0"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        total_sets = cleaned_data.get("total_sets")
        good_sets = cleaned_data.get("good_sets")
        rejected_sets = cleaned_data.get("rejected_sets")
        if total_sets is None or good_sets is None or rejected_sets is None:
            return cleaned_data
        if (good_sets + rejected_sets).quantize(Decimal("0.001")) != total_sets:
            raise ValidationError("Good sets plus rejected sets must equal total sets.")
        return cleaned_data
