from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User
from inventory.models import RawMaterial, add_vendor_to_material
from partners.models import Partner
from purchasing.models import (
    PurchaseLineInput,
    PurchaseOrder,
    cancel_purchase_order,
    create_grouped_purchase_orders,
    receive_purchase_order,
)


class Command(BaseCommand):
    help = "Seed demo vendors/materials and purchase orders in open/partial/received/cancelled states."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Reset demo suppliers/materials/orders before seeding.",
        )

    def handle(self, *args, **options):
        if options.get("reset"):
            PurchaseOrder.objects.filter(notes__startswith="[DEMO]").delete()
            RawMaterial.objects.filter(code__startswith="DEMO-").delete()
            Partner.objects.filter(name__startswith="Demo Supplier ").delete()
            self.stdout.write(self.style.WARNING("Existing demo data reset."))

        admin = User.objects.filter(role=User.Role.ADMIN).order_by("id").first()
        if not admin:
            admin = User.objects.create_user(
                username="admin",
                password="admin123",
                first_name="System",
                last_name="Admin",
                role=User.Role.ADMIN,
                is_staff=True,
                is_superuser=True,
            )
            self.stdout.write(self.style.SUCCESS("Created default admin user: admin / admin123"))

        supplier_a, _ = Partner.objects.update_or_create(
            vendor_id="VEND-DEMO-A",
            defaults={
                "name": "Demo Supplier A",
                "partner_type": Partner.PartnerType.SUPPLIER,
                "gst_number": "29DEMOS1234A1Z1",
                "address_line1": "Industrial Area 12",
                "city": "Bengaluru",
                "state": "Karnataka",
                "pincode": "560001",
            },
        )
        supplier_b, _ = Partner.objects.update_or_create(
            vendor_id="VEND-DEMO-B",
            defaults={
                "name": "Demo Supplier B",
                "partner_type": Partner.PartnerType.SUPPLIER,
                "gst_number": "27DEMOS5678B1Z2",
                "address_line1": "MIDC Zone 4",
                "city": "Mumbai",
                "state": "Maharashtra",
                "pincode": "400001",
            },
        )

        material_canvas, _ = RawMaterial.objects.update_or_create(
            rm_id="RM-DEMO-CANVAS",
            colour_code="BLU",
            defaults={
                "code": "DEMO-CANVAS",
                "name": "Canvas Roll",
                "material_type": RawMaterial.MaterialType.FABRIC,
                "colour": "Blue",
                "unit": RawMaterial.Unit.METER,
                "current_stock": Decimal("120.000"),
                "reorder_level": Decimal("30.000"),
                "vendor": supplier_a,
            },
        )
        add_vendor_to_material(material=material_canvas, vendor=supplier_a)
        material_zip, _ = RawMaterial.objects.update_or_create(
            rm_id="RM-DEMO-ZIP",
            colour_code="BLK",
            defaults={
                "code": "DEMO-ZIP",
                "name": "Zip Chain",
                "material_type": RawMaterial.MaterialType.HARDWARE,
                "colour": "Black",
                "unit": RawMaterial.Unit.METER,
                "current_stock": Decimal("80.000"),
                "reorder_level": Decimal("25.000"),
                "vendor": supplier_b,
            },
        )
        add_vendor_to_material(material=material_zip, vendor=supplier_b)

        created_orders = []
        created_orders.append(
            self._ensure_demo_order(
                admin=admin,
                note="[DEMO] PO Open",
                material=material_canvas,
                ordered_qty=Decimal("15.000"),
                receive_qty=None,
                cancelled=False,
            )
        )
        created_orders.append(
            self._ensure_demo_order(
                admin=admin,
                note="[DEMO] PO Partial",
                material=material_canvas,
                ordered_qty=Decimal("12.000"),
                receive_qty=Decimal("4.000"),
                cancelled=False,
            )
        )
        created_orders.append(
            self._ensure_demo_order(
                admin=admin,
                note="[DEMO] PO Received",
                material=material_zip,
                ordered_qty=Decimal("10.000"),
                receive_qty=Decimal("10.000"),
                cancelled=False,
            )
        )
        created_orders.append(
            self._ensure_demo_order(
                admin=admin,
                note="[DEMO] PO Cancelled",
                material=material_zip,
                ordered_qty=Decimal("6.000"),
                receive_qty=None,
                cancelled=True,
            )
        )

        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        for order in created_orders:
            self.stdout.write(f"- PO #{order.id}: {order.notes} [{order.get_status_display()}]")

    def _ensure_demo_order(
        self,
        *,
        admin: User,
        note: str,
        material: RawMaterial,
        ordered_qty: Decimal,
        receive_qty: Decimal | None,
        cancelled: bool,
    ) -> PurchaseOrder:
        existing = PurchaseOrder.objects.filter(notes=note).order_by("-id").first()
        if existing:
            return existing

        order = create_grouped_purchase_orders(
            order_date=timezone.localdate(),
            notes=note,
            created_by=admin,
            lines=[PurchaseLineInput(material=material, quantity=ordered_qty)],
        )[0]

        item = order.items.first()
        assert item is not None

        if receive_qty is not None and receive_qty > 0:
            if receive_qty >= item.quantity:
                receive_purchase_order(purchase_order=order, received_by=admin)
            else:
                receive_purchase_order(
                    purchase_order=order,
                    received_by=admin,
                    line_quantities={item.id: receive_qty},
                )

        if cancelled:
            cancel_purchase_order(purchase_order=order, cancelled_by=admin)

        order.refresh_from_db()
        return order
