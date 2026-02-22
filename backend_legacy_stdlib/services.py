from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from db import connect
from security import (
    generate_session_token,
    parse_iso,
    session_expiry_iso,
    utcnow_iso,
    verify_password,
    hash_password,
)

UNITS = ("kg", "m", "pieces", "litre")
ROLES = ("admin", "inventory_manager", "production_manager", "viewer")
VENDOR_TYPES = ("supplier", "buyer", "both")
PRODUCTION_STATUSES = ("planned", "in_progress", "completed")
GST_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[A-Z0-9]{1}Z[A-Z0-9]{1}$")


class ValidationError(Exception):
    pass


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(row) for row in rows if row is not None]


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1",
            (username.strip(),),
        ).fetchone()
    if not row:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return row_to_dict(row)


def create_session(user_id: int) -> tuple[str, str]:
    token = generate_session_token()
    expiry = session_expiry_iso()
    with connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expiry),
        )
        conn.commit()
    return token, expiry


def get_user_by_session(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None

    with connect() as conn:
        row = conn.execute(
            """
            SELECT s.token, s.expires_at, u.*
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ? AND u.is_active = 1
            """,
            (token,),
        ).fetchone()
        if not row:
            return None

        expiry = parse_iso(row["expires_at"])
        if expiry < datetime.now(timezone.utc):
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
            return None

    user = {k: row[k] for k in row.keys() if k not in {"token", "expires_at"}}
    return user


def delete_session(token: str | None) -> None:
    if not token:
        return
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()


def list_users() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, username, full_name, role, is_active, created_at
            FROM users
            ORDER BY id
            """
        ).fetchall()
    return rows_to_dicts(rows)


def create_user(username: str, full_name: str, password: str, role: str) -> None:
    username = username.strip().lower()
    full_name = full_name.strip()
    if not username or not full_name or not password:
        raise ValidationError("All user fields are required.")
    if role not in ROLES:
        raise ValidationError("Invalid role selected.")
    if len(password) < 6:
        raise ValidationError("Password must be at least 6 characters.")

    with connect() as conn:
        try:
            conn.execute(
                """
                INSERT INTO users (username, full_name, password_hash, role, is_active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (username, full_name, hash_password(password), role, utcnow_iso()),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValidationError("Username already exists.") from exc


def deactivate_user(user_id: int, acting_user_id: int) -> None:
    if user_id == acting_user_id:
        raise ValidationError("You cannot deactivate your own account.")
    with connect() as conn:
        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()


def list_vendors() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM vendors ORDER BY name"
        ).fetchall()
    return rows_to_dicts(rows)


def create_vendor(data: dict[str, str]) -> None:
    name = data.get("name", "").strip()
    vendor_type = data.get("vendor_type", "").strip()
    gst = data.get("gst_number", "").strip().upper()
    address1 = data.get("address_line1", "").strip()
    address2 = data.get("address_line2", "").strip()
    city = data.get("city", "").strip()
    state = data.get("state", "").strip()
    pincode = data.get("pincode", "").strip()
    contact_person = data.get("contact_person", "").strip()
    phone = data.get("phone", "").strip()
    email = data.get("email", "").strip()

    if not all((name, vendor_type, gst, address1, city, state, pincode)):
        raise ValidationError("Please fill all required vendor fields.")
    if vendor_type not in VENDOR_TYPES:
        raise ValidationError("Invalid vendor type.")
    if not GST_PATTERN.match(gst):
        raise ValidationError("GST number format is invalid.")
    if not pincode.isdigit() or len(pincode) != 6:
        raise ValidationError("Pincode must be a 6-digit number.")

    with connect() as conn:
        try:
            conn.execute(
                """
                INSERT INTO vendors (
                    name, vendor_type, gst_number, address_line1, address_line2,
                    city, state, pincode, contact_person, phone, email, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    vendor_type,
                    gst,
                    address1,
                    address2,
                    city,
                    state,
                    pincode,
                    contact_person,
                    phone,
                    email,
                    utcnow_iso(),
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValidationError("Vendor with this name already exists.") from exc


def list_supplier_vendors() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM vendors
            WHERE vendor_type IN ('supplier', 'both')
            ORDER BY name
            """
        ).fetchall()
    return rows_to_dicts(rows)


def list_raw_materials() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT rm.*, v.name AS vendor_name
            FROM raw_materials rm
            JOIN vendors v ON v.id = rm.vendor_id
            ORDER BY rm.name
            """
        ).fetchall()
    return rows_to_dicts(rows)


def get_raw_material(material_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT rm.*, v.name AS vendor_name
            FROM raw_materials rm
            JOIN vendors v ON v.id = rm.vendor_id
            WHERE rm.id = ?
            """,
            (material_id,),
        ).fetchone()
    return row_to_dict(row)


def create_raw_material(data: dict[str, str], user_id: int) -> None:
    name = data.get("name", "").strip()
    code = data.get("code", "").strip().upper()
    unit = data.get("unit", "").strip()
    vendor_id = data.get("vendor_id", "").strip()
    opening_stock = data.get("opening_stock", "0").strip()
    reorder_level = data.get("reorder_level", "0").strip()

    if not all((name, code, unit, vendor_id)):
        raise ValidationError("All raw material fields are required.")
    if unit not in UNITS:
        raise ValidationError("Invalid unit selected.")

    try:
        vendor_id_int = int(vendor_id)
        opening_stock_value = float(opening_stock)
        reorder_level_value = float(reorder_level)
    except ValueError as exc:
        raise ValidationError("Numeric fields have invalid values.") from exc

    if opening_stock_value < 0 or reorder_level_value < 0:
        raise ValidationError("Stock values cannot be negative.")

    with connect() as conn:
        vendor_exists = conn.execute(
            "SELECT id FROM vendors WHERE id = ? AND vendor_type IN ('supplier', 'both')",
            (vendor_id_int,),
        ).fetchone()
        if not vendor_exists:
            raise ValidationError("Select a valid supplier vendor.")

        try:
            cursor = conn.execute(
                """
                INSERT INTO raw_materials (
                    name, code, unit, current_stock, reorder_level, vendor_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    code,
                    unit,
                    opening_stock_value,
                    reorder_level_value,
                    vendor_id_int,
                    utcnow_iso(),
                ),
            )
            material_id = cursor.lastrowid
            if opening_stock_value > 0:
                conn.execute(
                    """
                    INSERT INTO inventory_ledger (
                        material_id, txn_type, quantity, unit, reason, reference_type, reference_id,
                        created_by, created_at
                    ) VALUES (?, 'IN', ?, ?, ?, 'opening_stock', ?, ?, ?)
                    """,
                    (
                        material_id,
                        opening_stock_value,
                        unit,
                        "Opening stock",
                        material_id,
                        user_id,
                        utcnow_iso(),
                    ),
                )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValidationError("Raw material code already exists.") from exc


def adjust_raw_material_stock(material_id: int, delta: float, reason: str, user_id: int) -> None:
    reason = reason.strip()
    if delta == 0:
        raise ValidationError("Adjustment quantity cannot be zero.")
    if not reason:
        raise ValidationError("Reason is required.")

    with connect() as conn:
        row = conn.execute(
            "SELECT id, current_stock, unit FROM raw_materials WHERE id = ?",
            (material_id,),
        ).fetchone()
        if not row:
            raise ValidationError("Raw material not found.")

        new_stock = row["current_stock"] + delta
        if new_stock < 0:
            raise ValidationError("Stock cannot become negative.")

        txn_type = "IN" if delta > 0 else "OUT"
        qty = abs(delta)
        conn.execute(
            "UPDATE raw_materials SET current_stock = ? WHERE id = ?",
            (new_stock, material_id),
        )
        conn.execute(
            """
            INSERT INTO inventory_ledger (
                material_id, txn_type, quantity, unit, reason, reference_type, reference_id,
                created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, 'manual_adjustment', ?, ?, ?)
            """,
            (material_id, txn_type, qty, row["unit"], reason, material_id, user_id, utcnow_iso()),
        )
        conn.commit()


def list_recent_ledger(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT l.*, rm.name AS material_name
            FROM inventory_ledger l
            JOIN raw_materials rm ON rm.id = l.material_id
            ORDER BY l.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return rows_to_dicts(rows)


def list_finished_products() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM finished_products ORDER BY name"
        ).fetchall()
    return rows_to_dicts(rows)


def create_finished_product(name: str, sku: str) -> None:
    name = name.strip()
    sku = sku.strip().upper()

    if not name or not sku:
        raise ValidationError("Product name and SKU are required.")

    with connect() as conn:
        try:
            conn.execute(
                "INSERT INTO finished_products (name, sku, created_at) VALUES (?, ?, ?)",
                (name, sku, utcnow_iso()),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValidationError("Product SKU already exists.") from exc


def list_bom_items() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT bi.id, bi.qty_per_unit, bi.product_id, bi.material_id,
                   fp.name AS product_name, fp.sku,
                   rm.name AS material_name, rm.code, rm.unit
            FROM bom_items bi
            JOIN finished_products fp ON fp.id = bi.product_id
            JOIN raw_materials rm ON rm.id = bi.material_id
            ORDER BY fp.name, rm.name
            """
        ).fetchall()
    return rows_to_dicts(rows)


def add_bom_item(product_id: int, material_id: int, qty_per_unit: float) -> None:
    if qty_per_unit <= 0:
        raise ValidationError("Quantity per unit must be greater than zero.")

    with connect() as conn:
        product = conn.execute(
            "SELECT id FROM finished_products WHERE id = ?",
            (product_id,),
        ).fetchone()
        material = conn.execute(
            "SELECT id FROM raw_materials WHERE id = ?",
            (material_id,),
        ).fetchone()
        if not product or not material:
            raise ValidationError("Select valid product and raw material.")

        try:
            conn.execute(
                """
                INSERT INTO bom_items (product_id, material_id, qty_per_unit)
                VALUES (?, ?, ?)
                """,
                (product_id, material_id, qty_per_unit),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValidationError("This raw material is already mapped in BOM for the product.") from exc


def remove_bom_item(bom_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM bom_items WHERE id = ?", (bom_id,))
        conn.commit()


def list_production_orders() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT po.*, fp.name AS product_name, fp.sku, u.username AS created_by_username
            FROM production_orders po
            JOIN finished_products fp ON fp.id = po.product_id
            LEFT JOIN users u ON u.id = po.created_by
            ORDER BY po.id DESC
            """
        ).fetchall()
    return rows_to_dicts(rows)


def create_production_order(product_id: int, quantity: int, notes: str, user_id: int) -> int:
    if quantity <= 0:
        raise ValidationError("Production quantity must be greater than zero.")

    with connect() as conn:
        product = conn.execute(
            "SELECT id FROM finished_products WHERE id = ?",
            (product_id,),
        ).fetchone()
        if not product:
            raise ValidationError("Select a valid finished product.")

        bom_rows = conn.execute(
            """
            SELECT bi.material_id, bi.qty_per_unit, rm.name AS material_name,
                   rm.current_stock, rm.unit
            FROM bom_items bi
            JOIN raw_materials rm ON rm.id = bi.material_id
            WHERE bi.product_id = ?
            """,
            (product_id,),
        ).fetchall()

        if not bom_rows:
            raise ValidationError("No BOM defined for this product.")

        shortages: list[str] = []
        requirements: list[dict[str, Any]] = []
        for item in bom_rows:
            required_qty = float(item["qty_per_unit"]) * quantity
            current_stock = float(item["current_stock"])
            requirements.append(
                {
                    "material_id": item["material_id"],
                    "material_name": item["material_name"],
                    "required_qty": required_qty,
                    "unit": item["unit"],
                    "available_qty": current_stock,
                }
            )
            if current_stock < required_qty:
                shortages.append(
                    f"{item['material_name']}: required {required_qty:.3f} {item['unit']}, available {current_stock:.3f}"
                )

        if shortages:
            raise ValidationError("Insufficient stock. " + "; ".join(shortages))

        cursor = conn.execute(
            """
            INSERT INTO production_orders (product_id, quantity, status, notes, created_by, created_at)
            VALUES (?, ?, 'planned', ?, ?, ?)
            """,
            (product_id, quantity, notes.strip(), user_id, utcnow_iso()),
        )
        order_id = cursor.lastrowid

        for req in requirements:
            material_id = req["material_id"]
            required_qty = req["required_qty"]
            unit = req["unit"]

            conn.execute(
                "UPDATE raw_materials SET current_stock = current_stock - ? WHERE id = ?",
                (required_qty, material_id),
            )
            conn.execute(
                """
                INSERT INTO production_consumption (production_order_id, material_id, required_qty)
                VALUES (?, ?, ?)
                """,
                (order_id, material_id, required_qty),
            )
            conn.execute(
                """
                INSERT INTO inventory_ledger (
                    material_id, txn_type, quantity, unit, reason,
                    reference_type, reference_id, created_by, created_at
                ) VALUES (?, 'OUT', ?, ?, ?, 'production_order', ?, ?, ?)
                """,
                (
                    material_id,
                    required_qty,
                    unit,
                    f"Consumed by production order #{order_id}",
                    order_id,
                    user_id,
                    utcnow_iso(),
                ),
            )

        conn.commit()

    return order_id


def update_production_status(order_id: int, status: str) -> None:
    if status not in PRODUCTION_STATUSES:
        raise ValidationError("Invalid production status.")
    with connect() as conn:
        conn.execute("UPDATE production_orders SET status = ? WHERE id = ?", (status, order_id))
        conn.commit()


def list_purchase_orders() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT po.*, v.name AS vendor_name
            FROM purchase_orders po
            JOIN vendors v ON v.id = po.vendor_id
            ORDER BY po.id DESC
            """
        ).fetchall()
    return rows_to_dicts(rows)


def create_purchase_orders_from_items(
    order_date: str,
    notes: str,
    user_id: int,
    material_ids: list[int],
    quantities: list[float],
) -> list[int]:
    if not order_date:
        raise ValidationError("Order date is required.")

    try:
        datetime.strptime(order_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValidationError("Order date is invalid.") from exc

    if len(material_ids) != len(quantities) or not material_ids:
        raise ValidationError("Add at least one purchase order line item.")

    lines: list[tuple[int, float]] = []
    for material_id, quantity in zip(material_ids, quantities):
        if quantity <= 0:
            raise ValidationError("Quantities must be greater than zero.")
        lines.append((material_id, quantity))

    with connect() as conn:
        material_rows = conn.execute(
            """
            SELECT id, name, vendor_id, unit
            FROM raw_materials
            WHERE id IN ({})
            """.format(
                ",".join("?" for _ in material_ids)
            ),
            material_ids,
        ).fetchall()

        by_material = {row["id"]: row for row in material_rows}
        if len(by_material) != len(set(material_ids)):
            raise ValidationError("One or more selected raw materials are invalid.")

        grouped: dict[int, list[tuple[int, float, str]]] = defaultdict(list)
        for material_id, quantity in lines:
            item = by_material[material_id]
            grouped[item["vendor_id"]].append((material_id, quantity, item["unit"]))

        created_po_ids: list[int] = []
        for vendor_id, group_items in grouped.items():
            cursor = conn.execute(
                """
                INSERT INTO purchase_orders (vendor_id, order_date, notes, created_by, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (vendor_id, order_date, notes.strip(), user_id, utcnow_iso()),
            )
            po_id = cursor.lastrowid
            for material_id, quantity, unit in group_items:
                conn.execute(
                    """
                    INSERT INTO purchase_order_items (purchase_order_id, material_id, quantity, unit)
                    VALUES (?, ?, ?, ?)
                    """,
                    (po_id, material_id, quantity, unit),
                )
            created_po_ids.append(po_id)

        conn.commit()

    return created_po_ids


def get_purchase_order(po_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        header = conn.execute(
            """
            SELECT po.*, v.name AS vendor_name, v.gst_number, v.address_line1, v.address_line2,
                   v.city, v.state, v.pincode
            FROM purchase_orders po
            JOIN vendors v ON v.id = po.vendor_id
            WHERE po.id = ?
            """,
            (po_id,),
        ).fetchone()
        if not header:
            return None

        lines = conn.execute(
            """
            SELECT poi.*, rm.name AS material_name, rm.code
            FROM purchase_order_items poi
            JOIN raw_materials rm ON rm.id = poi.material_id
            WHERE poi.purchase_order_id = ?
            ORDER BY poi.id
            """,
            (po_id,),
        ).fetchall()

    po = row_to_dict(header)
    po["items"] = rows_to_dicts(lines)
    return po


def dashboard_summary() -> dict[str, Any]:
    with connect() as conn:
        total_materials = conn.execute("SELECT COUNT(*) FROM raw_materials").fetchone()[0]
        total_products = conn.execute("SELECT COUNT(*) FROM finished_products").fetchone()[0]
        total_vendors = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
        in_progress = conn.execute(
            """
            SELECT COUNT(*) FROM production_orders
            WHERE status IN ('planned', 'in_progress')
            """
        ).fetchone()[0]

        low_stock_rows = conn.execute(
            """
            SELECT rm.name, rm.code, rm.current_stock, rm.reorder_level, rm.unit
            FROM raw_materials rm
            WHERE rm.current_stock <= rm.reorder_level
            ORDER BY rm.current_stock ASC
            LIMIT 10
            """
        ).fetchall()

    return {
        "total_materials": total_materials,
        "total_products": total_products,
        "total_vendors": total_vendors,
        "in_progress": in_progress,
        "low_stock_items": rows_to_dicts(low_stock_rows),
        "recent_ledger": list_recent_ledger(8),
    }
