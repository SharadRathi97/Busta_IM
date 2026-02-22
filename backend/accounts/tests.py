from __future__ import annotations

import csv
from decimal import Decimal
from io import StringIO

from django.test import TestCase
from django.urls import reverse

from inventory.models import RawMaterial
from partners.models import Partner
from production.models import BOMItem, FinishedProduct, ProductionOrder, cancel_production_order, create_production_order_and_deduct_stock

from .models import AuditLog, User


class TransactionHistoryExportTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_txn",
            password="test12345",
            role=User.Role.ADMIN,
        )
        self.vendor = Partner.objects.create(
            name="Txn Supplier",
            partner_type=Partner.PartnerType.SUPPLIER,
            gst_number="29ABCDE4321F1Z5",
            address_line1="Txn Road",
            city="Bengaluru",
            state="Karnataka",
            pincode="560001",
        )
        self.material = RawMaterial.objects.create(
            name="Txn Fabric",
            rm_id="RMID-TXN-BASE",
            code="RM-TXN",
            material_type=RawMaterial.MaterialType.FABRIC,
            colour="Blue",
            colour_code="BLU",
            unit=RawMaterial.Unit.METER,
            current_stock=Decimal("200.000"),
            reorder_level=Decimal("20.000"),
            vendor=self.vendor,
        )
        self.product = FinishedProduct.objects.create(name="Txn Tote", sku="FP-TXN")
        BOMItem.objects.create(product=self.product, material=self.material, qty_per_unit=Decimal("2.000"))

    def _csv_rows(self, response):
        body = response.content.decode("utf-8")
        reader = csv.DictReader(StringIO(body))
        return list(reader)

    def test_download_scoped_transaction_history_filters_by_module(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("partners:list"),
            {
                "action": "create_partner",
                "vendor_id": "VEND-TXN-001",
                "name": "Txn Buyer",
                "partner_type": Partner.PartnerType.BUYER,
                "gst_number": "27ABCDE4321F1Z5",
                "address_line1": "Buyer Street",
                "address_line2": "",
                "city": "Pune",
                "state": "Maharashtra",
                "pincode": "411001",
                "contact_person": "",
                "phone": "",
                "email": "",
            },
        )
        self.client.post(
            reverse("inventory:list"),
            {
                "action": "create_material",
                "name": "Txn Strap",
                "rm_id": "RMID-TXN-001",
                "code": "RM-TXN-2",
                "material_type": RawMaterial.MaterialType.ACCESSORY,
                "colour": "Black",
                "colour_code": "BLK",
                "unit": RawMaterial.Unit.METER,
                "cost_per_unit": "10.000",
                "vendor": str(self.vendor.id),
                "opening_stock": "10.000",
                "reorder_level": "2.000",
            },
        )

        raw_response = self.client.get(reverse("accounts:download_transactions", args=["raw_materials"]))
        all_response = self.client.get(reverse("accounts:download_transactions", args=["all"]))
        self.assertEqual(raw_response.status_code, 200)
        self.assertEqual(all_response.status_code, 200)

        raw_rows = self._csv_rows(raw_response)
        all_rows = self._csv_rows(all_response)
        self.assertTrue(any(row["app"] == "inventory" for row in raw_rows))
        self.assertFalse(any(row["app"] == "partners" for row in raw_rows))
        self.assertTrue(any(row["app"] == "partners" for row in all_rows))

    def test_finished_product_delete_logs_cascaded_production_order_deletes(self):
        cancelled_order = create_production_order_and_deduct_stock(
            product=self.product,
            quantity=2,
            notes="To cancel",
            created_by=self.admin,
        )
        cancel_production_order(production_order=cancelled_order, cancelled_by=self.admin)
        completed_order = create_production_order_and_deduct_stock(
            product=self.product,
            quantity=1,
            notes="To complete",
            created_by=self.admin,
        )
        completed_order.status = ProductionOrder.Status.COMPLETED
        completed_order.save(update_fields=["status"])

        self.client.force_login(self.admin)
        response = self.client.post(reverse("production:delete_product", args=[self.product.id]))
        self.assertEqual(response.status_code, 302)

        order_delete_logs = AuditLog.objects.filter(
            app_label="production",
            model_name="productionorder",
            action=AuditLog.Action.DELETE,
            actor_username=self.admin.username,
        )
        product_delete_logs = AuditLog.objects.filter(
            app_label="production",
            model_name="finishedproduct",
            action=AuditLog.Action.DELETE,
            actor_username=self.admin.username,
        )
        self.assertGreaterEqual(order_delete_logs.count(), 2)
        self.assertEqual(product_delete_logs.count(), 1)

        all_response = self.client.get(reverse("accounts:download_transactions", args=["all"]))
        rows = self._csv_rows(all_response)
        self.assertTrue(
            any(
                row["app"] == "production"
                and row["model"] == "productionorder"
                and row["action"] == "delete"
                and row["username"] == self.admin.username
                for row in rows
            )
        )
        self.assertTrue(
            any(
                row["app"] == "production"
                and row["model"] == "finishedproduct"
                and row["action"] == "delete"
                and row["username"] == self.admin.username
                for row in rows
            )
        )


class RoleAccessConsistencyTests(TestCase):
    def setUp(self):
        self.inventory_manager = User.objects.create_user(
            username="inventory_role_user",
            password="test12345",
            role=User.Role.INVENTORY_MANAGER,
        )
        self.production_manager = User.objects.create_user(
            username="production_role_user",
            password="test12345",
            role=User.Role.PRODUCTION_MANAGER,
        )
        self.viewer = User.objects.create_user(
            username="viewer_role_user",
            password="test12345",
            role=User.Role.VIEWER,
        )

    def test_inventory_manager_navigation_shows_only_inventory_area(self):
        self.client.force_login(self.inventory_manager)
        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("partners:list"))
        self.assertContains(response, reverse("inventory:list"))
        self.assertContains(response, reverse("inventory:mro_list"))
        self.assertContains(response, reverse("purchasing:list"))
        self.assertNotContains(response, reverse("production:products"))
        self.assertNotContains(response, reverse("production:orders"))
        self.assertNotContains(response, reverse("accounts:user_list"))

    def test_production_manager_navigation_shows_only_production_area(self):
        self.client.force_login(self.production_manager)
        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("production:products"))
        self.assertContains(response, reverse("production:orders"))
        self.assertNotContains(response, reverse("partners:list"))
        self.assertNotContains(response, reverse("inventory:list"))
        self.assertNotContains(response, reverse("inventory:mro_list"))
        self.assertNotContains(response, reverse("purchasing:list"))
        self.assertNotContains(response, reverse("accounts:user_list"))

    def test_viewer_navigation_shows_both_operational_areas(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("partners:list"))
        self.assertContains(response, reverse("inventory:list"))
        self.assertContains(response, reverse("inventory:mro_list"))
        self.assertContains(response, reverse("purchasing:list"))
        self.assertContains(response, reverse("production:products"))
        self.assertContains(response, reverse("production:orders"))
        self.assertNotContains(response, reverse("accounts:user_list"))

    def test_production_manager_is_denied_inventory_pages(self):
        self.client.force_login(self.production_manager)
        response = self.client.get(reverse("inventory:list"), follow=True)

        self.assertRedirects(response, reverse("dashboard:home"))
        self.assertContains(response, "You do not have permission to access raw materials.")

    def test_inventory_manager_is_denied_production_pages(self):
        self.client.force_login(self.inventory_manager)
        response = self.client.get(reverse("production:products"), follow=True)

        self.assertRedirects(response, reverse("dashboard:home"))
        self.assertContains(response, "You do not have permission to access products and BOM.")

    def test_production_manager_is_denied_purchase_order_pages(self):
        self.client.force_login(self.production_manager)
        response = self.client.get(reverse("purchasing:list"), follow=True)

        self.assertRedirects(response, reverse("dashboard:home"))
        self.assertContains(response, "You do not have permission to access purchase orders.")

    def test_production_manager_is_denied_inventory_csv_template(self):
        self.client.force_login(self.production_manager)
        response = self.client.get(reverse("inventory:csv_template"), follow=True)

        self.assertRedirects(response, reverse("dashboard:home"))
        self.assertContains(response, "You do not have permission to access raw materials.")

    def test_production_manager_is_denied_mro_pages(self):
        self.client.force_login(self.production_manager)
        response = self.client.get(reverse("inventory:mro_list"), follow=True)

        self.assertRedirects(response, reverse("dashboard:home"))
        self.assertContains(response, "You do not have permission to access MRO inventory.")

    def test_inventory_manager_is_denied_production_csv_template(self):
        self.client.force_login(self.inventory_manager)
        response = self.client.get(reverse("production:bom_csv_template"), follow=True)

        self.assertRedirects(response, reverse("dashboard:home"))
        self.assertContains(response, "You do not have permission to access products and BOM.")


class UserDeletionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_user_delete",
            password="test12345",
            role=User.Role.ADMIN,
        )
        self.target = User.objects.create_user(
            username="target_user_delete",
            password="test12345",
            role=User.Role.VIEWER,
        )
        self.viewer = User.objects.create_user(
            username="viewer_user_delete",
            password="test12345",
            role=User.Role.VIEWER,
        )

    def test_admin_can_delete_another_user(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("accounts:delete_user", args=[self.target.id]), follow=True)

        self.assertRedirects(response, reverse("accounts:user_list"))
        self.assertFalse(User.objects.filter(pk=self.target.id).exists())
        self.assertContains(response, "User target_user_delete deleted.")

    def test_admin_cannot_delete_own_account(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("accounts:delete_user", args=[self.admin.id]), follow=True)

        self.assertRedirects(response, reverse("accounts:user_list"))
        self.assertTrue(User.objects.filter(pk=self.admin.id).exists())
        self.assertContains(response, "You cannot delete your own account.")

    def test_non_admin_cannot_delete_user(self):
        self.client.force_login(self.viewer)
        response = self.client.post(reverse("accounts:delete_user", args=[self.target.id]), follow=True)

        self.assertRedirects(response, reverse("dashboard:home"))
        self.assertTrue(User.objects.filter(pk=self.target.id).exists())
        self.assertContains(response, "You do not have permission to access this area.")
