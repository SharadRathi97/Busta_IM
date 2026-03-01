from __future__ import annotations

from dataclasses import dataclass

from .models import User
from .permissions import (
    INVENTORY_VIEW_ROLES,
    PRODUCTION_VIEW_ROLES,
)


@dataclass(frozen=True)
class NavigationLink:
    label: str
    url_name: str
    active_view_prefixes: tuple[str, ...]
    allowed_roles: frozenset[str]


@dataclass(frozen=True)
class NavigationDropdown:
    label: str
    allowed_roles: frozenset[str]
    children: tuple[NavigationLink, ...]


NAV_ITEMS: tuple[NavigationLink | NavigationDropdown, ...] = (
    NavigationLink(
        label="Dashboard",
        url_name="dashboard:home",
        active_view_prefixes=("dashboard:",),
        allowed_roles=frozenset(role for role, _label in User.Role.choices),
    ),
    NavigationLink(
        label="Vendors",
        url_name="partners:list",
        active_view_prefixes=("partners:",),
        allowed_roles=INVENTORY_VIEW_ROLES,
    ),
    NavigationDropdown(
        label="Inventory",
        allowed_roles=INVENTORY_VIEW_ROLES,
        children=(
            NavigationLink(
                label="Raw Materials",
                url_name="inventory:list",
                active_view_prefixes=(
                    "inventory:list",
                    "inventory:csv_template",
                    "inventory:edit",
                    "inventory:delete",
                    "inventory:adjust",
                    "inventory:release_production_request",
                    "inventory:reject_production_request",
                ),
                allowed_roles=INVENTORY_VIEW_ROLES,
            ),
            NavigationLink(
                label="Parts",
                url_name="inventory:parts_list",
                active_view_prefixes=("inventory:parts_list",),
                allowed_roles=INVENTORY_VIEW_ROLES,
            ),
            NavigationLink(
                label="Finished Products",
                url_name="inventory:finished_products_list",
                active_view_prefixes=("inventory:finished_products_list",),
                allowed_roles=INVENTORY_VIEW_ROLES,
            ),
            NavigationLink(
                label="MRO",
                url_name="inventory:mro_list",
                active_view_prefixes=("inventory:mro_",),
                allowed_roles=INVENTORY_VIEW_ROLES,
            ),
        ),
    ),
    NavigationLink(
        label="Purchase Orders",
        url_name="purchasing:list",
        active_view_prefixes=("purchasing:",),
        allowed_roles=INVENTORY_VIEW_ROLES,
    ),
    NavigationLink(
        label="Products and Parts",
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
    NavigationLink(
        label="Production Orders",
        url_name="production:orders",
        active_view_prefixes=(
            "production:orders",
            "production:update_status",
            "production:cancel_order",
        ),
        allowed_roles=PRODUCTION_VIEW_ROLES,
    ),
    NavigationLink(
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


def _is_active(*, view_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(view_name.startswith(prefix) for prefix in prefixes)


def build_navigation_items(*, role: str, view_name: str | None) -> list[dict[str, object]]:
    current_view_name = view_name or ""
    items: list[dict[str, object]] = []

    for item in NAV_ITEMS:
        if role not in item.allowed_roles:
            continue

        if isinstance(item, NavigationLink):
            items.append(
                {
                    "type": "link",
                    "label": item.label,
                    "url_name": item.url_name,
                    "is_active": _is_active(view_name=current_view_name, prefixes=item.active_view_prefixes),
                }
            )
            continue

        children: list[dict[str, object]] = []
        is_active = False
        for child in item.children:
            if role not in child.allowed_roles:
                continue
            child_active = _is_active(view_name=current_view_name, prefixes=child.active_view_prefixes)
            if child_active:
                is_active = True
            children.append(
                {
                    "label": child.label,
                    "url_name": child.url_name,
                    "is_active": child_active,
                }
            )

        if children:
            items.append(
                {
                    "type": "dropdown",
                    "label": item.label,
                    "children": children,
                    "is_active": is_active,
                }
            )

    return items
