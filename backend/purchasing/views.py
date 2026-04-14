from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from datetime import date

logger = logging.getLogger(__name__)

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from accounts.models import User
from accounts.permissions import PURCHASING_MANAGE_ROLES, PURCHASING_VIEW_ROLES, require_roles, verify_action_password
from inventory.models import RawMaterial
from partners.models import Partner

from .exports import purchase_order_to_excel, purchase_order_to_pdf
from .forms import PurchaseLineForm, PurchaseOrderCreateForm, parse_purchase_lines, parse_receive_quantities
from .models import (
    PurchaseLineInput,
    PurchaseOrder,
    approve_purchase_order_admin,
    approve_purchase_order_inventory,
    cancel_purchase_order,
    create_grouped_purchase_orders_with_vendor,
    receive_purchase_order,
    reopen_purchase_order,
)


def _can_manage_purchase_orders(user) -> bool:
    return user.role in PURCHASING_MANAGE_ROLES


def _can_inventory_approve_po(user) -> bool:
    return user.role == User.Role.INVENTORY_MANAGER


def _can_admin_approve_po(user) -> bool:
    return user.role == User.Role.ADMIN


def _deny_purchasing_view(request):
    return require_roles(
        request,
        PURCHASING_VIEW_ROLES,
        redirect_to="dashboard:home",
        area="purchase orders",
        action="access",
    )


def _next_url_or_default(request) -> str:
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return reverse("purchasing:list")


def _parse_iso_date(raw_value: str | None) -> date | None:
    if not raw_value:
        return None
    try:
        return date.fromisoformat(raw_value)
    except ValueError:
        logger.warning("Invalid date filter value ignored: %s", raw_value)
        return None


def _resolve_vendor(vendor_id: str | None):
    if not vendor_id or not vendor_id.isdigit():
        return None
    return (
        Partner.objects.filter(
            id=int(vendor_id),
            partner_type__in=[Partner.PartnerType.SUPPLIER, Partner.PartnerType.BOTH],
        )
        .order_by("name")
        .first()
    )


def _extract_po_line_rows(payload) -> list[dict[str, str]]:
    material_values = payload.getlist("material")
    quantity_values = payload.getlist("quantity")

    row_count = max(len(material_values), len(quantity_values))
    rows: list[dict[str, str]] = []
    for index in range(row_count):
        material_value = (material_values[index] if index < len(material_values) else "").strip()
        quantity_value = (quantity_values[index] if index < len(quantity_values) else "").strip()
        if not material_value and not quantity_value:
            continue
        rows.append({"material": material_value, "quantity": quantity_value})
    return rows


def _build_vendor_material_map() -> dict[str, list[dict[str, object]]]:
    mapping: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
    materials = (
        RawMaterial.objects.select_related("vendor")
        .prefetch_related("vendor_links")
        .order_by("name", "rm_id", "colour_code", "id")
    )
    for material in materials:
        vendor_ids = {material.vendor_id}
        vendor_ids.update(link.vendor_id for link in material.vendor_links.all())

        rm_identifier = (material.rm_id or "").strip().upper() or f"RM-{material.id}"
        colour_name = (material.colour or "").strip()
        colour_code = (material.colour_code or "").strip().upper()
        if colour_name and colour_code:
            colour_label = f"{colour_name} ({colour_code})"
        elif colour_name:
            colour_label = colour_name
        elif colour_code:
            colour_label = colour_code
        else:
            colour_label = "Default"

        variant_payload = {
            "id": material.id,
            "label": colour_label,
            "colour": colour_name,
            "colour_code": colour_code,
            "unit": material.unit,
        }

        for vendor_id in vendor_ids:
            vendor_key = str(vendor_id)
            vendor_groups = mapping[vendor_key]
            group = vendor_groups.get(rm_identifier)
            if not group:
                group = {
                    "group_key": rm_identifier,
                    "label": f"{material.name} ({rm_identifier})",
                    "variants": [],
                }
                vendor_groups[rm_identifier] = group
            group["variants"].append(variant_payload)

    resolved_mapping: dict[str, list[dict[str, object]]] = {}
    for vendor_key, groups in mapping.items():
        group_list = list(groups.values())
        for group in group_list:
            variants = group["variants"]
            variants.sort(key=lambda item: (str(item["label"]), int(item["id"])))
        group_list.sort(key=lambda item: str(item["label"]))
        resolved_mapping[vendor_key] = group_list
    return resolved_mapping


def _build_low_stock_vendor_groups() -> list[dict[str, object]]:
    low_stock_materials = (
        RawMaterial.objects.select_related("vendor")
        .prefetch_related("vendor_links__vendor")
        .filter(current_stock__lte=F("reorder_level"))
        .order_by("vendor__name", "name", "rm_id", "colour_code", "id")
    )

    grouped: dict[int, dict[str, object]] = {}
    for material in low_stock_materials:
        vendor_map: dict[int, Partner] = {material.vendor_id: material.vendor}
        for link in material.vendor_links.all():
            vendor_map[link.vendor_id] = link.vendor

        vendor_options = [
            {"id": partner.id, "name": partner.name}
            for partner in sorted(vendor_map.values(), key=lambda item: (item.name.lower(), item.id))
        ]
        shortage = material.reorder_level - material.current_stock
        suggested_qty = shortage if shortage > 0 else Decimal("0.001")

        group = grouped.setdefault(
            material.vendor_id,
            {
                "vendor": material.vendor,
                "rows": [],
            },
        )
        group["rows"].append(
            {
                "material": material,
                "vendor_options": vendor_options,
                "vendor_option_ids": [option["id"] for option in vendor_options],
                "suggested_qty": suggested_qty,
            }
        )

    group_list = list(grouped.values())
    for group in group_list:
        group["rows"].sort(
            key=lambda row: (
                str(row["material"].name).lower(),
                str(row["material"].rm_id).lower(),
                str(row["material"].colour_code).lower(),
            )
        )
        vendor_name_map: dict[int, str] = {}
        common_vendor_ids: set[int] | None = None
        for row in group["rows"]:
            row_vendor_ids = set(row.pop("vendor_option_ids", []))
            for option in row["vendor_options"]:
                vendor_name_map[option["id"]] = option["name"]
            common_vendor_ids = row_vendor_ids if common_vendor_ids is None else (common_vendor_ids & row_vendor_ids)
        common_vendor_ids = common_vendor_ids or {group["vendor"].id}
        batch_vendor_options = [
            {"id": vendor_id, "name": vendor_name_map.get(vendor_id, f"Vendor {vendor_id}")}
            for vendor_id in sorted(common_vendor_ids, key=lambda item: (vendor_name_map.get(item, "").lower(), item))
        ]
        group["batch_vendor_options"] = batch_vendor_options
        group["default_batch_vendor_id"] = (
            group["vendor"].id if group["vendor"].id in common_vendor_ids else batch_vendor_options[0]["id"]
        )
        group["batch_form_id"] = f"low-stock-vendor-po-{group['vendor'].id}"
    group_list.sort(key=lambda group: (str(group["vendor"].name).lower(), group["vendor"].id))
    return group_list


def _vendor_allowed_for_material(*, material: RawMaterial, vendor_id: int) -> bool:
    if material.vendor_id == vendor_id:
        return True
    return any(link.vendor_id == vendor_id for link in material.vendor_links.all())


@login_required
@require_http_methods(["GET", "POST"])
def purchase_order_page(request):
    denied = _deny_purchasing_view(request)
    if denied:
        return denied

    can_manage = _can_manage_purchase_orders(request.user)

    selected_vendor = None
    if request.method == "POST":
        form = PurchaseOrderCreateForm(request.POST)
        selected_vendor = _resolve_vendor(request.POST.get("vendor"))
    else:
        selected_vendor = _resolve_vendor(request.GET.get("create_vendor"))
        form = PurchaseOrderCreateForm(
            initial={
                "vendor": selected_vendor.id if selected_vendor else None,
                "order_date": date.today(),
            }
        )

    if request.method == "POST":
        denied = require_roles(
            request,
            PURCHASING_MANAGE_ROLES,
            redirect_to="purchasing:list",
            area="purchase orders",
        )
        if denied:
            return denied

        material_ids = request.POST.getlist("material")
        quantities = request.POST.getlist("quantity")
        try:
            if not selected_vendor:
                raise ValidationError("Select a vendor before adding line items.")
            lines = parse_purchase_lines(material_ids, quantities, vendor=selected_vendor)
        except (ValidationError, ValueError) as exc:
            form.add_error(None, str(exc))
            lines = []

        if form.is_valid() and lines and selected_vendor:
            created = create_grouped_purchase_orders_with_vendor(
                order_date=form.cleaned_data["order_date"],
                notes=form.cleaned_data["notes"],
                payment_pdc_days=form.cleaned_data["payment_pdc_days"],
                delivery_terms=form.cleaned_data["delivery_terms"],
                freight_terms=form.cleaned_data["freight_terms"],
                packaging_ident_terms=form.cleaned_data["packaging_ident_terms"],
                inspection_report_terms=form.cleaned_data["inspection_report_terms"],
                packing_terms=form.cleaned_data["packing_terms"],
                created_by=request.user,
                lines=lines,
                vendor=selected_vendor,
            )
            po_ids = ", ".join(f"#{order.id}" for order in created)
            logger.info("Purchase order(s) created: %s by user=%s", po_ids, request.user.username)
            messages.success(request, f"Purchase order(s) created: {po_ids}")
            return redirect("purchasing:list")

    all_orders = (
        PurchaseOrder.objects.select_related("vendor", "created_by", "received_by", "cancelled_by")
        .prefetch_related("items__material")
    )
    pending_orders = (
        all_orders.select_related(
            "inventory_approved_by",
            "admin_approved_by",
        )
        .filter(Q(inventory_approved_at__isnull=True) | Q(admin_approved_at__isnull=True))
        .order_by("-id")
    )
    approved_orders = all_orders.filter(
        inventory_approved_at__isnull=False,
        admin_approved_at__isnull=False,
    )

    status_filter = request.GET.get("status", "").strip()
    vendor_filter = request.GET.get("vendor", "").strip()
    q_filter = request.GET.get("q", "").strip()
    date_from = _parse_iso_date(request.GET.get("date_from"))
    date_to = _parse_iso_date(request.GET.get("date_to"))

    valid_statuses = {value for value, _label in PurchaseOrder.Status.choices}
    if status_filter in valid_statuses:
        approved_orders = approved_orders.filter(status=status_filter)
    if vendor_filter.isdigit():
        approved_orders = approved_orders.filter(vendor_id=int(vendor_filter))
    if date_from:
        approved_orders = approved_orders.filter(order_date__gte=date_from)
    if date_to:
        approved_orders = approved_orders.filter(order_date__lte=date_to)
    if q_filter:
        query = Q(vendor__name__icontains=q_filter) | Q(notes__icontains=q_filter) | Q(items__material__name__icontains=q_filter)
        if q_filter.isdigit():
            query |= Q(id=int(q_filter))
        approved_orders = approved_orders.filter(query).distinct()

    approved_orders = approved_orders.order_by("-id")
    paginator = Paginator(approved_orders, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    line_form = PurchaseLineForm(vendor=selected_vendor)
    vendors = (
        Partner.objects.filter(
            purchase_orders__inventory_approved_at__isnull=False,
            purchase_orders__admin_approved_at__isnull=False,
        )
        .order_by("name")
        .distinct()
    )
    line_rows_seed = (
        _extract_po_line_rows(request.POST) if request.method == "POST" else [{"material": "", "quantity": ""}]
    )
    if not line_rows_seed:
        line_rows_seed = [{"material": "", "quantity": ""}]

    show_create_po_terms_modal = request.method == "POST" and bool(
        form.errors.get("payment_pdc_days")
        or form.errors.get("delivery_terms")
        or form.errors.get("freight_terms")
        or form.errors.get("packaging_ident_terms")
        or form.errors.get("inspection_report_terms")
        or form.errors.get("packing_terms")
    )

    context = {
        "can_manage": can_manage,
        "can_inventory_approve_po": _can_inventory_approve_po(request.user),
        "can_admin_approve_po": _can_admin_approve_po(request.user),
        "form": form,
        "show_create_po_terms_modal": show_create_po_terms_modal,
        "line_form": line_form,
        "selected_create_vendor": selected_vendor,
        "low_stock_vendor_groups": _build_low_stock_vendor_groups() if can_manage else [],
        "pending_approval_orders": pending_orders[:100],
        "orders": page_obj.object_list,
        "page_obj": page_obj,
        "vendors": vendors,
        "status_choices": PurchaseOrder.Status.choices,
        "vendor_material_map": _build_vendor_material_map(),
        "line_rows_seed": line_rows_seed,
        "next_url": request.get_full_path(),
        "filter_values": {
            "status": status_filter,
            "vendor": vendor_filter,
            "q": q_filter,
            "date_from": request.GET.get("date_from", ""),
            "date_to": request.GET.get("date_to", ""),
        },
    }
    return render(request, "purchasing/purchase_orders.html", context)


@login_required
@require_http_methods(["POST"])
def create_low_stock_vendor_purchase_order_pdf(request):
    denied = _deny_purchasing_view(request)
    if denied:
        return denied

    denied = require_roles(
        request,
        PURCHASING_MANAGE_ROLES,
        redirect_to="purchasing:list",
        area="purchase orders",
    )
    if denied:
        return denied

    vendor_id_raw = (request.POST.get("vendor_id") or "").strip()
    if not vendor_id_raw.isdigit():
        messages.error(request, "Select a valid vendor for batch purchase order creation.")
        return redirect(_next_url_or_default(request))

    vendor = (
        Partner.objects.filter(
            id=int(vendor_id_raw),
            partner_type__in=[Partner.PartnerType.SUPPLIER, Partner.PartnerType.BOTH],
        )
        .order_by("name")
        .first()
    )
    if not vendor:
        messages.error(request, "Selected vendor is not valid for purchase orders.")
        return redirect(_next_url_or_default(request))

    material_values = request.POST.getlist("material_id")
    quantity_values = request.POST.getlist("quantity")
    row_count = max(len(material_values), len(quantity_values))
    rows: list[tuple[str, str]] = []
    for index in range(row_count):
        material_value = (material_values[index] if index < len(material_values) else "").strip()
        quantity_value = (quantity_values[index] if index < len(quantity_values) else "").strip()
        if not material_value and not quantity_value:
            continue
        rows.append((material_value, quantity_value))

    if not rows:
        messages.error(request, "No low-stock rows were submitted for batch purchase order creation.")
        return redirect(_next_url_or_default(request))

    material_ids: list[int] = []
    for material_value, _quantity_value in rows:
        if not material_value.isdigit():
            messages.error(request, "Invalid low-stock raw material in batch payload.")
            return redirect(_next_url_or_default(request))
        material_ids.append(int(material_value))

    materials = {
        material.id: material
        for material in (
            RawMaterial.objects.select_related("vendor")
            .prefetch_related("vendor_links__vendor")
            .filter(id__in=material_ids)
        )
    }

    lines: list[PurchaseLineInput] = []
    for material_value, quantity_value in rows:
        if not material_value or not quantity_value:
            messages.error(request, "Each low-stock row requires a quantity.")
            return redirect(_next_url_or_default(request))

        material = materials.get(int(material_value))
        if not material:
            messages.error(request, "One or more low-stock materials are no longer available.")
            return redirect(_next_url_or_default(request))
        if material.current_stock > material.reorder_level:
            messages.error(request, f"{material.name} is no longer in low-stock state.")
            return redirect(_next_url_or_default(request))
        if not _vendor_allowed_for_material(material=material, vendor_id=vendor.id):
            messages.error(request, f"{vendor.name} does not supply {material.name}.")
            return redirect(_next_url_or_default(request))

        try:
            quantity = Decimal(quantity_value)
        except (InvalidOperation, TypeError):
            messages.error(request, f"Enter a valid quantity for {material.name}.")
            return redirect(_next_url_or_default(request))
        if quantity <= 0:
            messages.error(request, f"Quantity for {material.name} must be greater than zero.")
            return redirect(_next_url_or_default(request))

        lines.append(PurchaseLineInput(material=material, quantity=quantity))

    if not lines:
        messages.error(request, "Add at least one valid low-stock line for batch purchase order creation.")
        return redirect(_next_url_or_default(request))

    try:
        created_orders = create_grouped_purchase_orders_with_vendor(
            order_date=date.today(),
            notes=f"Low stock batch restock for {vendor.name}",
            created_by=request.user,
            lines=lines,
            vendor=vendor,
        )
    except ValidationError as exc:
        error = exc.messages[0] if exc.messages else "Unable to create purchase order."
        messages.error(request, error)
        return redirect(_next_url_or_default(request))

    created_order = created_orders[0]
    payload = purchase_order_to_pdf(created_order)
    response = HttpResponse(payload, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="purchase_order_{created_order.id}.pdf"'
    return response


@login_required
@require_http_methods(["POST"])
def approve_purchase_order_inventory_action(request, po_id: int):
    denied = require_roles(
        request,
        PURCHASING_MANAGE_ROLES,
        redirect_to="purchasing:list",
        area="purchase orders",
    )
    if denied:
        return denied
    if not _can_inventory_approve_po(request.user):
        messages.error(request, "Only inventory manager can provide inventory approval.")
        return redirect(_next_url_or_default(request))
    if not verify_action_password(request, action_label="approve this purchase order"):
        return redirect(_next_url_or_default(request))

    po = get_object_or_404(PurchaseOrder, pk=po_id)
    try:
        approved = approve_purchase_order_inventory(purchase_order=po, approved_by=request.user)
        logger.info("PO #%d inventory approved by user=%s", approved.id, request.user.username)
        if approved.is_fully_approved:
            messages.success(request, f"Purchase order #{approved.id} fully approved and moved to final list.")
        else:
            messages.success(request, f"Inventory approval recorded for purchase order #{approved.id}.")
    except ValidationError as exc:
        error = exc.messages[0] if exc.messages else "Unable to approve purchase order."
        messages.error(request, error)
    return redirect(_next_url_or_default(request))


@login_required
@require_http_methods(["POST"])
def approve_purchase_order_admin_action(request, po_id: int):
    denied = require_roles(
        request,
        PURCHASING_MANAGE_ROLES,
        redirect_to="purchasing:list",
        area="purchase orders",
    )
    if denied:
        return denied
    if not _can_admin_approve_po(request.user):
        messages.error(request, "Only admin can provide admin approval.")
        return redirect(_next_url_or_default(request))
    if not verify_action_password(request, action_label="approve this purchase order"):
        return redirect(_next_url_or_default(request))

    po = get_object_or_404(PurchaseOrder, pk=po_id)
    try:
        approved = approve_purchase_order_admin(purchase_order=po, approved_by=request.user)
        logger.info("PO #%d admin approved by user=%s", approved.id, request.user.username)
        if approved.is_fully_approved:
            messages.success(request, f"Purchase order #{approved.id} fully approved and moved to final list.")
        else:
            messages.success(request, f"Admin approval recorded for purchase order #{approved.id}.")
    except ValidationError as exc:
        error = exc.messages[0] if exc.messages else "Unable to approve purchase order."
        messages.error(request, error)
    return redirect(_next_url_or_default(request))


@login_required
@require_http_methods(["POST"])
def delete_pending_purchase_order_action(request, po_id: int):
    denied = require_roles(
        request,
        PURCHASING_MANAGE_ROLES,
        redirect_to="purchasing:list",
        area="purchase orders",
    )
    if denied:
        return denied
    if not _can_admin_approve_po(request.user):
        messages.error(request, "Only admin can delete purchase orders pending approvals.")
        return redirect(_next_url_or_default(request))

    po = get_object_or_404(PurchaseOrder, pk=po_id)
    if po.is_fully_approved:
        messages.error(request, f"Purchase order #{po.id} is already in final list and cannot be deleted here.")
        return redirect(_next_url_or_default(request))

    deleted_id = po.id
    po.delete()
    logger.info("PO #%d deleted from pending approvals by user=%s", deleted_id, request.user.username)
    messages.success(request, f"Purchase order #{deleted_id} deleted from pending approvals.")
    return redirect(_next_url_or_default(request))


@login_required
@require_http_methods(["GET", "POST"])
def receive_purchase_order_page(request, po_id: int):
    denied = require_roles(
        request,
        PURCHASING_MANAGE_ROLES,
        redirect_to="purchasing:list",
        area="purchase orders",
    )
    if denied:
        return denied

    po = get_object_or_404(PurchaseOrder.objects.prefetch_related("items__material"), pk=po_id)
    if not po.is_fully_approved:
        messages.error(request, f"Purchase order #{po.id} is pending approvals and cannot be received yet.")
        return redirect(_next_url_or_default(request))
    items = list(po.items.select_related("material"))

    if request.method == "POST":
        try:
            quantities = parse_receive_quantities(items, request.POST)
            updated = receive_purchase_order(
                purchase_order=po,
                received_by=request.user,
                line_quantities=quantities,
            )
            logger.info("PO #%d received (status=%s) by user=%s", updated.id, updated.status, request.user.username)
            messages.success(request, f"Purchase order #{updated.id} updated to {updated.get_status_display()}.")
            return redirect(_next_url_or_default(request))
        except ValidationError as exc:
            error = exc.messages[0] if exc.messages else "Unable to receive purchase order."
            messages.error(request, error)
            po.refresh_from_db()
            items = list(po.items.select_related("material"))

    context = {
        "po": po,
        "items": items,
        "next_url": _next_url_or_default(request),
    }
    return render(request, "purchasing/receive_purchase_order.html", context)


@login_required
@require_http_methods(["POST"])
def cancel_purchase_order_action(request, po_id: int):
    denied = require_roles(
        request,
        PURCHASING_MANAGE_ROLES,
        redirect_to="purchasing:list",
        area="purchase orders",
    )
    if denied:
        return denied

    po = get_object_or_404(PurchaseOrder, pk=po_id)
    if not po.is_fully_approved:
        messages.error(request, f"Purchase order #{po.id} is pending approvals and cannot be cancelled from final list.")
        return redirect(_next_url_or_default(request))
    try:
        cancel_purchase_order(purchase_order=po, cancelled_by=request.user)
        logger.info("PO #%d cancelled by user=%s", po.id, request.user.username)
        messages.success(request, f"Purchase order #{po.id} cancelled.")
    except ValidationError as exc:
        error = exc.messages[0] if exc.messages else "Unable to cancel purchase order."
        messages.error(request, error)
    return redirect(_next_url_or_default(request))


@login_required
@require_http_methods(["POST"])
def reopen_purchase_order_action(request, po_id: int):
    denied = require_roles(
        request,
        PURCHASING_MANAGE_ROLES,
        redirect_to="purchasing:list",
        area="purchase orders",
    )
    if denied:
        return denied

    po = get_object_or_404(PurchaseOrder, pk=po_id)
    if not po.is_fully_approved:
        messages.error(request, f"Purchase order #{po.id} is pending approvals and cannot be reopened from final list.")
        return redirect(_next_url_or_default(request))
    try:
        reopened = reopen_purchase_order(purchase_order=po)
        messages.success(request, f"Purchase order #{reopened.id} reopened.")
    except ValidationError as exc:
        error = exc.messages[0] if exc.messages else "Unable to reopen purchase order."
        messages.error(request, error)
    return redirect(_next_url_or_default(request))


@login_required
@require_http_methods(["GET"])
def export_purchase_order_excel(request, po_id: int):
    denied = _deny_purchasing_view(request)
    if denied:
        return denied

    po = get_object_or_404(PurchaseOrder.objects.select_related("vendor").prefetch_related("items__material"), pk=po_id)
    if not po.is_fully_approved:
        messages.error(request, f"Purchase order #{po.id} is pending approvals and cannot be exported yet.")
        return redirect(_next_url_or_default(request))
    payload = purchase_order_to_excel(po)
    response = HttpResponse(
        payload,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="purchase_order_{po.id}.xlsx"'
    return response


@login_required
@require_http_methods(["GET"])
def export_purchase_order_pdf(request, po_id: int):
    denied = _deny_purchasing_view(request)
    if denied:
        return denied

    po = get_object_or_404(PurchaseOrder.objects.select_related("vendor").prefetch_related("items__material"), pk=po_id)
    if not po.is_fully_approved:
        messages.error(request, f"Purchase order #{po.id} is pending approvals and cannot be exported yet.")
        return redirect(_next_url_or_default(request))
    payload = purchase_order_to_pdf(po)
    response = HttpResponse(payload, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="purchase_order_{po.id}.pdf"'
    return response
