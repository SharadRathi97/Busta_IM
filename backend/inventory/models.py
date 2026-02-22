from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction

from partners.models import Partner


class RawMaterial(models.Model):
    class MaterialType(models.TextChoices):
        FABRIC = "fabric", "Fabric"
        MESH = "mesh", "Mesh"
        THREAD = "thread", "Thread"
        HARDWARE = "hardware", "Hardware"
        ACCESSORY = "accessory", "Accessory"
        PACKAGING = "packaging", "Packaging"
        OTHER = "other", "Other"

    class Unit(models.TextChoices):
        KG = "kg", "kg"
        METER = "m", "m"
        PIECES = "pieces", "pieces"
        LITRE = "litre", "litre"

    name = models.CharField(max_length=150)
    rm_id = models.CharField(max_length=50)
    code = models.CharField(max_length=50)
    material_type = models.CharField(max_length=32, choices=MaterialType.choices, default=MaterialType.OTHER)
    colour = models.CharField(max_length=80, blank=True)
    colour_code = models.CharField(max_length=30)
    unit = models.CharField(max_length=16, choices=Unit.choices)
    cost_per_unit = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    current_stock = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    reorder_level = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    vendor = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name="materials")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["rm_id", "colour_code"],
                name="uniq_raw_material_rm_id_colour_code",
            )
        ]

    def __str__(self) -> str:
        identifier = self.rm_id or self.code
        return f"{self.name} ({identifier})"

    @property
    def is_low_stock(self) -> bool:
        return self.current_stock <= self.reorder_level

    @property
    def supplier_names(self) -> str:
        names: set[str] = {self.vendor.name}
        names.update(self.vendor_links.select_related("vendor").values_list("vendor__name", flat=True))
        return ", ".join(sorted(names))


class RawMaterialVendor(models.Model):
    material = models.ForeignKey(RawMaterial, on_delete=models.CASCADE, related_name="vendor_links")
    vendor = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name="material_links")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("material", "vendor")
        ordering = ["material__name", "vendor__name"]

    def __str__(self) -> str:
        return f"{self.material} supplied by {self.vendor}"


class InventoryLedger(models.Model):
    class TxnType(models.TextChoices):
        IN = "IN", "IN"
        OUT = "OUT", "OUT"
        ADJUST = "ADJUST", "ADJUST"

    material = models.ForeignKey(RawMaterial, on_delete=models.CASCADE, related_name="ledger_entries")
    txn_type = models.CharField(max_length=10, choices=TxnType.choices)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    unit = models.CharField(max_length=16)
    reason = models.CharField(max_length=255)
    reference_type = models.CharField(max_length=50, blank=True)
    reference_id = models.PositiveIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class MROItem(models.Model):
    class ItemType(models.TextChoices):
        TOOL = "tool", "Tool"
        FACTORY_PART = "factory_part", "Factory Part"
        MACHINE_SPARE = "machine_spare", "Machine Spare Part"
        OTHER = "other", "Other"

    class Unit(models.TextChoices):
        PIECES = "pieces", "pieces"
        SET = "set", "set"
        KG = "kg", "kg"
        METER = "m", "m"
        LITRE = "litre", "litre"

    name = models.CharField(max_length=150)
    mro_id = models.CharField(max_length=50, unique=True)
    code = models.CharField(max_length=50)
    item_type = models.CharField(max_length=32, choices=ItemType.choices, default=ItemType.OTHER)
    unit = models.CharField(max_length=16, choices=Unit.choices, default=Unit.PIECES)
    cost_per_unit = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    current_stock = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    reorder_level = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    location = models.CharField(max_length=120, blank=True)
    vendor = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name="mro_items")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "mro_id"]

    def __str__(self) -> str:
        return f"{self.name} ({self.mro_id})"

    @property
    def is_low_stock(self) -> bool:
        return self.current_stock <= self.reorder_level


class MROInventoryLedger(models.Model):
    class TxnType(models.TextChoices):
        IN = "IN", "IN"
        OUT = "OUT", "OUT"
        ADJUST = "ADJUST", "ADJUST"

    item = models.ForeignKey(MROItem, on_delete=models.CASCADE, related_name="ledger_entries")
    txn_type = models.CharField(max_length=10, choices=TxnType.choices)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    unit = models.CharField(max_length=16)
    reason = models.CharField(max_length=255)
    reference_type = models.CharField(max_length=50, blank=True)
    reference_id = models.PositiveIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


def create_raw_material_with_opening_stock(
    *,
    name: str,
    rm_id: str,
    code: str,
    material_type: str,
    colour: str,
    colour_code: str,
    unit: str,
    cost_per_unit: Decimal,
    vendor: Partner,
    additional_vendors: list[Partner] | None = None,
    opening_stock: Decimal,
    reorder_level: Decimal,
    created_by,
) -> RawMaterial:
    if vendor.partner_type not in {Partner.PartnerType.SUPPLIER, Partner.PartnerType.BOTH}:
        raise ValueError("Selected partner is not a supplier.")

    extra_vendors = additional_vendors or []
    for extra_vendor in extra_vendors:
        if extra_vendor.partner_type not in {Partner.PartnerType.SUPPLIER, Partner.PartnerType.BOTH}:
            raise ValueError("All selected additional vendors must be suppliers.")

    resolved_rm_id = rm_id.strip().upper()
    resolved_colour_code = colour_code.strip().upper()
    resolved_code = code.strip().upper() or (
        f"{resolved_rm_id}-{resolved_colour_code}" if resolved_rm_id and resolved_colour_code else ""
    )
    if not resolved_rm_id:
        raise ValueError("RM ID is required.")
    if not resolved_colour_code:
        raise ValueError("Colour code is required.")
    if not resolved_code:
        raise ValueError("Material code could not be resolved.")

    with transaction.atomic():
        material = RawMaterial.objects.create(
            name=name,
            rm_id=resolved_rm_id,
            code=resolved_code,
            material_type=material_type,
            colour=colour.strip(),
            colour_code=resolved_colour_code,
            unit=unit,
            cost_per_unit=cost_per_unit,
            vendor=vendor,
            current_stock=opening_stock,
            reorder_level=reorder_level,
        )
        add_vendor_to_material(material=material, vendor=vendor)
        for extra_vendor in extra_vendors:
            add_vendor_to_material(material=material, vendor=extra_vendor)

        if opening_stock > 0:
            InventoryLedger.objects.create(
                material=material,
                txn_type=InventoryLedger.TxnType.IN,
                quantity=opening_stock,
                unit=material.unit,
                reason="Opening stock",
                reference_type="opening_stock",
                reference_id=material.id,
                created_by=created_by,
            )
    return material


def add_vendor_to_material(*, material: RawMaterial, vendor: Partner) -> None:
    if vendor.partner_type not in {Partner.PartnerType.SUPPLIER, Partner.PartnerType.BOTH}:
        raise ValueError("Selected partner is not a supplier.")
    RawMaterialVendor.objects.get_or_create(material=material, vendor=vendor)


def update_raw_material_details(
    *,
    material: RawMaterial,
    name: str,
    rm_id: str,
    code: str,
    material_type: str,
    colour: str,
    colour_code: str,
    unit: str,
    cost_per_unit: Decimal,
    vendor: Partner,
    additional_vendors: list[Partner] | None = None,
    reorder_level: Decimal,
) -> RawMaterial:
    if vendor.partner_type not in {Partner.PartnerType.SUPPLIER, Partner.PartnerType.BOTH}:
        raise ValueError("Selected partner is not a supplier.")

    extra_vendors = additional_vendors or []
    for extra_vendor in extra_vendors:
        if extra_vendor.partner_type not in {Partner.PartnerType.SUPPLIER, Partner.PartnerType.BOTH}:
            raise ValueError("All selected additional vendors must be suppliers.")

    resolved_rm_id = rm_id.strip().upper()
    resolved_colour_code = colour_code.strip().upper()
    resolved_code = code.strip().upper() or (
        f"{resolved_rm_id}-{resolved_colour_code}" if resolved_rm_id and resolved_colour_code else ""
    )
    if not resolved_rm_id:
        raise ValueError("RM ID is required.")
    if not resolved_colour_code:
        raise ValueError("Colour code is required.")
    if not resolved_code:
        raise ValueError("Material code could not be resolved.")

    desired_vendor_ids = {vendor.id}
    desired_vendor_ids.update(v.id for v in extra_vendors)

    with transaction.atomic():
        locked = RawMaterial.objects.select_for_update().get(pk=material.pk)
        locked.name = name
        locked.rm_id = resolved_rm_id
        locked.code = resolved_code
        locked.material_type = material_type
        locked.colour = colour.strip()
        locked.colour_code = resolved_colour_code
        locked.unit = unit
        locked.cost_per_unit = cost_per_unit
        locked.vendor = vendor
        locked.reorder_level = reorder_level
        locked.save(
            update_fields=[
                "name",
                "rm_id",
                "code",
                "material_type",
                "colour",
                "colour_code",
                "unit",
                "cost_per_unit",
                "vendor",
                "reorder_level",
            ]
        )

        RawMaterialVendor.objects.filter(material=locked).exclude(vendor_id__in=desired_vendor_ids).delete()
        for vendor_id in desired_vendor_ids:
            RawMaterialVendor.objects.get_or_create(material=locked, vendor_id=vendor_id)

    return locked


def adjust_stock(*, material: RawMaterial, delta: Decimal, reason: str, created_by) -> RawMaterial:
    if delta == 0:
        raise ValueError("Adjustment quantity cannot be zero.")

    with transaction.atomic():
        locked = RawMaterial.objects.select_for_update().get(pk=material.pk)
        new_stock = locked.current_stock + delta
        if new_stock < 0:
            raise ValueError("Stock cannot become negative.")

        locked.current_stock = new_stock
        locked.save(update_fields=["current_stock"])

        InventoryLedger.objects.create(
            material=locked,
            txn_type=InventoryLedger.TxnType.IN if delta > 0 else InventoryLedger.TxnType.OUT,
            quantity=abs(delta),
            unit=locked.unit,
            reason=reason,
            reference_type="manual_adjustment",
            reference_id=locked.id,
            created_by=created_by,
        )

    return locked


def create_mro_item_with_opening_stock(
    *,
    name: str,
    mro_id: str,
    code: str,
    item_type: str,
    unit: str,
    cost_per_unit: Decimal,
    vendor: Partner,
    location: str,
    opening_stock: Decimal,
    reorder_level: Decimal,
    created_by,
) -> MROItem:
    if vendor.partner_type not in {Partner.PartnerType.SUPPLIER, Partner.PartnerType.BOTH}:
        raise ValueError("Selected partner is not a supplier.")

    resolved_mro_id = mro_id.strip().upper()
    resolved_code = code.strip().upper() or resolved_mro_id
    if not resolved_mro_id:
        raise ValueError("MRO ID is required.")
    if not resolved_code:
        raise ValueError("Item code could not be resolved.")

    with transaction.atomic():
        item = MROItem.objects.create(
            name=name,
            mro_id=resolved_mro_id,
            code=resolved_code,
            item_type=item_type,
            unit=unit,
            cost_per_unit=cost_per_unit,
            current_stock=opening_stock,
            reorder_level=reorder_level,
            location=location.strip(),
            vendor=vendor,
        )
        if opening_stock > 0:
            MROInventoryLedger.objects.create(
                item=item,
                txn_type=MROInventoryLedger.TxnType.IN,
                quantity=opening_stock,
                unit=item.unit,
                reason="Opening stock",
                reference_type="opening_stock",
                reference_id=item.id,
                created_by=created_by,
            )
    return item


def update_mro_item_details(
    *,
    item: MROItem,
    name: str,
    mro_id: str,
    code: str,
    item_type: str,
    unit: str,
    cost_per_unit: Decimal,
    vendor: Partner,
    location: str,
    reorder_level: Decimal,
) -> MROItem:
    if vendor.partner_type not in {Partner.PartnerType.SUPPLIER, Partner.PartnerType.BOTH}:
        raise ValueError("Selected partner is not a supplier.")

    resolved_mro_id = mro_id.strip().upper()
    resolved_code = code.strip().upper() or resolved_mro_id
    if not resolved_mro_id:
        raise ValueError("MRO ID is required.")
    if not resolved_code:
        raise ValueError("Item code could not be resolved.")

    with transaction.atomic():
        locked = MROItem.objects.select_for_update().get(pk=item.pk)
        locked.name = name
        locked.mro_id = resolved_mro_id
        locked.code = resolved_code
        locked.item_type = item_type
        locked.unit = unit
        locked.cost_per_unit = cost_per_unit
        locked.vendor = vendor
        locked.location = location.strip()
        locked.reorder_level = reorder_level
        locked.save(
            update_fields=[
                "name",
                "mro_id",
                "code",
                "item_type",
                "unit",
                "cost_per_unit",
                "vendor",
                "location",
                "reorder_level",
            ]
        )
    return locked


def adjust_mro_stock(*, item: MROItem, delta: Decimal, reason: str, created_by) -> MROItem:
    if delta == 0:
        raise ValueError("Adjustment quantity cannot be zero.")

    with transaction.atomic():
        locked = MROItem.objects.select_for_update().get(pk=item.pk)
        new_stock = locked.current_stock + delta
        if new_stock < 0:
            raise ValueError("Stock cannot become negative.")

        locked.current_stock = new_stock
        locked.save(update_fields=["current_stock"])

        MROInventoryLedger.objects.create(
            item=locked,
            txn_type=MROInventoryLedger.TxnType.IN if delta > 0 else MROInventoryLedger.TxnType.OUT,
            quantity=abs(delta),
            unit=locked.unit,
            reason=reason,
            reference_type="manual_adjustment",
            reference_id=locked.id,
            created_by=created_by,
        )
    return locked
