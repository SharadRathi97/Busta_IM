from __future__ import annotations

import csv
from io import StringIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Min, Q, Value
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.permissions import INVENTORY_MANAGE_ROLES, INVENTORY_VIEW_ROLES, require_roles
from partners.models import Partner
from production.models import (
    ProductionOrder,
    reject_raw_materials_for_production_order,
    release_raw_materials_for_production_order,
)

from .forms import (
    MROItemCreateForm,
    MROItemUpdateForm,
    MROStockAdjustmentForm,
    RawMaterialCSVUploadForm,
    RawMaterialCreateForm,
    RawMaterialUpdateForm,
    StockAdjustmentForm,
)
from .models import (
    MROItem,
    RawMaterial,
    adjust_mro_stock,
    adjust_stock,
    create_mro_item_with_opening_stock,
    create_raw_material_with_opening_stock,
    update_mro_item_details,
    update_raw_material_details,
)


def _get_sorting(sort_key: str, direction: str):
    sort_map = {
        "id": "id",
        "rm_id": "rm_id",
        "material": "name",
        "code": "code",
        "type": "material_type",
        "colour": "colour",
        "colour_code": "colour_code",
        "stock": "current_stock",
        "cost": "cost_per_unit",
        "reorder": "reorder_level",
        "suppliers": "supplier_sort",
        "adjust": "id",
        "actions": "id",
    }
    resolved_key = sort_key if sort_key in sort_map else "material"
    resolved_direction = direction if direction in {"asc", "desc"} else "asc"
    order_field = sort_map[resolved_key]
    if resolved_direction == "desc":
        order_field = f"-{order_field}"
    return resolved_key, resolved_direction, order_field


def _build_sort_state(active_sort: str, active_direction: str):
    keys = [
        "id",
        "rm_id",
        "material",
        "code",
        "type",
        "colour",
        "colour_code",
        "stock",
        "cost",
        "reorder",
        "suppliers",
        "adjust",
        "actions",
    ]
    state: dict[str, dict[str, str | bool]] = {}
    for key in keys:
        is_active = key == active_sort
        next_direction = "desc" if is_active and active_direction == "asc" else "asc"
        icon = "↑" if is_active and active_direction == "asc" else "↓" if is_active else "↕"
        state[key] = {"active": is_active, "next": next_direction, "icon": icon}
    return state


def _get_mro_sorting(sort_key: str, direction: str):
    sort_map = {
        "id": "id",
        "mro_id": "mro_id",
        "item": "name",
        "code": "code",
        "type": "item_type",
        "stock": "current_stock",
        "cost": "cost_per_unit",
        "reorder": "reorder_level",
        "location": "location",
        "supplier": "vendor__name",
        "adjust": "id",
        "actions": "id",
    }
    resolved_key = sort_key if sort_key in sort_map else "item"
    resolved_direction = direction if direction in {"asc", "desc"} else "asc"
    order_field = sort_map[resolved_key]
    if resolved_direction == "desc":
        order_field = f"-{order_field}"
    return resolved_key, resolved_direction, order_field


def _build_mro_sort_state(active_sort: str, active_direction: str):
    keys = [
        "id",
        "mro_id",
        "item",
        "code",
        "type",
        "stock",
        "cost",
        "reorder",
        "location",
        "supplier",
        "adjust",
        "actions",
    ]
    state: dict[str, dict[str, str | bool]] = {}
    for key in keys:
        is_active = key == active_sort
        next_direction = "desc" if is_active and active_direction == "asc" else "asc"
        icon = "↑" if is_active and active_direction == "asc" else "↓" if is_active else "↕"
        state[key] = {"active": is_active, "next": next_direction, "icon": icon}
    return state


RAW_MATERIAL_CSV_COLUMNS = [
    "name",
    "rm_id",
    "code",
    "material_type",
    "colour",
    "colour_code",
    "unit",
    "cost_per_unit",
    "vendor_gst_number",
    "additional_vendor_gst_numbers",
    "opening_stock",
    "reorder_level",
]


def _read_csv_rows(csv_file):
    try:
        content = csv_file.read().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("CSV must be UTF-8 encoded.") from exc

    reader = csv.DictReader(StringIO(content))
    fieldnames = [name.strip() for name in (reader.fieldnames or []) if name]
    if not fieldnames:
        raise ValidationError("CSV file is empty or missing headers.")

    missing_headers = [column for column in RAW_MATERIAL_CSV_COLUMNS if column not in fieldnames]
    if missing_headers:
        raise ValidationError(f"Missing required columns: {', '.join(missing_headers)}")

    rows = []
    for row in reader:
        normalized = {key.strip(): (value or "").strip() for key, value in row.items() if key}
        if not any(normalized.get(column, "") for column in RAW_MATERIAL_CSV_COLUMNS):
            continue
        rows.append(normalized)
    return rows


def _resolve_supplier_by_gst(gst_number: str):
    return Partner.objects.filter(
        gst_number__iexact=gst_number.strip(),
        partner_type__in=[Partner.PartnerType.SUPPLIER, Partner.PartnerType.BOTH],
    ).first()


def _parse_additional_vendor_gst_numbers(raw_value: str) -> list[str]:
    if not raw_value:
        return []
    return [item.strip() for item in raw_value.split("|") if item.strip()]


def _extract_material_variant_rows(payload) -> list[dict[str, str]]:
    colour_values = payload.getlist("variant_colour")
    colour_code_values = payload.getlist("variant_colour_code")
    code_values = payload.getlist("variant_code")
    opening_stock_values = payload.getlist("variant_opening_stock")

    row_count = max(len(colour_values), len(colour_code_values), len(code_values), len(opening_stock_values))
    rows: list[dict[str, str]] = []
    for index in range(row_count):
        colour = (colour_values[index] if index < len(colour_values) else "").strip()
        colour_code = (colour_code_values[index] if index < len(colour_code_values) else "").strip()
        code = (code_values[index] if index < len(code_values) else "").strip()
        opening_stock = (opening_stock_values[index] if index < len(opening_stock_values) else "").strip()
        if not colour and not colour_code and not code and not opening_stock:
            continue
        rows.append(
            {
                "colour": colour,
                "colour_code": colour_code,
                "code": code,
                "opening_stock": opening_stock,
            }
        )
    return rows


def _deny_inventory_view(request):
    return require_roles(
        request,
        INVENTORY_VIEW_ROLES,
        redirect_to="dashboard:home",
        area="raw materials",
        action="access",
    )


def _deny_mro_view(request):
    return require_roles(
        request,
        INVENTORY_VIEW_ROLES,
        redirect_to="dashboard:home",
        area="MRO inventory",
        action="access",
    )


def _import_raw_materials_from_rows(rows: list[dict[str, str]], created_by):
    if not rows:
        raise ValidationError("CSV has no data rows.")

    payloads: list[dict] = []
    errors: list[str] = []

    for row_number, row in enumerate(rows, start=2):
        vendor = _resolve_supplier_by_gst(row.get("vendor_gst_number", ""))
        if not vendor:
            errors.append(f"Row {row_number}: vendor_gst_number not found or not a supplier.")
            continue

        additional_gst_numbers = _parse_additional_vendor_gst_numbers(row.get("additional_vendor_gst_numbers", ""))
        additional_vendors: list[Partner] = []
        missing_additional: list[str] = []
        for gst_number in additional_gst_numbers:
            extra_vendor = _resolve_supplier_by_gst(gst_number)
            if not extra_vendor:
                missing_additional.append(gst_number)
            else:
                additional_vendors.append(extra_vendor)
        if missing_additional:
            errors.append(
                f"Row {row_number}: additional vendors not found/not suppliers ({', '.join(missing_additional)})."
            )
            continue

        form_data = {
            "name": row.get("name", ""),
            "rm_id": row.get("rm_id", ""),
            "code": row.get("code", ""),
            "material_type": row.get("material_type", ""),
            "colour": row.get("colour", ""),
            "colour_code": row.get("colour_code", ""),
            "unit": row.get("unit", ""),
            "cost_per_unit": row.get("cost_per_unit", ""),
            "vendor": str(vendor.id),
            "additional_vendors": [str(vendor_obj.id) for vendor_obj in additional_vendors],
            "opening_stock": row.get("opening_stock", ""),
            "reorder_level": row.get("reorder_level", ""),
        }
        form = RawMaterialCreateForm(data=form_data)
        if not form.is_valid():
            row_errors = []
            for field, field_errors in form.errors.items():
                if field == "__all__":
                    row_errors.extend(str(err) for err in field_errors)
                else:
                    row_errors.extend(f"{field}: {err}" for err in field_errors)
            errors.append(f"Row {row_number}: {'; '.join(row_errors)}")
            continue

        payloads.append(
            {
                "name": form.cleaned_data["name"],
                "rm_id": form.cleaned_data["rm_id"],
                "code": form.cleaned_data["code"],
                "material_type": form.cleaned_data["material_type"],
                "colour": form.cleaned_data["colour"],
                "colour_code": form.cleaned_data["colour_code"],
                "unit": form.cleaned_data["unit"],
                "cost_per_unit": form.cleaned_data["cost_per_unit"],
                "vendor": vendor,
                "additional_vendors": additional_vendors,
                "opening_stock": form.cleaned_data["opening_stock"],
                "reorder_level": form.cleaned_data["reorder_level"],
            }
        )

    if errors:
        raise ValidationError(errors)

    with transaction.atomic():
        for payload in payloads:
            create_raw_material_with_opening_stock(
                name=payload["name"],
                rm_id=payload["rm_id"],
                code=payload["code"],
                material_type=payload["material_type"],
                colour=payload["colour"],
                colour_code=payload["colour_code"],
                unit=payload["unit"],
                cost_per_unit=payload["cost_per_unit"],
                vendor=payload["vendor"],
                additional_vendors=payload["additional_vendors"],
                opening_stock=payload["opening_stock"],
                reorder_level=payload["reorder_level"],
                created_by=created_by,
            )
    return len(payloads)


@login_required
@require_http_methods(["GET"])
def raw_material_csv_template(request):
    denied = _deny_inventory_view(request)
    if denied:
        return denied

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(RAW_MATERIAL_CSV_COLUMNS)
    writer.writerow(
        [
            "Canvas Cloth",
            "RM-ID-001",
            "RM-CANVAS",
            "fabric",
            "Blue",
            "BLU",
            "m",
            "55.000",
            "29ABCDE1234F1Z5",
            "",
            "100.000",
            "10.000",
        ]
    )
    response = HttpResponse(output.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="raw_material_upload_template.csv"'
    return response


@login_required
@require_http_methods(["GET", "POST"])
def material_list(request):
    denied = _deny_inventory_view(request)
    if denied:
        return denied

    can_manage = request.user.role in INVENTORY_MANAGE_ROLES
    action = request.POST.get("action") if request.method == "POST" else None
    create_form = RawMaterialCreateForm(request.POST if action in {None, "create_material"} else None)
    csv_form = RawMaterialCSVUploadForm(request.POST if action == "upload_csv" else None, request.FILES if action == "upload_csv" else None)
    variant_rows_seed = [{"colour": "", "colour_code": "", "code": "", "opening_stock": ""}]
    show_create_modal = False
    show_csv_modal = False

    if request.method == "POST":
        denied = require_roles(
            request,
            INVENTORY_MANAGE_ROLES,
            redirect_to="inventory:list",
            area="raw materials",
        )
        if denied:
            return denied
        if action in {None, "create_material"}:
            variant_rows = _extract_material_variant_rows(request.POST)
            variant_rows_seed = variant_rows or variant_rows_seed
            if variant_rows:
                common_values = {
                    "name": (request.POST.get("name") or "").strip(),
                    "rm_id": (request.POST.get("rm_id") or "").strip(),
                    "material_type": (request.POST.get("material_type") or "").strip(),
                    "unit": (request.POST.get("unit") or "").strip(),
                    "cost_per_unit": (request.POST.get("cost_per_unit") or "").strip(),
                    "vendor": (request.POST.get("vendor") or "").strip(),
                    "additional_vendors": request.POST.getlist("additional_vendors"),
                    "reorder_level": (request.POST.get("reorder_level") or "").strip(),
                }

                row_forms: list[RawMaterialCreateForm] = []
                row_errors: list[str] = []
                for row_index, variant in enumerate(variant_rows, start=1):
                    row_form = RawMaterialCreateForm(
                        data={
                            **common_values,
                            "colour": variant["colour"],
                            "colour_code": variant["colour_code"],
                            "code": variant["code"],
                            "opening_stock": variant["opening_stock"],
                        }
                    )
                    row_forms.append(row_form)
                    if row_form.is_valid():
                        continue

                    errors_for_row: list[str] = []
                    for field_name, field_errors in row_form.errors.items():
                        if field_name == "__all__":
                            errors_for_row.extend(str(err) for err in field_errors)
                        else:
                            errors_for_row.extend(f"{field_name}: {err}" for err in field_errors)
                    row_errors.append(f"Row {row_index}: {'; '.join(errors_for_row)}")

                if not row_errors:
                    seen_variant_rows: dict[tuple[str, str], int] = {}
                    for row_index, row_form in enumerate(row_forms, start=1):
                        variant_key = (
                            row_form.cleaned_data["rm_id"],
                            row_form.cleaned_data["colour_code"],
                        )
                        previous_variant_row = seen_variant_rows.get(variant_key)
                        if previous_variant_row:
                            row_errors.append(
                                f"Row {row_index}: Duplicate RM ID + Colour Code in submission (already in row {previous_variant_row})."
                            )
                        else:
                            seen_variant_rows[variant_key] = row_index

                if row_errors:
                    for row_error in row_errors[:8]:
                        messages.error(request, row_error)
                    if len(row_errors) > 8:
                        messages.error(request, f"...and {len(row_errors) - 8} more row errors.")
                    show_create_modal = True
                else:
                    try:
                        with transaction.atomic():
                            for row_form in row_forms:
                                create_raw_material_with_opening_stock(
                                    name=row_form.cleaned_data["name"],
                                    rm_id=row_form.cleaned_data["rm_id"],
                                    code=row_form.cleaned_data["code"],
                                    material_type=row_form.cleaned_data["material_type"],
                                    colour=row_form.cleaned_data["colour"],
                                    colour_code=row_form.cleaned_data["colour_code"],
                                    unit=row_form.cleaned_data["unit"],
                                    cost_per_unit=row_form.cleaned_data["cost_per_unit"],
                                    vendor=row_form.cleaned_data["vendor"],
                                    additional_vendors=row_form.cleaned_data["additional_vendors"],
                                    opening_stock=row_form.cleaned_data["opening_stock"],
                                    reorder_level=row_form.cleaned_data["reorder_level"],
                                    created_by=request.user,
                                )
                        messages.success(request, f"Raw materials created for {len(row_forms)} colour variant(s).")
                        return redirect("inventory:list")
                    except ValueError as exc:
                        messages.error(request, str(exc))
                        show_create_modal = True
                    except IntegrityError:
                        messages.error(
                            request,
                            "Duplicate raw material entry detected. RM ID + Colour Code must be unique for each row.",
                        )
                        show_create_modal = True
            else:
                if create_form.is_valid():
                    try:
                        create_raw_material_with_opening_stock(
                            name=create_form.cleaned_data["name"],
                            rm_id=create_form.cleaned_data["rm_id"],
                            code=create_form.cleaned_data["code"],
                            material_type=create_form.cleaned_data["material_type"],
                            colour=create_form.cleaned_data["colour"],
                            colour_code=create_form.cleaned_data["colour_code"],
                            unit=create_form.cleaned_data["unit"],
                            cost_per_unit=create_form.cleaned_data["cost_per_unit"],
                            vendor=create_form.cleaned_data["vendor"],
                            additional_vendors=create_form.cleaned_data["additional_vendors"],
                            opening_stock=create_form.cleaned_data["opening_stock"],
                            reorder_level=create_form.cleaned_data["reorder_level"],
                            created_by=request.user,
                        )
                        messages.success(request, "Raw material created.")
                        return redirect("inventory:list")
                    except ValueError as exc:
                        create_form.add_error("vendor", str(exc))
                else:
                    messages.error(request, "Add at least one colour variant before creating raw material.")
                show_create_modal = True

        if action == "upload_csv":
            if csv_form.is_valid():
                try:
                    rows = _read_csv_rows(csv_form.cleaned_data["csv_file"])
                    imported_count = _import_raw_materials_from_rows(rows, created_by=request.user)
                    messages.success(request, f"Raw material CSV imported. Created: {imported_count}.")
                    return redirect("inventory:list")
                except ValidationError as exc:
                    errors = exc.messages if hasattr(exc, "messages") else [str(exc)]
                    for error in errors[:8]:
                        messages.error(request, error)
                    if len(errors) > 8:
                        messages.error(request, f"...and {len(errors) - 8} more row errors.")
            else:
                for error in csv_form.errors.get("csv_file", []):
                    messages.error(request, f"CSV: {error}")
            show_csv_modal = True

    q_filter = request.GET.get("q", "").strip()
    type_filter = request.GET.get("material_type", "").strip()
    vendor_filter = request.GET.get("vendor", "").strip()
    stock_filter = request.GET.get("stock", "").strip()

    materials_qs = (
        RawMaterial.objects.select_related("vendor")
        .prefetch_related("vendor_links__vendor")
    )
    if q_filter:
        materials_qs = materials_qs.filter(
            Q(rm_id__icontains=q_filter)
            | Q(name__icontains=q_filter)
            | Q(code__icontains=q_filter)
            | Q(colour__icontains=q_filter)
            | Q(colour_code__icontains=q_filter)
            | Q(vendor__name__icontains=q_filter)
            | Q(vendor_links__vendor__name__icontains=q_filter)
        ).distinct()

    valid_material_types = {value for value, _label in RawMaterial.MaterialType.choices}
    if type_filter in valid_material_types:
        materials_qs = materials_qs.filter(material_type=type_filter)

    if vendor_filter.isdigit():
        vendor_id = int(vendor_filter)
        materials_qs = materials_qs.filter(Q(vendor_id=vendor_id) | Q(vendor_links__vendor_id=vendor_id)).distinct()

    if stock_filter == "low":
        materials_qs = materials_qs.filter(current_stock__lte=F("reorder_level"))
    elif stock_filter == "healthy":
        materials_qs = materials_qs.filter(current_stock__gt=F("reorder_level"))

    sort_key, sort_direction, order_field = _get_sorting(
        request.GET.get("sort", ""),
        request.GET.get("direction", ""),
    )
    materials_qs = materials_qs.annotate(
        supplier_sort=Coalesce(Min("vendor_links__vendor__name"), F("vendor__name"), Value(""))
    ).order_by(order_field, "id")

    paginator = Paginator(materials_qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    suppliers = Partner.objects.filter(
        partner_type__in=[Partner.PartnerType.SUPPLIER, Partner.PartnerType.BOTH]
    ).order_by("name")
    pending_production_requests = []
    if can_manage:
        pending_production_requests = list(
            ProductionOrder.objects.filter(status=ProductionOrder.Status.AWAITING_RM_RELEASE)
            .select_related("product", "created_by")
            .prefetch_related("consumptions__material")
            .order_by("-id")
        )

    context = {
        "materials": page_obj.object_list,
        "page_obj": page_obj,
        "create_form": create_form,
        "csv_form": csv_form,
        "adjust_form": StockAdjustmentForm(),
        "can_manage": can_manage,
        "show_create_modal": show_create_modal,
        "show_csv_modal": show_csv_modal,
        "variant_rows_seed": variant_rows_seed,
        "sort_key": sort_key,
        "sort_direction": sort_direction,
        "sort_state": _build_sort_state(sort_key, sort_direction),
        "pending_production_requests": pending_production_requests,
        "material_type_choices": RawMaterial.MaterialType.choices,
        "supplier_choices": suppliers,
        "filter_values": {
            "q": q_filter,
            "material_type": type_filter,
            "vendor": vendor_filter,
            "stock": stock_filter,
        },
    }
    return render(request, "inventory/materials.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def material_edit(request, material_id: int):
    denied = require_roles(
        request,
        INVENTORY_MANAGE_ROLES,
        redirect_to="inventory:list",
        area="raw materials",
    )
    if denied:
        return denied

    material = get_object_or_404(RawMaterial.objects.prefetch_related("vendor_links"), pk=material_id)

    additional_vendor_ids = list(
        material.vendor_links.exclude(vendor_id=material.vendor_id).values_list("vendor_id", flat=True)
    )
    initial = {
        "name": material.name,
        "rm_id": material.rm_id,
        "code": material.code,
        "material_type": material.material_type,
        "colour": material.colour,
        "colour_code": material.colour_code,
        "unit": material.unit,
        "cost_per_unit": material.cost_per_unit,
        "vendor": material.vendor_id,
        "additional_vendors": additional_vendor_ids,
        "reorder_level": material.reorder_level,
    }
    form = RawMaterialUpdateForm(request.POST or None, material=material, initial=initial)

    if request.method == "POST" and form.is_valid():
        try:
            update_raw_material_details(
                material=material,
                name=form.cleaned_data["name"],
                rm_id=form.cleaned_data["rm_id"],
                code=form.cleaned_data["code"],
                material_type=form.cleaned_data["material_type"],
                colour=form.cleaned_data["colour"],
                colour_code=form.cleaned_data["colour_code"],
                unit=form.cleaned_data["unit"],
                cost_per_unit=form.cleaned_data["cost_per_unit"],
                vendor=form.cleaned_data["vendor"],
                additional_vendors=form.cleaned_data["additional_vendors"],
                reorder_level=form.cleaned_data["reorder_level"],
            )
            messages.success(request, "Raw material updated.")
            return redirect("inventory:list")
        except ValueError as exc:
            form.add_error("vendor", str(exc))

    context = {
        "form": form,
        "material": material,
    }
    return render(request, "inventory/material_edit.html", context)


@login_required
@require_http_methods(["POST"])
def material_delete(request, material_id: int):
    denied = require_roles(
        request,
        INVENTORY_MANAGE_ROLES,
        redirect_to="inventory:list",
        area="raw materials",
    )
    if denied:
        return denied

    material = get_object_or_404(RawMaterial.objects.prefetch_related("vendor_links", "bom_usage"), pk=material_id)
    try:
        with transaction.atomic():
            material.vendor_links.all().delete()
            material.bom_usage.all().delete()
            material.delete()
        messages.success(request, "Raw material deleted.")
    except ProtectedError as exc:
        protected_labels = sorted({obj._meta.verbose_name for obj in exc.protected_objects})
        linked_text = f" Linked records: {', '.join(protected_labels)}." if protected_labels else ""
        messages.error(request, f"Raw material cannot be deleted because it is linked to existing records.{linked_text}")
    return redirect("inventory:list")


@login_required
@require_http_methods(["POST"])
def adjust_material_stock(request):
    denied = require_roles(
        request,
        INVENTORY_MANAGE_ROLES,
        redirect_to="inventory:list",
        area="raw materials",
    )
    if denied:
        return denied

    form = StockAdjustmentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Invalid stock adjustment input.")
        return redirect("inventory:list")

    material = get_object_or_404(RawMaterial, pk=form.cleaned_data["material_id"])
    try:
        adjust_stock(
            material=material,
            delta=form.cleaned_data["delta"],
            reason=form.cleaned_data["reason"],
            created_by=request.user,
        )
        messages.success(request, "Stock adjusted.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("inventory:list")


@login_required
@require_http_methods(["POST"])
def release_production_request(request, order_id: int):
    denied = require_roles(
        request,
        INVENTORY_MANAGE_ROLES,
        redirect_to="inventory:list",
        area="raw materials",
    )
    if denied:
        return denied

    order = get_object_or_404(ProductionOrder, pk=order_id)
    try:
        updated = release_raw_materials_for_production_order(
            production_order=order,
            released_by=request.user,
        )
        messages.success(
            request,
            f"Released raw materials for production order #{updated.id}. Status moved to Planned.",
        )
    except ValidationError as exc:
        message = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
        messages.error(request, message)
    return redirect("inventory:list")


@login_required
@require_http_methods(["POST"])
def reject_production_request(request, order_id: int):
    denied = require_roles(
        request,
        INVENTORY_MANAGE_ROLES,
        redirect_to="inventory:list",
        area="raw materials",
    )
    if denied:
        return denied

    order = get_object_or_404(ProductionOrder, pk=order_id)
    try:
        updated = reject_raw_materials_for_production_order(production_order=order)
        messages.success(
            request,
            f"Rejected raw material release for production order #{updated.id}. Order cancelled.",
        )
    except ValidationError as exc:
        message = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
        messages.error(request, message)
    return redirect("inventory:list")


@login_required
@require_http_methods(["GET", "POST"])
def mro_list(request):
    denied = _deny_mro_view(request)
    if denied:
        return denied

    can_manage = request.user.role in INVENTORY_MANAGE_ROLES
    action = request.POST.get("action") if request.method == "POST" else None
    create_form = MROItemCreateForm(request.POST if action in {None, "create_mro_item"} else None)
    show_create_modal = False

    if request.method == "POST":
        denied = require_roles(
            request,
            INVENTORY_MANAGE_ROLES,
            redirect_to="inventory:mro_list",
            area="MRO inventory",
        )
        if denied:
            return denied

        if action in {None, "create_mro_item"}:
            if create_form.is_valid():
                try:
                    create_mro_item_with_opening_stock(
                        name=create_form.cleaned_data["name"],
                        mro_id=create_form.cleaned_data["mro_id"],
                        code=create_form.cleaned_data["code"],
                        item_type=create_form.cleaned_data["item_type"],
                        unit=create_form.cleaned_data["unit"],
                        cost_per_unit=create_form.cleaned_data["cost_per_unit"],
                        vendor=create_form.cleaned_data["vendor"],
                        location=create_form.cleaned_data["location"],
                        opening_stock=create_form.cleaned_data["opening_stock"],
                        reorder_level=create_form.cleaned_data["reorder_level"],
                        created_by=request.user,
                    )
                    messages.success(request, "MRO item created.")
                    return redirect("inventory:mro_list")
                except ValueError as exc:
                    create_form.add_error("vendor", str(exc))
            show_create_modal = True

    q_filter = request.GET.get("q", "").strip()
    type_filter = request.GET.get("item_type", "").strip()
    vendor_filter = request.GET.get("vendor", "").strip()
    stock_filter = request.GET.get("stock", "").strip()

    items_qs = MROItem.objects.select_related("vendor")
    if q_filter:
        items_qs = items_qs.filter(
            Q(mro_id__icontains=q_filter)
            | Q(name__icontains=q_filter)
            | Q(code__icontains=q_filter)
            | Q(location__icontains=q_filter)
            | Q(vendor__name__icontains=q_filter)
        )

    valid_item_types = {value for value, _label in MROItem.ItemType.choices}
    if type_filter in valid_item_types:
        items_qs = items_qs.filter(item_type=type_filter)

    if vendor_filter.isdigit():
        items_qs = items_qs.filter(vendor_id=int(vendor_filter))

    if stock_filter == "low":
        items_qs = items_qs.filter(current_stock__lte=F("reorder_level"))
    elif stock_filter == "healthy":
        items_qs = items_qs.filter(current_stock__gt=F("reorder_level"))

    sort_key, sort_direction, order_field = _get_mro_sorting(
        request.GET.get("sort", ""),
        request.GET.get("direction", ""),
    )
    items_qs = items_qs.order_by(order_field, "id")

    paginator = Paginator(items_qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    suppliers = Partner.objects.filter(
        partner_type__in=[Partner.PartnerType.SUPPLIER, Partner.PartnerType.BOTH]
    ).order_by("name")

    context = {
        "items": page_obj.object_list,
        "page_obj": page_obj,
        "create_form": create_form,
        "adjust_form": MROStockAdjustmentForm(),
        "can_manage": can_manage,
        "show_create_modal": show_create_modal,
        "sort_key": sort_key,
        "sort_direction": sort_direction,
        "sort_state": _build_mro_sort_state(sort_key, sort_direction),
        "item_type_choices": MROItem.ItemType.choices,
        "supplier_choices": suppliers,
        "filter_values": {
            "q": q_filter,
            "item_type": type_filter,
            "vendor": vendor_filter,
            "stock": stock_filter,
        },
    }
    return render(request, "inventory/mro_items.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def mro_edit(request, item_id: int):
    denied = require_roles(
        request,
        INVENTORY_MANAGE_ROLES,
        redirect_to="inventory:mro_list",
        area="MRO inventory",
    )
    if denied:
        return denied

    item = get_object_or_404(MROItem, pk=item_id)
    initial = {
        "name": item.name,
        "mro_id": item.mro_id,
        "code": item.code,
        "item_type": item.item_type,
        "unit": item.unit,
        "cost_per_unit": item.cost_per_unit,
        "vendor": item.vendor_id,
        "location": item.location,
        "reorder_level": item.reorder_level,
    }
    form = MROItemUpdateForm(request.POST or None, item=item, initial=initial)

    if request.method == "POST" and form.is_valid():
        try:
            update_mro_item_details(
                item=item,
                name=form.cleaned_data["name"],
                mro_id=form.cleaned_data["mro_id"],
                code=form.cleaned_data["code"],
                item_type=form.cleaned_data["item_type"],
                unit=form.cleaned_data["unit"],
                cost_per_unit=form.cleaned_data["cost_per_unit"],
                vendor=form.cleaned_data["vendor"],
                location=form.cleaned_data["location"],
                reorder_level=form.cleaned_data["reorder_level"],
            )
            messages.success(request, "MRO item updated.")
            return redirect("inventory:mro_list")
        except ValueError as exc:
            form.add_error("vendor", str(exc))

    context = {
        "form": form,
        "item": item,
    }
    return render(request, "inventory/mro_item_edit.html", context)


@login_required
@require_http_methods(["POST"])
def mro_delete(request, item_id: int):
    denied = require_roles(
        request,
        INVENTORY_MANAGE_ROLES,
        redirect_to="inventory:mro_list",
        area="MRO inventory",
    )
    if denied:
        return denied

    item = get_object_or_404(MROItem, pk=item_id)
    try:
        item.delete()
        messages.success(request, "MRO item deleted.")
    except ProtectedError as exc:
        protected_labels = sorted({obj._meta.verbose_name for obj in exc.protected_objects})
        linked_text = f" Linked records: {', '.join(protected_labels)}." if protected_labels else ""
        messages.error(request, f"MRO item cannot be deleted because it is linked to existing records.{linked_text}")
    return redirect("inventory:mro_list")


@login_required
@require_http_methods(["POST"])
def adjust_mro_item_stock(request):
    denied = require_roles(
        request,
        INVENTORY_MANAGE_ROLES,
        redirect_to="inventory:mro_list",
        area="MRO inventory",
    )
    if denied:
        return denied

    form = MROStockAdjustmentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Invalid stock adjustment input.")
        return redirect("inventory:mro_list")

    item = get_object_or_404(MROItem, pk=form.cleaned_data["item_id"])
    try:
        adjust_mro_stock(
            item=item,
            delta=form.cleaned_data["delta"],
            reason=form.cleaned_data["reason"],
            created_by=request.user,
        )
        messages.success(request, "MRO stock adjusted.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("inventory:mro_list")
