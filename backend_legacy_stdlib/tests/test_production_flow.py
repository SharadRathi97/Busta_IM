from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from db import connect, init_db
from services import (
    ValidationError,
    add_bom_item,
    create_finished_product,
    create_purchase_orders_from_items,
    create_production_order,
    create_raw_material,
    create_vendor,
)


class ProductionFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        os.environ["BUSTA_DB_PATH"] = self.db_path
        init_db()

        create_vendor(
            {
                "name": "Test Supplier",
                "vendor_type": "supplier",
                "gst_number": "29ABCDE1234F1Z5",
                "address_line1": "Industrial Area",
                "address_line2": "",
                "city": "Bengaluru",
                "state": "Karnataka",
                "pincode": "560001",
                "contact_person": "Rahul",
                "phone": "9999999999",
                "email": "supplier@example.com",
            }
        )

        create_raw_material(
            {
                "name": "Canvas Cloth",
                "code": "RM-CANVAS",
                "unit": "m",
                "vendor_id": "1",
                "opening_stock": "100",
                "reorder_level": "20",
            },
            user_id=1,
        )

        create_finished_product("Eco Tote", "FP-TOTE")
        add_bom_item(product_id=1, material_id=1, qty_per_unit=2.0)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        os.environ.pop("BUSTA_DB_PATH", None)

    def test_production_order_deducts_stock(self) -> None:
        order_id = create_production_order(product_id=1, quantity=10, notes="Run A", user_id=1)
        self.assertGreater(order_id, 0)

        with connect() as conn:
            material = conn.execute("SELECT current_stock FROM raw_materials WHERE id = 1").fetchone()
            self.assertIsNotNone(material)
            self.assertAlmostEqual(material["current_stock"], 80.0)

            ledger = conn.execute(
                "SELECT quantity, txn_type FROM inventory_ledger WHERE reference_type = 'production_order' AND reference_id = ?",
                (order_id,),
            ).fetchall()
            self.assertEqual(len(ledger), 1)
            self.assertEqual(ledger[0]["txn_type"], "OUT")
            self.assertAlmostEqual(ledger[0]["quantity"], 20.0)

    def test_production_order_fails_when_stock_insufficient(self) -> None:
        with self.assertRaises(ValidationError):
            create_production_order(product_id=1, quantity=60, notes="Too big", user_id=1)

    def test_purchase_orders_are_grouped_by_vendor(self) -> None:
        create_vendor(
            {
                "name": "Second Supplier",
                "vendor_type": "supplier",
                "gst_number": "27ABCDE1234F1Z1",
                "address_line1": "Logistics Park",
                "address_line2": "",
                "city": "Mumbai",
                "state": "Maharashtra",
                "pincode": "400001",
                "contact_person": "Neha",
                "phone": "8888888888",
                "email": "second@example.com",
            }
        )
        create_raw_material(
            {
                "name": "Zip Chain",
                "code": "RM-ZIP",
                "unit": "m",
                "vendor_id": "2",
                "opening_stock": "40",
                "reorder_level": "5",
            },
            user_id=1,
        )

        po_ids = create_purchase_orders_from_items(
            order_date="2026-02-20",
            notes="Restock",
            user_id=1,
            material_ids=[1, 2],
            quantities=[25.0, 10.0],
        )

        self.assertEqual(len(po_ids), 2)

        with connect() as conn:
            po_count = conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0]
            item_count = conn.execute("SELECT COUNT(*) FROM purchase_order_items").fetchone()[0]
        self.assertEqual(po_count, 2)
        self.assertEqual(item_count, 2)


if __name__ == "__main__":
    unittest.main()
