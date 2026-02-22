from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import AuditLog, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Role", {"fields": ("role",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Role", {"fields": ("role",)}),)
    list_display = ("username", "first_name", "last_name", "role", "is_active", "is_staff")
    list_filter = ("role", "is_active", "is_staff")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "app_label", "model_name", "object_pk", "actor_username")
    list_filter = ("action", "app_label", "model_name", "actor_role", "created_at")
    search_fields = ("model_name", "object_pk", "object_repr", "actor_username")
    readonly_fields = (
        "app_label",
        "model_name",
        "table_name",
        "object_pk",
        "object_repr",
        "action",
        "details",
        "actor",
        "actor_username",
        "actor_role",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
