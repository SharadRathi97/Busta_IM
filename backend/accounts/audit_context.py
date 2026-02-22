from __future__ import annotations

from contextvars import ContextVar, Token


_audit_actor: ContextVar[dict | None] = ContextVar("audit_actor", default=None)


def set_audit_actor(user) -> Token:
    if user is not None and getattr(user, "is_authenticated", False):
        payload = {
            "id": user.id,
            "username": user.username,
            "role": getattr(user, "role", ""),
        }
        return _audit_actor.set(payload)
    return _audit_actor.set(None)


def get_audit_actor() -> dict | None:
    return _audit_actor.get()


def reset_audit_actor(token: Token) -> None:
    _audit_actor.reset(token)
