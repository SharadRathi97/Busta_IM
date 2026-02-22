from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

PBKDF2_ROUNDS = 200_000
SESSION_TTL_HOURS = 12


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"{PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        rounds_str, salt_hex, digest_hex = stored_hash.split("$", 2)
        rounds = int(rounds_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, TypeError):
        return False

    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(digest, expected)


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def session_expiry_iso() -> str:
    expiry = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    return expiry.replace(microsecond=0).isoformat()
