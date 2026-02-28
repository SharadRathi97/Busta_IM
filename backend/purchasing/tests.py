from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from inventory.models import InventoryLedger, RawMaterial, RawMaterialVendor
from partners.models import Partner

from .models import (
    PurchaseLineInput,
    PurchaseOrder,
    cancel_purchase_order,
    create_grouped_purchase_orders,
    receive_purchase_order,
    reopen_purchase_order,
)


class PurchasingFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="inventory_user",
            password="test12345",
            role=User.Role.INVENTORY_MANAGER,
        )
        self.admin = User.objects.create_user(
            username="admin_user",
            password="test12345",
            role=User.Role.ADMIN,
        )
        self.viewer = User.objects.create_user(
            username="viewer_user",
            password="test12345",
            role=User.Role.VIEWER,
        )
        self.vendor_a = Partner.objects.create(
            name="Supplier A",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="29ABCDE1234F1Z5",
            address_line1="Area 1",
            city="Bengaluru",
            state="Karnataka",
            pincode="560001",
        )
        self.vendor_b = Partner.objects.create(
            name="Supplier B",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="27ABCDE1234F1Z1",
            address_line1="Area 2",
            city="Mumbai",
            state="Maharashtra",
            pincode="400001",
        )
        self.material_a = RawMaterial.objects.create(
            name="Canvas",
            rm_id="RMID-CANVAS-001",
            code="RM-CANVAS",
            colour_code="NA",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("20.000"),
            reorder_level=Decimal("5.000"),
            vendor=self.vendor_a,
        )
        self.material_b = RawMaterial.objects.create(
            name="Zip Chain",
            rm_id="RMID-ZIP-001",
            code="RM-ZIP",
            colour_code="NA",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("10.000"),
            reorder_level=Decimal("2.000"),
            vendor=self.vendor_b,
        )

    def _mark_fully_approved(self, order: PurchaseOrder):
        now = timezone.now()
        order.inventory_approved_by = self.user
        order.inventory_approved_at = now
        order.admin_approved_by = self.admin
        order.admin_approved_at = now
        order.save(
            update_fields=[
                "inventory_approved_by",
                "inventory_approved_at",
                "admin_approved_by",
                "admin_approved_at",
            ]
        )

    def _create_order(self, quantity_a: str = "5.000", quantity_b: str | None = None) -> PurchaseOrder:
        lines = [PurchaseLineInput(material=self.material_a, quantity=Decimal(quantity_a))]
        if quantity_b is not None:
            lines.append(PurchaseLineInput(material=self.material_b, quantity=Decimal(quantity_b)))
        order = create_grouped_purchase_orders(
            order_date="2026-02-20",
            notes="Restock",
            created_by=self.user,
            lines=lines,
        )[0]
        self._mark_fully_approved(order)
        return order

    def test_create_grouped_purchase_orders(self):
        orders = create_grouped_purchase_orders(
            order_date="2026-02-20",
            notes="Restock",
            created_by=self.user,
            lines=[
                PurchaseLineInput(material=self.material_a, quantity=Decimal("25.000")),
                PurchaseLineInput(material=self.material_b, quantity=Decimal("12.500")),
            ],
        )

        self.assertEqual(len(orders), 2)
        self.assertEqual(PurchaseOrder.objects.count(), 2)
        self.assertEqual(sum(order.items.count() for order in orders), 2)
        self.assertEqual(orders[0].items.first().unit_rate, orders[0].items.first().material.cost_per_unit)

    def test_receive_purchase_order_full_marks_received(self):
        order = self._create_order(quantity_a="5.500")

        receive_purchase_order(purchase_order=order, received_by=self.user)

        order.refresh_from_db()
        self.material_a.refresh_from_db()
        item = order.items.get(material=self.material_a)
        self.assertEqual(order.status, PurchaseOrder.Status.RECEIVED)
        self.assertIsNotNone(order.received_at)
        self.assertEqual(order.received_by_id, self.user.id)
        self.assertEqual(item.received_quantity, Decimal("5.500"))
        self.assertEqual(self.material_a.current_stock, Decimal("25.500"))

        ledger = InventoryLedger.objects.get(reference_type="purchase_order", reference_id=order.id)
        self.assertEqual(ledger.txn_type, InventoryLedger.TxnType.IN)
        self.assertEqual(ledger.quantity, Decimal("5.500"))
        self.assertEqual(ledger.material_id, self.material_a.id)

    def test_receive_purchase_order_partial_marks_partially_received(self):
        order = self._create_order(quantity_a="5.000")
        item = order.items.get(material=self.material_a)

        receive_purchase_order(
            purchase_order=order,
            received_by=self.user,
            line_quantities={item.id: Decimal("2.000")},
        )

        order.refresh_from_db()
        self.material_a.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.PARTIALLY_RECEIVED)
        self.assertEqual(item.received_quantity, Decimal("2.000"))
        self.assertEqual(item.pending_quantity, Decimal("3.000"))
        self.assertEqual(self.material_a.current_stock, Decimal("22.000"))
        self.assertEqual(
            InventoryLedger.objects.filter(reference_type="purchase_order", reference_id=order.id).count(),
            1,
        )

    def test_receive_purchase_order_cannot_exceed_pending(self):
        order = self._create_order(quantity_a="4.000")
        item = order.items.get(material=self.material_a)

        with self.assertRaises(ValidationError):
            receive_purchase_order(
                purchase_order=order,
                received_by=self.user,
                line_quantities={item.id: Decimal("4.100")},
            )

        order.refresh_from_db()
        self.material_a.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.OPEN)
        self.assertEqual(item.received_quantity, Decimal("0.000"))
        self.assertEqual(self.material_a.current_stock, Decimal("20.000"))
        self.assertFalse(InventoryLedger.objects.filter(reference_type="purchase_order", reference_id=order.id).exists())

    def test_cancel_and_reopen_purchase_order(self):
        order = self._create_order(quantity_a="3.000")

        cancel_purchase_order(purchase_order=order, cancelled_by=self.user)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.CANCELLED)
        self.assertEqual(order.cancelled_by_id, self.user.id)
        self.assertIsNotNone(order.cancelled_at)

        reopen_purchase_order(purchase_order=order)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.OPEN)
        self.assertIsNone(order.cancelled_by)
        self.assertIsNone(order.cancelled_at)

    def test_cancel_partially_received_then_reopen_preserves_partial(self):
        order = self._create_order(quantity_a="6.000")
        item = order.items.get(material=self.material_a)
        receive_purchase_order(
            purchase_order=order,
            received_by=self.user,
            line_quantities={item.id: Decimal("2.000")},
        )
        cancel_purchase_order(purchase_order=order, cancelled_by=self.user)

        reopen_purchase_order(purchase_order=order)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.PARTIALLY_RECEIVED)

    def test_fully_received_order_cannot_be_cancelled(self):
        order = self._create_order(quantity_a="2.000")
        receive_purchase_order(purchase_order=order, received_by=self.user)
        with self.assertRaises(ValidationError):
            cancel_purchase_order(purchase_order=order, cancelled_by=self.user)

    def test_receive_purchase_order_view_blocks_viewer_role(self):
        order = self._create_order(quantity_a="3.000")
        item = order.items.get(material=self.material_a)

        self.client.force_login(self.viewer)
        response = self.client.post(
            reverse("purchasing:receive", args=[order.id]),
            {f"receive_{item.id}": "1.000"},
        )
        self.assertEqual(response.status_code, 302)

        order.refresh_from_db()
        self.material_a.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.OPEN)
        self.assertEqual(self.material_a.current_stock, Decimal("20.000"))
        self.assertFalse(InventoryLedger.objects.filter(reference_type="purchase_order", reference_id=order.id).exists())

    def test_receive_purchase_order_view_allows_inventory_manager(self):
        order = self._create_order(quantity_a="4.000")
        item = order.items.get(material=self.material_a)

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("purchasing:receive", args=[order.id]),
            {f"receive_{item.id}": "1.500"},
        )
        self.assertEqual(response.status_code, 302)

        order.refresh_from_db()
        item.refresh_from_db()
        self.material_a.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.PARTIALLY_RECEIVED)
        self.assertEqual(item.received_quantity, Decimal("1.500"))
        self.assertEqual(self.material_a.current_stock, Decimal("21.500"))

    def test_receive_purchase_order_get_page_renders(self):
        order = self._create_order(quantity_a="4.000")
        self.client.force_login(self.user)
        response = self.client.get(reverse("purchasing:receive", args=[order.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Receive Purchase Order #{order.id}")

    def test_cancel_and_reopen_views_for_inventory_manager(self):
        order = self._create_order(quantity_a="2.000")

        self.client.force_login(self.user)
        cancel_response = self.client.post(reverse("purchasing:cancel", args=[order.id]))
        self.assertEqual(cancel_response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.CANCELLED)

        reopen_response = self.client.post(reverse("purchasing:reopen", args=[order.id]))
        self.assertEqual(reopen_response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.OPEN)

    def test_purchase_order_list_filters(self):
        self._create_order(quantity_a="3.000")
        other_order = create_grouped_purchase_orders(
            order_date=date(2026, 2, 15),
            notes="Zip restock",
            created_by=self.user,
            lines=[PurchaseLineInput(material=self.material_b, quantity=Decimal("7.000"))],
        )[0]
        self._mark_fully_approved(other_order)
        cancel_purchase_order(purchase_order=other_order, cancelled_by=self.user)

        self.client.force_login(self.user)
        response = self.client.get(
            reverse("purchasing:list"),
            {
                "status": PurchaseOrder.Status.CANCELLED,
                "vendor": str(self.vendor_b.id),
                "q": "Zip",
                "date_from": "2026-02-01",
                "date_to": "2026-02-20",
            },
        )
        self.assertEqual(response.status_code, 200)
        filtered_orders = list(response.context["orders"])
        self.assertEqual(len(filtered_orders), 1)
        self.assertEqual(filtered_orders[0].id, other_order.id)

    def test_create_purchase_order_page_filters_materials_by_selected_vendor(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("purchasing:list"), {"create_vendor": str(self.vendor_a.id)})
        self.assertEqual(response.status_code, 200)

        materials = set(response.context["line_form"].fields["material"].queryset.values_list("id", flat=True))
        self.assertIn(self.material_a.id, materials)
        self.assertNotIn(self.material_b.id, materials)

    def test_vendor_material_map_groups_colour_variants(self):
        RawMaterial.objects.create(
            name="Canvas",
            rm_id="RMID-CANVAS-001",
            code="HSN-6305",
            colour="Blue",
            colour_code="BLU",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("15.000"),
            reorder_level=Decimal("5.000"),
            vendor=self.vendor_a,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("purchasing:list"), {"create_vendor": str(self.vendor_a.id)})
        self.assertEqual(response.status_code, 200)

        vendor_material_map = response.context["vendor_material_map"]
        vendor_groups = vendor_material_map[str(self.vendor_a.id)]
        canvas_group = next(group for group in vendor_groups if group["group_key"] == "RMID-CANVAS-001")

        self.assertEqual(canvas_group["label"], "Canvas (RMID-CANVAS-001)")
        self.assertEqual(len(canvas_group["variants"]), 2)
        variant_labels = {variant["label"] for variant in canvas_group["variants"]}
        self.assertIn("NA", variant_labels)
        self.assertIn("Blue (BLU)", variant_labels)

    def test_create_purchase_order_rejects_material_not_sold_by_selected_vendor(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("purchasing:list"),
            {
                "vendor": str(self.vendor_a.id),
                "order_date": "2026-02-20",
                "notes": "Invalid mapping",
                "payment_pdc_days": "30",
                "delivery_terms": "As per Schedule",
                "freight_terms": PurchaseOrder.FreightTerms.EXTRA_AS_APPLICABLE,
                "packaging_ident_terms": "Included",
                "inspection_report_terms": "Along with Material",
                "packing_terms": "Standard Packing",
                "material": [str(self.material_b.id)],
                "quantity": ["2.000"],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PurchaseOrder.objects.count(), 0)
        self.assertContains(response, "not sold by the chosen vendor")

    def test_create_purchase_order_allows_material_linked_as_additional_vendor(self):
        vendor_c = Partner.objects.create(
            name="Supplier C",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="24ABCDE1234F1Z2",
            address_line1="Area 3",
            city="Ahmedabad",
            state="Gujarat",
            pincode="380001",
        )
        RawMaterialVendor.objects.create(material=self.material_a, vendor=vendor_c)

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("purchasing:list"),
            {
                "vendor": str(vendor_c.id),
                "order_date": "2026-02-20",
                "notes": "Vendor C order",
                "payment_pdc_days": "45",
                "delivery_terms": "As per Schedule",
                "freight_terms": PurchaseOrder.FreightTerms.INCLUDED,
                "packaging_ident_terms": "Included",
                "inspection_report_terms": "Along with Material",
                "packing_terms": "Standard Packing",
                "material": [str(self.material_a.id)],
                "quantity": ["3.000"],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        order = PurchaseOrder.objects.get(notes="Vendor C order")
        self.assertEqual(order.vendor_id, vendor_c.id)
        self.assertEqual(order.items.get().material_id, self.material_a.id)
        self.assertEqual(order.payment_pdc_days, 45)
        self.assertEqual(order.delivery_terms, "As per Schedule")
        self.assertEqual(order.freight_terms, PurchaseOrder.FreightTerms.INCLUDED)
        self.assertTrue(order.is_pending_approval)

    def test_purchase_order_create_modal_fields_render(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("purchasing:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "id=\"createPoTermsModal\"")
        self.assertContains(response, "PDC against")
        self.assertContains(response, "Extra as applicable")
        self.assertContains(response, "Along with Material")


class PurchaseOrderApprovalWorkflowTests(TestCase):
    def setUp(self):
        self.inventory_manager = User.objects.create_user(
            username="approve_inventory_manager",
            password="test12345",
            role=User.Role.INVENTORY_MANAGER,
        )
        self.admin = User.objects.create_user(
            username="approve_admin",
            password="test12345",
            role=User.Role.ADMIN,
        )
        self.viewer = User.objects.create_user(
            username="approve_viewer",
            password="test12345",
            role=User.Role.VIEWER,
        )
        self.vendor = Partner.objects.create(
            name="Approval Supplier",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="29ABCDE0000F1Z5",
            address_line1="Approval Road",
            city="Bengaluru",
            state="Karnataka",
            pincode="560001",
        )
        self.material = RawMaterial.objects.create(
            name="Approval Material",
            rm_id="RMID-APPROVAL-001",
            code="RM-APPROVAL",
            colour_code="NA",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("10.000"),
            reorder_level=Decimal("5.000"),
            cost_per_unit=Decimal("12.500"),
            vendor=self.vendor,
        )
        self.pending_order = create_grouped_purchase_orders(
            order_date="2026-02-20",
            notes="Needs approvals",
            created_by=self.inventory_manager,
            lines=[PurchaseLineInput(material=self.material, quantity=Decimal("5.000"))],
        )[0]

    def test_pending_po_shows_in_pending_section_not_final_list(self):
        self.client.force_login(self.inventory_manager)
        response = self.client.get(reverse("purchasing:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PO Pending Approvals")
        self.assertIn(self.pending_order.id, [po.id for po in response.context["pending_approval_orders"]])
        self.assertNotIn(self.pending_order.id, [po.id for po in response.context["orders"]])

    def test_po_moves_to_final_list_after_both_approvals(self):
        self.client.force_login(self.inventory_manager)
        self.client.post(reverse("purchasing:approve_inventory", args=[self.pending_order.id]))
        self.pending_order.refresh_from_db()
        self.assertIsNotNone(self.pending_order.inventory_approved_at)
        self.assertIsNone(self.pending_order.admin_approved_at)

        self.client.force_login(self.admin)
        self.client.post(reverse("purchasing:approve_admin", args=[self.pending_order.id]))
        self.pending_order.refresh_from_db()
        self.assertIsNotNone(self.pending_order.inventory_approved_at)
        self.assertIsNotNone(self.pending_order.admin_approved_at)
        self.assertTrue(self.pending_order.is_fully_approved)

        response = self.client.get(reverse("purchasing:list"))
        self.assertIn(self.pending_order.id, [po.id for po in response.context["orders"]])
        self.assertNotIn(self.pending_order.id, [po.id for po in response.context["pending_approval_orders"]])

    def test_inventory_manager_cannot_use_admin_approval_endpoint(self):
        self.client.force_login(self.inventory_manager)
        response = self.client.post(
            reverse("purchasing:approve_admin", args=[self.pending_order.id]),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Only admin can provide admin approval.")

    def test_viewer_cannot_approve_purchase_orders(self):
        self.client.force_login(self.viewer)
        response = self.client.post(
            reverse("purchasing:approve_inventory", args=[self.pending_order.id]),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You do not have permission to manage purchase orders.")

    def test_admin_can_delete_pending_purchase_order(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("purchasing:delete_pending", args=[self.pending_order.id]),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Purchase order #{self.pending_order.id} deleted from pending approvals.")
        self.assertFalse(PurchaseOrder.objects.filter(id=self.pending_order.id).exists())

    def test_inventory_manager_cannot_delete_pending_purchase_order(self):
        self.client.force_login(self.inventory_manager)
        response = self.client.post(
            reverse("purchasing:delete_pending", args=[self.pending_order.id]),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Only admin can delete purchase orders pending approvals.")
        self.assertTrue(PurchaseOrder.objects.filter(id=self.pending_order.id).exists())

    def test_admin_cannot_delete_fully_approved_purchase_order_from_pending_section(self):
        now = timezone.now()
        self.pending_order.inventory_approved_by = self.inventory_manager
        self.pending_order.inventory_approved_at = now
        self.pending_order.admin_approved_by = self.admin
        self.pending_order.admin_approved_at = now
        self.pending_order.save(
            update_fields=[
                "inventory_approved_by",
                "inventory_approved_at",
                "admin_approved_by",
                "admin_approved_at",
            ]
        )

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("purchasing:delete_pending", args=[self.pending_order.id]),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already in final list")
        self.assertTrue(PurchaseOrder.objects.filter(id=self.pending_order.id).exists())

    def test_pending_po_cannot_be_received_or_exported(self):
        self.client.force_login(self.inventory_manager)
        receive_response = self.client.get(reverse("purchasing:receive", args=[self.pending_order.id]), follow=True)
        self.assertEqual(receive_response.status_code, 200)
        self.assertContains(receive_response, "pending approvals")

        export_response = self.client.get(reverse("purchasing:export_pdf", args=[self.pending_order.id]), follow=True)
        self.assertEqual(export_response.status_code, 200)
        self.assertContains(export_response, "pending approvals")


class LowStockPlannerOnPurchasingPageTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username="low_stock_inventory_manager",
            password="test12345",
            role=User.Role.INVENTORY_MANAGER,
        )
        self.viewer = User.objects.create_user(
            username="low_stock_viewer",
            password="test12345",
            role=User.Role.VIEWER,
        )
        self.vendor_a = Partner.objects.create(
            name="Primary Restock Vendor",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="29ABCDE9012F1Z5",
            address_line1="Main Road",
            city="Bengaluru",
            state="Karnataka",
            pincode="560001",
        )
        self.vendor_b = Partner.objects.create(
            name="Alt Restock Vendor",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="29ABCDE3456F1Z5",
            address_line1="Second Street",
            city="Bengaluru",
            state="Karnataka",
            pincode="560002",
        )
        self.vendor_c = Partner.objects.create(
            name="Unlinked Vendor",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="29ABCDE7890F1Z5",
            address_line1="Third Street",
            city="Bengaluru",
            state="Karnataka",
            pincode="560003",
        )
        self.material_a = RawMaterial.objects.create(
            name="Ripstop Nylon",
            rm_id="RMID-RIPSTOP-001",
            code="RM-RIPSTOP",
            colour_code="NA",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("4.000"),
            reorder_level=Decimal("10.000"),
            vendor=self.vendor_a,
        )
        self.material_b = RawMaterial.objects.create(
            name="Woven Lining",
            rm_id="RMID-LINING-001",
            code="RM-LINING",
            colour_code="NA",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("6.000"),
            reorder_level=Decimal("12.000"),
            vendor=self.vendor_a,
        )
        RawMaterialVendor.objects.create(material=self.material_a, vendor=self.vendor_b)

    def test_purchase_order_page_shows_low_stock_planner_for_manager(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("purchasing:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Low Stock Purchase Planner")
        self.assertContains(response, "Create Vendor PO PDF (Batch)")

    def test_create_low_stock_batch_purchase_order_pdf_creates_single_vendor_po(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("purchasing:create_low_stock_batch_po_pdf"),
            {
                "vendor_id": str(self.vendor_a.id),
                "material_id": [str(self.material_a.id), str(self.material_b.id)],
                "quantity": ["8.500", "9.250"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        po = PurchaseOrder.objects.latest("id")
        self.assertEqual(po.vendor_id, self.vendor_a.id)
        self.assertEqual(po.items.count(), 2)
        self.assertEqual(
            set(po.items.values_list("material_id", flat=True)),
            {self.material_a.id, self.material_b.id},
        )
        self.assertEqual(po.items.get(material=self.material_a).quantity, Decimal("8.500"))
        self.assertEqual(po.items.get(material=self.material_b).quantity, Decimal("9.250"))
        self.assertIn(f"purchase_order_{po.id}.pdf", response["Content-Disposition"])
        self.assertGreater(len(response.content), 100)

    def test_create_low_stock_batch_purchase_order_rejects_vendor_not_supplying_all_items(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("purchasing:create_low_stock_batch_po_pdf"),
            {
                "vendor_id": str(self.vendor_b.id),
                "material_id": [str(self.material_a.id), str(self.material_b.id)],
                "quantity": ["7.000", "5.000"],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"{self.vendor_b.name} does not supply {self.material_b.name}.")
        self.assertEqual(PurchaseOrder.objects.count(), 0)

    def test_create_low_stock_batch_purchase_order_requires_manage_permissions(self):
        self.client.force_login(self.viewer)
        response = self.client.post(
            reverse("purchasing:create_low_stock_batch_po_pdf"),
            {
                "vendor_id": str(self.vendor_a.id),
                "material_id": [str(self.material_a.id), str(self.material_b.id)],
                "quantity": ["4.000", "3.000"],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You do not have permission to manage purchase orders.")
        self.assertEqual(PurchaseOrder.objects.count(), 0)
