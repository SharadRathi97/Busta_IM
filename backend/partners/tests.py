from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import User

from .models import Partner


class PartnerCSVImportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="partner_admin",
            password="test12345",
            role=User.Role.ADMIN,
        )

    def test_partner_csv_template_download(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("partners:csv_template"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("vendor_upload_template.csv", response["Content-Disposition"])
        self.assertIn("vendor_id,name,partner_type,gst_number", response.content.decode("utf-8"))

    def test_partner_csv_upload_creates_partner(self):
        self.client.force_login(self.user)
        csv_content = (
            "vendor_id,name,partner_type,gst_number,address_line1,address_line2,city,state,pincode,contact_person,phone,email\n"
            "VEND-CSV-001,CSV Supplier,supplier,29ABCDE1234F1Z5,Area 1,,Bengaluru,Karnataka,560001,Ravi,9999999999,csv@example.com\n"
        )
        upload = SimpleUploadedFile("vendors.csv", csv_content.encode("utf-8"), content_type="text/csv")

        response = self.client.post(
            reverse("partners:list"),
            {
                "action": "upload_csv",
                "csv_file": upload,
            },
        )

        self.assertRedirects(response, reverse("partners:list"))
        self.assertTrue(Partner.objects.filter(name="CSV Supplier").exists())
