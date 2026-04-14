import tempfile
from io import BytesIO
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from accounts.models import User
from inventory.models import InventoryLedger, RawMaterial
from partners.models import Partner

from .models import (
    BOMItem,
    FINISHED_PRODUCT_IMAGE_SIZE,
    FinishedProduct,
    FinishedStock,
    FinishedStockLedger,
    Marker,
    MarkerOutput,
    PartProduction,
    ProductionConsumption,
    ProductionOrder,
    cancel_production_order,
    complete_marker_production_order,
    complete_production_order,
    create_marker_production_order_with_rm_request,
    create_production_order_with_rm_request,
    create_production_order_and_deduct_stock,
    reject_raw_materials_for_production_order,
    release_raw_materials_for_production_order,
)


def _make_test_image_file(name: str = "product.png", *, size: tuple[int, int] = (900, 500), color: str = "navy"):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class ProductionOrderFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin_test",
            password="test12345",
            role=User.Role.ADMIN,
        )
        self.vendor = Partner.objects.create(
            name="Main Supplier",
            vendor_id="VEND-TEST-001",
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
        self.material_alt = RawMaterial.objects.create(
            name="Recycled Fabric",
            rm_id="RMID-RECYCLED-001",
            code="RM-RECYCLE",
            colour_code="NA",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("120.000"),
            reorder_level=Decimal("12.000"),
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

    def test_create_order_with_rm_request_uses_one_time_bom_override(self):
        order = create_production_order_with_rm_request(
            product=self.product,
            quantity=10,
            notes="Needs release with one-time BOM edit",
            created_by=self.user,
            bom_qty_overrides={f"raw:{self.material.id}": Decimal("2.500")},
        )

        self.assertEqual(order.status, ProductionOrder.Status.AWAITING_RM_RELEASE)
        consumption = ProductionConsumption.objects.get(production_order=order, material=self.material)
        self.assertEqual(consumption.required_qty, Decimal("25.000"))
        self.material.refresh_from_db()
        self.assertEqual(self.material.current_stock, Decimal("100.000"))

    def test_create_order_with_rm_request_allows_component_swap(self):
        order = create_production_order_with_rm_request(
            product=self.product,
            quantity=10,
            notes="Swap the material for this one run",
            created_by=self.user,
            bom_qty_overrides={f"raw:{self.material.id}": (f"raw:{self.material_alt.id}", Decimal("1.500"))},
        )

        self.assertEqual(order.status, ProductionOrder.Status.AWAITING_RM_RELEASE)
        self.assertFalse(ProductionConsumption.objects.filter(production_order=order, material=self.material).exists())
        swapped = ProductionConsumption.objects.get(production_order=order, material=self.material_alt)
        self.assertEqual(swapped.required_qty, Decimal("15.000"))
        self.material.refresh_from_db()
        self.material_alt.refresh_from_db()
        self.assertEqual(self.material.current_stock, Decimal("100.000"))
        self.assertEqual(self.material_alt.current_stock, Decimal("120.000"))

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


class MarkerProductionFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="marker_admin",
            password="test12345",
            role=User.Role.ADMIN,
        )
        self.vendor = Partner.objects.create(
            name="Cutting Supplier",
            vendor_id="VEND-TEST-002",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="29MARKER1234F1Z5",
            address_line1="Cutting Area",
            city="Bengaluru",
            state="Karnataka",
            pincode="560001",
        )
        self.fabric = RawMaterial.objects.create(
            name="Air Mesh",
            rm_id="RMID-MESH-001",
            code="RM-MESH-BLK",
            material_type=RawMaterial.MaterialType.MESH,
            colour="Black",
            colour_code="BLK",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("100.000"),
            reorder_level=Decimal("10.000"),
            vendor=self.vendor,
        )
        self.part = FinishedProduct.objects.create(
            name="Shoulder Pad",
            sku="PT-SHOULDER-BLK",
            item_type=FinishedProduct.ItemType.PART,
            colour="Black",
        )
        self.marker = Marker.objects.create(
            marker_id="MKR-001",
            material=self.fabric,
            colour="Black",
            sku_id="SKU-MKR-001",
            length_per_layer=Decimal("1.000"),
            sets_per_layer=Decimal("2.000"),
        )
        MarkerOutput.objects.create(
            marker=self.marker,
            part=self.part,
            quantity_per_set=Decimal("2.000"),
        )

    def test_create_marker_order_requests_planned_fabric_without_deducting_stock(self):
        order = create_marker_production_order_with_rm_request(
            marker=self.marker,
            sets=10,
            notes="Cut pads",
            created_by=self.user,
        )

        self.assertEqual(order.target_type, ProductionOrder.TargetType.MARKER)
        self.assertEqual(order.status, ProductionOrder.Status.AWAITING_RM_RELEASE)
        self.assertFalse(order.raw_material_released)
        consumption = ProductionConsumption.objects.get(production_order=order, material=self.fabric)
        self.assertEqual(consumption.required_qty, Decimal("5.000"))
        self.fabric.refresh_from_db()
        self.assertEqual(self.fabric.current_stock, Decimal("100.000"))

    def test_complete_marker_order_records_cut_and_adds_part_inventory(self):
        order = create_marker_production_order_with_rm_request(
            marker=self.marker,
            sets=10,
            notes="Cut pads",
            created_by=self.user,
        )
        release_raw_materials_for_production_order(production_order=order, released_by=self.user)
        self.fabric.refresh_from_db()
        self.assertEqual(self.fabric.current_stock, Decimal("95.000"))

        completed = complete_marker_production_order(
            production_order=order,
            actual_length=Decimal("1.000"),
            actual_layers=6,
            actual_sets_per_layer=Decimal("2.000"),
            total_sets=Decimal("10.000"),
            actual_fabric_issued=Decimal("6.000"),
            good_sets=Decimal("9.000"),
            rejected_sets=Decimal("1.000"),
            completed_by=self.user,
        )

        completed.refresh_from_db()
        self.fabric.refresh_from_db()
        cut = PartProduction.objects.get(production_order=completed)
        part_stock = FinishedStock.objects.get(product=self.part)

        self.assertEqual(completed.status, ProductionOrder.Status.COMPLETED)
        self.assertEqual(completed.produced_qty, Decimal("9.000"))
        self.assertEqual(completed.scrap_qty, Decimal("1.000"))
        self.assertEqual(cut.actual_fabric_issued, Decimal("6.000"))
        self.assertEqual(self.fabric.current_stock, Decimal("94.000"))
        self.assertEqual(part_stock.current_stock, Decimal("18.000"))
        self.assertTrue(
            FinishedStockLedger.objects.filter(
                product=self.part,
                reference_type="part_production",
                reference_id=cut.id,
                quantity=Decimal("18.000"),
            ).exists()
        )
        consumption = ProductionConsumption.objects.get(production_order=completed, material=self.fabric)
        self.assertEqual(consumption.required_qty, Decimal("6.000"))

    def test_marker_order_view_creates_rm_request(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("production:orders"),
            {
                "order_type": ProductionOrder.TargetType.MARKER,
                "marker": str(self.marker.id),
                "quantity": "8",
                "notes": "UI marker order",
            },
        )

        self.assertRedirects(response, reverse("production:orders"))
        order = ProductionOrder.objects.latest("id")
        self.assertEqual(order.marker_id, self.marker.id)
        self.assertEqual(order.target_type, ProductionOrder.TargetType.MARKER)
        consumption = ProductionConsumption.objects.get(production_order=order, material=self.fabric)
        self.assertEqual(consumption.required_qty, Decimal("4.000"))


class ProductBOMActionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="prod_admin",
            password="test12345",
            role=User.Role.ADMIN,
        )
        self.vendor = Partner.objects.create(
            name="Aux Supplier",
            vendor_id="VEND-TEST-003",
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
        self.part = FinishedProduct.objects.create(
            name="Handle Strap",
            sku="PT-HANDLE",
            item_type=FinishedProduct.ItemType.PART,
            colour="Black",
        )
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
                "component": f"raw:{self.material_b.id}",
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

    def test_delete_finished_product_missing_id_redirects_with_message(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("production:delete_product", args=[999999]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selected item no longer exists.")

    def test_add_bom_item_from_product_modal_creates_mapping(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("production:products"),
            {
                "action": "add_bom",
                "open_bom": str(self.product.id),
                "bom-product": str(self.product.id),
                "bom-component": f"raw:{self.material_b.id}",
                "bom-qty_per_unit": "0.750",
            },
        )

        self.assertRedirects(response, f"{reverse('production:products')}?open_bom={self.product.id}")
        self.assertTrue(BOMItem.objects.filter(product=self.product, material=self.material_b).exists())

    def test_add_bom_item_form_uses_component_typeahead(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("production:products"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-add-bom-form")
        self.assertContains(response, "data-component-typeahead")
        self.assertContains(response, f"addBomComponentOptions{self.product.id}")
        self.assertContains(response, f'data-value="raw:{self.material_b.id}"')

    def test_add_bom_item_from_product_modal_reopens_on_validation_error(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("production:products"),
            {
                "action": "add_bom",
                "open_bom": str(self.product.id),
                "bom-product": str(self.product.id),
                "bom-component": f"raw:{self.material_a.id}",
                "bom-qty_per_unit": "0.700",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["open_bom_id"], self.product.id)
        self.assertContains(response, "This BOM mapping already exists.")
        self.assertEqual(BOMItem.objects.count(), 1)

    def test_bulk_add_bom_items_creates_multiple_rows(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("production:products"),
            {
                "action": "add_bom_bulk",
                "bom_product": [str(self.product.id), str(self.product_two.id)],
                "bom_component": [f"raw:{self.material_b.id}", f"raw:{self.material_c.id}"],
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
                "bom_component": [f"raw:{self.material_a.id}"],
                "bom_qty": ["0.700"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertEqual(BOMItem.objects.count(), 1)

    def test_add_marker_and_output_part_from_products_page(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("production:products"),
            {
                "action": "add_marker",
                "marker-marker_id": "mkr-sleeve",
                "marker-material": str(self.material_c.id),
                "marker-colour": "Black",
                "marker-sku_id": self.product.sku,
                "marker-length_per_layer": "1.000",
                "marker-sets_per_layer": "2.000",
                "marker_output_part": [str(self.part.id)],
                "marker_output_qty": ["2.000"],
            },
        )

        marker = Marker.objects.get(marker_id="MKR-SLEEVE")
        self.assertRedirects(response, reverse("production:products"))
        self.assertTrue(
            MarkerOutput.objects.filter(
                marker=marker,
                part=self.part,
                quantity_per_set=Decimal("2.000"),
            ).exists()
        )

    def test_add_marker_can_create_output_parts_inline(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("production:products"),
            {
                "action": "add_marker",
                "marker-marker_id": "mkr-inline",
                "marker-material": str(self.material_c.id),
                "marker-colour": "NA",
                "marker-sku_id": self.product.sku,
                "marker-length_per_layer": "1.000",
                "marker-sets_per_layer": "2.000",
                "marker_output_part": [str(self.part.id)],
                "marker_output_qty": ["3.000"],
            },
        )

        marker = Marker.objects.get(marker_id="MKR-INLINE")
        self.assertRedirects(response, reverse("production:products"))
        self.assertTrue(
            MarkerOutput.objects.filter(
                marker=marker,
                part=self.part,
                quantity_per_set=Decimal("3.000"),
            ).exists()
        )

    def test_add_marker_page_uses_typeahead_catalogs(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("production:products"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "markerMaterialInput")
        self.assertContains(response, "markerPartSuggestions")
        self.assertContains(response, "finishedProductSkuSuggestions")
        self.assertContains(response, "markerMaterialCatalogData")

    def test_products_page_context_filters_component_options_per_product(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("production:products"))
        self.assertEqual(response.status_code, 200)

        product_component_map = response.context["product_component_map"]
        first_product_components = product_component_map[str(self.product.id)]
        second_product_components = product_component_map[str(self.product_two.id)]

        def available_component_values(options):
            values: set[str] = set()
            for option in options:
                if option["kind"] == "raw_material":
                    values.update(variant["value"] for variant in option["variants"])
                else:
                    values.add(option["value"])
            return values

        first_component_values = available_component_values(first_product_components)
        second_component_values = available_component_values(second_product_components)

        self.assertNotIn(f"raw:{self.material_a.id}", first_component_values)
        self.assertIn(f"raw:{self.material_b.id}", first_component_values)
        self.assertIn(f"raw:{self.material_c.id}", first_component_values)
        self.assertIn(f"raw:{self.material_a.id}", second_component_values)
        self.assertIn(f"part:{self.part.id}", first_component_values)

    def test_products_page_groups_raw_material_variants_under_one_component_option(self):
        material_blue = RawMaterial.objects.create(
            name="Webbing Tape",
            rm_id="RMID-WEB-100",
            code="RM-WEB-100-BLU",
            colour="Blue",
            colour_code="BLU",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("20.000"),
            reorder_level=Decimal("2.000"),
            vendor=self.vendor,
        )
        material_black = RawMaterial.objects.create(
            name="Webbing Tape",
            rm_id="RMID-WEB-100",
            code="RM-WEB-100-BLK",
            colour="Black",
            colour_code="BLK",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("18.000"),
            reorder_level=Decimal("2.000"),
            vendor=self.vendor,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("production:products"))

        self.assertEqual(response.status_code, 200)
        product_components = response.context["product_component_map"][str(self.product.id)]
        grouped_option = next(
            option
            for option in product_components
            if option["kind"] == "raw_material" and option["label"] == "Raw Material - Webbing Tape (RMID-WEB-100)"
        )
        self.assertEqual(
            {variant["value"] for variant in grouped_option["variants"]},
            {f"raw:{material_blue.id}", f"raw:{material_black.id}"},
        )
        self.assertEqual(
            {variant["label"] for variant in grouped_option["variants"]},
            {"Blue / BLU", "Black / BLK"},
        )

    def test_bom_item_component_name_includes_material_variant(self):
        material = RawMaterial.objects.create(
            name="Panel Fabric",
            rm_id="RMID-PANEL-001",
            code="RM-PANEL-001-BLU",
            colour="Blue",
            colour_code="BLU",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("25.000"),
            reorder_level=Decimal("3.000"),
            vendor=self.vendor,
        )
        bom_item = BOMItem.objects.create(
            product=self.product_two,
            material=material,
            qty_per_unit=Decimal("1.000"),
        )

        self.assertEqual(bom_item.component_name, "Panel Fabric (Blue / BLU)")

    def test_bulk_add_bom_allows_part_component_for_finished_product(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("production:products"),
            {
                "action": "add_bom_bulk",
                "bom_product": [str(self.product.id)],
                "bom_component": [f"part:{self.part.id}"],
                "bom_qty": ["1.000"],
            },
        )
        self.assertRedirects(response, f"{reverse('production:products')}?open_bom={self.product.id}")
        self.assertTrue(BOMItem.objects.filter(product=self.product, part=self.part).exists())

    def test_add_part_requires_colour(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("production:products"),
            {
                "action": "add_part",
                "part-name": "Shoulder Pad",
                "part-sku": self.product.sku,
                "part-item_type": FinishedProduct.ItemType.PART,
                "part-colour": "",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Colour is required for parts.")
        self.assertFalse(
            FinishedProduct.objects.filter(
                sku=self.product.sku,
                item_type=FinishedProduct.ItemType.PART,
                name="Shoulder Pad",
            ).exists()
        )

    def test_add_part_with_colour_creates_part(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("production:products"),
            {
                "action": "add_part",
                "part-name": "Shoulder Pad",
                "part-sku": self.product.sku,
                "part-item_type": FinishedProduct.ItemType.PART,
                "part-colour": "Navy",
            },
        )

        self.assertRedirects(response, reverse("production:products"))
        created = FinishedProduct.objects.get(sku=self.product.sku, item_type=FinishedProduct.ItemType.PART)
        self.assertEqual(created.item_type, FinishedProduct.ItemType.PART)
        self.assertEqual(created.colour, "Navy")

    def test_add_finished_product_with_image_resizes_upload(self):
        self.client.force_login(self.user)
        upload = _make_test_image_file()

        with tempfile.TemporaryDirectory() as media_root:
            with self.settings(MEDIA_ROOT=media_root):
                response = self.client.post(
                    reverse("production:products"),
                    {
                        "action": "add_product",
                        "prod-name": "Photo Tote",
                        "prod-sku": "FP-PHOTO",
                        "prod-item_type": FinishedProduct.ItemType.FINISHED,
                        "prod-product_image": upload,
                    },
                )

                self.assertRedirects(response, reverse("production:products"))
                created = FinishedProduct.objects.get(sku="FP-PHOTO")
                self.assertTrue(created.product_image.name.startswith("finished_products/"))
                with Image.open(created.product_image.path) as saved_image:
                    self.assertEqual(saved_image.size, FINISHED_PRODUCT_IMAGE_SIZE)

    def test_products_page_shows_finished_product_image_column(self):
        self.client.force_login(self.user)

        with tempfile.TemporaryDirectory() as media_root:
            with self.settings(MEDIA_ROOT=media_root):
                self.product.product_image = _make_test_image_file(name="existing-product.png")
                self.product.save()

                response = self.client.get(reverse("production:products"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<th>Image</th>", html=True)
        self.assertContains(response, self.product.product_image.url)
        self.assertContains(response, "product-thumb")
        content = response.content.decode("utf-8")
        finished_products_section = content.split("<h2 class=\"h5\">Finished Products</h2>", 1)[1]
        self.assertLess(finished_products_section.index("<th>ID</th>"), finished_products_section.index("<th>Image</th>"))

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
            vendor_id="VEND-TEST-004",
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
        self.material_alt = RawMaterial.objects.create(
            name="Linen Fabric",
            rm_id="RMID-LINEN-001",
            code="RM-LINEN",
            colour_code="NA",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("150.000"),
            reorder_level=Decimal("15.000"),
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

    def test_create_order_view_accepts_one_time_bom_override(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("production:orders"),
            {
                "product": str(self.product.id),
                "quantity": "6",
                "notes": "UI create request with BOM edit",
                "bom_row_key": [f"raw:{self.material.id}"],
                "bom_component_value": [f"raw:{self.material.id}"],
                "bom_qty_per_unit": ["2.500"],
            },
        )

        self.assertRedirects(response, reverse("production:orders"))
        order = ProductionOrder.objects.latest("id")
        consumption = ProductionConsumption.objects.get(production_order=order, material=self.material)
        self.assertEqual(consumption.required_qty, Decimal("15.000"))
        self.material.refresh_from_db()
        self.assertEqual(self.material.current_stock, Decimal("200.000"))

    def test_create_order_view_allows_component_change_for_one_time_run(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("production:orders"),
            {
                "product": str(self.product.id),
                "quantity": "6",
                "notes": "Switch material for this run",
                "bom_row_key": [f"raw:{self.material.id}"],
                "bom_component_value": [f"raw:{self.material_alt.id}"],
                "bom_qty_per_unit": ["1.500"],
            },
        )

        self.assertRedirects(response, reverse("production:orders"))
        order = ProductionOrder.objects.latest("id")
        self.assertFalse(ProductionConsumption.objects.filter(production_order=order, material=self.material).exists())
        swapped = ProductionConsumption.objects.get(production_order=order, material=self.material_alt)
        self.assertEqual(swapped.required_qty, Decimal("9.000"))
        self.material.refresh_from_db()
        self.material_alt.refresh_from_db()
        self.assertEqual(self.material.current_stock, Decimal("200.000"))
        self.assertEqual(self.material_alt.current_stock, Decimal("150.000"))

    def test_orders_page_shows_view_bom_option_and_component_details(self):
        order = create_production_order_with_rm_request(
            product=self.product,
            quantity=4,
            notes="View BOM modal",
            created_by=self.admin,
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse("production:orders"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"orderBomModal{order.id}")
        self.assertContains(response, "View BOM")
        self.assertContains(response, self.material.name)
        self.assertContains(response, self.material.code)

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
