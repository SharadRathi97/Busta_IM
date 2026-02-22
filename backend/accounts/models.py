from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        INVENTORY_MANAGER = "inventory_manager", "Inventory Manager"
        PRODUCTION_MANAGER = "production_manager", "Production Manager"
        VIEWER = "viewer", "Viewer"

    role = models.CharField(max_length=32, choices=Role.choices, default=Role.VIEWER)

    def is_admin(self) -> bool:
        return self.role == self.Role.ADMIN

    def can_manage_inventory(self) -> bool:
        return self.role in {self.Role.ADMIN, self.Role.INVENTORY_MANAGER}

    def can_manage_production(self) -> bool:
        return self.role in {self.Role.ADMIN, self.Role.PRODUCTION_MANAGER}


class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"

    app_label = models.CharField(max_length=32)
    model_name = models.CharField(max_length=64)
    table_name = models.CharField(max_length=128)
    object_pk = models.CharField(max_length=64)
    object_repr = models.CharField(max_length=255, blank=True)
    action = models.CharField(max_length=16, choices=Action.choices)
    details = models.JSONField(default=dict, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    actor_username = models.CharField(max_length=150, blank=True)
    actor_role = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["app_label", "created_at"]),
            models.Index(fields=["model_name", "created_at"]),
            models.Index(fields=["action", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} {self.action} {self.model_name}#{self.object_pk}"
