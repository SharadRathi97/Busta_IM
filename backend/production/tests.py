from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from inventory.models import InventoryLedger, RawMaterial
from partners.models import Partner

from .models import (
    BOMItem,
    FinishedProduct,
    FinishedStock,
    FinishedStockLedger,
    ProductionConsumption,
    ProductionOrder,
    cancel_production_order,
    complete_production_order,
    create_production_order_with_rm_request,
    create_production_order_and_deduct_stock,
    reject_raw_materials_for_production_order,
    release_raw_materials_for_production_order,
)


class ProductionOrderFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin_test",
            password="test12345",
            role=User.Role.ADMIN,
        )
        self.vendor = Partner.objects.create(
            name="Main Supplier",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="29ABCDE1234F1Z5",
            address_line1="Industrial Area",
            city="Bengaluru",
            state="Karnataka",
            pincode="560001",
        )
        self.material = RawMaterial.objects.create(
            name="Canvas Cloth",
            rm_id="RMID-CANVAS-001",
            code="RM-CANVAS",
            colour_code="NA",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("100.000"),
            reorder_level=Decimal("10.000"),
            vendor=self.vendor,
        )
        self.product = FinishedProduct.objects.create(name="Eco Tote", sku="FP-TOTE")
        BOMItem.objects.create(product=self.product, material=self.material, qty_per_unit=Decimal("2.000"))

    def test_create_order_deducts_stock_and_writes_consumption(self):
        order = create_production_order_and_deduct_stock(
            product=self.product,
            quantity=10,
            notes="Run A",
            created_by=self.user,
        )
        self.assertIsNotNone(order.id)
        self.assertEqual(order.planned_qty, Decimal("10.000"))

        self.material.refresh_from_db()
        self.assertEqual(self.material.current_stock, Decimal("80.000"))

        consumption = ProductionConsumption.objects.get(production_order=order, material=self.material)
        self.assertEqual(consumption.required_qty, Decimal("20.000"))

    def test_create_order_raises_when_stock_insufficient(self):
        with self.assertRaises(ValidationError):
            create_production_order_and_deduct_stock(
                product=self.product,
                quantity=70,
                notes="Too high",
                created_by=self.user,
            )

    def test_cancel_order_restores_stock_and_marks_cancelled(self):
        order = create_production_order_and_deduct_stock(
            product=self.product,
            quantity=10,
            notes="Run A",
            created_by=self.user,
        )

        cancel_production_order(production_order=order, cancelled_by=self.user)

        order.refresh_from_db()
        self.material.refresh_from_db()
        self.assertEqual(order.status, ProductionOrder.Status.CANCELLED)
        self.assertEqual(self.material.current_stock, Decimal("100.000"))
        self.assertTrue(
            InventoryLedger.objects.filter(
                reference_type="production_order",
                reference_id=order.id,
                txn_type=InventoryLedger.TxnType.IN,
            ).exists()
        )

    def test_cancel_completed_order_raises(self):
        order = create_production_order_and_deduct_stock(
            product=self.product,
            quantity=5,
            notes="Run B",
            created_by=self.user,
        )
        order.status = ProductionOrder.Status.COMPLETED
        order.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            cancel_production_order(production_order=order, cancelled_by=self.user)

    def test_complete_order_updates_finished_stock_and_ledger(self):
        order = create_production_order_and_deduct_stock(
            product=self.product,
            quantity=10,
            notes="Run C",
            created_by=self.user,
        )

        completed = complete_production_order(
            production_order=order,
            produced_qty=Decimal("9.500"),
            scrap_qty=Decimal("0.500"),
            completed_by=self.user,
        )

        completed.refresh_from_db()
        self.assertEqual(completed.status, ProductionOrder.Status.COMPLETED)
        self.assertEqual(completed.produced_qty, Decimal("9.500"))
        self.assertEqual(completed.scrap_qty, Decimal("0.500"))
        self.assertEqual(completed.variance_qty, Decimal("-0.500"))
        self.assertEqual(completed.completed_by_id, self.user.id)
        self.assertIsNotNone(completed.completed_at)

        stock = FinishedStock.objects.get(product=self.product)
        self.assertEqual(stock.current_stock, Decimal("9.500"))
        ledger = FinishedStockLedger.objects.get(
            reference_type="production_order",
            reference_id=order.id,
        )
        self.assertEqual(ledger.txn_type, FinishedStockLedger.TxnType.IN)
        self.assertEqual(ledger.quantity, Decimal("9.500"))

    def test_complete_order_requires_positive_produced_qty(self):
        order = create_production_order_and_deduct_stock(
            product=self.product,
            quantity=2,
            notes="Invalid completion",
            created_by=self.user,
        )
        with self.assertRaises(ValidationError):
            complete_production_order(
                production_order=order,
                produced_qty=Decimal("0.000"),
                scrap_qty=Decimal("0"),
                completed_by=self.user,
            )

    def test_create_order_with_rm_request_does_not_deduct_stock(self):
        order = create_production_order_with_rm_request(
            product=self.product,
            quantity=10,
            notes="Needs release",
            created_by=self.user,
        )
        self.assertEqual(order.status, ProductionOrder.Status.AWAITING_RM_RELEASE)
        self.assertFalse(order.raw_material_released)
        self.material.refresh_from_db()
        self.assertEqual(self.material.current_stock, Decimal("100.000"))
        consumption = ProductionConsumption.objects.get(production_order=order, material=self.material)
        self.assertEqual(consumption.required_qty, Decimal("20.000"))

    def test_release_rm_request_deducts_stock_and_moves_to_planned(self):
        order = create_production_order_with_rm_request(
            product=self.product,
            quantity=5,
            notes="Release this",
            created_by=self.user,
        )
        release_raw_materials_for_production_order(production_order=order, released_by=self.user)
        order.refresh_from_db()
        self.material.refresh_from_db()

        self.assertEqual(order.status, ProductionOrder.Status.PLANNED)
        self.assertTrue(order.raw_material_released)
        self.assertEqual(self.material.current_stock, Decimal("90.000"))
        self.assertTrue(
            InventoryLedger.objects.filter(
                reference_type="production_order",
                reference_id=order.id,
                txn_type=InventoryLedger.TxnType.OUT,
            ).exists()
        )

    def test_reject_rm_request_cancels_without_deduction(self):
        order = create_production_order_with_rm_request(
            product=self.product,
            quantity=5,
            notes="Reject this",
            created_by=self.user,
        )
        reject_raw_materials_for_production_order(production_order=order)
        order.refresh_from_db()
        self.material.refresh_from_db()

        self.assertEqual(order.status, ProductionOrder.Status.CANCELLED)
        self.assertFalse(order.raw_material_released)
        self.assertEqual(self.material.current_stock, Decimal("100.000"))


class ProductBOMActionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="prod_admin",
            password="test12345",
            role=User.Role.ADMIN,
        )
        self.vendor = Partner.objects.create(
            name="Aux Supplier",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="27ABCDE1234F1Z5",
            address_line1="Phase 2",
            city="Pune",
            state="Maharashtra",
            pincode="411001",
        )
        self.material_a = RawMaterial.objects.create(
            name="Nylon Thread",
            rm_id="RMID-THREAD-001",
            code="RM-THREAD",
            colour_code="NA",
            unit=RawMaterial.Unit.KG,
            current_stock=Decimal("40.000"),
            reorder_level=Decimal("5.000"),
            vendor=self.vendor,
        )
        self.material_b = RawMaterial.objects.create(
            name="Zip Roll",
            rm_id="RMID-ZIP-001",
            code="RM-ZIP",
            colour_code="NA",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("60.000"),
            reorder_level=Decimal("8.000"),
            vendor=self.vendor,
        )
        self.material_c = RawMaterial.objects.create(
            name="Foam Sheet",
            rm_id="RMID-FOAM-001",
            code="RM-FOAM",
            colour_code="NA",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("50.000"),
            reorder_level=Decimal("6.000"),
            vendor=self.vendor,
        )
        self.product = FinishedProduct.objects.create(name="Laptop Sleeve", sku="FP-SLEEVE")
        self.product_two = FinishedProduct.objects.create(name="Gym Duffel", sku="FP-DUFFEL")
        self.bom_item = BOMItem.objects.create(
            product=self.product,
            material=self.material_a,
            qty_per_unit=Decimal("0.500"),
        )

    def test_update_bom_item_changes_material_and_qty(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("production:update_bom", args=[self.bom_item.id]),
            {
                "material": self.material_b.id,
                "qty_per_unit": "0.750",
            },
        )

        self.assertRedirects(response, f"{reverse('production:products')}?open_bom={self.product.id}")
        self.bom_item.refresh_from_db()
        self.assertEqual(self.bom_item.material_id, self.material_b.id)
        self.assertEqual(self.bom_item.qty_per_unit, Decimal("0.750"))

    def test_delete_bom_item_removes_mapping(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse("production:delete_bom", args=[self.bom_item.id]))

        self.assertRedirects(response, f"{reverse('production:products')}?open_bom={self.product.id}")
        self.assertFalse(BOMItem.objects.filter(id=self.bom_item.id).exists())

    def test_delete_finished_product_removes_product_and_bom(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse("production:delete_product", args=[self.product.id]))

        self.assertRedirects(response, reverse("production:products"))
        self.assertFalse(FinishedProduct.objects.filter(id=self.product.id).exists())
        self.assertFalse(BOMItem.objects.filter(product_id=self.product.id).exists())

    def test_delete_finished_product_blocked_when_linked_to_production_order(self):
        create_production_order_and_deduct_stock(
            product=self.product,
            quantity=1,
            notes="Linked order",
            created_by=self.user,
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("production:delete_product", args=[self.product.id]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(FinishedProduct.objects.filter(id=self.product.id).exists())
        self.assertContains(response, "Finished product cannot be deleted")

    def test_delete_finished_product_allows_only_cancelled_or_completed_orders(self):
        cancelled_order = create_production_order_and_deduct_stock(
            product=self.product,
            quantity=1,
            notes="Cancelled order",
            created_by=self.user,
        )
        cancel_production_order(production_order=cancelled_order, cancelled_by=self.user)
        completed_order = create_production_order_and_deduct_stock(
            product=self.product,
            quantity=1,
            notes="Completed order",
            created_by=self.user,
        )
        completed_order.status = ProductionOrder.Status.COMPLETED
        completed_order.save(update_fields=["status"])
        self.client.force_login(self.user)

        response = self.client.post(reverse("production:delete_product", args=[self.product.id]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(FinishedProduct.objects.filter(id=self.product.id).exists())
        self.assertFalse(ProductionOrder.objects.filter(id=cancelled_order.id).exists())
        self.assertFalse(ProductionOrder.objects.filter(id=completed_order.id).exists())
        self.assertContains(response, "Removed 2 cancelled/completed production order(s)")

    def test_bulk_add_bom_items_creates_multiple_rows(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("production:products"),
            {
                "action": "add_bom_bulk",
                "bom_product": [str(self.product.id), str(self.product_two.id)],
                "bom_material": [str(self.material_b.id), str(self.material_c.id)],
                "bom_qty": ["0.750", "1.250"],
            },
        )

        self.assertRedirects(response, f"{reverse('production:products')}?open_bom={self.product.id}")
        self.assertTrue(BOMItem.objects.filter(product=self.product, material=self.material_b).exists())
        self.assertTrue(BOMItem.objects.filter(product=self.product_two, material=self.material_c).exists())

    def test_bulk_add_bom_items_rejects_existing_mapping(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("production:products"),
            {
                "action": "add_bom_bulk",
                "bom_product": [str(self.product.id)],
                "bom_material": [str(self.material_a.id)],
                "bom_qty": ["0.700"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertEqual(BOMItem.objects.count(), 1)

    def test_products_page_context_filters_material_options_per_product(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("production:products"))
        self.assertEqual(response.status_code, 200)

        product_material_map = response.context["product_material_map"]
        first_product_material_ids = {item["id"] for item in product_material_map[str(self.product.id)]}
        second_product_material_ids = {item["id"] for item in product_material_map[str(self.product_two.id)]}

        self.assertNotIn(self.material_a.id, first_product_material_ids)
        self.assertIn(self.material_b.id, first_product_material_ids)
        self.assertIn(self.material_c.id, first_product_material_ids)
        self.assertIn(self.material_a.id, second_product_material_ids)

    def test_export_product_bom_excel(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("production:export_bom_excel", args=[self.product.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn(f'bom_{self.product.id}.xlsx', response["Content-Disposition"])
        self.assertGreater(len(response.content), 100)

    def test_export_product_bom_pdf(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("production:export_bom_pdf", args=[self.product.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(f'bom_{self.product.id}.pdf', response["Content-Disposition"])
        self.assertGreater(len(response.content), 100)

    def test_bom_csv_template_download(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("production:bom_csv_template"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("bom_upload_template.csv", response["Content-Disposition"])
        self.assertIn("product_sku,material_code,qty_per_unit", response.content.decode("utf-8"))

    def test_bom_csv_upload_creates_mapping(self):
        self.client.force_login(self.user)
        csv_content = f"product_sku,material_code,qty_per_unit\n{self.product_two.sku},{self.material_b.code},1.200\n"
        upload = SimpleUploadedFile("bom.csv", csv_content.encode("utf-8"), content_type="text/csv")

        response = self.client.post(
            reverse("production:products"),
            {
                "action": "upload_bom_csv",
                "csv_file": upload,
            },
        )

        self.assertRedirects(response, reverse("production:products"))
        self.assertTrue(BOMItem.objects.filter(product=self.product_two, material=self.material_b).exists())


class ProductionOrderActionViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="prod_manager",
            password="test12345",
            role=User.Role.PRODUCTION_MANAGER,
        )
        self.viewer = User.objects.create_user(
            username="prod_viewer",
            password="test12345",
            role=User.Role.VIEWER,
        )
        self.vendor = Partner.objects.create(
            name="Prod Supplier",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="29ABCDE5678F1Z5",
            address_line1="Industrial Road",
            city="Bengaluru",
            state="Karnataka",
            pincode="560001",
        )
        self.material = RawMaterial.objects.create(
            name="Polyester Fabric",
            rm_id="RMID-POLY-001",
            code="RM-POLY",
            colour_code="NA",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("200.000"),
            reorder_level=Decimal("20.000"),
            vendor=self.vendor,
        )
        self.product = FinishedProduct.objects.create(name="Carry Bag", sku="FP-CARRY")
        BOMItem.objects.create(product=self.product, material=self.material, qty_per_unit=Decimal("2.000"))

    def test_cancel_order_view_marks_cancelled_and_restores_stock(self):
        order = create_production_order_and_deduct_stock(
            product=self.product,
            quantity=10,
            notes="Cancel me",
            created_by=self.admin,
        )
        self.material.refresh_from_db()
        self.assertEqual(self.material.current_stock, Decimal("180.000"))

        self.client.force_login(self.admin)
        response = self.client.post(reverse("production:cancel_order", args=[order.id]))

        self.assertRedirects(response, reverse("production:orders"))
        order.refresh_from_db()
        self.material.refresh_from_db()
        self.assertEqual(order.status, ProductionOrder.Status.CANCELLED)
        self.assertEqual(self.material.current_stock, Decimal("200.000"))

    def test_cancel_order_view_blocks_viewer(self):
        order = create_production_order_and_deduct_stock(
            product=self.product,
            quantity=4,
            notes="No permission",
            created_by=self.admin,
        )
        self.client.force_login(self.viewer)
        response = self.client.post(reverse("production:cancel_order", args=[order.id]))

        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertNotEqual(order.status, ProductionOrder.Status.CANCELLED)

    def test_update_status_rejects_completed_order_changes(self):
        order = create_production_order_and_deduct_stock(
            product=self.product,
            quantity=2,
            notes="Freeze after complete",
            created_by=self.admin,
        )
        order.status = ProductionOrder.Status.COMPLETED
        order.save(update_fields=["status"])

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("production:update_status"),
            {
                "order_id": order.id,
                "status": ProductionOrder.Status.IN_PROGRESS,
            },
        )

        self.assertRedirects(response, reverse("production:orders"))
        order.refresh_from_db()
        self.assertEqual(order.status, ProductionOrder.Status.COMPLETED)

    def test_update_status_to_completed_posts_finished_stock(self):
        order = create_production_order_and_deduct_stock(
            product=self.product,
            quantity=3,
            notes="Complete via action",
            created_by=self.admin,
        )

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("production:update_status"),
            {
                "order_id": order.id,
                "status": ProductionOrder.Status.COMPLETED,
                "produced_qty": "2.750",
                "scrap_qty": "0.250",
            },
        )

        self.assertRedirects(response, reverse("production:orders"))
        order.refresh_from_db()
        self.assertEqual(order.status, ProductionOrder.Status.COMPLETED)
        self.assertEqual(order.produced_qty, Decimal("2.750"))
        self.assertEqual(order.scrap_qty, Decimal("0.250"))
        self.assertTrue(FinishedStock.objects.filter(product=self.product, current_stock=Decimal("2.750")).exists())

    def test_create_order_view_sets_awaiting_rm_release(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("production:orders"),
            {
                "product": str(self.product.id),
                "quantity": "6",
                "notes": "UI create request",
            },
        )
        self.assertRedirects(response, reverse("production:orders"))
        order = ProductionOrder.objects.latest("id")
        self.assertEqual(order.status, ProductionOrder.Status.AWAITING_RM_RELEASE)
        self.assertFalse(order.raw_material_released)
        self.material.refresh_from_db()
        self.assertEqual(self.material.current_stock, Decimal("200.000"))

    def test_update_status_to_in_progress_blocked_until_rm_release(self):
        order = create_production_order_with_rm_request(
            product=self.product,
            quantity=4,
            notes="Gate check",
            created_by=self.admin,
        )
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("production:update_status"),
            {
                "order_id": order.id,
                "status": ProductionOrder.Status.IN_PROGRESS,
            },
        )
        self.assertRedirects(response, reverse("production:orders"))
        order.refresh_from_db()
        self.assertEqual(order.status, ProductionOrder.Status.AWAITING_RM_RELEASE)
