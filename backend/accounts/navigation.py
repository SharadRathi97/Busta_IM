from __future__ import annotations

from dataclasses import dataclass

from .models import User
from .permissions import (
    INVENTORY_VIEW_ROLES,
    PRODUCTION_VIEW_ROLES,
)


@dataclass(frozen=True)
class NavigationItem:
    label: str
    url_name: str
    active_view_prefixes: tuple[str, ...]
    allowed_roles: frozenset[str]


NAV_ITEMS: tuple[NavigationItem, ...] = (
    NavigationItem(
        label="Dashboard",
        url_name="dashboard:home",
        active_view_prefixes=("dashboard:",),
        allowed_roles=frozenset(role for role, _label in User.Role.choices),
    ),
    NavigationItem(
        label="Vendors",
        url_name="partners:list",
        active_view_prefixes=("partners:",),
        allowed_roles=INVENTORY_VIEW_ROLES,
    ),
    NavigationItem(
        label="Raw Materials",
        url_name="inventory:list",
        active_view_prefixes=(
            "inventory:list",
            "inventory:csv_template",
            "inventory:edit",
            "inventory:delete",
            "inventory:adjust",
        ),
        allowed_roles=INVENTORY_VIEW_ROLES,
    ),
    NavigationItem(
        label="MRO Inventory",
        url_name="inventory:mro_list",
        active_view_prefixes=("inventory:mro_",),
        allowed_roles=INVENTORY_VIEW_ROLES,
    ),
    NavigationItem(
        label="Purchase Orders",
        url_name="purchasing:list",
        active_view_prefixes=("purchasing:",),
        allowed_roles=INVENTORY_VIEW_ROLES,
    ),
    NavigationItem(
        label="Products & BOM",
        url_name="production:products",
        active_view_prefixes=(
            "production:products",
            "production:delete_product",
            "production:bom_csv_template",
            "production:export_bom_",
            "production:update_bom",
            "production:delete_bom",
        ),
        allowed_roles=PRODUCTION_VIEW_ROLES,
    ),
    NavigationItem(
        label="Production Orders",
        url_name="production:orders",
        active_view_prefixes=(
            "production:orders",
            "production:update_status",
            "production:cancel_order",
        ),
        allowed_roles=PRODUCTION_VIEW_ROLES,
    ),
    NavigationItem(
        label="Users",
        url_name="accounts:user_list",
        active_view_prefixes=(
            "accounts:user_list",
            "accounts:deactivate_user",
            "accounts:delete_user",
            "accounts:download_transactions",
        ),
        allowed_roles=frozenset({User.Role.ADMIN}),
    ),
)


def build_navigation_items(*, role: str, view_name: str | None) -> list[dict[str, str | bool]]:
    current_view_name = view_name or ""
    items: list[dict[str, str | bool]] = []
    for item in NAV_ITEMS:
        if role not in item.allowed_roles:
            continue
        items.append(
            {
                "label": item.label,
                "url_name": item.url_name,
                "is_active": any(current_view_name.startswith(prefix) for prefix in item.active_view_prefixes),
            }
        )
    return items
