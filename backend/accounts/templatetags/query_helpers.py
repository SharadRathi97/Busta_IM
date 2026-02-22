from __future__ import annotations

from django import template


register = template.Library()


@register.simple_tag
def replace_query(request, **kwargs) -> str:
    params = request.GET.copy()
    for key, value in kwargs.items():
        if value in {None, ""}:
            params.pop(key, None)
            continue
        params[key] = str(value)

    encoded = params.urlencode()
    return f"?{encoded}" if encoded else ""
