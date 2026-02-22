from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from django.db import IntegrityError
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.db.utils import OperationalError, ProgrammingError
from django.dispatch import receiver

from .audit_context import get_audit_actor
from .models import AuditLog


AUDITED_APP_LABELS = {
    "accounts",
    "partners",
    "inventory",
    "production",
    "purchasing",
}


def _is_auditable_model(sender) -> bool:
    model_meta = getattr(sender, "_meta", None)
    if not model_meta:
        return False
    if model_meta.app_label not in AUDITED_APP_LABELS:
        return False
    if model_meta.model_name == "auditlog":
        return False
    return True


def _serialize_value(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, memoryview):
        return bytes(value).decode("utf-8", errors="replace")
    return value


def _snapshot_instance(instance) -> dict[str, object]:
    snapshot: dict[str, object] = {}
    for field in instance._meta.concrete_fields:
        field_name = field.name
        if field_name == "password":
            snapshot[field_name] = "***"
            continue
        raw_value = field.value_from_object(instance)
        snapshot[field_name] = _serialize_value(raw_value)
    return snapshot


def _object_repr(instance) -> str:
    try:
        return str(instance)
    except Exception:
        return ""


def _create_audit_log(*, instance, action: str, details: dict[str, object]):
    actor = get_audit_actor() or {}
    payload = {
        "app_label": instance._meta.app_label,
        "model_name": instance._meta.model_name,
        "table_name": instance._meta.db_table,
        "object_pk": str(instance.pk),
        "object_repr": _object_repr(instance)[:255],
        "action": action,
        "details": details,
        "actor_username": actor.get("username", ""),
        "actor_role": actor.get("role", ""),
    }
    actor_id = actor.get("id")
    if actor_id:
        payload["actor_id"] = actor_id

    try:
        AuditLog.objects.create(**payload)
    except IntegrityError:
        payload.pop("actor_id", None)
        AuditLog.objects.create(**payload)
    except (OperationalError, ProgrammingError):
        # During migrations/bootstrap, audit table may not yet exist.
        return


@receiver(pre_save, dispatch_uid="accounts_audit_pre_save")
def audit_pre_save(sender, instance, **kwargs):
    if not _is_auditable_model(sender):
        return
    if instance._state.adding or not instance.pk:
        instance._audit_change_set = {}
        return

    existing = sender.objects.filter(pk=instance.pk).first()
    if existing is None:
        instance._audit_change_set = {}
        return

    before_snapshot = _snapshot_instance(existing)
    after_snapshot = _snapshot_instance(instance)
    change_set: dict[str, dict[str, object]] = {}
    for field_name, previous_value in before_snapshot.items():
        current_value = after_snapshot.get(field_name)
        if previous_value != current_value:
            change_set[field_name] = {"from": previous_value, "to": current_value}
    instance._audit_change_set = change_set


@receiver(post_save, dispatch_uid="accounts_audit_post_save")
def audit_post_save(sender, instance, created, **kwargs):
    if not _is_auditable_model(sender):
        return

    if created:
        _create_audit_log(
            instance=instance,
            action=AuditLog.Action.CREATE,
            details={"fields": _snapshot_instance(instance)},
        )
        return

    change_set = getattr(instance, "_audit_change_set", None) or {}
    if not change_set:
        return
    _create_audit_log(
        instance=instance,
        action=AuditLog.Action.UPDATE,
        details={"changes": change_set},
    )


@receiver(pre_delete, dispatch_uid="accounts_audit_pre_delete")
def audit_pre_delete(sender, instance, **kwargs):
    if not _is_auditable_model(sender):
        return
    instance._audit_delete_snapshot = _snapshot_instance(instance)


@receiver(post_delete, dispatch_uid="accounts_audit_post_delete")
def audit_post_delete(sender, instance, **kwargs):
    if not _is_auditable_model(sender):
        return
    delete_snapshot = getattr(instance, "_audit_delete_snapshot", None) or _snapshot_instance(instance)
    _create_audit_log(
        instance=instance,
        action=AuditLog.Action.DELETE,
        details={"fields": delete_snapshot},
    )
