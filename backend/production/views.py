from __future__ import annotations

import csv
from datetime import date
from io import StringIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from accounts.permissions import PRODUCTION_MANAGE_ROLES, PRODUCTION_VIEW_ROLES, require_roles
from inventory.models import RawMaterial

from .exports import bom_to_excel, bom_to_pdf
from .forms import BOMCSVUploadForm, BOMItemForm, BOMItemUpdateForm, FinishedProductForm, ProductionOrderCreateForm, ProductionStatusForm
from .models import (
    BOMItem,
    FinishedProduct,
    FinishedStock,
    ProductionOrder,
    cancel_production_order,
    complete_production_order,
    create_production_order_with_rm_request,
)


BOM_CSV_COLUMNS = ["product_sku", "material_code", "qty_per_unit"]


def _read_csv_rows(csv_file):
    try:
        content = csv_file.read().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("CSV must be UTF-8 encoded.") from exc

    reader = csv.DictReader(StringIO(content))
    fieldnames = [name.strip() for name in (reader.fieldnames or []) if name]
    if not fieldnames:
        raise ValidationError("CSV file is empty or missing headers.")

    missing_headers = [column for column in BOM_CSV_COLUMNS if column not in fieldnames]
    if missing_headers:
        raise ValidationError(f"Missing required columns: {', '.join(missing_headers)}")

    rows = []
    for row in reader:
        normalized = {key.strip(): (value or "").strip() for key, value in row.items() if key}
        if not any(normalized.get(column, "") for column in BOM_CSV_COLUMNS):
            continue
        rows.append(normalized)
    return rows


def _import_bom_from_rows(rows: list[dict[str, str]]):
    if not rows:
        raise ValidationError("CSV has no data rows.")

    errors: list[str] = []
    pending_items: list[BOMItem] = []
    seen_pairs: set[tuple[int, int]] = set()

    for row_number, row in enumerate(rows, start=2):
        product_sku = row.get("product_sku", "").upper()
        material_code = row.get("material_code", "").upper()

        product = FinishedProduct.objects.filter(sku=product_sku).first()
        if not product:
            errors.append(f"Row {row_number}: product_sku '{product_sku}' not found.")
            continue

        material = RawMaterial.objects.filter(code=material_code).first()
        if not material:
            errors.append(f"Row {row_number}: material_code '{material_code}' not found.")
            continue

        row_form = BOMItemForm(
            data={
                "product": product.id,
                "material": material.id,
                "qty_per_unit": row.get("qty_per_unit", ""),
            }
        )
        if not row_form.is_valid():
            row_errors = []
            for field, field_errors in row_form.errors.items():
                if field == "__all__":
                    row_errors.extend(str(err) for err in field_errors)
                else:
                    row_errors.extend(f"{field}: {err}" for err in field_errors)
            errors.append(f"Row {row_number}: {'; '.join(row_errors)}")
            continue

        pair = (product.id, material.id)
        if pair in seen_pairs:
            errors.append(f"Row {row_number}: duplicate product/material pair in this CSV.")
            continue
        seen_pairs.add(pair)

        if BOMItem.objects.filter(product_id=product.id, material_id=material.id).exists():
            errors.append(f"Row {row_number}: mapping already exists for {product_sku} and {material_code}.")
            continue

        pending_items.append(
            BOMItem(
                product=product,
                material=material,
                qty_per_unit=row_form.cleaned_data["qty_per_unit"],
            )
        )

    if errors:
        raise ValidationError(errors)

    with transaction.atomic():
        BOMItem.objects.bulk_create(pending_items)
    return len(pending_items)


def _extract_bom_bulk_rows(post_data):
    product_values = post_data.getlist("bom_product")
    material_values = post_data.getlist("bom_material")
    qty_values = post_data.getlist("bom_qty")

    row_count = max(len(product_values), len(material_values), len(qty_values))
    rows: list[dict[str, str]] = []
    for index in range(row_count):
        product_value = (product_values[index] if index < len(product_values) else "").strip()
        material_value = (material_values[index] if index < len(material_values) else "").strip()
        qty_value = (qty_values[index] if index < len(qty_values) else "").strip()
        if not product_value and not material_value and not qty_value:
            continue
        rows.append(
            {
                "product": product_value,
                "material": material_value,
                "qty_per_unit": qty_value,
            }
        )
    return rows


def _redirect_products(open_bom_id: int | None = None):
    url = reverse("production:products")
    if open_bom_id:
        return redirect(f"{url}?open_bom={open_bom_id}")
    return redirect(url)


def _next_url_or_default(request) -> str:
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return reverse("production:orders")


def _parse_iso_date(raw_value: str | None) -> date | None:
    if not raw_value:
        return None
    try:
        return date.fromisoformat(raw_value)
    except ValueError:
        return None


def _deny_production_view(request, *, area: str):
    return require_roles(
        request,
        PRODUCTION_VIEW_ROLES,
        redirect_to="dashboard:home",
        area=area,
        action="access",
    )


@login_required
@require_http_methods(["GET"])
def bom_csv_template(request):
    denied = _deny_production_view(request, area="products and BOM")
    if denied:
        return denied

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(BOM_CSV_COLUMNS)
    writer.writerow(["FP-TOTE", "RM-CANVAS", "2.000"])
    response = HttpResponse(output.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="bom_upload_template.csv"'
    return response


@login_required
@require_http_methods(["GET", "POST"])
def product_bom_page(request):
    denied = _deny_production_view(request, area="products and BOM")
    if denied:
        return denied

    can_manage = request.user.role in PRODUCTION_MANAGE_ROLES
    action = request.POST.get("action") if request.method == "POST" else None
    product_form = FinishedProductForm(request.POST if action == "add_product" else None, prefix="prod")
    bom_form = BOMItemForm(request.POST if action == "add_bom" else None, prefix="bom")
    csv_form = BOMCSVUploadForm(request.POST if action == "upload_bom_csv" else None, request.FILES if action == "upload_bom_csv" else None)
    show_add_bom_modal = False
    show_upload_csv_modal = False
    bom_bulk_rows = [{"product": "", "material": "", "qty_per_unit": ""}]

    if request.method == "POST":
        denied = require_roles(
            request,
            PRODUCTION_MANAGE_ROLES,
            redirect_to="production:products",
            area="products and BOM",
        )
        if denied:
            return denied

        if action == "add_product" and product_form.is_valid():
            product_form.save()
            messages.success(request, "Finished product added.")
            return redirect("production:products")

        if action == "add_bom" and bom_form.is_valid():
            try:
                bom_form.save()
                messages.success(request, "BOM item added.")
                return _redirect_products(bom_form.cleaned_data["product"].id)
            except IntegrityError:
                bom_form.add_error(None, "This BOM mapping already exists.")

        if action == "add_bom_bulk":
            bom_bulk_rows = _extract_bom_bulk_rows(request.POST)
            if not bom_bulk_rows:
                messages.error(request, "Add at least one BOM row before saving.")
                show_add_bom_modal = True
            else:
                items_to_create: list[BOMItem] = []
                row_errors: list[str] = []
                seen_pairs: set[tuple[int, int]] = set()
                field_labels = {
                    "product": "product",
                    "material": "material",
                    "qty_per_unit": "quantity",
                }

                for row_index, row_data in enumerate(bom_bulk_rows, start=1):
                    row_form = BOMItemForm(data=row_data)
                    if not row_form.is_valid():
                        errors_for_row: list[str] = []
                        for field_name, field_errors in row_form.errors.items():
                            if field_name == "__all__":
                                errors_for_row.extend(str(err) for err in field_errors)
                                continue
                            field_label = field_labels.get(field_name, field_name)
                            errors_for_row.extend(f"{field_label}: {err}" for err in field_errors)
                        row_errors.append(f"Row {row_index}: {'; '.join(errors_for_row)}")
                        continue

                    product = row_form.cleaned_data["product"]
                    material = row_form.cleaned_data["material"]
                    qty_per_unit = row_form.cleaned_data["qty_per_unit"]
                    pair = (product.id, material.id)

                    if pair in seen_pairs:
                        row_errors.append(f"Row {row_index}: duplicate product/material pair in this submission.")
                        continue
                    seen_pairs.add(pair)

                    if BOMItem.objects.filter(product_id=product.id, material_id=material.id).exists():
                        row_errors.append(
                            f"Row {row_index}: mapping already exists for {product.name} and {material.name}."
                        )
                        continue

                    items_to_create.append(BOMItem(product=product, material=material, qty_per_unit=qty_per_unit))

                if row_errors:
                    for row_error in row_errors[:8]:
                        messages.error(request, row_error)
                    if len(row_errors) > 8:
                        messages.error(request, f"...and {len(row_errors) - 8} more row errors.")
                    show_add_bom_modal = True
                else:
                    try:
                        with transaction.atomic():
                            BOMItem.objects.bulk_create(items_to_create)
                        messages.success(request, f"{len(items_to_create)} BOM item(s) added.")
                        first_product_id = items_to_create[0].product_id if items_to_create else None
                        return _redirect_products(first_product_id)
                    except IntegrityError:
                        messages.error(request, "Some BOM mappings already exist. Please refresh and try again.")
                        show_add_bom_modal = True

        if action == "upload_bom_csv":
            if csv_form.is_valid():
                try:
                    rows = _read_csv_rows(csv_form.cleaned_data["csv_file"])
                    imported_count = _import_bom_from_rows(rows)
                    messages.success(request, f"BOM CSV imported. Created: {imported_count}.")
                    return redirect("production:products")
                except ValidationError as exc:
                    errors = exc.messages if hasattr(exc, "messages") else [str(exc)]
                    for error in errors[:8]:
                        messages.error(request, error)
                    if len(errors) > 8:
                        messages.error(request, f"...and {len(errors) - 8} more row errors.")
            else:
                for error in csv_form.errors.get("csv_file", []):
                    messages.error(request, f"CSV: {error}")
            show_upload_csv_modal = True

    open_bom_raw = (request.GET.get("open_bom") or "").strip()
    open_bom_id = int(open_bom_raw) if open_bom_raw.isdigit() else None
    products = list(FinishedProduct.objects.prefetch_related("bom_items__material").order_by("name"))
    materials = list(RawMaterial.objects.order_by("name"))
    product_choices = [{"id": product.id, "label": f"{product.name} ({product.sku})"} for product in products]
    product_material_map: dict[str, list[dict[str, int | str]]] = {}
    for product in products:
        mapped_material_ids = {item.material_id for item in product.bom_items.all()}
        product_material_map[str(product.id)] = [
            {"id": material.id, "label": f"{material.name} ({material.code})"}
            for material in materials
            if material.id not in mapped_material_ids
        ]

    context = {
        "can_manage": can_manage,
        "product_form": product_form,
        "bom_form": bom_form,
        "csv_form": csv_form,
        "products": products,
        "bom_material_choices": materials,
        "product_choices": product_choices,
        "product_material_map": product_material_map,
        "open_bom_id": open_bom_id,
        "show_add_bom_modal": show_add_bom_modal,
        "show_upload_csv_modal": show_upload_csv_modal,
        "bom_bulk_rows": bom_bulk_rows,
    }
    return render(request, "production/products.html", context)


@login_required
@require_http_methods(["POST"])
def update_bom_item(request, bom_id: int):
    denied = require_roles(
        request,
        PRODUCTION_MANAGE_ROLES,
        redirect_to="production:products",
        area="products and BOM",
    )
    if denied:
        return denied

    bom_item = get_object_or_404(BOMItem.objects.select_related("product"), pk=bom_id)
    open_bom_id = bom_item.product_id
    form = BOMItemUpdateForm(request.POST, instance=bom_item)
    if form.is_valid():
        try:
            form.save()
            messages.success(request, "BOM item updated.")
        except IntegrityError:
            messages.error(request, "This BOM mapping already exists for the selected product.")
    else:
        messages.error(request, "Invalid BOM item input.")
    return _redirect_products(open_bom_id)


@login_required
@require_http_methods(["POST"])
def delete_bom_item(request, bom_id: int):
    denied = require_roles(
        request,
        PRODUCTION_MANAGE_ROLES,
        redirect_to="production:products",
        area="products and BOM",
    )
    if denied:
        return denied

    bom_item = get_object_or_404(BOMItem.objects.only("id", "product_id"), pk=bom_id)
    open_bom_id = bom_item.product_id
    bom_item.delete()
    messages.success(request, "BOM item deleted.")
    return _redirect_products(open_bom_id)


@login_required
@require_http_methods(["POST"])
def delete_finished_product(request, product_id: int):
    denied = require_roles(
        request,
        PRODUCTION_MANAGE_ROLES,
        redirect_to="production:products",
        area="products and BOM",
    )
    if denied:
        return denied

    product = get_object_or_404(FinishedProduct.objects.only("id", "name", "sku"), pk=product_id)
    product_label = f"{product.name} ({product.sku})"
    allowed_terminal_statuses = [
        ProductionOrder.Status.CANCELLED,
        ProductionOrder.Status.COMPLETED,
    ]
    try:
        with transaction.atomic():
            product_orders = ProductionOrder.objects.select_for_update().filter(product_id=product.id)
            blocking_orders = product_orders.exclude(status__in=allowed_terminal_statuses)
            if blocking_orders.exists():
                messages.error(
                    request,
                    "Finished product cannot be deleted while it has active production orders. "
                    "Only products with cancelled/completed orders can be deleted.",
                )
                return redirect("production:products")

            deletable_order_count = product_orders.count()
            if deletable_order_count:
                product_orders.delete()
            product.delete()

        if deletable_order_count:
            messages.success(
                request,
                f"Finished product {product_label} deleted. Removed {deletable_order_count} cancelled/completed production order(s).",
            )
        else:
            messages.success(request, f"Finished product {product_label} deleted.")
    except ProtectedError as exc:
        protected_labels = sorted({obj._meta.verbose_name for obj in exc.protected_objects})
        linked_text = f" Linked records: {', '.join(protected_labels)}." if protected_labels else ""
        messages.error(
            request,
            f"Finished product cannot be deleted because it is linked to existing records.{linked_text}",
        )
    return redirect("production:products")


@login_required
@require_http_methods(["GET"])
def export_product_bom_excel(request, product_id: int):
    denied = _deny_production_view(request, area="products and BOM")
    if denied:
        return denied

    product = get_object_or_404(FinishedProduct.objects.prefetch_related("bom_items__material"), pk=product_id)
    payload = bom_to_excel(product)
    response = HttpResponse(
        payload,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="bom_{product.id}.xlsx"'
    return response


@login_required
@require_http_methods(["GET"])
def export_product_bom_pdf(request, product_id: int):
    denied = _deny_production_view(request, area="products and BOM")
    if denied:
        return denied

    product = get_object_or_404(FinishedProduct.objects.prefetch_related("bom_items__material"), pk=product_id)
    payload = bom_to_pdf(product)
    response = HttpResponse(payload, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="bom_{product.id}.pdf"'
    return response


@login_required
@require_http_methods(["GET", "POST"])
def production_orders_page(request):
    denied = _deny_production_view(request, area="production orders")
    if denied:
        return denied

    can_manage = request.user.role in PRODUCTION_MANAGE_ROLES
    create_form = ProductionOrderCreateForm(request.POST or None)

    if request.method == "POST":
        denied = require_roles(
            request,
            PRODUCTION_MANAGE_ROLES,
            redirect_to="production:orders",
            area="production orders",
        )
        if denied:
            return denied

        if create_form.is_valid():
            try:
                order = create_production_order_with_rm_request(
                    product=create_form.cleaned_data["product"],
                    quantity=create_form.cleaned_data["quantity"],
                    notes=create_form.cleaned_data["notes"],
                    created_by=request.user,
                )
                messages.success(
                    request,
                    f"Production order #{order.id} created. Status: Awaiting RM Release.",
                )
                return redirect(_next_url_or_default(request))
            except ValidationError as exc:
                create_form.add_error(None, str(exc))

    orders = ProductionOrder.objects.select_related("product", "created_by")
    status_filter = request.GET.get("status", "").strip()
    product_filter = request.GET.get("product", "").strip()
    q_filter = request.GET.get("q", "").strip()
    date_from = _parse_iso_date(request.GET.get("date_from"))
    date_to = _parse_iso_date(request.GET.get("date_to"))

    valid_statuses = {value for value, _label in ProductionOrder.Status.choices}
    if status_filter in valid_statuses:
        orders = orders.filter(status=status_filter)
    if product_filter.isdigit():
        orders = orders.filter(product_id=int(product_filter))
    if date_from:
        orders = orders.filter(created_at__date__gte=date_from)
    if date_to:
        orders = orders.filter(created_at__date__lte=date_to)
    if q_filter:
        query = Q(product__name__icontains=q_filter) | Q(notes__icontains=q_filter) | Q(created_by__username__icontains=q_filter)
        if q_filter.isdigit():
            query |= Q(id=int(q_filter))
        orders = orders.filter(query)

    orders = orders.order_by("-id")
    paginator = Paginator(orders, 20)
    page_obj = paginator.get_page(request.GET.get("page"))
    status_form = ProductionStatusForm()
    kpi_open_orders = ProductionOrder.objects.filter(
        status__in=[
            ProductionOrder.Status.AWAITING_RM_RELEASE,
            ProductionOrder.Status.PLANNED,
            ProductionOrder.Status.IN_PROGRESS,
        ]
    ).count()
    kpi_completed_orders = ProductionOrder.objects.filter(status=ProductionOrder.Status.COMPLETED).count()
    kpi_total_scrap = (
        ProductionOrder.objects.filter(status=ProductionOrder.Status.COMPLETED).aggregate(total=Sum("scrap_qty"))["total"]
        or 0
    )
    kpi_finished_stock = FinishedStock.objects.aggregate(total=Sum("current_stock"))["total"] or 0
    context = {
        "can_manage": can_manage,
        "create_form": create_form,
        "orders": page_obj.object_list,
        "page_obj": page_obj,
        "status_form": status_form,
        "status_choices": ProductionOrder.Status.choices,
        "product_choices": FinishedProduct.objects.order_by("name"),
        "filter_values": {
            "status": status_filter,
            "product": product_filter,
            "q": q_filter,
            "date_from": request.GET.get("date_from", ""),
            "date_to": request.GET.get("date_to", ""),
        },
        "kpi_open_orders": kpi_open_orders,
        "kpi_completed_orders": kpi_completed_orders,
        "kpi_total_scrap": kpi_total_scrap,
        "kpi_finished_stock": kpi_finished_stock,
        "next_url": request.get_full_path(),
    }
    return render(request, "production/orders.html", context)


@login_required
@require_http_methods(["POST"])
def update_production_status(request):
    denied = require_roles(
        request,
        PRODUCTION_MANAGE_ROLES,
        redirect_to="production:orders",
        area="production orders",
    )
    if denied:
        return denied

    form = ProductionStatusForm(request.POST)
    if not form.is_valid():
        first_error = next(iter(form.errors.values())) if form.errors else None
        message = first_error[0] if first_error else "Invalid status update input."
        messages.error(request, message)
        return redirect(_next_url_or_default(request))

    order = get_object_or_404(ProductionOrder, pk=form.cleaned_data["order_id"])
    if order.status == ProductionOrder.Status.AWAITING_RM_RELEASE:
        messages.error(
            request,
            "This production order is awaiting raw material release from inventory. Status update is blocked.",
        )
        return redirect(_next_url_or_default(request))
    if order.status == ProductionOrder.Status.CANCELLED:
        messages.error(request, "Cancelled production order cannot be updated.")
        return redirect(_next_url_or_default(request))
    if order.status == ProductionOrder.Status.COMPLETED:
        messages.error(request, "Completed production order cannot be updated.")
        return redirect(_next_url_or_default(request))
    if form.cleaned_data["status"] == ProductionOrder.Status.CANCELLED:
        messages.error(request, "Use Cancel / Reject action to cancel an order.")
        return redirect(_next_url_or_default(request))

    next_status = form.cleaned_data["status"]
    if next_status == ProductionOrder.Status.AWAITING_RM_RELEASE:
        messages.error(request, "Awaiting RM Release is system managed and cannot be set manually.")
        return redirect(_next_url_or_default(request))
    if next_status == ProductionOrder.Status.IN_PROGRESS and not order.raw_material_released:
        messages.error(
            request,
            "Raw materials are not released yet. Inventory must release materials before moving to In Progress.",
        )
        return redirect(_next_url_or_default(request))
    if next_status == ProductionOrder.Status.COMPLETED:
        try:
            completed = complete_production_order(
                production_order=order,
                produced_qty=form.cleaned_data["produced_qty"],
                scrap_qty=form.cleaned_data["scrap_qty"],
                completed_by=request.user,
            )
            messages.success(
                request,
                f"Production order #{completed.id} completed. "
                f"Produced: {completed.produced_qty}, Scrap: {completed.scrap_qty}, Variance: {completed.variance_qty}.",
            )
        except ValidationError as exc:
            message = exc.messages[0] if exc.messages else "Unable to complete production order."
            messages.error(request, message)
        return redirect(_next_url_or_default(request))

    order.status = next_status
    order.save(update_fields=["status"])
    messages.success(request, "Production order status updated.")
    return redirect(_next_url_or_default(request))


@login_required
@require_http_methods(["POST"])
def cancel_production_order_action(request, order_id: int):
    denied = require_roles(
        request,
        PRODUCTION_MANAGE_ROLES,
        redirect_to="production:orders",
        area="production orders",
    )
    if denied:
        return denied

    order = get_object_or_404(ProductionOrder, pk=order_id)
    try:
        cancel_production_order(production_order=order, cancelled_by=request.user)
        messages.success(request, f"Production order #{order.id} cancelled.")
    except ValidationError as exc:
        messages.error(request, str(exc))
    return redirect(_next_url_or_default(request))
