from __future__ import annotations

from .navigation import build_navigation_items
from .models import User


def app_navigation(request):
    user = getattr(request, "user", None)
    if not isinstance(user, User) or not user.is_authenticated:
        return {"app_navigation": []}

    view_name = request.resolver_match.view_name if request.resolver_match else ""
    return {
        "app_navigation": build_navigation_items(role=user.role, view_name=view_name),
    }
