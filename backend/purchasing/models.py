from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from inventory.models import InventoryLedger, RawMaterial
from partners.models import Partner


class PurchaseOrder(models.Model):
    DEFAULT_DELIVERY_TERMS = "As per Schedule"
    DEFAULT_PACKAGING_IDENT_TERMS = "Included"
    DEFAULT_INSPECTION_REPORT_TERMS = "Along with Material"
    DEFAULT_PACKING_TERMS = "Standard Packing"
    DEFAULT_PAYMENT_PDC_DAYS = 45

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        PARTIALLY_RECEIVED = "partially_received", "Partially Received"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    class FreightTerms(models.TextChoices):
        EXTRA_AS_APPLICABLE = "extra_as_applicable", "Extra as applicable"
        INCLUDED = "included", "Included"

    vendor = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name="purchase_orders")
    order_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    notes = models.CharField(max_length=255, blank=True)
    payment_pdc_days = models.PositiveIntegerField(default=DEFAULT_PAYMENT_PDC_DAYS)
    delivery_terms = models.CharField(max_length=100, default=DEFAULT_DELIVERY_TERMS)
    freight_terms = models.CharField(
        max_length=32,
        choices=FreightTerms.choices,
        default=FreightTerms.EXTRA_AS_APPLICABLE,
    )
    packaging_ident_terms = models.CharField(max_length=100, default=DEFAULT_PACKAGING_IDENT_TERMS)
    inspection_report_terms = models.CharField(max_length=100, default=DEFAULT_INSPECTION_REPORT_TERMS)
    packing_terms = models.CharField(max_length=100, default=DEFAULT_PACKING_TERMS)
    inventory_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_approved_purchase_orders",
    )
    inventory_approved_at = models.DateTimeField(null=True, blank=True)
    admin_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_approved_purchase_orders",
    )
    admin_approved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_purchase_orders",
    )
    received_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_purchase_orders",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"PO-{self.id} ({self.vendor.name})"

    @property
    def can_receive(self) -> bool:
        return self.status in {self.Status.OPEN, self.Status.PARTIALLY_RECEIVED}

    @property
    def can_cancel(self) -> bool:
        return self.status in {self.Status.OPEN, self.Status.PARTIALLY_RECEIVED}

    @property
    def can_reopen(self) -> bool:
        return self.status == self.Status.CANCELLED

    @property
    def is_fully_approved(self) -> bool:
        return bool(self.inventory_approved_at and self.admin_approved_at)

    @property
    def is_pending_approval(self) -> bool:
        return not self.is_fully_approved


class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="items")
    material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    unit_rate = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    line_amount = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    received_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    unit = models.CharField(max_length=16)

    class Meta:
        ordering = ["id"]

    @property
    def pending_quantity(self) -> Decimal:
        pending = self.quantity - self.received_quantity
        return pending if pending > 0 else Decimal("0.000")


@dataclass
class PurchaseLineInput:
    material: RawMaterial
    quantity: Decimal


def create_grouped_purchase_orders(
    *,
    order_date,
    notes: str,
    created_by,
    lines: list[PurchaseLineInput],
    payment_pdc_days: int = PurchaseOrder.DEFAULT_PAYMENT_PDC_DAYS,
    delivery_terms: str = PurchaseOrder.DEFAULT_DELIVERY_TERMS,
    freight_terms: str = PurchaseOrder.FreightTerms.EXTRA_AS_APPLICABLE,
    packaging_ident_terms: str = PurchaseOrder.DEFAULT_PACKAGING_IDENT_TERMS,
    inspection_report_terms: str = PurchaseOrder.DEFAULT_INSPECTION_REPORT_TERMS,
    packing_terms: str = PurchaseOrder.DEFAULT_PACKING_TERMS,
) -> list[PurchaseOrder]:
    return create_grouped_purchase_orders_with_vendor(
        order_date=order_date,
        notes=notes,
        created_by=created_by,
        lines=lines,
        vendor=None,
        payment_pdc_days=payment_pdc_days,
        delivery_terms=delivery_terms,
        freight_terms=freight_terms,
        packaging_ident_terms=packaging_ident_terms,
        inspection_report_terms=inspection_report_terms,
        packing_terms=packing_terms,
    )


def create_grouped_purchase_orders_with_vendor(
    *,
    order_date,
    notes: str,
    created_by,
    lines: list[PurchaseLineInput],
    vendor: Partner | None,
    payment_pdc_days: int = PurchaseOrder.DEFAULT_PAYMENT_PDC_DAYS,
    delivery_terms: str = PurchaseOrder.DEFAULT_DELIVERY_TERMS,
    freight_terms: str = PurchaseOrder.FreightTerms.EXTRA_AS_APPLICABLE,
    packaging_ident_terms: str = PurchaseOrder.DEFAULT_PACKAGING_IDENT_TERMS,
    inspection_report_terms: str = PurchaseOrder.DEFAULT_INSPECTION_REPORT_TERMS,
    packing_terms: str = PurchaseOrder.DEFAULT_PACKING_TERMS,
) -> list[PurchaseOrder]:
    if not lines:
        raise ValidationError("Add at least one raw material line item.")

    for line in lines:
        if line.quantity <= 0:
            raise ValidationError("Quantity must be greater than zero.")

    if vendor:
        if vendor.partner_type not in {Partner.PartnerType.SUPPLIER, Partner.PartnerType.BOTH}:
            raise ValidationError("Selected vendor is not valid for purchase orders.")
        with transaction.atomic():
            order = PurchaseOrder.objects.create(
                vendor=vendor,
                order_date=order_date,
                notes=notes,
                payment_pdc_days=payment_pdc_days,
                delivery_terms=delivery_terms,
                freight_terms=freight_terms,
                packaging_ident_terms=packaging_ident_terms,
                inspection_report_terms=inspection_report_terms,
                packing_terms=packing_terms,
                created_by=created_by,
            )
            for line in lines:
                unit_rate = line.material.cost_per_unit
                PurchaseOrderItem.objects.create(
                    purchase_order=order,
                    material=line.material,
                    quantity=line.quantity,
                    unit_rate=unit_rate,
                    line_amount=(line.quantity * unit_rate),
                    unit=line.material.unit,
                )
        return [order]

    grouped: dict[int, list[PurchaseLineInput]] = defaultdict(list)
    for line in lines:
        grouped[line.material.vendor_id].append(line)

    created_orders: list[PurchaseOrder] = []
    with transaction.atomic():
        for vendor_id, vendor_lines in grouped.items():
            grouped_vendor = Partner.objects.get(pk=vendor_id)
            order = PurchaseOrder.objects.create(
                vendor=grouped_vendor,
                order_date=order_date,
                notes=notes,
                payment_pdc_days=payment_pdc_days,
                delivery_terms=delivery_terms,
                freight_terms=freight_terms,
                packaging_ident_terms=packaging_ident_terms,
                inspection_report_terms=inspection_report_terms,
                packing_terms=packing_terms,
                created_by=created_by,
            )
            for line in vendor_lines:
                unit_rate = line.material.cost_per_unit
                PurchaseOrderItem.objects.create(
                    purchase_order=order,
                    material=line.material,
                    quantity=line.quantity,
                    unit_rate=unit_rate,
                    line_amount=(line.quantity * unit_rate),
                    unit=line.material.unit,
                )
            created_orders.append(order)

    return created_orders


def _derive_status(items: list[PurchaseOrderItem]) -> str:
    all_received = all(item.received_quantity >= item.quantity for item in items)
    any_received = any(item.received_quantity > 0 for item in items)
    if all_received:
        return PurchaseOrder.Status.RECEIVED
    if any_received:
        return PurchaseOrder.Status.PARTIALLY_RECEIVED
    return PurchaseOrder.Status.OPEN


def receive_purchase_order(
    *,
    purchase_order: PurchaseOrder,
    received_by,
    line_quantities: dict[int, Decimal] | None = None,
) -> PurchaseOrder:
    with transaction.atomic():
        locked_order = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if locked_order.status == PurchaseOrder.Status.CANCELLED:
            raise ValidationError("Cancelled purchase order cannot be received.")
        if locked_order.status == PurchaseOrder.Status.RECEIVED:
            raise ValidationError("Purchase order is already fully received.")

        items = list(locked_order.items.select_related("material"))
        if not items:
            raise ValidationError("Purchase order has no line items to receive.")

        quantities: dict[int, Decimal] = {}
        if line_quantities:
            quantities = {
                item_id: quantity
                for item_id, quantity in line_quantities.items()
                if quantity and quantity > 0
            }
            if not quantities:
                raise ValidationError("Enter at least one quantity greater than zero.")
        else:
            for item in items:
                if item.pending_quantity > 0:
                    quantities[item.id] = item.pending_quantity
            if not quantities:
                raise ValidationError("No pending quantities left to receive.")

        item_map = {item.id: item for item in items}
        unknown_ids = set(quantities.keys()) - set(item_map.keys())
        if unknown_ids:
            raise ValidationError("Invalid purchase order item in receive payload.")

        for item_id, qty in quantities.items():
            item = item_map[item_id]
            if qty > item.pending_quantity:
                raise ValidationError(
                    f"Receive quantity for {item.material.name} cannot exceed pending {item.pending_quantity}."
                )

        material_ids = [item.material_id for item in items]
        materials = {m.id: m for m in RawMaterial.objects.select_for_update().filter(id__in=material_ids)}

        for item in items:
            receive_qty = quantities.get(item.id, Decimal("0"))
            if receive_qty <= 0:
                continue
            material = materials[item.material_id]
            material.current_stock += receive_qty
            material.save(update_fields=["current_stock"])
            item.received_quantity += receive_qty
            item.save(update_fields=["received_quantity"])

            InventoryLedger.objects.create(
                material=material,
                txn_type=InventoryLedger.TxnType.IN,
                quantity=receive_qty,
                unit=item.unit,
                reason=f"Received against purchase order #{locked_order.id} ({item.material.name})",
                reference_type="purchase_order",
                reference_id=locked_order.id,
                created_by=received_by,
            )

        new_status = _derive_status(items)
        locked_order.status = new_status

        update_fields = ["status"]
        if new_status == PurchaseOrder.Status.RECEIVED:
            locked_order.received_by = received_by
            locked_order.received_at = timezone.now()
            update_fields.extend(["received_by", "received_at"])
        locked_order.save(update_fields=update_fields)

    return locked_order


def cancel_purchase_order(*, purchase_order: PurchaseOrder, cancelled_by) -> PurchaseOrder:
    with transaction.atomic():
        locked_order = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if locked_order.status == PurchaseOrder.Status.RECEIVED:
            raise ValidationError("Fully received purchase order cannot be cancelled.")
        if locked_order.status == PurchaseOrder.Status.CANCELLED:
            raise ValidationError("Purchase order is already cancelled.")

        locked_order.status = PurchaseOrder.Status.CANCELLED
        locked_order.cancelled_by = cancelled_by
        locked_order.cancelled_at = timezone.now()
        locked_order.save(update_fields=["status", "cancelled_by", "cancelled_at"])

    return locked_order


def reopen_purchase_order(*, purchase_order: PurchaseOrder) -> PurchaseOrder:
    with transaction.atomic():
        locked_order = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if locked_order.status != PurchaseOrder.Status.CANCELLED:
            raise ValidationError("Only cancelled purchase orders can be reopened.")

        items = list(locked_order.items.all())
        if not items:
            raise ValidationError("Purchase order has no line items.")

        pending_exists = any(item.pending_quantity > 0 for item in items)
        if not pending_exists:
            raise ValidationError("Fully received purchase order cannot be reopened.")

        has_receipts = any(item.received_quantity > 0 for item in items)
        locked_order.status = (
            PurchaseOrder.Status.PARTIALLY_RECEIVED if has_receipts else PurchaseOrder.Status.OPEN
        )
        locked_order.cancelled_by = None
        locked_order.cancelled_at = None
        locked_order.save(update_fields=["status", "cancelled_by", "cancelled_at"])

    return locked_order


def approve_purchase_order_inventory(*, purchase_order: PurchaseOrder, approved_by) -> PurchaseOrder:
    with transaction.atomic():
        locked_order = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if locked_order.status == PurchaseOrder.Status.CANCELLED:
            raise ValidationError("Cancelled purchase order cannot be approved.")
        if locked_order.inventory_approved_at:
            raise ValidationError("Purchase order is already approved by inventory manager.")
        locked_order.inventory_approved_by = approved_by
        locked_order.inventory_approved_at = timezone.now()
        locked_order.save(update_fields=["inventory_approved_by", "inventory_approved_at"])
    return locked_order


def approve_purchase_order_admin(*, purchase_order: PurchaseOrder, approved_by) -> PurchaseOrder:
    with transaction.atomic():
        locked_order = PurchaseOrder.objects.select_for_update().get(pk=purchase_order.pk)
        if locked_order.status == PurchaseOrder.Status.CANCELLED:
            raise ValidationError("Cancelled purchase order cannot be approved.")
        if locked_order.admin_approved_at:
            raise ValidationError("Purchase order is already approved by admin.")
        locked_order.admin_approved_by = approved_by
        locked_order.admin_approved_at = timezone.now()
        locked_order.save(update_fields=["admin_approved_by", "admin_approved_at"])
    return locked_order
