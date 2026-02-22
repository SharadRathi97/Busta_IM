from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse


def load_dotenv(path: str | Path | None) -> None:
    if not path:
        return
    dotenv_path = Path(path)
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: list[str] | None = None) -> list[str]:
    raw_value = os.getenv(name, "")
    if not raw_value:
        return list(default or [])
    return [part.strip() for part in raw_value.split(",") if part.strip()]


def parse_database_url(database_url: str | None, *, default_sqlite_path: Path) -> dict[str, str | dict[str, str]]:
    if not database_url:
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(default_sqlite_path),
        }

    parsed = urlparse(database_url)
    scheme = parsed.scheme.lower()

    if scheme in {"postgres", "postgresql", "pgsql", "postgresql+psycopg"}:
        if not parsed.path or parsed.path == "/":
            raise ValueError("DATABASE_URL is missing a database name.")
        db_name = unquote(parsed.path.lstrip("/"))
        config: dict[str, str | dict[str, str]] = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": db_name,
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or ""),
        }
        query_params = dict(parse_qsl(parsed.query, keep_blank_values=False))
        if query_params:
            config["OPTIONS"] = query_params
        return config

    if scheme == "sqlite":
        db_path = unquote(parsed.path or "")
        if parsed.netloc and parsed.netloc not in {"", "localhost"}:
            db_path = f"/{parsed.netloc}{db_path}"
        if db_path in {"", "/"}:
            db_path = str(default_sqlite_path)
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": db_path,
        }

    raise ValueError(f"Unsupported database scheme '{scheme}' in DATABASE_URL.")
