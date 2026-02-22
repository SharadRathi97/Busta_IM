from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from security import hash_password, utcnow_iso

ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = ROOT / "data" / "app.db"


def db_path() -> str:
    configured = os.environ.get("BUSTA_DB_PATH")
    if configured:
        return configured
    return str(DEFAULT_DB_PATH)


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(db_path())
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    Path(db_path()).parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'inventory_manager', 'production_manager', 'viewer')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS vendors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                vendor_type TEXT NOT NULL CHECK(vendor_type IN ('supplier', 'buyer', 'both')),
                gst_number TEXT NOT NULL,
                address_line1 TEXT NOT NULL,
                address_line2 TEXT,
                city TEXT NOT NULL,
                state TEXT NOT NULL,
                pincode TEXT NOT NULL,
                contact_person TEXT,
                phone TEXT,
                email TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS raw_materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                code TEXT NOT NULL UNIQUE,
                unit TEXT NOT NULL CHECK(unit IN ('kg', 'm', 'pieces', 'litre')),
                current_stock REAL NOT NULL DEFAULT 0,
                reorder_level REAL NOT NULL DEFAULT 0,
                vendor_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(vendor_id) REFERENCES vendors(id)
            );

            CREATE TABLE IF NOT EXISTS finished_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                sku TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bom_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                material_id INTEGER NOT NULL,
                qty_per_unit REAL NOT NULL CHECK(qty_per_unit > 0),
                UNIQUE(product_id, material_id),
                FOREIGN KEY(product_id) REFERENCES finished_products(id) ON DELETE CASCADE,
                FOREIGN KEY(material_id) REFERENCES raw_materials(id)
            );

            CREATE TABLE IF NOT EXISTS inventory_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                material_id INTEGER NOT NULL,
                txn_type TEXT NOT NULL CHECK(txn_type IN ('IN', 'OUT', 'ADJUST')),
                quantity REAL NOT NULL,
                unit TEXT NOT NULL,
                reason TEXT NOT NULL,
                reference_type TEXT,
                reference_id INTEGER,
                created_by INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY(material_id) REFERENCES raw_materials(id),
                FOREIGN KEY(created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS production_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                status TEXT NOT NULL CHECK(status IN ('planned', 'in_progress', 'completed')),
                notes TEXT,
                created_by INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY(product_id) REFERENCES finished_products(id),
                FOREIGN KEY(created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS production_consumption (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                production_order_id INTEGER NOT NULL,
                material_id INTEGER NOT NULL,
                required_qty REAL NOT NULL,
                FOREIGN KEY(production_order_id) REFERENCES production_orders(id) ON DELETE CASCADE,
                FOREIGN KEY(material_id) REFERENCES raw_materials(id)
            );

            CREATE TABLE IF NOT EXISTS purchase_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor_id INTEGER NOT NULL,
                order_date TEXT NOT NULL,
                notes TEXT,
                created_by INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY(vendor_id) REFERENCES vendors(id),
                FOREIGN KEY(created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS purchase_order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                purchase_order_id INTEGER NOT NULL,
                material_id INTEGER NOT NULL,
                quantity REAL NOT NULL CHECK(quantity > 0),
                unit TEXT NOT NULL,
                FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id) ON DELETE CASCADE,
                FOREIGN KEY(material_id) REFERENCES raw_materials(id)
            );

            CREATE INDEX IF NOT EXISTS idx_material_vendor ON raw_materials(vendor_id);
            CREATE INDEX IF NOT EXISTS idx_bom_product ON bom_items(product_id);
            CREATE INDEX IF NOT EXISTS idx_ledger_material ON inventory_ledger(material_id);
            CREATE INDEX IF NOT EXISTS idx_prod_order_product ON production_orders(product_id);
            CREATE INDEX IF NOT EXISTS idx_po_vendor ON purchase_orders(vendor_id);
            """
        )

        has_users = conn.execute("SELECT EXISTS(SELECT 1 FROM users)").fetchone()[0]
        if not has_users:
            conn.execute(
                """
                INSERT INTO users (username, full_name, password_hash, role, is_active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (
                    "admin",
                    "System Admin",
                    hash_password("admin123"),
                    "admin",
                    utcnow_iso(),
                ),
            )
            conn.commit()
