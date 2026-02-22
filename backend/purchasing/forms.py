from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from inventory.models import RawMaterial
from partners.models import Partner

from .models import PurchaseLineInput, PurchaseOrderItem


class PurchaseOrderCreateForm(forms.Form):
    vendor = forms.ModelChoiceField(
        queryset=Partner.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    order_date = forms.DateField(widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}))
    notes = forms.CharField(required=False, max_length=255, widget=forms.TextInput(attrs={"class": "form-control"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vendor"].queryset = Partner.objects.filter(
            partner_type__in=[Partner.PartnerType.SUPPLIER, Partner.PartnerType.BOTH]
        ).order_by("name")


class PurchaseLineForm(forms.Form):
    material = forms.ModelChoiceField(
        queryset=RawMaterial.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    quantity = forms.DecimalField(
        min_value=Decimal("0.001"),
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
    )

    def __init__(self, *args, vendor: Partner | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        if vendor:
            self.fields["material"].queryset = (
                RawMaterial.objects.select_related("vendor")
                .filter(Q(vendor=vendor) | Q(vendor_links__vendor=vendor))
                .distinct()
                .order_by("name")
            )


def parse_purchase_lines(material_ids: list[str], quantities: list[str], *, vendor: Partner) -> list[PurchaseLineInput]:
    if len(material_ids) != len(quantities):
        raise ValidationError("Invalid line item payload.")

    if vendor.partner_type not in {Partner.PartnerType.SUPPLIER, Partner.PartnerType.BOTH}:
        raise ValidationError("Selected vendor is not valid for purchase orders.")

    material_ids_int: list[int] = []
    for material_id in material_ids:
        if not material_id:
            continue
        try:
            material_ids_int.append(int(material_id))
        except ValueError as exc:
            raise ValidationError("Invalid raw material in line items.") from exc

    material_map = {
        material.id: material
        for material in (
            RawMaterial.objects.select_related("vendor")
            .filter(Q(vendor=vendor) | Q(vendor_links__vendor=vendor), id__in=material_ids_int)
            .distinct()
        )
    }

    lines: list[PurchaseLineInput] = []
    for material_id, quantity in zip(material_ids, quantities):
        if not material_id or not quantity:
            continue
        try:
            material = material_map[int(material_id)]
            qty = Decimal(quantity)
        except KeyError as exc:
            raise ValidationError("Selected raw material is not sold by the chosen vendor.") from exc
        except Exception as exc:
            raise ValidationError("Invalid raw material or quantity in line items.") from exc

        if qty <= 0:
            raise ValidationError("Line quantity must be greater than zero.")

        lines.append(PurchaseLineInput(material=material, quantity=qty))

    if not lines:
        raise ValidationError("Add at least one line item.")

    return lines


def parse_receive_quantities(items: list[PurchaseOrderItem], payload) -> dict[int, Decimal]:
    quantities: dict[int, Decimal] = {}
    for item in items:
        field_name = f"receive_{item.id}"
        raw_value = payload.get(field_name, "")
        if raw_value in {None, ""}:
            continue

        try:
            qty = Decimal(str(raw_value))
        except Exception as exc:
            raise ValidationError(f"Invalid quantity for {item.material.name}.") from exc

        if qty < 0:
            raise ValidationError(f"Receive quantity for {item.material.name} cannot be negative.")
        if qty == 0:
            continue
        if qty > item.pending_quantity:
            raise ValidationError(
                f"Receive quantity for {item.material.name} cannot exceed pending {item.pending_quantity}."
            )

        quantities[item.id] = qty

    if not quantities:
        raise ValidationError("Enter at least one quantity greater than zero.")

    return quantities
