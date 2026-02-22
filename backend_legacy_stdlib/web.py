from __future__ import annotations

import mimetypes
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode
import html

from exporter import generate_purchase_order_excel
from services import (
    PRODUCTION_STATUSES,
    ROLES,
    UNITS,
    ValidationError,
    add_bom_item,
    authenticate,
    create_finished_product,
    create_production_order,
    create_purchase_orders_from_items,
    create_raw_material,
    create_session,
    create_user,
    create_vendor,
    dashboard_summary,
    deactivate_user,
    delete_session,
    get_purchase_order,
    get_user_by_session,
    list_bom_items,
    list_finished_products,
    list_production_orders,
    list_purchase_orders,
    list_raw_materials,
    list_supplier_vendors,
    list_users,
    list_vendors,
    remove_bom_item,
    update_production_status,
    adjust_raw_material_stock,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSION_COOKIE = "busta_session"


def e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def parse_cookies(environ: dict[str, Any]) -> dict[str, str]:
    cookie_str = environ.get("HTTP_COOKIE", "")
    cookie = SimpleCookie()
    cookie.load(cookie_str)
    return {k: morsel.value for k, morsel in cookie.items()}


def parse_post_data(environ: dict[str, Any]) -> dict[str, list[str]]:
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except ValueError:
        length = 0
    body = environ["wsgi.input"].read(length).decode("utf-8") if length > 0 else ""
    return parse_qs(body)


def first(form: dict[str, list[str]], key: str, default: str = "") -> str:
    values = form.get(key)
    if not values:
        return default
    return values[0]


def query_params(environ: dict[str, Any]) -> dict[str, list[str]]:
    return parse_qs(environ.get("QUERY_STRING", ""))


def make_response(
    start_response,
    body: str | bytes,
    status: str = "200 OK",
    headers: list[tuple[str, str]] | None = None,
    content_type: str = "text/html; charset=utf-8",
):
    if isinstance(body, str):
        payload = body.encode("utf-8")
    else:
        payload = body
    response_headers = [("Content-Type", content_type), ("Content-Length", str(len(payload)))]
    if headers:
        response_headers.extend(headers)
    start_response(status, response_headers)
    return [payload]


def redirect(
    start_response,
    location: str,
    headers: list[tuple[str, str]] | None = None,
):
    all_headers = [("Location", location)]
    if headers:
        all_headers.extend(headers)
    start_response("302 Found", all_headers)
    return [b""]


def build_location(path: str, msg: str | None = None, err: str | None = None) -> str:
    params: dict[str, str] = {}
    if msg:
        params["msg"] = msg
    if err:
        params["err"] = err
    if not params:
        return path
    return f"{path}?{urlencode(params)}"


def has_role(user: dict[str, Any], roles: tuple[str, ...]) -> bool:
    return bool(user and user.get("role") in roles)


def nav_html(user: dict[str, Any]) -> str:
    links = [
        ('/dashboard', 'Dashboard'),
        ('/vendors', 'Vendors'),
        ('/materials', 'Raw Materials'),
        ('/products', 'Products & BOM'),
        ('/production-orders', 'Production Orders'),
        ('/purchase-orders', 'Purchase Orders'),
    ]
    if user.get("role") == "admin":
        links.append(('/users', 'Users'))

    items = "".join(
        f'<a href="{url}" class="nav-link">{label}</a>' for url, label in links
    )
    return f"""
    <nav class="top-nav">
      <div class="brand">Busta IM</div>
      <div class="links">{items}</div>
      <div class="right">
        <span class="user-chip">{e(user.get("full_name"))} ({e(user.get("role"))})</span>
        <a href="/logout" class="nav-link logout">Logout</a>
      </div>
    </nav>
    """


def layout(
    title: str,
    content: str,
    user: dict[str, Any] | None = None,
    message: str | None = None,
    error: str | None = None,
) -> str:
    flash = ""
    if message:
        flash = f'<div class="alert success">{e(message)}</div>'
    if error:
        flash = f'<div class="alert error">{e(error)}</div>'

    nav = nav_html(user) if user else ""
    return f"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>{e(title)} | Busta IM</title>
      <link rel="stylesheet" href="/static/style.css" />
    </head>
    <body>
      {nav}
      <main class="container">
        {flash}
        {content}
      </main>
    </body>
    </html>
    """


def login_page(error: str | None = None) -> str:
    content = """
    <section class="auth-card">
      <h1>Inventory & Production Management</h1>
      <p>Sign in to continue</p>
      <form method="post" action="/login" class="stack">
        <label>Username
          <input type="text" name="username" required autocomplete="username" />
        </label>
        <label>Password
          <input type="password" name="password" required autocomplete="current-password" />
        </label>
        <button type="submit">Login</button>
      </form>
      <p class="helper">Default admin: <code>admin</code> / <code>admin123</code></p>
    </section>
    """
    return layout("Login", content, user=None, error=error)


def render_dashboard(user: dict[str, Any], params: dict[str, list[str]]) -> str:
    summary = dashboard_summary()
    low_stock_rows = "".join(
        (
            f"<tr><td>{e(item['name'])}</td><td>{e(item['code'])}</td>"
            f"<td>{float(item['current_stock']):.3f}</td><td>{float(item['reorder_level']):.3f}</td><td>{e(item['unit'])}</td></tr>"
        )
        for item in summary["low_stock_items"]
    )
    if not low_stock_rows:
        low_stock_rows = '<tr><td colspan="5">No low stock alerts.</td></tr>'

    ledger_rows = "".join(
        (
            f"<tr><td>{entry['id']}</td><td>{e(entry['material_name'])}</td><td>{e(entry['txn_type'])}</td>"
            f"<td>{float(entry['quantity']):.3f} {e(entry['unit'])}</td><td>{e(entry['reason'])}</td><td>{e(entry['created_at'])}</td></tr>"
        )
        for entry in summary["recent_ledger"]
    )
    if not ledger_rows:
        ledger_rows = '<tr><td colspan="6">No transactions recorded yet.</td></tr>'

    content = f"""
    <h1>Dashboard</h1>
    <section class="cards-grid">
      <article class="kpi-card"><h3>Raw Materials</h3><p>{summary['total_materials']}</p></article>
      <article class="kpi-card"><h3>Finished Products</h3><p>{summary['total_products']}</p></article>
      <article class="kpi-card"><h3>Vendors</h3><p>{summary['total_vendors']}</p></article>
      <article class="kpi-card"><h3>Production In Progress</h3><p>{summary['in_progress']}</p></article>
    </section>

    <section class="panel">
      <h2>Low Stock Alerts</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Material</th><th>Code</th><th>Stock</th><th>Reorder Level</th><th>Unit</th></tr></thead>
          <tbody>{low_stock_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Recent Inventory Transactions</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>ID</th><th>Material</th><th>Type</th><th>Quantity</th><th>Reason</th><th>Timestamp</th></tr></thead>
          <tbody>{ledger_rows}</tbody>
        </table>
      </div>
    </section>
    """
    return layout(
        "Dashboard",
        content,
        user=user,
        message=first(params, "msg"),
        error=first(params, "err"),
    )


def render_users(user: dict[str, Any], params: dict[str, list[str]]) -> str:
    if user.get("role") != "admin":
        return layout("Forbidden", "<h1>403 - Forbidden</h1>", user=user, error="Admin access required.")

    users = list_users()
    user_rows = "".join(
        f"""
        <tr>
          <td>{entry['id']}</td>
          <td>{e(entry['username'])}</td>
          <td>{e(entry['full_name'])}</td>
          <td>{e(entry['role'])}</td>
          <td>{'Active' if entry['is_active'] else 'Inactive'}</td>
          <td>
            {'-' if not entry['is_active'] else f'<form method="post" action="/users/deactivate" onsubmit="return confirm(\'Deactivate user?\')"><input type="hidden" name="user_id" value="{entry['id']}" /><button type="submit" class="danger">Deactivate</button></form>'}
          </td>
        </tr>
        """
        for entry in users
    )

    role_options = "".join(f"<option value=\"{role}\">{role}</option>" for role in ROLES)

    content = f"""
    <h1>User Management</h1>
    <section class="panel split">
      <div>
        <h2>Add User</h2>
        <form method="post" action="/users/new" class="stack">
          <label>Username <input type="text" name="username" required /></label>
          <label>Full Name <input type="text" name="full_name" required /></label>
          <label>Password <input type="password" name="password" minlength="6" required /></label>
          <label>Role
            <select name="role" required>{role_options}</select>
          </label>
          <button type="submit">Create User</button>
        </form>
      </div>
      <div>
        <h2>Users</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>ID</th><th>Username</th><th>Name</th><th>Role</th><th>Status</th><th>Action</th></tr></thead>
            <tbody>{user_rows}</tbody>
          </table>
        </div>
      </div>
    </section>
    """
    return layout(
        "Users",
        content,
        user=user,
        message=first(params, "msg"),
        error=first(params, "err"),
    )


def vendor_form_section() -> str:
    type_options = "".join(
        f"<option value=\"{value}\">{value.title()}</option>" for value in ("supplier", "buyer", "both")
    )
    return f"""
    <h2>Add Vendor / Buyer</h2>
    <form method="post" action="/vendors/new" class="grid-form">
      <label>Name<input type="text" name="name" required /></label>
      <label>Type
        <select name="vendor_type" required>{type_options}</select>
      </label>
      <label>GST Number<input type="text" name="gst_number" maxlength="15" required /></label>
      <label>Address Line 1<input type="text" name="address_line1" required /></label>
      <label>Address Line 2<input type="text" name="address_line2" /></label>
      <label>City<input type="text" name="city" required /></label>
      <label>State<input type="text" name="state" required /></label>
      <label>Pincode<input type="text" name="pincode" pattern="[0-9]{6}" required /></label>
      <label>Contact Person<input type="text" name="contact_person" /></label>
      <label>Phone<input type="text" name="phone" /></label>
      <label>Email<input type="email" name="email" /></label>
      <div class="form-actions"><button type="submit">Add Vendor</button></div>
    </form>
    """


def render_vendors(user: dict[str, Any], params: dict[str, list[str]]) -> str:
    rows = list_vendors()
    table_rows = "".join(
        f"""
        <tr>
          <td>{vendor['id']}</td>
          <td>{e(vendor['name'])}</td>
          <td>{e(vendor['vendor_type'])}</td>
          <td>{e(vendor['gst_number'])}</td>
          <td>{e(vendor['address_line1'])}, {e(vendor.get('address_line2') or '')}, {e(vendor['city'])}, {e(vendor['state'])} - {e(vendor['pincode'])}</td>
          <td>{e(vendor.get('contact_person') or '-')}</td>
        </tr>
        """
        for vendor in rows
    )
    if not table_rows:
        table_rows = '<tr><td colspan="6">No vendors added yet.</td></tr>'

    form_html = ""
    if has_role(user, ("admin", "inventory_manager")):
        form_html = vendor_form_section()

    content = f"""
    <h1>Vendors / Buyers</h1>
    <section class="panel">{form_html}</section>
    <section class="panel">
      <h2>Vendor List</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>ID</th><th>Name</th><th>Type</th><th>GSTIN</th><th>Address</th><th>Contact</th></tr></thead>
          <tbody>{table_rows}</tbody>
        </table>
      </div>
    </section>
    """

    return layout(
        "Vendors",
        content,
        user=user,
        message=first(params, "msg"),
        error=first(params, "err"),
    )


def render_materials(user: dict[str, Any], params: dict[str, list[str]]) -> str:
    suppliers = list_supplier_vendors()
    materials = list_raw_materials()

    vendor_options = "".join(
        f'<option value="{vendor["id"]}">{e(vendor["name"])} ({e(vendor["vendor_type"])})</option>'
        for vendor in suppliers
    )
    unit_options = "".join(f'<option value="{unit}">{unit}</option>' for unit in UNITS)

    add_material_form = ""
    if has_role(user, ("admin", "inventory_manager")):
        add_material_form = f"""
        <h2>Add Raw Material</h2>
        <form method="post" action="/materials/new" class="grid-form">
          <label>Name<input type="text" name="name" required /></label>
          <label>Code<input type="text" name="code" required /></label>
          <label>Unit<select name="unit" required>{unit_options}</select></label>
          <label>Supplier Vendor<select name="vendor_id" required><option value="">Select vendor</option>{vendor_options}</select></label>
          <label>Opening Stock<input type="number" step="0.001" min="0" name="opening_stock" value="0" required /></label>
          <label>Reorder Level<input type="number" step="0.001" min="0" name="reorder_level" value="0" required /></label>
          <div class="form-actions"><button type="submit">Add Raw Material</button></div>
        </form>
        """

    rows = ""
    for material in materials:
        adjust_form = "-"
        if has_role(user, ("admin", "inventory_manager")):
            adjust_form = f"""
            <form method="post" action="/materials/adjust" class="inline-form">
              <input type="hidden" name="material_id" value="{material['id']}" />
              <input type="number" step="0.001" name="delta" placeholder="+/- qty" required />
              <input type="text" name="reason" placeholder="Reason" required />
              <button type="submit">Apply</button>
            </form>
            """
        low_class = "low" if float(material["current_stock"]) <= float(material["reorder_level"]) else ""
        rows += f"""
        <tr class="{low_class}">
          <td>{material['id']}</td>
          <td>{e(material['name'])}</td>
          <td>{e(material['code'])}</td>
          <td>{float(material['current_stock']):.3f} {e(material['unit'])}</td>
          <td>{float(material['reorder_level']):.3f}</td>
          <td>{e(material['vendor_name'])}</td>
          <td>{adjust_form}</td>
        </tr>
        """

    if not rows:
        rows = '<tr><td colspan="7">No raw materials available.</td></tr>'

    vendor_warning = ""
    if not suppliers:
        vendor_warning = '<p class="note warning">Add supplier vendors first. Raw material creation requires vendor selection.</p>'

    content = f"""
    <h1>Raw Materials Inventory</h1>
    {vendor_warning}
    <section class="panel">{add_material_form}</section>
    <section class="panel">
      <h2>Inventory</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>ID</th><th>Material</th><th>Code</th><th>Current Stock</th><th>Reorder</th><th>Supplier</th><th>Adjust</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>
    """

    return layout(
        "Raw Materials",
        content,
        user=user,
        message=first(params, "msg"),
        error=first(params, "err"),
    )


def render_products(user: dict[str, Any], params: dict[str, list[str]]) -> str:
    products = list_finished_products()
    materials = list_raw_materials()
    bom_items = list_bom_items()

    product_options = "".join(
        f'<option value="{product["id"]}">{e(product["name"])} ({e(product["sku"])})</option>'
        for product in products
    )
    material_options = "".join(
        f'<option value="{material["id"]}">{e(material["name"])} ({e(material["code"])} - {e(material["unit"])})</option>'
        for material in materials
    )

    product_rows = "".join(
        f"<tr><td>{product['id']}</td><td>{e(product['name'])}</td><td>{e(product['sku'])}</td></tr>"
        for product in products
    )
    if not product_rows:
        product_rows = '<tr><td colspan="3">No finished products yet.</td></tr>'

    bom_rows = ""
    for item in bom_items:
        delete_control = ""
        if has_role(user, ("admin", "production_manager")):
            delete_control = f"""
            <form method="post" action="/products/bom/delete" onsubmit="return confirm('Remove BOM item?')">
              <input type="hidden" name="bom_id" value="{item['id']}" />
              <button type="submit" class="danger">Delete</button>
            </form>
            """
        bom_rows += f"""
        <tr>
          <td>{item['id']}</td>
          <td>{e(item['product_name'])}</td>
          <td>{e(item['material_name'])}</td>
          <td>{float(item['qty_per_unit']):.3f} {e(item['unit'])}</td>
          <td>{delete_control or '-'}</td>
        </tr>
        """
    if not bom_rows:
        bom_rows = '<tr><td colspan="5">No BOM mappings available.</td></tr>'

    forms_html = ""
    if has_role(user, ("admin", "production_manager")):
        forms_html = f"""
        <section class="panel split">
          <div>
            <h2>Add Finished Product</h2>
            <form method="post" action="/products/new" class="stack">
              <label>Product Name<input type="text" name="name" required /></label>
              <label>SKU<input type="text" name="sku" required /></label>
              <button type="submit">Add Product</button>
            </form>
          </div>
          <div>
            <h2>Add / Update BOM</h2>
            <form method="post" action="/products/bom/add" class="stack">
              <label>Finished Product
                <select name="product_id" required>
                  <option value="">Select product</option>
                  {product_options}
                </select>
              </label>
              <label>Raw Material
                <select name="material_id" required>
                  <option value="">Select material</option>
                  {material_options}
                </select>
              </label>
              <label>Qty Needed For 1 Unit
                <input type="number" name="qty_per_unit" min="0.001" step="0.001" required />
              </label>
              <button type="submit">Add BOM Item</button>
            </form>
          </div>
        </section>
        """

    content = f"""
    <h1>Finished Products & BOM</h1>
    {forms_html}
    <section class="panel split">
      <div>
        <h2>Finished Products</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>ID</th><th>Name</th><th>SKU</th></tr></thead>
            <tbody>{product_rows}</tbody>
          </table>
        </div>
      </div>
      <div>
        <h2>BOM Mapping</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>ID</th><th>Product</th><th>Material</th><th>Qty / Unit</th><th>Action</th></tr></thead>
            <tbody>{bom_rows}</tbody>
          </table>
        </div>
      </div>
    </section>
    """

    return layout(
        "Products & BOM",
        content,
        user=user,
        message=first(params, "msg"),
        error=first(params, "err"),
    )


def render_production_orders(user: dict[str, Any], params: dict[str, list[str]]) -> str:
    products = list_finished_products()
    orders = list_production_orders()

    product_options = "".join(
        f'<option value="{product["id"]}">{e(product["name"])} ({e(product["sku"])})</option>'
        for product in products
    )

    create_form = ""
    if has_role(user, ("admin", "production_manager")):
        create_form = f"""
        <section class="panel">
          <h2>Create Production Order</h2>
          <form method="post" action="/production-orders/new" class="grid-form">
            <label>Product
              <select name="product_id" required>
                <option value="">Select product</option>
                {product_options}
              </select>
            </label>
            <label>Quantity (units)
              <input type="number" name="quantity" min="1" step="1" required />
            </label>
            <label>Notes
              <input type="text" name="notes" maxlength="250" />
            </label>
            <div class="form-actions"><button type="submit">Create Order</button></div>
          </form>
        </section>
        """

    rows = ""
    for order in orders:
        status_badge = f"<span class='badge {e(order['status'])}'>{e(order['status'])}</span>"
        status_control = status_badge
        if has_role(user, ("admin", "production_manager")):
            options = "".join(
                f"<option value=\"{status}\" {'selected' if order['status'] == status else ''}>{status}</option>"
                for status in PRODUCTION_STATUSES
            )
            status_control = f"""
            <form method="post" action="/production-orders/status" class="inline-form compact">
              <input type="hidden" name="order_id" value="{order['id']}" />
              <select name="status">{options}</select>
              <button type="submit">Update</button>
            </form>
            """
        rows += f"""
        <tr>
          <td>{order['id']}</td>
          <td>{e(order['product_name'])}</td>
          <td>{order['quantity']}</td>
          <td>{status_badge}</td>
          <td>{e(order.get('created_by_username') or '-')}</td>
          <td>{e(order['created_at'])}</td>
          <td>{status_control}</td>
        </tr>
        """

    if not rows:
        rows = '<tr><td colspan="7">No production orders created.</td></tr>'

    content = f"""
    <h1>Production Orders</h1>
    {create_form}
    <section class="panel">
      <h2>Orders</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>ID</th><th>Product</th><th>Qty</th><th>Status</th><th>Created By</th><th>Created At</th><th>Action</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>
    """

    return layout(
        "Production Orders",
        content,
        user=user,
        message=first(params, "msg"),
        error=first(params, "err"),
    )


def render_purchase_orders(user: dict[str, Any], params: dict[str, list[str]]) -> str:
    materials = list_raw_materials()
    purchase_orders = list_purchase_orders()
    material_options = "".join(
        f'<option value="{material["id"]}">{e(material["name"])} ({e(material["code"])} | {e(material["vendor_name"])})</option>'
        for material in materials
    )

    create_form = ""
    if has_role(user, ("admin", "inventory_manager")):
        create_form = f"""
        <section class="panel">
          <h2>Create Purchase Order</h2>
          <p class="note">You can add materials from different vendors. The system auto-creates separate POs per vendor.</p>
          <form method="post" action="/purchase-orders/new" id="po-form" class="stack">
            <label>Order Date
              <input type="date" name="order_date" required />
            </label>
            <label>Notes
              <input type="text" name="notes" maxlength="250" />
            </label>
            <div id="line-items" class="stack">
              <div class="po-line">
                <select name="material_id" required>
                  <option value="">Select raw material</option>
                  {material_options}
                </select>
                <input type="number" name="quantity" min="0.001" step="0.001" placeholder="Qty" required />
              </div>
            </div>
            <div class="row-actions">
              <button type="button" id="add-line" class="secondary">Add Line Item</button>
              <button type="submit">Create Purchase Order</button>
            </div>
          </form>
        </section>
        <script>
          (function() {{
            const addBtn = document.getElementById('add-line');
            const lines = document.getElementById('line-items');
            if (!addBtn || !lines) return;
            addBtn.addEventListener('click', () => {{
              const wrapper = document.createElement('div');
              wrapper.className = 'po-line';
              wrapper.innerHTML = `<select name="material_id" required><option value="">Select raw material</option>{material_options}</select>
                <input type="number" name="quantity" min="0.001" step="0.001" placeholder="Qty" required />
                <button type="button" class="danger tiny remove-line">X</button>`;
              lines.appendChild(wrapper);
            }});
            lines.addEventListener('click', (evt) => {{
              if (evt.target && evt.target.classList.contains('remove-line')) {{
                const line = evt.target.closest('.po-line');
                if (line && lines.children.length > 1) line.remove();
              }}
            }});
          }})();
        </script>
        """

    po_rows = "".join(
        f"""
        <tr>
          <td>{po['id']}</td>
          <td>{e(po['vendor_name'])}</td>
          <td>{e(po['order_date'])}</td>
          <td>{e(po.get('notes') or '-')}</td>
          <td><a href="/purchase-orders/export?po_id={po['id']}">Download Excel</a></td>
        </tr>
        """
        for po in purchase_orders
    )
    if not po_rows:
        po_rows = '<tr><td colspan="5">No purchase orders yet.</td></tr>'

    content = f"""
    <h1>Purchase Orders</h1>
    {create_form}
    <section class="panel">
      <h2>Purchase Orders</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>ID</th><th>Vendor</th><th>Order Date</th><th>Notes</th><th>Export</th></tr></thead>
          <tbody>{po_rows}</tbody>
        </table>
      </div>
    </section>
    """
    return layout(
        "Purchase Orders",
        content,
        user=user,
        message=first(params, "msg"),
        error=first(params, "err"),
    )


def serve_static(path: str, start_response):
    requested = (STATIC_DIR / path.removeprefix("/static/")).resolve()
    static_root = STATIC_DIR.resolve()
    if static_root not in requested.parents and requested != static_root:
        return make_response(start_response, "Forbidden", status="403 Forbidden", content_type="text/plain")
    if not requested.exists() or not requested.is_file():
        return make_response(start_response, "Not found", status="404 Not Found", content_type="text/plain")
    data = requested.read_bytes()
    content_type, _ = mimetypes.guess_type(requested.name)
    return make_response(
        start_response,
        data,
        status="200 OK",
        content_type=content_type or "application/octet-stream",
    )


def forbidden(start_response, user: dict[str, Any] | None):
    page = layout("Forbidden", "<h1>403 - Forbidden</h1><p>You are not allowed to perform this action.</p>", user=user)
    return make_response(start_response, page, status="403 Forbidden")


def application(environ: dict[str, Any], start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/")
    params = query_params(environ)
    cookies = parse_cookies(environ)
    session_token = cookies.get(SESSION_COOKIE)
    current_user = get_user_by_session(session_token)

    if path.startswith("/static/"):
        return serve_static(path, start_response)

    if path == "/health":
        return make_response(start_response, "ok", content_type="text/plain")

    if path == "/login" and method == "GET":
        if current_user:
            return redirect(start_response, "/dashboard")
        return make_response(start_response, login_page(error=first(params, "err")))

    if path == "/login" and method == "POST":
        form = parse_post_data(environ)
        username = first(form, "username")
        password = first(form, "password")
        user = authenticate(username, password)
        if not user:
            return make_response(start_response, login_page(error="Invalid credentials."), status="401 Unauthorized")

        token, _ = create_session(user["id"])
        headers = [
            (
                "Set-Cookie",
                f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax",
            )
        ]
        return redirect(start_response, "/dashboard", headers=headers)

    if path == "/logout":
        delete_session(session_token)
        headers = [("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; Max-Age=0")]
        return redirect(start_response, "/login", headers=headers)

    if not current_user:
        return redirect(start_response, "/login")

    if path == "/":
        return redirect(start_response, "/dashboard")

    if path == "/dashboard" and method == "GET":
        return make_response(start_response, render_dashboard(current_user, params))

    if path == "/users" and method == "GET":
        return make_response(start_response, render_users(current_user, params))

    if path == "/users/new" and method == "POST":
        if current_user.get("role") != "admin":
            return forbidden(start_response, current_user)
        form = parse_post_data(environ)
        try:
            create_user(
                first(form, "username"),
                first(form, "full_name"),
                first(form, "password"),
                first(form, "role"),
            )
            return redirect(start_response, build_location("/users", msg="User created successfully."))
        except ValidationError as exc:
            return redirect(start_response, build_location("/users", err=str(exc)))

    if path == "/users/deactivate" and method == "POST":
        if current_user.get("role") != "admin":
            return forbidden(start_response, current_user)
        form = parse_post_data(environ)
        try:
            user_id = int(first(form, "user_id", "0"))
            deactivate_user(user_id, current_user["id"])
            return redirect(start_response, build_location("/users", msg="User deactivated."))
        except (ValueError, ValidationError) as exc:
            return redirect(start_response, build_location("/users", err=str(exc)))

    if path == "/vendors" and method == "GET":
        return make_response(start_response, render_vendors(current_user, params))

    if path == "/vendors/new" and method == "POST":
        if not has_role(current_user, ("admin", "inventory_manager")):
            return forbidden(start_response, current_user)
        form = parse_post_data(environ)
        form_payload = {k: first(form, k) for k in form.keys()}
        try:
            create_vendor(form_payload)
            return redirect(start_response, build_location("/vendors", msg="Vendor added."))
        except ValidationError as exc:
            return redirect(start_response, build_location("/vendors", err=str(exc)))

    if path == "/materials" and method == "GET":
        return make_response(start_response, render_materials(current_user, params))

    if path == "/materials/new" and method == "POST":
        if not has_role(current_user, ("admin", "inventory_manager")):
            return forbidden(start_response, current_user)
        form = parse_post_data(environ)
        form_payload = {k: first(form, k) for k in form.keys()}
        try:
            create_raw_material(form_payload, current_user["id"])
            return redirect(start_response, build_location("/materials", msg="Raw material added."))
        except ValidationError as exc:
            return redirect(start_response, build_location("/materials", err=str(exc)))

    if path == "/materials/adjust" and method == "POST":
        if not has_role(current_user, ("admin", "inventory_manager")):
            return forbidden(start_response, current_user)
        form = parse_post_data(environ)
        try:
            material_id = int(first(form, "material_id", "0"))
            delta = float(first(form, "delta", "0"))
            reason = first(form, "reason")
            adjust_raw_material_stock(material_id, delta, reason, current_user["id"])
            return redirect(start_response, build_location("/materials", msg="Stock adjusted."))
        except (ValueError, ValidationError) as exc:
            return redirect(start_response, build_location("/materials", err=str(exc)))

    if path == "/products" and method == "GET":
        return make_response(start_response, render_products(current_user, params))

    if path == "/products/new" and method == "POST":
        if not has_role(current_user, ("admin", "production_manager")):
            return forbidden(start_response, current_user)
        form = parse_post_data(environ)
        try:
            create_finished_product(first(form, "name"), first(form, "sku"))
            return redirect(start_response, build_location("/products", msg="Finished product added."))
        except ValidationError as exc:
            return redirect(start_response, build_location("/products", err=str(exc)))

    if path == "/products/bom/add" and method == "POST":
        if not has_role(current_user, ("admin", "production_manager")):
            return forbidden(start_response, current_user)
        form = parse_post_data(environ)
        try:
            product_id = int(first(form, "product_id", "0"))
            material_id = int(first(form, "material_id", "0"))
            qty_per_unit = float(first(form, "qty_per_unit", "0"))
            add_bom_item(product_id, material_id, qty_per_unit)
            return redirect(start_response, build_location("/products", msg="BOM item added."))
        except (ValueError, ValidationError) as exc:
            return redirect(start_response, build_location("/products", err=str(exc)))

    if path == "/products/bom/delete" and method == "POST":
        if not has_role(current_user, ("admin", "production_manager")):
            return forbidden(start_response, current_user)
        form = parse_post_data(environ)
        try:
            bom_id = int(first(form, "bom_id", "0"))
            remove_bom_item(bom_id)
            return redirect(start_response, build_location("/products", msg="BOM item removed."))
        except ValueError as exc:
            return redirect(start_response, build_location("/products", err=str(exc)))

    if path == "/production-orders" and method == "GET":
        return make_response(start_response, render_production_orders(current_user, params))

    if path == "/production-orders/new" and method == "POST":
        if not has_role(current_user, ("admin", "production_manager")):
            return forbidden(start_response, current_user)
        form = parse_post_data(environ)
        try:
            product_id = int(first(form, "product_id", "0"))
            quantity = int(first(form, "quantity", "0"))
            notes = first(form, "notes")
            order_id = create_production_order(product_id, quantity, notes, current_user["id"])
            return redirect(
                start_response,
                build_location("/production-orders", msg=f"Production order #{order_id} created and stock deducted."),
            )
        except (ValueError, ValidationError) as exc:
            return redirect(start_response, build_location("/production-orders", err=str(exc)))

    if path == "/production-orders/status" and method == "POST":
        if not has_role(current_user, ("admin", "production_manager")):
            return forbidden(start_response, current_user)
        form = parse_post_data(environ)
        try:
            order_id = int(first(form, "order_id", "0"))
            status = first(form, "status")
            update_production_status(order_id, status)
            return redirect(start_response, build_location("/production-orders", msg="Order status updated."))
        except (ValueError, ValidationError) as exc:
            return redirect(start_response, build_location("/production-orders", err=str(exc)))

    if path == "/purchase-orders" and method == "GET":
        return make_response(start_response, render_purchase_orders(current_user, params))

    if path == "/purchase-orders/new" and method == "POST":
        if not has_role(current_user, ("admin", "inventory_manager")):
            return forbidden(start_response, current_user)
        form = parse_post_data(environ)
        order_date = first(form, "order_date")
        notes = first(form, "notes")

        raw_material_ids = form.get("material_id", [])
        raw_quantities = form.get("quantity", [])

        material_ids: list[int] = []
        quantities: list[float] = []
        try:
            for material_id, quantity in zip(raw_material_ids, raw_quantities):
                if not material_id or not quantity:
                    continue
                material_ids.append(int(material_id))
                quantities.append(float(quantity))

            created_ids = create_purchase_orders_from_items(
                order_date,
                notes,
                current_user["id"],
                material_ids,
                quantities,
            )
            ids_text = ", ".join(f"#{pid}" for pid in created_ids)
            return redirect(start_response, build_location("/purchase-orders", msg=f"Purchase order(s) created: {ids_text}"))
        except (ValueError, ValidationError) as exc:
            return redirect(start_response, build_location("/purchase-orders", err=str(exc)))

    if path == "/purchase-orders/export" and method == "GET":
        po_id_raw = first(params, "po_id", "0")
        try:
            po_id = int(po_id_raw)
        except ValueError:
            return make_response(start_response, "Invalid purchase order id", status="400 Bad Request", content_type="text/plain")

        po = get_purchase_order(po_id)
        if not po:
            return make_response(start_response, "Purchase order not found", status="404 Not Found", content_type="text/plain")

        file_path = generate_purchase_order_excel(po)
        payload = file_path.read_bytes()
        headers = [
            (
                "Content-Disposition",
                f"attachment; filename=purchase_order_{po_id}.xlsx",
            )
        ]
        return make_response(
            start_response,
            payload,
            headers=headers,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    page = layout("Not Found", "<h1>404 - Not Found</h1>", user=current_user)
    return make_response(start_response, page, status="404 Not Found")
