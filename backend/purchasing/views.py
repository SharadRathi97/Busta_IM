from __future__ import annotations

from collections import defaultdict
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from accounts.permissions import PURCHASING_MANAGE_ROLES, PURCHASING_VIEW_ROLES, require_roles
from inventory.models import RawMaterial
from partners.models import Partner

from .exports import purchase_order_to_excel, purchase_order_to_pdf
from .forms import PurchaseLineForm, PurchaseOrderCreateForm, parse_purchase_lines, parse_receive_quantities
from .models import (
    PurchaseOrder,
    cancel_purchase_order,
    create_grouped_purchase_orders_with_vendor,
    receive_purchase_order,
    reopen_purchase_order,
)


def _can_manage_purchase_orders(user) -> bool:
    return user.role in PURCHASING_MANAGE_ROLES


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
        except Exception as exc:
            form.add_error(None, str(exc))
            lines = []

        if form.is_valid() and lines and selected_vendor:
            created = create_grouped_purchase_orders_with_vendor(
                order_date=form.cleaned_data["order_date"],
                notes=form.cleaned_data["notes"],
                created_by=request.user,
                lines=lines,
                vendor=selected_vendor,
            )
            po_ids = ", ".join(f"#{order.id}" for order in created)
            messages.success(request, f"Purchase order(s) created: {po_ids}")
            return redirect("purchasing:list")

    orders = (
        PurchaseOrder.objects.select_related("vendor", "created_by", "received_by", "cancelled_by")
        .prefetch_related("items__material")
    )

    status_filter = request.GET.get("status", "").strip()
    vendor_filter = request.GET.get("vendor", "").strip()
    q_filter = request.GET.get("q", "").strip()
    date_from = _parse_iso_date(request.GET.get("date_from"))
    date_to = _parse_iso_date(request.GET.get("date_to"))

    valid_statuses = {value for value, _label in PurchaseOrder.Status.choices}
    if status_filter in valid_statuses:
        orders = orders.filter(status=status_filter)
    if vendor_filter.isdigit():
        orders = orders.filter(vendor_id=int(vendor_filter))
    if date_from:
        orders = orders.filter(order_date__gte=date_from)
    if date_to:
        orders = orders.filter(order_date__lte=date_to)
    if q_filter:
        query = Q(vendor__name__icontains=q_filter) | Q(notes__icontains=q_filter) | Q(items__material__name__icontains=q_filter)
        if q_filter.isdigit():
            query |= Q(id=int(q_filter))
        orders = orders.filter(query).distinct()

    orders = orders.order_by("-id")
    paginator = Paginator(orders, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    line_form = PurchaseLineForm(vendor=selected_vendor)
    vendors = Partner.objects.filter(purchase_orders__isnull=False).order_by("name").distinct()
    line_rows_seed = (
        _extract_po_line_rows(request.POST) if request.method == "POST" else [{"material": "", "quantity": ""}]
    )
    if not line_rows_seed:
        line_rows_seed = [{"material": "", "quantity": ""}]

    context = {
        "can_manage": can_manage,
        "form": form,
        "line_form": line_form,
        "selected_create_vendor": selected_vendor,
        "orders": page_obj.object_list,
        "page_obj": page_obj,
        "vendors": vendors,
        "status_choices": PurchaseOrder.Status.choices,
        "vendor_material_map": _build_vendor_material_map(),
        "line_rows_seed": line_rows_seed,
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
    items = list(po.items.select_related("material"))

    if request.method == "POST":
        try:
            quantities = parse_receive_quantities(items, request.POST)
            updated = receive_purchase_order(
                purchase_order=po,
                received_by=request.user,
                line_quantities=quantities,
            )
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
    try:
        cancel_purchase_order(purchase_order=po, cancelled_by=request.user)
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
    payload = purchase_order_to_pdf(po)
    response = HttpResponse(payload, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="purchase_order_{po.id}.pdf"'
    return response
