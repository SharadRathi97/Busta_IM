"""Shared view helpers to reduce duplication across apps."""

from __future__ import annotations


def get_sorting(
    sort_key: str,
    direction: str,
    sort_map: dict[str, str],
    default_key: str = "name",
) -> tuple[str, str, str]:
    resolved_key = sort_key if sort_key in sort_map else default_key
    resolved_direction = direction if direction in {"asc", "desc"} else "asc"
    order_field = sort_map[resolved_key]
    if resolved_direction == "desc":
        order_field = f"-{order_field}"
    return resolved_key, resolved_direction, order_field


def build_sort_state(
    keys: list[str],
    active_sort: str,
    active_direction: str,
) -> dict[str, dict[str, str | bool]]:
    state: dict[str, dict[str, str | bool]] = {}
    for key in keys:
        is_active = key == active_sort
        next_direction = "desc" if is_active and active_direction == "asc" else "asc"
        icon = "\u2191" if is_active and active_direction == "asc" else "\u2193" if is_active else "\u2195"
        state[key] = {"active": is_active, "next": next_direction, "icon": icon}
    return state
