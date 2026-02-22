"""
Environment selector for Django settings.

Set DJANGO_ENV to:
- development (default)
- production
"""

from __future__ import annotations

import os

django_env = os.getenv("DJANGO_ENV", "development").strip().lower()

if django_env in {"production", "prod"}:
    from .settings_prod import *  # noqa: F401,F403
else:
    from .settings_dev import *  # noqa: F401,F403
