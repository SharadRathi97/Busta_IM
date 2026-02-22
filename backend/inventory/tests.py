from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from partners.models import Partner
from production.models import BOMItem, FinishedProduct, ProductionOrder, create_production_order_with_rm_request

from .models import MROInventoryLedger, MROItem, RawMaterial, RawMaterialVendor


class RawMaterialCostTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="inv_admin",
            password="test12345",
            role=User.Role.ADMIN,
        )
        self.vendor = Partner.objects.create(
            name="Cost Vendor",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="29ABCDE5678F1Z5",
            address_line1="Warehouse Lane",
            city="Bengaluru",
            state="Karnataka",
            pincode="560010",
        )

    def test_create_raw_material_with_cost_per_unit(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("inventory:list"),
            {
                "name": "Webbing Strap",
                "rm_id": "RMID-WEB-001",
                "code": "RM-WEB",
                "material_type": RawMaterial.MaterialType.ACCESSORY,
                "colour": "Black",
                "colour_code": "BLK",
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "12.500",
                "vendor": str(self.vendor.id),
                "opening_stock": "25.000",
                "reorder_level": "5.000",
            },
        )

        self.assertRedirects(response, reverse("inventory:list"))
        material = RawMaterial.objects.get(code="RM-WEB")
        self.assertEqual(material.cost_per_unit, Decimal("12.500"))
        self.assertEqual(material.colour, "Black")

        list_response = self.client.get(reverse("inventory:list"))
        self.assertContains(list_response, "12.500")
        self.assertContains(list_response, "Black")

    def test_create_raw_material_without_code_defaults_to_rm_id_and_colour_code(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("inventory:list"),
            {
                "name": "Webbing Strap 2",
                "rm_id": "RMID-WEB-002",
                "code": "",
                "material_type": RawMaterial.MaterialType.ACCESSORY,
                "colour": "Grey",
                "colour_code": "GRY",
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "10.000",
                "vendor": str(self.vendor.id),
                "opening_stock": "10.000",
                "reorder_level": "2.000",
            },
        )

        self.assertRedirects(response, reverse("inventory:list"))
        material = RawMaterial.objects.get(rm_id="RMID-WEB-002")
        self.assertEqual(material.code, "RMID-WEB-002-GRY")

    def test_create_material_with_same_rm_id_for_multiple_colour_codes(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("inventory:list"),
            {
                "action": "create_material",
                "name": "Canvas Roll",
                "rm_id": "RMID-CANVAS-001",
                "material_type": RawMaterial.MaterialType.FABRIC,
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "55.000",
                "vendor": str(self.vendor.id),
                "reorder_level": "10.000",
                "variant_colour": ["Blue", "Red"],
                "variant_colour_code": ["BLU", "RED"],
                "variant_code": ["", ""],
                "variant_opening_stock": ["12.500", "8.000"],
            },
        )

        self.assertRedirects(response, reverse("inventory:list"))
        variants = RawMaterial.objects.filter(rm_id="RMID-CANVAS-001").order_by("colour_code")
        self.assertEqual(variants.count(), 2)
        self.assertEqual(
            list(variants.values_list("colour_code", flat=True)),
            ["BLU", "RED"],
        )
        self.assertEqual(
            list(variants.values_list("current_stock", flat=True)),
            [Decimal("12.500"), Decimal("8.000")],
        )

    def test_create_material_allows_same_code_for_different_colours(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("inventory:list"),
            {
                "action": "create_material",
                "name": "Lining Fabric",
                "rm_id": "RMID-LINE-001",
                "material_type": RawMaterial.MaterialType.FABRIC,
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "35.000",
                "vendor": str(self.vendor.id),
                "reorder_level": "8.000",
                "variant_colour": ["Blue", "Red"],
                "variant_colour_code": ["BLU", "RED"],
                "variant_code": ["HSN-6305", "HSN-6305"],
                "variant_opening_stock": ["11.000", "9.000"],
            },
        )

        self.assertRedirects(response, reverse("inventory:list"))
        variants = RawMaterial.objects.filter(rm_id="RMID-LINE-001").order_by("colour_code")
        self.assertEqual(variants.count(), 2)
        self.assertEqual(
            list(variants.values_list("code", flat=True)),
            ["HSN-6305", "HSN-6305"],
        )
        self.assertEqual(
            list(variants.values_list("colour_code", flat=True)),
            ["BLU", "RED"],
        )

    def test_create_material_rejects_duplicate_rm_id_and_colour_code_pair(self):
        self.client.force_login(self.user)
        RawMaterial.objects.create(
            name="Existing Canvas",
            rm_id="RMID-CANVAS-002",
            code="RMID-CANVAS-002-BLU",
            material_type=RawMaterial.MaterialType.FABRIC,
            colour="Blue",
            colour_code="BLU",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("10.000"),
            reorder_level=Decimal("2.000"),
            vendor=self.vendor,
        )

        response = self.client.post(
            reverse("inventory:list"),
            {
                "action": "create_material",
                "name": "New Canvas",
                "rm_id": "RMID-CANVAS-002",
                "material_type": RawMaterial.MaterialType.FABRIC,
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "52.000",
                "vendor": str(self.vendor.id),
                "reorder_level": "5.000",
                "variant_colour": ["Blue"],
                "variant_colour_code": ["BLU"],
                "variant_code": [""],
                "variant_opening_stock": ["5.000"],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RM ID and Colour Code combination already exists")
        self.assertEqual(
            RawMaterial.objects.filter(rm_id="RMID-CANVAS-002", colour_code="BLU").count(),
            1,
        )

    def test_create_material_rejects_duplicate_rm_id_and_colour_code_in_same_submit(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("inventory:list"),
            {
                "action": "create_material",
                "name": "Canvas Roll",
                "rm_id": "RMID-CANVAS-003",
                "material_type": RawMaterial.MaterialType.FABRIC,
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "40.000",
                "vendor": str(self.vendor.id),
                "reorder_level": "5.000",
                "variant_colour": ["Blue", "Blue"],
                "variant_colour_code": ["BLU", "BLU"],
                "variant_code": ["", ""],
                "variant_opening_stock": ["4.000", "6.000"],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Duplicate RM ID + Colour Code in submission")
        self.assertFalse(RawMaterial.objects.filter(rm_id="RMID-CANVAS-003").exists())

    def test_raw_material_csv_template_download(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("inventory:csv_template"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("raw_material_upload_template.csv", response["Content-Disposition"])
        self.assertIn("name,rm_id,code,material_type,colour,colour_code,unit,cost_per_unit", response.content.decode("utf-8"))

    def test_raw_material_csv_upload_creates_material(self):
        self.client.force_login(self.user)
        csv_content = (
            "name,rm_id,code,material_type,colour,colour_code,unit,cost_per_unit,vendor_gst_number,additional_vendor_gst_numbers,opening_stock,reorder_level\n"
            "CSV Canvas,RMID-CSV-001,RM-CSV,fabric,Blue,BLU,m,44.500,29ABCDE5678F1Z5,,120.000,25.000\n"
        )
        upload = SimpleUploadedFile("materials.csv", csv_content.encode("utf-8"), content_type="text/csv")

        response = self.client.post(
            reverse("inventory:list"),
            {
                "action": "upload_csv",
                "csv_file": upload,
            },
        )

        self.assertRedirects(response, reverse("inventory:list"))
        material = RawMaterial.objects.get(code="RM-CSV")
        self.assertEqual(material.cost_per_unit, Decimal("44.500"))
        self.assertEqual(material.colour, "Blue")
        self.assertEqual(material.rm_id, "RMID-CSV-001")
        self.assertEqual(material.colour_code, "BLU")

    def test_delete_raw_material_removes_vendor_and_bom_mappings(self):
        self.client.force_login(self.user)
        extra_vendor = Partner.objects.create(
            name="Extra Supplier",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="29ABCDE5678F1Z6",
            address_line1="Warehouse Street",
            city="Bengaluru",
            state="Karnataka",
            pincode="560011",
        )
        material = RawMaterial.objects.create(
            name="Delete Strap",
            rm_id="RMID-DEL-001",
            code="RM-DEL",
            material_type=RawMaterial.MaterialType.ACCESSORY,
            colour="Grey",
            colour_code="GRY",
            unit=RawMaterial.Unit.METER,
            cost_per_unit=Decimal("10.000"),
            current_stock=Decimal("5.000"),
            reorder_level=Decimal("1.000"),
            vendor=self.vendor,
        )
        RawMaterialVendor.objects.create(material=material, vendor=self.vendor)
        RawMaterialVendor.objects.create(material=material, vendor=extra_vendor)
        product = FinishedProduct.objects.create(name="Delete Test Product", sku="FP-DEL")
        bom_item = BOMItem.objects.create(product=product, material=material, qty_per_unit=Decimal("1.000"))

        response = self.client.post(reverse("inventory:delete", args=[material.id]))

        self.assertRedirects(response, reverse("inventory:list"))
        self.assertFalse(RawMaterial.objects.filter(pk=material.id).exists())
        self.assertFalse(RawMaterialVendor.objects.filter(material_id=material.id).exists())
        self.assertFalse(BOMItem.objects.filter(pk=bom_item.id).exists())


class MROInventoryFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mro_admin",
            password="test12345",
            role=User.Role.ADMIN,
        )
        self.vendor = Partner.objects.create(
            name="MRO Supplier",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="29ABCDE1111F1Z5",
            address_line1="MRO Zone",
            city="Bengaluru",
            state="Karnataka",
            pincode="560001",
        )

    def test_create_mro_item_with_opening_stock(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("inventory:mro_list"),
            {
                "action": "create_mro_item",
                "name": "Torque Wrench",
                "mro_id": "MRO-TOOL-001",
                "code": "",
                "item_type": MROItem.ItemType.TOOL,
                "unit": MROItem.Unit.PIECES,
                "cost_per_unit": "450.000",
                "vendor": str(self.vendor.id),
                "location": "Tool Cage A1",
                "opening_stock": "6.000",
                "reorder_level": "2.000",
            },
        )

        self.assertRedirects(response, reverse("inventory:mro_list"))
        item = MROItem.objects.get(mro_id="MRO-TOOL-001")
        self.assertEqual(item.code, "MRO-TOOL-001")
        self.assertEqual(item.current_stock, Decimal("6.000"))
        self.assertEqual(item.location, "Tool Cage A1")
        self.assertTrue(MROInventoryLedger.objects.filter(item=item, reason="Opening stock").exists())

    def test_create_mro_item_rejects_duplicate_mro_id(self):
        MROItem.objects.create(
            name="Existing Item",
            mro_id="MRO-SPARE-001",
            code="SP-001",
            item_type=MROItem.ItemType.MACHINE_SPARE,
            unit=MROItem.Unit.PIECES,
            cost_per_unit=Decimal("12.000"),
            current_stock=Decimal("4.000"),
            reorder_level=Decimal("1.000"),
            location="Rack 2",
            vendor=self.vendor,
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("inventory:mro_list"),
            {
                "action": "create_mro_item",
                "name": "Duplicate Item",
                "mro_id": "MRO-SPARE-001",
                "code": "SP-002",
                "item_type": MROItem.ItemType.MACHINE_SPARE,
                "unit": MROItem.Unit.PIECES,
                "cost_per_unit": "15.000",
                "vendor": str(self.vendor.id),
                "location": "Rack 3",
                "opening_stock": "3.000",
                "reorder_level": "1.000",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "MRO ID already exists.")
        self.assertEqual(MROItem.objects.filter(mro_id="MRO-SPARE-001").count(), 1)

    def test_edit_and_delete_mro_item(self):
        item = MROItem.objects.create(
            name="Bearing Set",
            mro_id="MRO-PART-001",
            code="BRG-SET",
            item_type=MROItem.ItemType.FACTORY_PART,
            unit=MROItem.Unit.SET,
            cost_per_unit=Decimal("250.000"),
            current_stock=Decimal("3.000"),
            reorder_level=Decimal("1.000"),
            location="Store R1",
            vendor=self.vendor,
        )

        self.client.force_login(self.user)
        update_response = self.client.post(
            reverse("inventory:mro_edit", args=[item.id]),
            {
                "name": "Bearing Set Updated",
                "mro_id": "MRO-PART-001",
                "code": "BRG-SET",
                "item_type": MROItem.ItemType.FACTORY_PART,
                "unit": MROItem.Unit.SET,
                "cost_per_unit": "275.000",
                "vendor": str(self.vendor.id),
                "location": "Store R2",
                "reorder_level": "2.000",
            },
        )
        self.assertRedirects(update_response, reverse("inventory:mro_list"))
        item.refresh_from_db()
        self.assertEqual(item.name, "Bearing Set Updated")
        self.assertEqual(item.location, "Store R2")
        self.assertEqual(item.reorder_level, Decimal("2.000"))

        delete_response = self.client.post(reverse("inventory:mro_delete", args=[item.id]))
        self.assertRedirects(delete_response, reverse("inventory:mro_list"))
        self.assertFalse(MROItem.objects.filter(pk=item.id).exists())

    def test_adjust_mro_stock(self):
        item = MROItem.objects.create(
            name="Safety Gloves",
            mro_id="MRO-CONS-001",
            code="GLOVES",
            item_type=MROItem.ItemType.OTHER,
            unit=MROItem.Unit.PIECES,
            cost_per_unit=Decimal("50.000"),
            current_stock=Decimal("10.000"),
            reorder_level=Decimal("3.000"),
            location="Consumables Bay",
            vendor=self.vendor,
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("inventory:mro_adjust"),
            {
                "item_id": item.id,
                "delta": "-2.000",
                "reason": "Issued to maintenance",
            },
        )

        self.assertRedirects(response, reverse("inventory:mro_list"))
        item.refresh_from_db()
        self.assertEqual(item.current_stock, Decimal("8.000"))
        self.assertTrue(
            MROInventoryLedger.objects.filter(
                item=item,
                txn_type=MROInventoryLedger.TxnType.OUT,
                quantity=Decimal("2.000"),
            ).exists()
        )


class ProductionRMRequestInventoryActionTests(TestCase):
    def setUp(self):
        self.inventory_manager = User.objects.create_user(
            username="inv_manager",
            password="test12345",
            role=User.Role.INVENTORY_MANAGER,
        )
        self.viewer = User.objects.create_user(
            username="inv_viewer",
            password="test12345",
            role=User.Role.VIEWER,
        )
        self.production_manager = User.objects.create_user(
            username="prod_mgr",
            password="test12345",
            role=User.Role.PRODUCTION_MANAGER,
        )
        self.vendor = Partner.objects.create(
            name="RM Request Supplier",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="29ABCDE2222F1Z5",
            address_line1="Store Yard",
            city="Bengaluru",
            state="Karnataka",
            pincode="560002",
        )
        self.material = RawMaterial.objects.create(
            name="Release Canvas",
            rm_id="RMID-REL-001",
            code="RM-REL",
            colour_code="NA",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("50.000"),
            reorder_level=Decimal("5.000"),
            vendor=self.vendor,
        )
        self.product = FinishedProduct.objects.create(name="Release Tote", sku="FP-REL")
        BOMItem.objects.create(product=self.product, material=self.material, qty_per_unit=Decimal("2.000"))

    def test_inventory_manager_sees_pending_rm_requests_table(self):
        order = create_production_order_with_rm_request(
            product=self.product,
            quantity=10,
            notes="Need inventory release",
            created_by=self.production_manager,
        )
        self.client.force_login(self.inventory_manager)
        response = self.client.get(reverse("inventory:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Production RM Requests")
        self.assertContains(response, f"#{order.id}")
        self.assertContains(response, "Release")

    def test_viewer_does_not_see_pending_rm_requests_table(self):
        create_production_order_with_rm_request(
            product=self.product,
            quantity=5,
            notes="Need release",
            created_by=self.production_manager,
        )
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("inventory:list"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Production RM Requests")

    def test_inventory_manager_can_release_rm_request(self):
        order = create_production_order_with_rm_request(
            product=self.product,
            quantity=5,
            notes="Approve",
            created_by=self.production_manager,
        )
        self.client.force_login(self.inventory_manager)
        response = self.client.post(reverse("inventory:release_production_request", args=[order.id]))

        self.assertRedirects(response, reverse("inventory:list"))
        order.refresh_from_db()
        self.material.refresh_from_db()
        self.assertEqual(order.status, ProductionOrder.Status.PLANNED)
        self.assertTrue(order.raw_material_released)
        self.assertEqual(self.material.current_stock, Decimal("40.000"))

    def test_inventory_manager_can_reject_rm_request(self):
        order = create_production_order_with_rm_request(
            product=self.product,
            quantity=5,
            notes="Reject",
            created_by=self.production_manager,
        )
        self.client.force_login(self.inventory_manager)
        response = self.client.post(reverse("inventory:reject_production_request", args=[order.id]))

        self.assertRedirects(response, reverse("inventory:list"))
        order.refresh_from_db()
        self.material.refresh_from_db()
        self.assertEqual(order.status, ProductionOrder.Status.CANCELLED)
        self.assertFalse(order.raw_material_released)
        self.assertEqual(self.material.current_stock, Decimal("50.000"))
