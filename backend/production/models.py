from __future__ import annotations

from io import BytesIO
from pathlib import Path
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from PIL import Image, ImageOps

from inventory.models import InventoryLedger, RawMaterial


FINISHED_PRODUCT_IMAGE_SIZE = (512, 512)
QTY_PRECISION = Decimal("0.001")
SUPPORTED_PRODUCT_IMAGE_FORMATS = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
}
IMAGE_RESAMPLING = getattr(Image, "Resampling", Image).LANCZOS


def finished_product_image_upload_path(instance, filename: str) -> str:
    extension = Path(filename or "").suffix.lower()
    if extension not in SUPPORTED_PRODUCT_IMAGE_FORMATS:
        extension = ".png"
    return f"finished_products/{uuid4().hex}{extension}"


class FinishedProduct(models.Model):
    class ItemType(models.TextChoices):
        FINISHED = "finished", "Finished Product"
        PART = "part", "Part"

    name = models.CharField(max_length=150)
    sku = models.CharField(max_length=50)
    item_type = models.CharField(max_length=16, choices=ItemType.choices, default=ItemType.FINISHED)
    colour = models.CharField(max_length=80, blank=True)
    product_image = models.ImageField(blank=True, upload_to=finished_product_image_upload_path)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["sku"],
                condition=Q(item_type="finished"),
                name="production_finishedproduct_finished_sku_unique",
            ),
        ]

    def __str__(self) -> str:
        if self.is_part and self.colour:
            return f"{self.name} [{self.colour}] ({self.sku})"
        return f"{self.name} ({self.sku})"

    def save(self, *args, **kwargs):
        old_image_name = None
        if self.pk:
            old_image_name = type(self).objects.filter(pk=self.pk).values_list("product_image", flat=True).first() or None

        super().save(*args, **kwargs)

        if self.product_image:
            self._resize_product_image()

        if old_image_name and old_image_name != self.product_image.name:
            storage = self._meta.get_field("product_image").storage
            if storage.exists(old_image_name):
                storage.delete(old_image_name)

    def delete(self, *args, **kwargs):
        image_name = self.product_image.name if self.product_image else ""
        storage = self._meta.get_field("product_image").storage
        super().delete(*args, **kwargs)
        if image_name and storage.exists(image_name):
            storage.delete(image_name)

    @property
    def is_part(self) -> bool:
        return self.item_type == self.ItemType.PART

    def _resize_product_image(self):
        if not self.product_image:
            return

        image_name = self.product_image.name
        output_format = SUPPORTED_PRODUCT_IMAGE_FORMATS.get(Path(image_name).suffix.lower(), "PNG")

        with self.product_image.open("rb") as image_file:
            with Image.open(image_file) as source_image:
                normalized = ImageOps.exif_transpose(source_image)
                if normalized.mode not in {"RGB", "RGBA"}:
                    normalized = normalized.convert("RGBA")

                contained = ImageOps.contain(normalized, FINISHED_PRODUCT_IMAGE_SIZE, IMAGE_RESAMPLING)
                canvas = Image.new("RGB", FINISHED_PRODUCT_IMAGE_SIZE, "#ffffff")
                offset = (
                    (FINISHED_PRODUCT_IMAGE_SIZE[0] - contained.width) // 2,
                    (FINISHED_PRODUCT_IMAGE_SIZE[1] - contained.height) // 2,
                )
                paste_image = contained if contained.mode == "RGBA" else contained.convert("RGB")
                if paste_image.mode == "RGBA":
                    canvas.paste(paste_image, offset, paste_image)
                else:
                    canvas.paste(paste_image, offset)

        output = BytesIO()
        save_kwargs: dict[str, object] = {}
        if output_format == "PNG":
            save_kwargs["optimize"] = True
        elif output_format == "JPEG":
            save_kwargs.update({"quality": 90, "optimize": True})
        elif output_format == "WEBP":
            save_kwargs.update({"quality": 90, "method": 6})
        canvas.save(output, format=output_format, **save_kwargs)
        output.seek(0)
        output_bytes = output.getvalue()

        storage = self.product_image.storage
        if storage.exists(image_name):
            with storage.open(image_name, "wb") as image_file:
                image_file.write(output_bytes)
            return

        self.product_image.save(image_name, ContentFile(output_bytes), save=False)
        super().save(update_fields=["product_image"])


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
            if self.material.variant_display:
                return f"{self.material.name} ({self.material.variant_display})"
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


class Marker(models.Model):
    marker_id = models.CharField(max_length=50, unique=True)
    material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT, related_name="markers")
    colour = models.CharField(max_length=80, blank=True)
    sku_id = models.CharField(max_length=50, blank=True)
    length_per_layer = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    sets_per_layer = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["marker_id"]

    def __str__(self) -> str:
        return f"{self.marker_id} - {self.material}"

    @property
    def marker_label(self) -> str:
        sku_label = f" / SKU {self.sku_id}" if self.sku_id else ""
        return f"{self.marker_id}{sku_label}"

    @property
    def material_label(self) -> str:
        if self.material.variant_display:
            return f"{self.material.name} ({self.material.variant_display})"
        return self.material.name

    @property
    def fabric_consumption_per_set(self) -> Decimal:
        if not self.sets_per_layer:
            return Decimal("0.000")
        return (self.length_per_layer / self.sets_per_layer).quantize(QTY_PRECISION)

    def planned_fabric_required(self, sets: int | Decimal) -> Decimal:
        planned_sets = Decimal(sets).quantize(QTY_PRECISION)
        return (planned_sets * self.fabric_consumption_per_set).quantize(QTY_PRECISION)


class MarkerOutput(models.Model):
    marker = models.ForeignKey(Marker, on_delete=models.CASCADE, related_name="outputs")
    part = models.ForeignKey(FinishedProduct, on_delete=models.PROTECT, related_name="marker_outputs")
    quantity_per_set = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )

    class Meta:
        ordering = ["marker__marker_id", "part__name"]
        constraints = [
            models.UniqueConstraint(fields=["marker", "part"], name="production_markeroutput_marker_part_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.marker} -> {self.part}: {self.quantity_per_set}"

    def clean(self):
        super().clean()
        if self.part_id and self.part.item_type != FinishedProduct.ItemType.PART:
            raise ValidationError("Marker outputs must be parts.")


class ProductionOrder(models.Model):
    class TargetType(models.TextChoices):
        FINISHED_PRODUCT = "finished_product", "Finished Product"
        MARKER = "marker", "Marker"

    class Status(models.TextChoices):
        AWAITING_RM_RELEASE = "awaiting_rm_release", "Awaiting RM Release"
        PLANNED = "planned", "Planned"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    target_type = models.CharField(
        max_length=24,
        choices=TargetType.choices,
        default=TargetType.FINISHED_PRODUCT,
    )
    product = models.ForeignKey(
        FinishedProduct,
        on_delete=models.PROTECT,
        related_name="production_orders",
        null=True,
        blank=True,
    )
    marker = models.ForeignKey(
        Marker,
        on_delete=models.PROTECT,
        related_name="production_orders",
        null=True,
        blank=True,
    )
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
        constraints = [
            models.CheckConstraint(
                check=(
                    (Q(product__isnull=False) & Q(marker__isnull=True) & Q(target_type="finished_product"))
                    | (Q(product__isnull=True) & Q(marker__isnull=False) & Q(target_type="marker"))
                ),
                name="production_order_exactly_one_target",
            ),
        ]

    def __str__(self) -> str:
        return f"PO-{self.id}: {self.target_name} x {self.quantity}"

    @property
    def variance_qty(self) -> Decimal:
        return self.produced_qty - self.planned_qty

    @property
    def is_marker_order(self) -> bool:
        return self.target_type == self.TargetType.MARKER

    @property
    def target_name(self) -> str:
        if self.product_id:
            return self.product.name
        if self.marker_id:
            return self.marker.marker_label
        return "-"

    @property
    def target_code(self) -> str:
        if self.product_id:
            return self.product.sku
        if self.marker_id:
            return self.marker.marker_id
        return "-"

    @property
    def target_display(self) -> str:
        if self.product_id:
            return f"{self.product.name} ({self.product.sku})"
        if self.marker_id:
            return f"Marker {self.marker.marker_label}"
        return "-"


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
            if self.material.variant_display:
                return f"{self.material.name} ({self.material.variant_display})"
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
    current_stock = models.DecimalField(
        max_digits=12, decimal_places=3, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["product__name"]
        constraints = [
            models.CheckConstraint(
                check=Q(current_stock__gte=Decimal("0")),
                name="finished_stock_non_negative",
            ),
        ]

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


class PartProduction(models.Model):
    production_order = models.OneToOneField(
        ProductionOrder,
        on_delete=models.PROTECT,
        related_name="part_production",
        null=True,
        blank=True,
    )
    marker = models.ForeignKey(Marker, on_delete=models.PROTECT, related_name="part_productions")
    actual_length = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    actual_layers = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    actual_sets_per_layer = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    total_sets = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    actual_fabric_issued = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    good_sets = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0"))],
    )
    rejected_sets = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0"))],
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"Cut-{self.id}: {self.marker.marker_id}"


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
            target_type=ProductionOrder.TargetType.FINISHED_PRODUCT,
            product=product,
            marker=None,
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
            target_type=ProductionOrder.TargetType.FINISHED_PRODUCT,
            product=product,
            marker=None,
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


def create_marker_production_order_with_rm_request(
    *,
    marker: Marker,
    sets: int,
    notes: str,
    created_by,
):
    planned_fabric = marker.planned_fabric_required(sets)
    if planned_fabric <= 0:
        raise ValidationError("Marker fabric requirement must be greater than zero.")
    if not marker.outputs.exists():
        raise ValidationError("No part outputs defined for selected marker.")

    with transaction.atomic():
        order = ProductionOrder.objects.create(
            target_type=ProductionOrder.TargetType.MARKER,
            product=None,
            marker=marker,
            quantity=sets,
            planned_qty=Decimal(sets).quantize(QTY_PRECISION),
            raw_material_released=False,
            status=ProductionOrder.Status.AWAITING_RM_RELEASE,
            notes=notes,
            created_by=created_by,
        )
        ProductionConsumption.objects.create(
            production_order=order,
            material=marker.material,
            part=None,
            required_qty=planned_fabric,
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
        if locked_order.target_type != ProductionOrder.TargetType.FINISHED_PRODUCT or not locked_order.product_id:
            raise ValidationError("Use marker completion for marker production orders.")
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


def complete_marker_production_order(
    *,
    production_order: ProductionOrder,
    actual_length: Decimal,
    actual_layers: int,
    actual_sets_per_layer: Decimal,
    total_sets: Decimal,
    actual_fabric_issued: Decimal,
    good_sets: Decimal,
    rejected_sets: Decimal,
    completed_by,
) -> ProductionOrder:
    actual_length = Decimal(actual_length).quantize(QTY_PRECISION)
    actual_sets_per_layer = Decimal(actual_sets_per_layer).quantize(QTY_PRECISION)
    total_sets = Decimal(total_sets).quantize(QTY_PRECISION)
    actual_fabric_issued = Decimal(actual_fabric_issued).quantize(QTY_PRECISION)
    good_sets = Decimal(good_sets).quantize(QTY_PRECISION)
    rejected_sets = Decimal(rejected_sets).quantize(QTY_PRECISION)

    if actual_length <= 0:
        raise ValidationError("Actual length must be greater than zero.")
    if actual_layers <= 0:
        raise ValidationError("Actual layers must be greater than zero.")
    if actual_sets_per_layer <= 0:
        raise ValidationError("Actual sets per layer must be greater than zero.")
    if total_sets <= 0:
        raise ValidationError("Total sets must be greater than zero.")
    if actual_fabric_issued <= 0:
        raise ValidationError("Actual fabric issued must be greater than zero.")
    if good_sets < 0 or rejected_sets < 0:
        raise ValidationError("Good and rejected sets cannot be negative.")
    if (good_sets + rejected_sets).quantize(QTY_PRECISION) != total_sets:
        raise ValidationError("Good sets plus rejected sets must equal total sets.")

    with transaction.atomic():
        locked_order = (
            ProductionOrder.objects.select_for_update()
            .select_related("marker", "marker__material")
            .get(pk=production_order.pk)
        )
        if locked_order.target_type != ProductionOrder.TargetType.MARKER or not locked_order.marker_id:
            raise ValidationError("This production order is not a marker order.")
        if locked_order.status == ProductionOrder.Status.CANCELLED:
            raise ValidationError("Cancelled production order cannot be completed.")
        if locked_order.status == ProductionOrder.Status.COMPLETED:
            raise ValidationError("Production order is already completed.")
        if not locked_order.raw_material_released or locked_order.status == ProductionOrder.Status.AWAITING_RM_RELEASE:
            raise ValidationError("Raw materials must be released before completing a marker order.")
        if hasattr(locked_order, "part_production"):
            raise ValidationError("Part production is already recorded for this order.")

        outputs = list(locked_order.marker.outputs.select_related("part"))
        if not outputs:
            raise ValidationError("No part outputs defined for selected marker.")

        consumption = (
            locked_order.consumptions.select_for_update()
            .select_related("material")
            .filter(material_id=locked_order.marker.material_id)
            .first()
        )
        if not consumption:
            raise ValidationError("Marker fabric consumption row is missing.")

        material = RawMaterial.objects.select_for_update().get(pk=locked_order.marker.material_id)
        fabric_delta = (actual_fabric_issued - consumption.required_qty).quantize(QTY_PRECISION)
        if fabric_delta > 0:
            if material.current_stock < fabric_delta:
                raise ValidationError(
                    f"Insufficient stock for additional fabric issue. Required {fabric_delta} {material.unit}, "
                    f"available {material.current_stock}."
                )
            material.current_stock -= fabric_delta
            material.save(update_fields=["current_stock"])
            InventoryLedger.objects.create(
                material=material,
                txn_type=InventoryLedger.TxnType.OUT,
                quantity=fabric_delta,
                unit=material.unit,
                reason=f"Additional fabric issued for marker production order #{locked_order.id}",
                reference_type="production_order",
                reference_id=locked_order.id,
                created_by=completed_by,
            )
        elif fabric_delta < 0:
            returned_qty = abs(fabric_delta).quantize(QTY_PRECISION)
            material.current_stock += returned_qty
            material.save(update_fields=["current_stock"])
            InventoryLedger.objects.create(
                material=material,
                txn_type=InventoryLedger.TxnType.IN,
                quantity=returned_qty,
                unit=material.unit,
                reason=f"Returned fabric from marker production order #{locked_order.id}",
                reference_type="production_order",
                reference_id=locked_order.id,
                created_by=completed_by,
            )

        consumption.required_qty = actual_fabric_issued
        consumption.save(update_fields=["required_qty"])

        cut = PartProduction.objects.create(
            production_order=locked_order,
            marker=locked_order.marker,
            actual_length=actual_length,
            actual_layers=actual_layers,
            actual_sets_per_layer=actual_sets_per_layer,
            total_sets=total_sets,
            actual_fabric_issued=actual_fabric_issued,
            good_sets=good_sets,
            rejected_sets=rejected_sets,
            created_by=completed_by,
        )

        part_stocks = {
            stock.product_id: stock
            for stock in FinishedStock.objects.select_for_update().filter(
                product_id__in=[output.part_id for output in outputs]
            )
        }
        for output in outputs:
            output_qty = (good_sets * output.quantity_per_set).quantize(QTY_PRECISION)
            if output_qty <= 0:
                continue
            part_stock = part_stocks.get(output.part_id)
            if not part_stock:
                part_stock, _created = FinishedStock.objects.select_for_update().get_or_create(
                    product=output.part,
                    defaults={"current_stock": Decimal("0")},
                )
                part_stocks[output.part_id] = part_stock
            part_stock.current_stock += output_qty
            part_stock.save(update_fields=["current_stock"])
            FinishedStockLedger.objects.create(
                product=output.part,
                txn_type=FinishedStockLedger.TxnType.IN,
                quantity=output_qty,
                reason=f"Cut from marker production #{cut.id}",
                reference_type="part_production",
                reference_id=cut.id,
                created_by=completed_by,
            )

        locked_order.status = ProductionOrder.Status.COMPLETED
        locked_order.produced_qty = good_sets
        locked_order.scrap_qty = rejected_sets
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
