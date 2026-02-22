from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from inventory.models import InventoryLedger, RawMaterial
from partners.models import Partner


class DashboardRecentTransactionsTests(TestCase):
    def setUp(self):
        self.viewer = User.objects.create_user(
            username="dashboard_viewer",
            password="test12345",
            role=User.Role.VIEWER,
        )
        self.actor = User.objects.create_user(
            username="inventory_actor",
            password="test12345",
            first_name="Inventory",
            last_name="Actor",
            role=User.Role.ADMIN,
        )
        self.vendor = Partner.objects.create(
            name="Dashboard Supplier",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="29ABCDE1234F1Z5",
            address_line1="Warehouse Street",
            city="Bengaluru",
            state="Karnataka",
            pincode="560001",
        )
        self.material = RawMaterial.objects.create(
            name="Dashboard Material",
            rm_id="RMID-DASH-001",
            code="RM-DASH",
            material_type=RawMaterial.MaterialType.OTHER,
            colour_code="NA",
            unit=RawMaterial.Unit.KG,
            cost_per_unit=Decimal("10.000"),
            current_stock=Decimal("5.000"),
            reorder_level=Decimal("2.000"),
            vendor=self.vendor,
        )
        InventoryLedger.objects.create(
            material=self.material,
            txn_type=InventoryLedger.TxnType.IN,
            quantity=Decimal("5.000"),
            unit=self.material.unit,
            reason="Opening stock",
            reference_type="opening_stock",
            reference_id=self.material.id,
            created_by=self.actor,
        )

    def test_dashboard_shows_user_in_recent_transactions(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "User")
        self.assertContains(response, "Inventory Actor")
