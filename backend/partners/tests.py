from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import User

from .models import Partner


def _make_partner(**overrides) -> Partner:
    defaults = {
        "vendor_id": "VEND-TEST-001",
        "name": "Test Supplier",
        "partner_type": Partner.PartnerType.SUPPLIER,
        "gst_number": "29ABCDE1234F1Z5",
        "address_line1": "123 Test St",
        "city": "Bengaluru",
        "state": "Karnataka",
        "pincode": "560001",
    }
    defaults.update(overrides)
    return Partner.objects.create(**defaults)


class PartnerModelTests(TestCase):
    def test_str_with_vendor_id(self):
        partner = _make_partner()
        self.assertEqual(str(partner), "VEND-TEST-001 - Test Supplier")

    def test_str_without_vendor_id(self):
        partner = _make_partner(vendor_id="", name="No ID Partner")
        self.assertEqual(str(partner), "No ID Partner")

    def test_full_address(self):
        partner = _make_partner(address_line2="Unit 5")
        self.assertIn("Unit 5", partner.full_address)
        self.assertIn("Bengaluru", partner.full_address)

    def test_vendor_id_unique(self):
        _make_partner(vendor_id="VEND-UNIQ")
        with self.assertRaises(Exception):
            _make_partner(vendor_id="VEND-UNIQ", name="Duplicate Vendor")

    def test_name_unique(self):
        _make_partner(name="Unique Name")
        with self.assertRaises(Exception):
            _make_partner(vendor_id="VEND-002", name="Unique Name")


class PartnerCSVImportTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="partner_admin",
            password="test12345",
            role=User.Role.ADMIN,
        )

    def test_partner_csv_template_download(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("partners:csv_template"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("vendor_upload_template.csv", response["Content-Disposition"])
        self.assertIn("vendor_id,name,partner_type,gst_number", response.content.decode("utf-8"))

    def test_partner_csv_upload_creates_partner(self):
        self.client.force_login(self.admin)
        csv_content = (
            "vendor_id,name,partner_type,gst_number,address_line1,address_line2,city,state,pincode,contact_person,phone,email\n"
            "VEND-CSV-001,CSV Supplier,supplier,29ABCDE1234F1Z5,Area 1,,Bengaluru,Karnataka,560001,Ravi,9999999999,csv@example.com\n"
        )
        upload = SimpleUploadedFile("vendors.csv", csv_content.encode("utf-8"), content_type="text/csv")
        response = self.client.post(
            reverse("partners:list"),
            {"action": "upload_csv", "csv_file": upload},
        )
        self.assertRedirects(response, reverse("partners:list"))
        self.assertTrue(Partner.objects.filter(name="CSV Supplier").exists())

    def test_partner_csv_upload_missing_column_shows_error(self):
        self.client.force_login(self.admin)
        csv_content = "vendor_id,name\nVEND-BAD,Bad Row\n"
        upload = SimpleUploadedFile("bad.csv", csv_content.encode("utf-8"), content_type="text/csv")
        response = self.client.post(
            reverse("partners:list"),
            {"action": "upload_csv", "csv_file": upload},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Partner.objects.filter(name="Bad Row").exists())

    def test_csv_upload_non_csv_rejected(self):
        self.client.force_login(self.admin)
        upload = SimpleUploadedFile("data.txt", b"not csv", content_type="text/plain")
        response = self.client.post(
            reverse("partners:list"),
            {"action": "upload_csv", "csv_file": upload},
        )
        self.assertEqual(response.status_code, 200)


class PartnerCRUDTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_user", password="test12345", role=User.Role.ADMIN
        )
        self.viewer = User.objects.create_user(
            username="viewer_user", password="test12345", role=User.Role.VIEWER
        )

    def test_list_page_loads(self):
        self.client.force_login(self.admin)
        _make_partner()
        response = self.client.get(reverse("partners:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Supplier")

    def test_create_partner(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("partners:list"),
            {
                "action": "create_partner",
                "vendor_id": "VEND-NEW-001",
                "name": "New Partner",
                "partner_type": "supplier",
                "gst_number": "29ABCDE1234F1Z5",
                "address_line1": "123 Main St",
                "city": "Mumbai",
                "state": "Maharashtra",
                "pincode": "400001",
            },
        )
        self.assertRedirects(response, reverse("partners:list"))
        self.assertTrue(Partner.objects.filter(name="New Partner").exists())

    def test_create_partner_invalid_gst_shows_error(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("partners:list"),
            {
                "action": "create_partner",
                "vendor_id": "VEND-BAD",
                "name": "Bad GST Partner",
                "partner_type": "supplier",
                "gst_number": "INVALID",
                "address_line1": "123 Main St",
                "city": "Mumbai",
                "state": "Maharashtra",
                "pincode": "400001",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Partner.objects.filter(name="Bad GST Partner").exists())

    def test_edit_partner(self):
        self.client.force_login(self.admin)
        partner = _make_partner()
        response = self.client.post(
            reverse("partners:edit", args=[partner.pk]),
            {
                "vendor_id": partner.vendor_id,
                "name": "Updated Name",
                "partner_type": "supplier",
                "gst_number": partner.gst_number,
                "address_line1": partner.address_line1,
                "city": partner.city,
                "state": partner.state,
                "pincode": partner.pincode,
            },
        )
        self.assertRedirects(response, reverse("partners:list"))
        partner.refresh_from_db()
        self.assertEqual(partner.name, "Updated Name")

    def test_delete_partner(self):
        self.client.force_login(self.admin)
        partner = _make_partner()
        pk = partner.pk
        response = self.client.post(reverse("partners:delete", args=[pk]))
        self.assertRedirects(response, reverse("partners:list"))
        self.assertFalse(Partner.objects.filter(pk=pk).exists())

    def test_delete_nonexistent_partner_returns_404(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("partners:delete", args=[99999]))
        self.assertEqual(response.status_code, 404)


class PartnerAuthorizationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_user", password="test12345", role=User.Role.ADMIN
        )
        self.viewer = User.objects.create_user(
            username="viewer_user", password="test12345", role=User.Role.VIEWER
        )

    def test_viewer_can_view_list(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("partners:list"))
        self.assertEqual(response.status_code, 200)

    def test_viewer_cannot_create_partner(self):
        self.client.force_login(self.viewer)
        response = self.client.post(
            reverse("partners:list"),
            {
                "action": "create_partner",
                "vendor_id": "VEND-BLOCKED",
                "name": "Blocked Partner",
                "partner_type": "supplier",
                "gst_number": "29ABCDE1234F1Z5",
                "address_line1": "Test",
                "city": "Test",
                "state": "Test",
                "pincode": "560001",
            },
        )
        self.assertNotEqual(response.status_code, 200)
        self.assertFalse(Partner.objects.filter(name="Blocked Partner").exists())

    def test_viewer_cannot_delete_partner(self):
        self.client.force_login(self.admin)
        partner = _make_partner()
        self.client.logout()
        self.client.force_login(self.viewer)
        response = self.client.post(reverse("partners:delete", args=[partner.pk]))
        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(Partner.objects.filter(pk=partner.pk).exists())

    def test_viewer_cannot_edit_partner(self):
        self.client.force_login(self.admin)
        partner = _make_partner()
        self.client.logout()
        self.client.force_login(self.viewer)
        response = self.client.post(
            reverse("partners:edit", args=[partner.pk]),
            {
                "vendor_id": partner.vendor_id,
                "name": "Hacked Name",
                "partner_type": "supplier",
                "gst_number": partner.gst_number,
                "address_line1": partner.address_line1,
                "city": partner.city,
                "state": partner.state,
                "pincode": partner.pincode,
            },
        )
        self.assertNotEqual(response.status_code, 200)
        partner.refresh_from_db()
        self.assertNotEqual(partner.name, "Hacked Name")

    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get(reverse("partners:list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)


class PartnerFilterTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_user", password="test12345", role=User.Role.ADMIN
        )
        _make_partner(vendor_id="VEND-S1", name="Supplier Alpha", partner_type=Partner.PartnerType.SUPPLIER)
        _make_partner(vendor_id="VEND-B1", name="Buyer Beta", partner_type=Partner.PartnerType.BUYER)
        _make_partner(vendor_id="VEND-SB1", name="Both Gamma", partner_type=Partner.PartnerType.BOTH)

    def test_filter_by_type_supplier(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("partners:list"), {"partner_type": "supplier"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Supplier Alpha")
        self.assertNotContains(response, "Buyer Beta")

    def test_filter_by_type_buyer(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("partners:list"), {"partner_type": "buyer"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Buyer Beta")
        self.assertNotContains(response, "Supplier Alpha")

    def test_search_by_name(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("partners:list"), {"q": "Alpha"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Supplier Alpha")
        self.assertNotContains(response, "Both Gamma")

    def test_search_by_vendor_id(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("partners:list"), {"q": "VEND-B1"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Buyer Beta")

    def test_sort_by_name_desc(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("partners:list"), {"sort": "name", "direction": "desc"})
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        alpha_pos = content.find("Supplier Alpha")
        gamma_pos = content.find("Both Gamma")
        # Desc: Supplier Alpha before Both Gamma
        self.assertLess(alpha_pos, gamma_pos)


class PartnerCascadeProtectionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_user", password="test12345", role=User.Role.ADMIN
        )

    def test_delete_partner_with_materials_fails_gracefully(self):
        from inventory.models import RawMaterial

        partner = _make_partner()
        RawMaterial.objects.create(
            name="Test Material",
            rm_id="RM-001",
            code="RM-001-CC",
            unit="kg",
            colour_code="RED",
            vendor=partner,
        )
        self.client.force_login(self.admin)
        response = self.client.post(reverse("partners:delete", args=[partner.pk]), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Partner.objects.filter(pk=partner.pk).exists())
