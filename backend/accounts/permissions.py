from __future__ import annotations

from functools import wraps
from typing import Iterable

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from .models import User

ALL_AUTHENTICATED_ROLES = frozenset(role for role, _label in User.Role.choices)
INVENTORY_VIEW_ROLES = frozenset({User.Role.ADMIN, User.Role.INVENTORY_MANAGER, User.Role.VIEWER})
INVENTORY_MANAGE_ROLES = frozenset({User.Role.ADMIN, User.Role.INVENTORY_MANAGER})
PRODUCTION_VIEW_ROLES = frozenset({User.Role.ADMIN, User.Role.PRODUCTION_MANAGER, User.Role.VIEWER})
PRODUCTION_MANAGE_ROLES = frozenset({User.Role.ADMIN, User.Role.PRODUCTION_MANAGER})
PURCHASING_VIEW_ROLES = INVENTORY_VIEW_ROLES
PURCHASING_MANAGE_ROLES = INVENTORY_MANAGE_ROLES


def _build_denial_message(*, action: str, area: str) -> str:
    if action == "access":
        return f"You do not have permission to access {area}."
    return f"You do not have permission to manage {area}."


def role_required(*roles: str):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            user = request.user
            assert isinstance(user, User)
            if user.role not in roles:
                messages.error(request, "You do not have permission to access this area.")
                return redirect("dashboard:home")
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def has_any_role(user, *roles: str) -> bool:
    if not isinstance(user, User):
        return False
    return user.role in set(roles)


def require_roles(
    request: HttpRequest,
    allowed_roles: Iterable[str],
    *,
    redirect_to: str,
    area: str,
    action: str = "manage",
) -> HttpResponse | None:
    user = request.user
    assert isinstance(user, User)
    if user.role in set(allowed_roles):
        return None

    messages.error(request, _build_denial_message(action=action, area=area))
    return redirect(redirect_to)
