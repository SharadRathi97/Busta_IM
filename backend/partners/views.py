from __future__ import annotations

import csv
from io import StringIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.permissions import INVENTORY_MANAGE_ROLES, INVENTORY_VIEW_ROLES, require_roles

from .forms import PartnerCSVUploadForm, PartnerForm
from .models import Partner


def _can_manage_partners(user) -> bool:
    return user.role in INVENTORY_MANAGE_ROLES


def _deny_partner_view(request):
    return require_roles(
        request,
        INVENTORY_VIEW_ROLES,
        redirect_to="dashboard:home",
        area="vendors and buyers",
        action="access",
    )


def _get_sorting(sort_key: str, direction: str):
    sort_map = {
        "vendor_id": "vendor_id",
        "name": "name",
        "type": "partner_type",
        "gstin": "gst_number",
        "address": "address_line1",
        "contact": "contact_person",
        "actions": "id",
    }
    resolved_key = sort_key if sort_key in sort_map else "name"
    resolved_direction = direction if direction in {"asc", "desc"} else "asc"
    order_field = sort_map[resolved_key]
    if resolved_direction == "desc":
        order_field = f"-{order_field}"
    return resolved_key, resolved_direction, order_field


def _build_sort_state(active_sort: str, active_direction: str):
    keys = ["vendor_id", "name", "type", "gstin", "address", "contact", "actions"]
    state: dict[str, dict[str, str | bool]] = {}
    for key in keys:
        is_active = key == active_sort
        next_direction = "desc" if is_active and active_direction == "asc" else "asc"
        icon = "↑" if is_active and active_direction == "asc" else "↓" if is_active else "↕"
        state[key] = {"active": is_active, "next": next_direction, "icon": icon}
    return state


PARTNER_CSV_COLUMNS = [
    "vendor_id",
    "name",
    "partner_type",
    "gst_number",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "pincode",
    "contact_person",
    "phone",
    "email",
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

    missing_headers = [column for column in PARTNER_CSV_COLUMNS if column not in fieldnames]
    if missing_headers:
        raise ValidationError(f"Missing required columns: {', '.join(missing_headers)}")

    rows = []
    for row in reader:
        normalized = {key.strip(): (value or "").strip() for key, value in row.items() if key}
        if not any(normalized.get(column, "") for column in PARTNER_CSV_COLUMNS):
            continue
        rows.append(normalized)
    return rows


def _import_partners_from_rows(rows: list[dict[str, str]]):
    if not rows:
        raise ValidationError("CSV has no data rows.")

    partner_payloads: list[dict] = []
    errors: list[str] = []
    for row_number, row in enumerate(rows, start=2):
        row_data = {column: row.get(column, "") for column in PARTNER_CSV_COLUMNS}
        row_data["partner_type"] = row_data["partner_type"] or Partner.PartnerType.SUPPLIER
        form = PartnerForm(data=row_data)
        if not form.is_valid():
            row_errors = []
            for field, field_errors in form.errors.items():
                if field == "__all__":
                    row_errors.extend(str(err) for err in field_errors)
                else:
                    row_errors.extend(f"{field}: {err}" for err in field_errors)
            errors.append(f"Row {row_number}: {'; '.join(row_errors)}")
            continue
        partner_payloads.append(form.cleaned_data)

    if errors:
        raise ValidationError(errors)

    created_count = 0
    updated_count = 0
    with transaction.atomic():
        for payload in partner_payloads:
            vendor_id = payload["vendor_id"]
            _obj, created = Partner.objects.update_or_create(vendor_id=vendor_id, defaults=payload)
            if created:
                created_count += 1
            else:
                updated_count += 1
    return created_count, updated_count


@login_required
@require_http_methods(["GET"])
def partner_csv_template(request):
    denied = _deny_partner_view(request)
    if denied:
        return denied

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(PARTNER_CSV_COLUMNS)
    writer.writerow(
        [
            "VEND-ACME-001",
            "Acme Suppliers",
            "supplier",
            "29ABCDE1234F1Z5",
            "Industrial Area",
            "Unit 42",
            "Bengaluru",
            "Karnataka",
            "560001",
            "Ravi Kumar",
            "9876543210",
            "ops@acme.com",
        ]
    )
    response = HttpResponse(output.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="vendor_upload_template.csv"'
    return response


@login_required
@require_http_methods(["GET", "POST"])
def partner_list_create(request):
    denied = _deny_partner_view(request)
    if denied:
        return denied

    action = request.POST.get("action") if request.method == "POST" else None
    form = PartnerForm(request.POST if action in {None, "create_partner"} else None)
    csv_form = PartnerCSVUploadForm(request.POST if action == "upload_csv" else None, request.FILES if action == "upload_csv" else None)
    can_add = _can_manage_partners(request.user)
    show_create_modal = False
    show_csv_modal = False

    if request.method == "POST":
        denied = require_roles(
            request,
            INVENTORY_MANAGE_ROLES,
            redirect_to="partners:list",
            area="vendors and buyers",
        )
        if denied:
            return denied

        if action in {None, "create_partner"}:
            if form.is_valid():
                form.save()
                messages.success(request, "Vendor/Buyer saved successfully.")
                return redirect("partners:list")
            show_create_modal = True

        if action == "upload_csv":
            if csv_form.is_valid():
                try:
                    rows = _read_csv_rows(csv_form.cleaned_data["csv_file"])
                    created_count, updated_count = _import_partners_from_rows(rows)
                    messages.success(
                        request,
                        f"Vendor CSV imported. Created: {created_count}, Updated: {updated_count}.",
                    )
                    return redirect("partners:list")
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

    sort_key, sort_direction, order_field = _get_sorting(
        request.GET.get("sort", ""),
        request.GET.get("direction", ""),
    )

    q_filter = request.GET.get("q", "").strip()
    type_filter = request.GET.get("partner_type", "").strip()

    partners_qs = Partner.objects.all()
    if q_filter:
        partners_qs = partners_qs.filter(
            Q(vendor_id__icontains=q_filter)
            | Q(name__icontains=q_filter)
            | Q(gst_number__icontains=q_filter)
            | Q(city__icontains=q_filter)
            | Q(state__icontains=q_filter)
            | Q(contact_person__icontains=q_filter)
            | Q(phone__icontains=q_filter)
            | Q(email__icontains=q_filter)
        )

    valid_partner_types = {value for value, _label in Partner.PartnerType.choices}
    if type_filter in valid_partner_types:
        partners_qs = partners_qs.filter(partner_type=type_filter)

    partners_qs = partners_qs.order_by(order_field, "name")
    paginator = Paginator(partners_qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "form": form,
        "partners": page_obj.object_list,
        "page_obj": page_obj,
        "can_add": can_add,
        "show_create_modal": show_create_modal,
        "show_csv_modal": show_csv_modal,
        "csv_form": csv_form,
        "sort_key": sort_key,
        "sort_direction": sort_direction,
        "sort_state": _build_sort_state(sort_key, sort_direction),
        "partner_type_choices": Partner.PartnerType.choices,
        "filter_values": {
            "q": q_filter,
            "partner_type": type_filter,
        },
    }
    return render(request, "partners/partners.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def partner_edit(request, partner_id: int):
    denied = require_roles(
        request,
        INVENTORY_MANAGE_ROLES,
        redirect_to="partners:list",
        area="vendors and buyers",
    )
    if denied:
        return denied

    partner = get_object_or_404(Partner, pk=partner_id)
    form = PartnerForm(request.POST or None, instance=partner)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Vendor/Buyer updated successfully.")
        return redirect("partners:list")

    context = {
        "form": form,
        "partner": partner,
    }
    return render(request, "partners/partner_edit.html", context)


@login_required
@require_http_methods(["POST"])
def partner_delete(request, partner_id: int):
    denied = require_roles(
        request,
        INVENTORY_MANAGE_ROLES,
        redirect_to="partners:list",
        area="vendors and buyers",
    )
    if denied:
        return denied

    partner = get_object_or_404(Partner, pk=partner_id)
    try:
        partner.delete()
        messages.success(request, "Vendor/Buyer deleted successfully.")
    except ProtectedError:
        messages.error(request, "Partner cannot be deleted because it is linked to existing records.")
    return redirect("partners:list")
