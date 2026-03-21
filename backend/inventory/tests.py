import tempfile
from io import BytesIO
from decimal import Decimal
from urllib.parse import quote

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from accounts.models import User
from partners.models import Partner
from production.models import BOMItem, FinishedProduct, ProductionOrder, create_production_order_with_rm_request

from .models import InventoryLedger, MROInventoryLedger, MROItem, RawMaterial, RawMaterialVendor


def _make_test_image_file(name: str = "finished-product.png", *, size: tuple[int, int] = (720, 480), color: str = "teal"):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


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

    def test_create_raw_material_records_invoice_number_on_opening_stock_transaction(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("inventory:list"),
            {
                "name": "Invoice Strap",
                "rm_id": "RMID-INV-001",
                "code": "RM-INV-001",
                "material_type": RawMaterial.MaterialType.ACCESSORY,
                "colour": "Black",
                "colour_code": "BLK",
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "9.500",
                "vendor": str(self.vendor.id),
                "invoice_number": "INV-2026-001",
                "opening_stock": "12.000",
                "reorder_level": "3.000",
            },
        )

        self.assertRedirects(response, reverse("inventory:list"))
        material = RawMaterial.objects.get(rm_id="RMID-INV-001")
        ledger_entry = InventoryLedger.objects.get(material=material, reference_type="opening_stock")
        self.assertEqual(ledger_entry.invoice_number, "INV-2026-001")

    def test_raw_material_list_shows_vendor_colour_code_and_pantone_columns(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("inventory:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "vendor colour code")
        self.assertContains(response, "Pantone Number")

    def test_raw_material_list_uses_auto_fit_table_markup(self):
        self.client.force_login(self.user)
        RawMaterial.objects.create(
            name="Auto Fit Canvas",
            rm_id="RMID-AUTO-FIT-001",
            code="RMID-AUTO-FIT-001-BLK",
            material_type=RawMaterial.MaterialType.FABRIC,
            colour="Black",
            colour_code="BLK",
            unit=RawMaterial.Unit.METER,
            cost_per_unit=Decimal("28.000"),
            current_stock=Decimal("18.000"),
            reorder_level=Decimal("5.000"),
            vendor=self.vendor,
        )
        response = self.client.get(reverse("inventory:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="materialsInventoryTable"')
        self.assertContains(response, 'data-auto-fit-columns="true"')
        self.assertContains(response, "materials-adjust-form")
        self.assertContains(response, "materials-row-actions")

    def test_raw_material_create_redirect_preserves_active_filters(self):
        self.client.force_login(self.user)
        next_url = f"{reverse('inventory:list')}?q=Canvas"

        response = self.client.post(
            next_url,
            {
                "action": "create_material",
                "name": "Canvas Roll",
                "rm_id": "RMID-CANVAS-010",
                "code": "RM-CANVAS-010",
                "material_type": RawMaterial.MaterialType.FABRIC,
                "colour": "Blue",
                "colour_code": "BLU",
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "22.500",
                "vendor": str(self.vendor.id),
                "opening_stock": "40.000",
                "reorder_level": "8.000",
            },
        )

        self.assertRedirects(response, next_url)

    def test_raw_material_edit_and_adjust_preserve_active_filters(self):
        self.client.force_login(self.user)
        material = RawMaterial.objects.create(
            name="Filter Retain Fabric",
            rm_id="RMID-FILTER-001",
            code="RM-FILTER-001",
            material_type=RawMaterial.MaterialType.FABRIC,
            colour="Grey",
            colour_code="GRY",
            unit=RawMaterial.Unit.METER,
            cost_per_unit=Decimal("18.500"),
            current_stock=Decimal("12.000"),
            reorder_level=Decimal("4.000"),
            vendor=self.vendor,
        )
        next_url = f"{reverse('inventory:list')}?q=Filter"

        edit_page = self.client.get(
            f"{reverse('inventory:edit', args=[material.id])}?next={quote(next_url, safe='')}"
        )
        self.assertEqual(edit_page.status_code, 200)
        self.assertContains(edit_page, f'href="{next_url}"')

        update_response = self.client.post(
            reverse("inventory:edit", args=[material.id]),
            {
                "next": next_url,
                "name": "Filter Retain Fabric Updated",
                "rm_id": "RMID-FILTER-001",
                "code": "RM-FILTER-001",
                "material_type": RawMaterial.MaterialType.FABRIC,
                "colour": "Grey",
                "colour_code": "GRY",
                "pantone_number": "",
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "19.000",
                "vendor": str(self.vendor.id),
                "reorder_level": "5.000",
            },
        )
        self.assertRedirects(update_response, next_url)

        adjust_response = self.client.post(
            reverse("inventory:adjust"),
            {
                "next": next_url,
                "material_id": material.id,
                "delta": "1.000",
                "reason": "Cycle count correction",
            },
        )
        self.assertRedirects(adjust_response, next_url)

    def test_raw_material_create_modal_includes_autofill_dataset(self):
        self.client.force_login(self.user)
        RawMaterial.objects.create(
            name="AutoFill Canvas",
            rm_id="RMID-AUTO-001",
            code="RMID-AUTO-001-BLU",
            material_type=RawMaterial.MaterialType.FABRIC,
            colour="Blue",
            colour_code="BLU",
            unit=RawMaterial.Unit.METER,
            cost_per_unit=Decimal("33.000"),
            current_stock=Decimal("15.000"),
            reorder_level=Decimal("4.000"),
            vendor=self.vendor,
        )

        response = self.client.get(reverse("inventory:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="materialAutofillRowsData"')
        self.assertContains(response, '"rm_id": "RMID-AUTO-001"')

    def test_raw_material_edit_page_includes_autofill_dataset(self):
        self.client.force_login(self.user)
        material = RawMaterial.objects.create(
            name="AutoFill Edit Canvas",
            rm_id="RMID-AUTO-EDIT-001",
            code="RMID-AUTO-EDIT-001-BLK",
            material_type=RawMaterial.MaterialType.FABRIC,
            colour="Black",
            colour_code="BLK",
            unit=RawMaterial.Unit.METER,
            cost_per_unit=Decimal("39.000"),
            current_stock=Decimal("20.000"),
            reorder_level=Decimal("5.000"),
            vendor=self.vendor,
        )

        response = self.client.get(reverse("inventory:edit", args=[material.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="materialEditAutofillRowsData"')
        self.assertContains(response, '"rm_id": "RMID-AUTO-EDIT-001"')

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

    def test_create_raw_material_without_code_defaults_to_rm_id_and_pantone(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("inventory:list"),
            {
                "name": "Pantone Strap",
                "rm_id": "RMID-PAN-001",
                "code": "",
                "material_type": RawMaterial.MaterialType.ACCESSORY,
                "colour": "Blue",
                "colour_code": "",
                "pantone_number": "PANTONE-286 C",
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "11.000",
                "vendor": str(self.vendor.id),
                "opening_stock": "12.000",
                "reorder_level": "2.000",
            },
        )

        self.assertRedirects(response, reverse("inventory:list"))
        material = RawMaterial.objects.get(rm_id="RMID-PAN-001")
        self.assertEqual(material.code, "RMID-PAN-001-PANTONE-286 C")
        self.assertEqual(material.pantone_number, "PANTONE-286 C")

    def test_create_raw_material_merges_same_rm_id_and_pantone(self):
        self.client.force_login(self.user)
        material = RawMaterial.objects.create(
            name="Existing Pantone Strap",
            rm_id="RMID-PAN-002",
            code="RMID-PAN-002-PANTONE-286 C",
            material_type=RawMaterial.MaterialType.ACCESSORY,
            colour="Blue",
            colour_code="",
            pantone_number="PANTONE-286 C",
            unit=RawMaterial.Unit.METER,
            cost_per_unit=Decimal("40.000"),
            current_stock=Decimal("8.000"),
            reorder_level=Decimal("2.000"),
            vendor=self.vendor,
        )

        response = self.client.post(
            reverse("inventory:list"),
            {
                "name": "Incoming Pantone Strap",
                "rm_id": "RMID-PAN-002",
                "code": "DIFF-PANTONE-CODE",
                "material_type": RawMaterial.MaterialType.ACCESSORY,
                "colour": "Blue",
                "colour_code": "",
                "pantone_number": "PANTONE-286 C",
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "52.000",
                "vendor": str(self.vendor.id),
                "opening_stock": "4.000",
                "reorder_level": "5.000",
            },
        )

        self.assertRedirects(response, reverse("inventory:list"))
        material.refresh_from_db()
        self.assertEqual(RawMaterial.objects.filter(rm_id="RMID-PAN-002", pantone_number="PANTONE-286 C").count(), 1)
        self.assertEqual(material.current_stock, Decimal("12.000"))
        self.assertEqual(material.cost_per_unit, Decimal("44.000"))
        self.assertEqual(material.code, "RMID-PAN-002-PANTONE-286 C")

    def test_create_raw_material_requires_vendor_colour_code_or_pantone(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("inventory:list"),
            {
                "name": "Invalid Strap",
                "rm_id": "RMID-INVALID-001",
                "code": "",
                "material_type": RawMaterial.MaterialType.ACCESSORY,
                "colour": "Black",
                "colour_code": "",
                "pantone_number": "",
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "10.000",
                "vendor": str(self.vendor.id),
                "opening_stock": "10.000",
                "reorder_level": "1.000",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter Vendor Colour Code or Pantone Number.")
        self.assertFalse(RawMaterial.objects.filter(rm_id="RMID-INVALID-001").exists())

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

    def test_create_material_same_code_and_colour_code_uses_weighted_average_cost(self):
        self.client.force_login(self.user)
        material = RawMaterial.objects.create(
            name="Existing Canvas",
            rm_id="RMID-CANVAS-002",
            code="RMID-CANVAS-002-BLU",
            material_type=RawMaterial.MaterialType.FABRIC,
            colour="Blue",
            colour_code="BLU",
            unit=RawMaterial.Unit.METER,
            cost_per_unit=Decimal("40.000"),
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
                "variant_pantone_number": [""],
                "variant_code": ["RMID-CANVAS-002-BLU"],
                "variant_opening_stock": ["5.000"],
            },
        )

        self.assertRedirects(response, reverse("inventory:list"))
        material.refresh_from_db()
        self.assertEqual(RawMaterial.objects.filter(rm_id="RMID-CANVAS-002", colour_code="BLU").count(), 1)
        self.assertEqual(material.cost_per_unit, Decimal("44.000"))
        self.assertEqual(material.current_stock, Decimal("15.000"))
        self.assertEqual(material.code, "RMID-CANVAS-002-BLU")

    def test_create_material_merge_existing_variant_records_additional_stock_transaction(self):
        self.client.force_login(self.user)
        material = RawMaterial.objects.create(
            name="Existing Canvas",
            rm_id="RMID-CANVAS-003",
            code="RMID-CANVAS-003-BLU",
            material_type=RawMaterial.MaterialType.FABRIC,
            colour="Blue",
            colour_code="BLU",
            unit=RawMaterial.Unit.METER,
            cost_per_unit=Decimal("40.000"),
            current_stock=Decimal("10.000"),
            reorder_level=Decimal("2.000"),
            vendor=self.vendor,
        )

        response = self.client.post(
            reverse("inventory:list"),
            {
                "action": "create_material",
                "name": "Existing Canvas",
                "rm_id": "RMID-CANVAS-003",
                "material_type": RawMaterial.MaterialType.FABRIC,
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "52.000",
                "vendor": str(self.vendor.id),
                "reorder_level": "5.000",
                "variant_colour": ["Blue"],
                "variant_colour_code": ["BLU"],
                "variant_pantone_number": [""],
                "variant_code": ["RMID-CANVAS-003-BLU"],
                "variant_opening_stock": ["5.000"],
            },
        )

        self.assertRedirects(response, reverse("inventory:list"))
        ledger_entry = InventoryLedger.objects.get(material=material, reference_type="stock_addition")
        self.assertEqual(ledger_entry.reason, "Additional stock")

    def test_create_material_merges_duplicate_rm_id_and_colour_code_with_different_code(self):
        self.client.force_login(self.user)
        material = RawMaterial.objects.create(
            name="Existing Canvas",
            rm_id="RMID-CANVAS-004",
            code="RMID-CANVAS-004-BLU",
            material_type=RawMaterial.MaterialType.FABRIC,
            colour="Blue",
            colour_code="BLU",
            unit=RawMaterial.Unit.METER,
            cost_per_unit=Decimal("40.000"),
            current_stock=Decimal("10.000"),
            reorder_level=Decimal("2.000"),
            vendor=self.vendor,
        )

        response = self.client.post(
            reverse("inventory:list"),
            {
                "action": "create_material",
                "name": "New Canvas",
                "rm_id": "RMID-CANVAS-004",
                "material_type": RawMaterial.MaterialType.FABRIC,
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "52.000",
                "vendor": str(self.vendor.id),
                "reorder_level": "5.000",
                "variant_colour": ["Blue"],
                "variant_colour_code": ["BLU"],
                "variant_pantone_number": [""],
                "variant_code": ["DIFF-CODE-001"],
                "variant_opening_stock": ["5.000"],
            },
        )

        self.assertRedirects(response, reverse("inventory:list"))
        material.refresh_from_db()
        self.assertEqual(RawMaterial.objects.filter(rm_id="RMID-CANVAS-004", colour_code="BLU").count(), 1)
        self.assertEqual(material.current_stock, Decimal("15.000"))
        self.assertEqual(material.cost_per_unit, Decimal("44.000"))
        self.assertEqual(material.code, "RMID-CANVAS-004-BLU")

    def test_create_material_merges_existing_case_insensitive_code_and_colour_code(self):
        self.client.force_login(self.user)
        material = RawMaterial.objects.create(
            name="Legacy Case Canvas",
            rm_id="rmid-case-001",
            code="rmid-case-001-blu",
            material_type=RawMaterial.MaterialType.FABRIC,
            colour="Blue",
            colour_code="blu",
            unit=RawMaterial.Unit.METER,
            cost_per_unit=Decimal("40.000"),
            current_stock=Decimal("10.000"),
            reorder_level=Decimal("2.000"),
            vendor=self.vendor,
        )

        response = self.client.post(
            reverse("inventory:list"),
            {
                "action": "create_material",
                "name": "Legacy Case Canvas",
                "rm_id": "RMID-CASE-001",
                "material_type": RawMaterial.MaterialType.FABRIC,
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "50.000",
                "vendor": str(self.vendor.id),
                "reorder_level": "5.000",
                "variant_colour": ["Blue"],
                "variant_colour_code": ["BLU"],
                "variant_pantone_number": [""],
                "variant_code": ["RMID-CASE-001-BLU"],
                "variant_opening_stock": ["5.000"],
            },
        )

        self.assertRedirects(response, reverse("inventory:list"))
        material.refresh_from_db()
        self.assertEqual(RawMaterial.objects.filter(pk=material.id).count(), 1)
        self.assertEqual(material.current_stock, Decimal("15.000"))
        self.assertEqual(material.cost_per_unit, Decimal("43.333"))

    def test_create_material_merges_when_selected_supplier_is_existing_additional_supplier(self):
        self.client.force_login(self.user)
        additional_supplier = Partner.objects.create(
            name="Additional Supplier",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="29ABCDE5678F2Z5",
            address_line1="Supplier Lane",
            city="Bengaluru",
            state="Karnataka",
            pincode="560012",
        )
        material = RawMaterial.objects.create(
            name="Supplier Merge Canvas",
            rm_id="RMID-SUP-MERGE-001",
            code="RMID-SUP-MERGE-001-BLK",
            material_type=RawMaterial.MaterialType.FABRIC,
            colour="Black",
            colour_code="BLK",
            unit=RawMaterial.Unit.METER,
            cost_per_unit=Decimal("20.000"),
            current_stock=Decimal("10.000"),
            reorder_level=Decimal("2.000"),
            vendor=self.vendor,
        )
        RawMaterialVendor.objects.create(material=material, vendor=self.vendor)
        RawMaterialVendor.objects.create(material=material, vendor=additional_supplier)

        response = self.client.post(
            reverse("inventory:list"),
            {
                "action": "create_material",
                "name": "Supplier Merge Canvas",
                "rm_id": "RMID-SUP-MERGE-001",
                "material_type": RawMaterial.MaterialType.FABRIC,
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "30.000",
                "vendor": str(additional_supplier.id),
                "reorder_level": "5.000",
                "variant_colour": ["Black"],
                "variant_colour_code": ["BLK"],
                "variant_pantone_number": [""],
                "variant_code": ["RMID-SUP-MERGE-001-BLK"],
                "variant_opening_stock": ["5.000"],
            },
        )

        self.assertRedirects(response, reverse("inventory:list"))
        material.refresh_from_db()
        self.assertEqual(RawMaterial.objects.filter(rm_id="RMID-SUP-MERGE-001", colour_code="BLK").count(), 1)
        self.assertEqual(material.current_stock, Decimal("15.000"))

    def test_create_material_merges_and_links_new_supplier(self):
        self.client.force_login(self.user)
        new_supplier = Partner.objects.create(
            name="New Supplier",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="29ABCDE5678F3Z5",
            address_line1="Supplier Street",
            city="Bengaluru",
            state="Karnataka",
            pincode="560013",
        )
        material = RawMaterial.objects.create(
            name="Supplier Link Canvas",
            rm_id="RMID-SUP-LINK-001",
            code="RMID-SUP-LINK-001-BLU",
            material_type=RawMaterial.MaterialType.FABRIC,
            colour="Blue",
            colour_code="BLU",
            unit=RawMaterial.Unit.METER,
            cost_per_unit=Decimal("25.000"),
            current_stock=Decimal("8.000"),
            reorder_level=Decimal("2.000"),
            vendor=self.vendor,
        )
        RawMaterialVendor.objects.create(material=material, vendor=self.vendor)

        response = self.client.post(
            reverse("inventory:list"),
            {
                "action": "create_material",
                "name": "Supplier Link Canvas",
                "rm_id": "RMID-SUP-LINK-001",
                "material_type": RawMaterial.MaterialType.FABRIC,
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "35.000",
                "vendor": str(new_supplier.id),
                "reorder_level": "5.000",
                "variant_colour": ["Blue"],
                "variant_colour_code": ["BLU"],
                "variant_pantone_number": [""],
                "variant_code": ["RMID-SUP-LINK-001-BLU"],
                "variant_opening_stock": ["4.000"],
            },
        )

        self.assertRedirects(response, reverse("inventory:list"))
        material.refresh_from_db()
        self.assertEqual(RawMaterial.objects.filter(rm_id="RMID-SUP-LINK-001", colour_code="BLU").count(), 1)
        self.assertTrue(
            RawMaterialVendor.objects.filter(material=material, vendor=new_supplier).exists(),
            "New supplier should be linked automatically to the existing material.",
        )

    def test_create_material_same_code_and_colour_code_but_different_rm_id_creates_new_row(self):
        self.client.force_login(self.user)
        RawMaterial.objects.create(
            name="Silicone Label - Air Mesh",
            rm_id="LABEL-AM",
            code="6406",
            material_type=RawMaterial.MaterialType.ACCESSORY,
            colour="Vallarta + Lemon",
            colour_code="VAL+LEM",
            unit=RawMaterial.Unit.PIECES,
            cost_per_unit=Decimal("5.000"),
            current_stock=Decimal("100.000"),
            reorder_level=Decimal("10.000"),
            vendor=self.vendor,
        )

        response = self.client.post(
            reverse("inventory:list"),
            {
                "action": "create_material",
                "name": "Silicone Label - CARRIMATE",
                "rm_id": "LABEL-CARRIMATE",
                "material_type": RawMaterial.MaterialType.ACCESSORY,
                "unit": RawMaterial.Unit.PIECES,
                "cost_per_unit": "6.000",
                "vendor": str(self.vendor.id),
                "reorder_level": "10.000",
                "variant_colour": ["Vallarta + Lemon"],
                "variant_colour_code": ["VAL+LEM"],
                "variant_pantone_number": [""],
                "variant_code": ["6406"],
                "variant_opening_stock": ["25.000"],
            },
        )

        self.assertRedirects(response, reverse("inventory:list"))
        self.assertEqual(RawMaterial.objects.filter(code="6406", colour_code="VAL+LEM").count(), 2)
        first = RawMaterial.objects.get(rm_id="LABEL-AM", colour_code="VAL+LEM")
        second = RawMaterial.objects.get(rm_id="LABEL-CARRIMATE", colour_code="VAL+LEM")
        self.assertEqual(first.current_stock, Decimal("100.000"))
        self.assertEqual(second.current_stock, Decimal("25.000"))

    def test_create_material_merges_duplicate_rm_id_and_colour_code_in_same_submit(self):
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
                "variant_pantone_number": ["", ""],
                "variant_code": ["", ""],
                "variant_opening_stock": ["4.000", "6.000"],
            },
        )

        self.assertRedirects(response, reverse("inventory:list"))
        material = RawMaterial.objects.get(rm_id="RMID-CANVAS-003", colour_code="BLU")
        self.assertEqual(RawMaterial.objects.filter(rm_id="RMID-CANVAS-003", colour_code="BLU").count(), 1)
        self.assertEqual(material.current_stock, Decimal("10.000"))
        self.assertEqual(material.cost_per_unit, Decimal("40.000"))

    def test_raw_material_csv_template_download(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("inventory:csv_template"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("raw_material_upload_template.csv", response["Content-Disposition"])
        self.assertIn(
            "name,rm_id,code,material_type,colour,colour_code,pantone_number,unit,cost_per_unit",
            response.content.decode("utf-8"),
        )

    def test_raw_material_csv_upload_creates_material(self):
        self.client.force_login(self.user)
        csv_content = (
            "name,rm_id,code,material_type,colour,colour_code,pantone_number,unit,cost_per_unit,vendor_gst_number,additional_vendor_gst_numbers,opening_stock,reorder_level\n"
            "CSV Canvas,RMID-CSV-001,RM-CSV,fabric,Blue,BLU,,m,44.500,29ABCDE5678F1Z5,,120.000,25.000\n"
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

    def test_raw_material_csv_upload_merges_duplicate_variant_rows_in_same_file(self):
        self.client.force_login(self.user)
        csv_content = (
            "name,rm_id,code,material_type,colour,colour_code,pantone_number,unit,cost_per_unit,vendor_gst_number,additional_vendor_gst_numbers,opening_stock,reorder_level\n"
            "CSV Canvas Blue,RMID-CSV-002,RM-CSV-BLU,fabric,Blue,BLU,,m,44.500,29ABCDE5678F1Z5,,120.000,25.000\n"
            "CSV Canvas Blue Duplicate,RMID-CSV-002,RM-CSV-BLU-2,fabric,Blue,BLU,,m,55.000,29ABCDE5678F1Z5,,20.000,5.000\n"
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
        material = RawMaterial.objects.get(rm_id="RMID-CSV-002", colour_code="BLU")
        self.assertEqual(RawMaterial.objects.filter(rm_id="RMID-CSV-002", colour_code="BLU").count(), 1)
        self.assertEqual(material.current_stock, Decimal("140.000"))
        self.assertEqual(material.cost_per_unit, Decimal("46.000"))

    def test_raw_material_csv_upload_shows_merge_conflict_as_row_error(self):
        self.client.force_login(self.user)
        RawMaterial.objects.create(
            name="Existing Blue Canvas",
            rm_id="RMID-CSV-CONFLICT-001",
            code="RMID-CSV-CONFLICT-001-BLU",
            material_type=RawMaterial.MaterialType.FABRIC,
            colour="Blue",
            colour_code="BLU",
            unit=RawMaterial.Unit.METER,
            cost_per_unit=Decimal("44.500"),
            current_stock=Decimal("10.000"),
            reorder_level=Decimal("2.000"),
            vendor=self.vendor,
        )
        RawMaterial.objects.create(
            name="Existing Pantone Canvas",
            rm_id="RMID-CSV-CONFLICT-001",
            code="RMID-CSV-CONFLICT-001-PANTONE-286 C",
            material_type=RawMaterial.MaterialType.FABRIC,
            colour="Blue",
            colour_code="NAVY",
            pantone_number="PANTONE-286 C",
            unit=RawMaterial.Unit.METER,
            cost_per_unit=Decimal("46.000"),
            current_stock=Decimal("12.000"),
            reorder_level=Decimal("3.000"),
            vendor=self.vendor,
        )
        csv_content = (
            "name,rm_id,code,material_type,colour,colour_code,pantone_number,unit,cost_per_unit,vendor_gst_number,additional_vendor_gst_numbers,opening_stock,reorder_level\n"
            "CSV Canvas Conflict,RMID-CSV-CONFLICT-001,RM-CSV-CONFLICT,fabric,Blue,BLU,PANTONE-286 C,m,50.000,29ABCDE5678F1Z5,,20.000,5.000\n"
        )
        upload = SimpleUploadedFile("materials.csv", csv_content.encode("utf-8"), content_type="text/csv")

        response = self.client.post(
            reverse("inventory:list"),
            {
                "action": "upload_csv",
                "csv_file": upload,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "RM ID + Vendor Colour Code and RM ID + Pantone Number combination matches different materials",
        )
        self.assertEqual(RawMaterial.objects.filter(rm_id="RMID-CSV-CONFLICT-001").count(), 2)

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

    def test_create_mro_item_without_cost_per_unit_defaults_to_zero(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("inventory:mro_list"),
            {
                "action": "create_mro_item",
                "name": "Hex Key Set",
                "mro_id": "MRO-TOOL-002",
                "code": "",
                "item_type": MROItem.ItemType.TOOL,
                "unit": MROItem.Unit.SET,
                "cost_per_unit": "",
                "vendor": str(self.vendor.id),
                "location": "Tool Cage A2",
                "opening_stock": "4.000",
                "reorder_level": "1.000",
            },
        )

        self.assertRedirects(response, reverse("inventory:mro_list"))
        item = MROItem.objects.get(mro_id="MRO-TOOL-002")
        self.assertEqual(item.cost_per_unit, Decimal("0.000"))
        self.assertEqual(item.current_stock, Decimal("4.000"))

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

    def test_adjust_mro_stock_preserves_active_filters(self):
        item = MROItem.objects.create(
            name="Filter Retain Gloves",
            mro_id="MRO-FILTER-001",
            code="GLOVES-F",
            item_type=MROItem.ItemType.OTHER,
            unit=MROItem.Unit.PIECES,
            cost_per_unit=Decimal("55.000"),
            current_stock=Decimal("10.000"),
            reorder_level=Decimal("3.000"),
            location="Consumables Bay",
            vendor=self.vendor,
        )
        self.client.force_login(self.user)
        next_url = f"{reverse('inventory:mro_list')}?q=Filter"

        response = self.client.post(
            reverse("inventory:mro_adjust"),
            {
                "next": next_url,
                "item_id": item.id,
                "delta": "-1.000",
                "reason": "Issued to maintenance",
            },
        )

        self.assertRedirects(response, next_url)


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
        response = self.client.post(
            reverse("inventory:release_production_request", args=[order.id]),
            {"action_password": "test12345"},
        )

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
        response = self.client.post(
            reverse("inventory:reject_production_request", args=[order.id]),
            {"action_password": "test12345"},
        )

        self.assertRedirects(response, reverse("inventory:list"))
        order.refresh_from_db()
        self.material.refresh_from_db()
        self.assertEqual(order.status, ProductionOrder.Status.CANCELLED)
        self.assertFalse(order.raw_material_released)
        self.assertEqual(self.material.current_stock, Decimal("50.000"))

    def test_inventory_manager_cannot_release_rm_request_with_wrong_password(self):
        order = create_production_order_with_rm_request(
            product=self.product,
            quantity=5,
            notes="Approve",
            created_by=self.production_manager,
        )
        self.client.force_login(self.inventory_manager)
        response = self.client.post(
            reverse("inventory:release_production_request", args=[order.id]),
            {"action_password": "wrong-pass"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Incorrect password. Action not completed.")
        order.refresh_from_db()
        self.material.refresh_from_db()
        self.assertEqual(order.status, ProductionOrder.Status.AWAITING_RM_RELEASE)
        self.assertFalse(order.raw_material_released)
        self.assertEqual(self.material.current_stock, Decimal("50.000"))


class FinishedProductsInventoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="finished_admin",
            password="test12345",
            role=User.Role.ADMIN,
        )

    def test_finished_products_list_shows_image_thumbnail_column(self):
        self.client.force_login(self.user)

        with tempfile.TemporaryDirectory() as media_root:
            with self.settings(MEDIA_ROOT=media_root):
                product = FinishedProduct.objects.create(
                    name="Showcase Tote",
                    sku="FP-SHOWCASE",
                    product_image=_make_test_image_file(),
                )

                response = self.client.get(reverse("inventory:finished_products_list"))

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "<th>Image</th>", html=True)
                self.assertContains(response, product.product_image.url)
                self.assertContains(response, "product-thumb")
                content = response.content.decode("utf-8")
                self.assertLess(content.index("<th>ID</th>"), content.index("<th>Image</th>"))
