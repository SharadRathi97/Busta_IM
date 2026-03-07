from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from inventory.models import InventoryLedger, RawMaterial


class FinishedProduct(models.Model):
    class ItemType(models.TextChoices):
        FINISHED = "finished", "Finished Product"
        PART = "part", "Part"

    name = models.CharField(max_length=150)
    sku = models.CharField(max_length=50, unique=True)
    item_type = models.CharField(max_length=16, choices=ItemType.choices, default=ItemType.FINISHED)
    colour = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        if self.is_part and self.colour:
            return f"{self.name} [{self.colour}] ({self.sku})"
        return f"{self.name} ({self.sku})"

    @property
    def is_part(self) -> bool:
        return self.item_type == self.ItemType.PART


class BOMItem(models.Model):
    product = models.ForeignKey(FinishedProduct, on_delete=models.CASCADE, related_name="bom_items")
    material = models.ForeignKey(
        RawMaterial,
        on_delete=models.PROTECT,
        related_name="bom_usage",
        null=True,
        blank=True,
    )
    part = models.ForeignKey(
        FinishedProduct,
        on_delete=models.PROTECT,
        related_name="used_in_bom_items",
        null=True,
        blank=True,
    )
    qty_per_unit = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])

    class Meta:
        ordering = ["product__name", "id"]
        constraints = [
            models.CheckConstraint(
                check=(
                    (Q(material__isnull=False) & Q(part__isnull=True))
                    | (Q(material__isnull=True) & Q(part__isnull=False))
                ),
                name="production_bomitem_exactly_one_component",
            ),
            models.UniqueConstraint(
                fields=["product", "material"],
                condition=Q(material__isnull=False),
                name="production_bomitem_product_material_unique",
            ),
            models.UniqueConstraint(
                fields=["product", "part"],
                condition=Q(part__isnull=False),
                name="production_bomitem_product_part_unique",
            ),
        ]

    def __str__(self) -> str:
        if self.material_id:
            return f"{self.product} -> {self.material}: {self.qty_per_unit}"
        return f"{self.product} -> {self.part}: {self.qty_per_unit}"

    @property
    def component_name(self) -> str:
        if self.material_id:
            return self.material.name
        if self.part_id:
            if self.part.colour:
                return f"{self.part.name} ({self.part.colour})"
            return self.part.name
        return "-"

    @property
    def component_code(self) -> str:
        if self.material_id:
            return self.material.code
        if self.part_id:
            return self.part.sku
        return "-"

    @property
    def component_unit(self) -> str:
        if self.material_id:
            return self.material.unit
        return "units"

    @property
    def component_cost_per_unit(self) -> Decimal:
        if self.material_id:
            return self.material.cost_per_unit
        return Decimal("0.000")

    @property
    def component_key(self) -> str:
        if self.material_id:
            return f"raw:{self.material_id}"
        if self.part_id:
            return f"part:{self.part_id}"
        return ""


class ProductionOrder(models.Model):
    class Status(models.TextChoices):
        AWAITING_RM_RELEASE = "awaiting_rm_release", "Awaiting RM Release"
        PLANNED = "planned", "Planned"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    product = models.ForeignKey(FinishedProduct, on_delete=models.PROTECT, related_name="production_orders")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    planned_qty = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    produced_qty = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    scrap_qty = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    raw_material_released = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="completed_production_orders",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"PO-{self.id}: {self.product} x {self.quantity}"

    @property
    def variance_qty(self) -> Decimal:
        return self.produced_qty - self.planned_qty


class ProductionConsumption(models.Model):
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="consumptions")
    material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT, null=True, blank=True)
    part = models.ForeignKey(FinishedProduct, on_delete=models.PROTECT, null=True, blank=True)
    required_qty = models.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                check=(
                    (Q(material__isnull=False) & Q(part__isnull=True))
                    | (Q(material__isnull=True) & Q(part__isnull=False))
                ),
                name="production_consumption_exactly_one_component",
            ),
        ]

    @property
    def component_name(self) -> str:
        if self.material_id:
            return self.material.name
        if self.part_id:
            return self.part.name
        return "-"

    @property
    def component_code(self) -> str:
        if self.material_id:
            return self.material.code
        if self.part_id:
            return self.part.sku
        return "-"

    @property
    def component_type(self) -> str:
        if self.material_id:
            return "Raw Material"
        if self.part_id:
            return "Part"
        return "-"

    @property
    def component_unit(self) -> str:
        if self.material_id:
            return self.material.unit
        return "units"

    @property
    def qty_per_unit_used(self) -> Decimal:
        if self.production_order.quantity <= 0:
            return Decimal("0.000")
        return (self.required_qty / Decimal(self.production_order.quantity)).quantize(Decimal("0.001"))


class FinishedStock(models.Model):
    product = models.OneToOneField(FinishedProduct, on_delete=models.CASCADE, related_name="stock_record")
    current_stock = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["product__name"]

    def __str__(self) -> str:
        return f"{self.product} stock: {self.current_stock}"


class FinishedStockLedger(models.Model):
    class TxnType(models.TextChoices):
        IN = "IN", "IN"
        OUT = "OUT", "OUT"
        ADJUST = "ADJUST", "ADJUST"

    product = models.ForeignKey(FinishedProduct, on_delete=models.CASCADE, related_name="stock_ledger_entries")
    txn_type = models.CharField(max_length=10, choices=TxnType.choices)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    reason = models.CharField(max_length=255)
    reference_type = models.CharField(max_length=50, blank=True)
    reference_id = models.PositiveIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


def _build_bom_requirements(
    *,
    product: FinishedProduct,
    quantity: int,
    bom_qty_overrides: dict[str, Decimal | tuple[str, Decimal]] | None = None,
) -> list[tuple[RawMaterial | None, FinishedProduct | None, Decimal]]:
    bom_items = list(BOMItem.objects.select_related("material", "part").filter(product=product))
    if not bom_items:
        raise ValidationError("No BOM defined for selected product.")

    if bom_qty_overrides is not None:
        expected_keys = {item.component_key for item in bom_items}
        provided_keys = set(bom_qty_overrides.keys())
        if expected_keys != provided_keys:
            raise ValidationError("BOM has changed for the selected product. Please review and submit again.")

    requirements: list[tuple[RawMaterial | None, FinishedProduct | None, Decimal]] = []
    selected_component_keys: set[str] = set()

    def resolve_component_key(component_key: str) -> tuple[RawMaterial | None, FinishedProduct | None]:
        normalized_key = (component_key or "").strip()
        if ":" not in normalized_key:
            raise ValidationError("Select a valid BOM component.")

        prefix, pk_raw = normalized_key.split(":", 1)
        if not pk_raw.isdigit():
            raise ValidationError("Select a valid BOM component.")

        component_id = int(pk_raw)
        if prefix == "raw":
            material = RawMaterial.objects.filter(pk=component_id).first()
            if not material:
                raise ValidationError("Selected raw material is no longer available.")
            return material, None
        if prefix == "part":
            part = FinishedProduct.objects.filter(
                pk=component_id,
                item_type=FinishedProduct.ItemType.PART,
            ).first()
            if not part:
                raise ValidationError("Selected part is no longer available.")
            if part.id == product.id:
                raise ValidationError("A part cannot include itself as a BOM component.")
            return None, part

        raise ValidationError("Select a valid BOM component.")

    for item in bom_items:
        component_key = item.component_key
        qty_per_unit = item.qty_per_unit
        if bom_qty_overrides is not None:
            override_value = bom_qty_overrides.get(item.component_key)
            if override_value is None:
                raise ValidationError("BOM has changed for the selected product. Please review and submit again.")

            if isinstance(override_value, tuple):
                if len(override_value) != 2:
                    raise ValidationError("Invalid BOM changes submitted. Please review and submit again.")
                component_key = str(override_value[0]).strip()
                try:
                    qty_per_unit = Decimal(override_value[1])
                except (InvalidOperation, TypeError, ValueError) as exc:
                    raise ValidationError("Invalid BOM quantity submitted. Please review and submit again.") from exc
            else:
                try:
                    qty_per_unit = Decimal(override_value)
                except (InvalidOperation, TypeError, ValueError) as exc:
                    raise ValidationError("Invalid BOM quantity submitted. Please review and submit again.") from exc

        qty_per_unit = qty_per_unit.quantize(Decimal("0.001"))
        if qty_per_unit <= 0:
            raise ValidationError("Each BOM quantity must be greater than zero.")

        if component_key in selected_component_keys:
            raise ValidationError("Duplicate BOM component selected. Each row must use a unique item.")
        selected_component_keys.add(component_key)

        material, part = resolve_component_key(component_key)

        required = (qty_per_unit * Decimal(quantity)).quantize(Decimal("0.001"))
        requirements.append((material, part, required))

    return requirements


def create_production_order_and_deduct_stock(
    *,
    product: FinishedProduct,
    quantity: int,
    notes: str,
    created_by,
    bom_qty_overrides: dict[str, Decimal | tuple[str, Decimal]] | None = None,
):
    requirements = _build_bom_requirements(
        product=product,
        quantity=quantity,
        bom_qty_overrides=bom_qty_overrides,
    )

    material_ids = [material.id for material, _part, _required in requirements if material]
    part_ids = [part.id for _material, part, _required in requirements if part]

    with transaction.atomic():
        materials = {
            m.id: m
            for m in RawMaterial.objects.select_for_update().filter(id__in=material_ids)
        }
        part_stocks = {
            stock.product_id: stock
            for stock in FinishedStock.objects.select_for_update().filter(product_id__in=part_ids)
        }

        shortages: list[str] = []
        for material, part, required in requirements:
            if material:
                locked_material = materials.get(material.id)
                if not locked_material:
                    shortages.append(f"Raw material ID {material.id} missing from inventory.")
                    continue
                if locked_material.current_stock < required:
                    shortages.append(
                        f"{locked_material.name}: required {required} {locked_material.unit}, available {locked_material.current_stock}"
                    )
                continue

            if not part:
                shortages.append("BOM item has no valid component.")
                continue
            part_stock = part_stocks.get(part.id)
            available = part_stock.current_stock if part_stock else Decimal("0")
            if available < required:
                shortages.append(
                    f"{part.name}: required {required} units, available {available}"
                )

        if shortages:
            raise ValidationError("Insufficient stock. " + "; ".join(shortages))

        order = ProductionOrder.objects.create(
            product=product,
            quantity=quantity,
            planned_qty=Decimal(quantity).quantize(Decimal("0.001")),
            raw_material_released=True,
            status=ProductionOrder.Status.PLANNED,
            notes=notes,
            created_by=created_by,
        )

        for material, part, required in requirements:
            if material:
                material = materials[material.id]
                material.current_stock -= required
                material.save(update_fields=["current_stock"])

                ProductionConsumption.objects.create(
                    production_order=order,
                    material=material,
                    part=None,
                    required_qty=required,
                )
                InventoryLedger.objects.create(
                    material=material,
                    txn_type=InventoryLedger.TxnType.OUT,
                    quantity=required,
                    unit=material.unit,
                    reason=f"Consumed by production order #{order.id}",
                    reference_type="production_order",
                    reference_id=order.id,
                    created_by=created_by,
                )
                continue

            if not part:
                raise ValidationError("BOM item has no valid component.")
            part_stock = part_stocks.get(part.id)
            if not part_stock:
                part_stock, _created = FinishedStock.objects.select_for_update().get_or_create(
                    product=part,
                    defaults={"current_stock": Decimal("0")},
                )
                part_stocks[part.id] = part_stock
            part_stock.current_stock -= required
            part_stock.save(update_fields=["current_stock"])

            ProductionConsumption.objects.create(
                production_order=order,
                material=None,
                part=part,
                required_qty=required,
            )
            FinishedStockLedger.objects.create(
                product=part,
                txn_type=FinishedStockLedger.TxnType.OUT,
                quantity=required,
                reason=f"Consumed by production order #{order.id}",
                reference_type="production_order",
                reference_id=order.id,
                created_by=created_by,
            )

    return order


def create_production_order_with_rm_request(
    *,
    product: FinishedProduct,
    quantity: int,
    notes: str,
    created_by,
    bom_qty_overrides: dict[str, Decimal | tuple[str, Decimal]] | None = None,
):
    requirements = _build_bom_requirements(
        product=product,
        quantity=quantity,
        bom_qty_overrides=bom_qty_overrides,
    )

    with transaction.atomic():
        order = ProductionOrder.objects.create(
            product=product,
            quantity=quantity,
            planned_qty=Decimal(quantity).quantize(Decimal("0.001")),
            raw_material_released=False,
            status=ProductionOrder.Status.AWAITING_RM_RELEASE,
            notes=notes,
            created_by=created_by,
        )
        for material, part, required in requirements:
            ProductionConsumption.objects.create(
                production_order=order,
                material=material,
                part=part,
                required_qty=required,
            )
    return order


def release_raw_materials_for_production_order(*, production_order: ProductionOrder, released_by) -> ProductionOrder:
    with transaction.atomic():
        locked_order = (
            ProductionOrder.objects.select_for_update()
            .select_related("product")
            .get(pk=production_order.pk)
        )
        if locked_order.status != ProductionOrder.Status.AWAITING_RM_RELEASE:
            raise ValidationError("This production order is not awaiting raw material release.")
        if locked_order.raw_material_released:
            raise ValidationError("Raw materials are already released for this production order.")

        consumptions = list(locked_order.consumptions.select_related("material", "part"))
        if not consumptions:
            raise ValidationError("No BOM requirements found for this production order.")

        material_ids = [item.material_id for item in consumptions if item.material_id]
        part_ids = [item.part_id for item in consumptions if item.part_id]
        materials = {m.id: m for m in RawMaterial.objects.select_for_update().filter(id__in=material_ids)}
        part_stocks = {
            stock.product_id: stock
            for stock in FinishedStock.objects.select_for_update().filter(product_id__in=part_ids)
        }

        shortages: list[str] = []
        for consumption in consumptions:
            if consumption.material_id:
                material = materials.get(consumption.material_id)
                if not material:
                    shortages.append(f"Raw material ID {consumption.material_id} missing from inventory.")
                    continue
                if material.current_stock < consumption.required_qty:
                    shortages.append(
                        f"{material.name}: required {consumption.required_qty} {material.unit}, available {material.current_stock}"
                    )
                continue

            if not consumption.part_id:
                shortages.append("Invalid BOM requirement without component.")
                continue
            part_stock = part_stocks.get(consumption.part_id)
            available = part_stock.current_stock if part_stock else Decimal("0")
            if available < consumption.required_qty:
                shortages.append(
                    f"{consumption.part.name}: required {consumption.required_qty} units, available {available}"
                )

        if shortages:
            raise ValidationError("Insufficient stock for release. " + "; ".join(shortages))

        for consumption in consumptions:
            if consumption.material_id:
                material = materials[consumption.material_id]
                material.current_stock -= consumption.required_qty
                material.save(update_fields=["current_stock"])
                InventoryLedger.objects.create(
                    material=material,
                    txn_type=InventoryLedger.TxnType.OUT,
                    quantity=consumption.required_qty,
                    unit=material.unit,
                    reason=f"Released for production order #{locked_order.id}",
                    reference_type="production_order",
                    reference_id=locked_order.id,
                    created_by=released_by,
                )
                continue

            part_stock = part_stocks.get(consumption.part_id)
            if not part_stock:
                part_stock, _created = FinishedStock.objects.select_for_update().get_or_create(
                    product=consumption.part,
                    defaults={"current_stock": Decimal("0")},
                )
                part_stocks[consumption.part_id] = part_stock
            part_stock.current_stock -= consumption.required_qty
            part_stock.save(update_fields=["current_stock"])
            FinishedStockLedger.objects.create(
                product=consumption.part,
                txn_type=FinishedStockLedger.TxnType.OUT,
                quantity=consumption.required_qty,
                reason=f"Released for production order #{locked_order.id}",
                reference_type="production_order",
                reference_id=locked_order.id,
                created_by=released_by,
            )

        locked_order.raw_material_released = True
        locked_order.status = ProductionOrder.Status.PLANNED
        locked_order.save(update_fields=["raw_material_released", "status"])

    return locked_order


def reject_raw_materials_for_production_order(*, production_order: ProductionOrder) -> ProductionOrder:
    with transaction.atomic():
        locked_order = ProductionOrder.objects.select_for_update().get(pk=production_order.pk)
        if locked_order.status != ProductionOrder.Status.AWAITING_RM_RELEASE:
            raise ValidationError("This production order is not awaiting raw material release.")
        if locked_order.raw_material_released:
            raise ValidationError("Raw materials are already released for this production order.")

        locked_order.status = ProductionOrder.Status.CANCELLED
        locked_order.save(update_fields=["status"])

    return locked_order


def complete_production_order(
    *,
    production_order: ProductionOrder,
    produced_qty: Decimal,
    scrap_qty: Decimal,
    completed_by,
) -> ProductionOrder:
    produced = Decimal(produced_qty).quantize(Decimal("0.001"))
    scrap = Decimal(scrap_qty).quantize(Decimal("0.001"))
    if produced <= 0:
        raise ValidationError("Produced quantity must be greater than zero.")
    if scrap < 0:
        raise ValidationError("Scrap quantity cannot be negative.")

    with transaction.atomic():
        locked_order = ProductionOrder.objects.select_for_update().select_related("product").get(pk=production_order.pk)
        if locked_order.status == ProductionOrder.Status.CANCELLED:
            raise ValidationError("Cancelled production order cannot be completed.")
        if locked_order.status == ProductionOrder.Status.COMPLETED:
            raise ValidationError("Production order is already completed.")

        finished_stock, _created = FinishedStock.objects.select_for_update().get_or_create(
            product=locked_order.product,
            defaults={"current_stock": Decimal("0")},
        )
        finished_stock.current_stock += produced
        finished_stock.save()

        FinishedStockLedger.objects.create(
            product=locked_order.product,
            txn_type=FinishedStockLedger.TxnType.IN,
            quantity=produced,
            reason=f"Completed production order #{locked_order.id}",
            reference_type="production_order",
            reference_id=locked_order.id,
            created_by=completed_by,
        )

        locked_order.status = ProductionOrder.Status.COMPLETED
        locked_order.produced_qty = produced
        locked_order.scrap_qty = scrap
        locked_order.completed_by = completed_by
        locked_order.completed_at = timezone.now()
        locked_order.save(
            update_fields=[
                "status",
                "produced_qty",
                "scrap_qty",
                "completed_by",
                "completed_at",
            ]
        )

    return locked_order


def cancel_production_order(*, production_order: ProductionOrder, cancelled_by) -> ProductionOrder:
    with transaction.atomic():
        locked_order = ProductionOrder.objects.select_for_update().get(pk=production_order.pk)
        if locked_order.status == ProductionOrder.Status.CANCELLED:
            raise ValidationError("Production order is already cancelled.")
        if locked_order.status == ProductionOrder.Status.COMPLETED:
            raise ValidationError("Completed production order cannot be cancelled.")

        if locked_order.raw_material_released:
            consumptions = list(locked_order.consumptions.select_related("material", "part"))
            material_ids = [item.material_id for item in consumptions if item.material_id]
            part_ids = [item.part_id for item in consumptions if item.part_id]
            materials = {m.id: m for m in RawMaterial.objects.select_for_update().filter(id__in=material_ids)}
            part_stocks = {
                stock.product_id: stock
                for stock in FinishedStock.objects.select_for_update().filter(product_id__in=part_ids)
            }

            for consumption in consumptions:
                if consumption.material_id:
                    material = materials.get(consumption.material_id)
                    if not material:
                        continue
                    material.current_stock += consumption.required_qty
                    material.save(update_fields=["current_stock"])
                    InventoryLedger.objects.create(
                        material=material,
                        txn_type=InventoryLedger.TxnType.IN,
                        quantity=consumption.required_qty,
                        unit=material.unit,
                        reason=f"Reverted by cancelling production order #{locked_order.id}",
                        reference_type="production_order",
                        reference_id=locked_order.id,
                        created_by=cancelled_by,
                    )
                    continue

                part_stock = part_stocks.get(consumption.part_id)
                if not part_stock:
                    part_stock, _created = FinishedStock.objects.select_for_update().get_or_create(
                        product=consumption.part,
                        defaults={"current_stock": Decimal("0")},
                    )
                    part_stocks[consumption.part_id] = part_stock
                part_stock.current_stock += consumption.required_qty
                part_stock.save(update_fields=["current_stock"])
                FinishedStockLedger.objects.create(
                    product=consumption.part,
                    txn_type=FinishedStockLedger.TxnType.IN,
                    quantity=consumption.required_qty,
                    reason=f"Reverted by cancelling production order #{locked_order.id}",
                    reference_type="production_order",
                    reference_id=locked_order.id,
                    created_by=cancelled_by,
                )

        locked_order.status = ProductionOrder.Status.CANCELLED
        locked_order.save(update_fields=["status"])

    return locked_order
