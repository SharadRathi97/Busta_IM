from __future__ import annotations

import csv
import json
from io import StringIO

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .forms import LoginForm, UserCreateForm
from .models import AuditLog, User
from .permissions import role_required


AUDIT_EXPORTS = {
    "raw_materials": {
        "label": "Raw Materials History",
        "apps": {"inventory"},
    },
    "production": {
        "label": "Production History",
        "apps": {"production"},
    },
    "purchase_orders": {
        "label": "Purchase Orders History",
        "apps": {"purchasing"},
    },
    "all": {
        "label": "Complete System History",
        "apps": None,
    },
}


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")

    form = LoginForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        return redirect("dashboard:home")

    return render(request, "accounts/login.html", {"form": form})


@login_required
def logout_view(request):
    logout(request)
    return redirect("accounts:login")


@role_required(User.Role.ADMIN)
@require_http_methods(["GET", "POST"])
def user_list_create(request):
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "User created successfully.")
        return redirect("accounts:user_list")

    users = User.objects.order_by("id")
    recent_transactions = AuditLog.objects.select_related("actor").order_by("-id")[:150]
    transaction_exports = [
        {
            "key": key,
            "label": meta["label"],
        }
        for key, meta in AUDIT_EXPORTS.items()
    ]
    return render(
        request,
        "accounts/users.html",
        {
            "form": form,
            "users": users,
            "recent_transactions": recent_transactions,
            "transaction_exports": transaction_exports,
        },
    )


@role_required(User.Role.ADMIN)
@require_http_methods(["POST"])
def deactivate_user(request, user_id: int):
    user = get_object_or_404(User, pk=user_id)
    if user.id == request.user.id:
        messages.error(request, "You cannot deactivate your own account.")
        return redirect("accounts:user_list")

    user.is_active = False
    user.save(update_fields=["is_active"])
    messages.success(request, "User deactivated.")
    return redirect("accounts:user_list")


@role_required(User.Role.ADMIN)
@require_http_methods(["POST"])
def delete_user(request, user_id: int):
    user = get_object_or_404(User, pk=user_id)
    if user.id == request.user.id:
        messages.error(request, "You cannot delete your own account.")
        return redirect("accounts:user_list")

    username = user.username
    user.delete()
    messages.success(request, f"User {username} deleted.")
    return redirect("accounts:user_list")


@role_required(User.Role.ADMIN)
@require_http_methods(["GET"])
def download_transaction_history(request, scope: str):
    scope_config = AUDIT_EXPORTS.get(scope)
    if not scope_config:
        raise Http404("Unknown transaction history scope.")

    logs = AuditLog.objects.select_related("actor").order_by("created_at", "id")
    app_filters = scope_config["apps"]
    if app_filters:
        logs = logs.filter(app_label__in=app_filters)

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "timestamp",
            "user_id",
            "username",
            "user_role",
            "action",
            "app",
            "model",
            "table",
            "record_pk",
            "record_repr",
            "details_json",
        ]
    )

    for log in logs:
        actor_username = log.actor_username or (log.actor.username if log.actor else "")
        actor_role = log.actor_role or (getattr(log.actor, "role", "") if log.actor else "")
        writer.writerow(
            [
                timezone.localtime(log.created_at).strftime("%Y-%m-%d %H:%M:%S %Z"),
                log.actor_id or "",
                actor_username,
                actor_role,
                log.action,
                log.app_label,
                log.model_name,
                log.table_name,
                log.object_pk,
                log.object_repr,
                json.dumps(log.details, ensure_ascii=True, separators=(",", ":"), default=str),
            ]
        )

    filename_scope = scope_config["label"].lower().replace(" ", "_")
    filename = f"{filename_scope}_{timezone.localdate().isoformat()}.csv"
    response = HttpResponse(output.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
