from django.contrib import admin

from .models import InventoryLedger, MROInventoryLedger, MROItem, RawMaterial, RawMaterialVendor


class RawMaterialVendorInline(admin.TabularInline):
    model = RawMaterialVendor
    extra = 1


@admin.register(RawMaterial)
class RawMaterialAdmin(admin.ModelAdmin):
    list_display = ("rm_id", "name", "code", "colour_code", "material_type", "unit", "current_stock", "reorder_level", "vendor", "supplier_names")
    search_fields = ("rm_id", "name", "code", "colour_code")
    list_filter = ("material_type", "unit", "vendor")
    inlines = [RawMaterialVendorInline]


@admin.register(RawMaterialVendor)
class RawMaterialVendorAdmin(admin.ModelAdmin):
    list_display = ("material", "vendor", "created_at")
    search_fields = ("material__name", "material__code", "vendor__name")
    list_filter = ("vendor",)


@admin.register(InventoryLedger)
class InventoryLedgerAdmin(admin.ModelAdmin):
    list_display = ("id", "material", "txn_type", "quantity", "unit", "reason", "created_at")
    search_fields = ("material__name", "reason", "reference_type")
    list_filter = ("txn_type", "created_at")


@admin.register(MROItem)
class MROItemAdmin(admin.ModelAdmin):
    list_display = (
        "mro_id",
        "name",
        "code",
        "item_type",
        "unit",
        "current_stock",
        "reorder_level",
        "location",
        "vendor",
    )
    search_fields = ("mro_id", "name", "code", "location")
    list_filter = ("item_type", "unit", "vendor")


@admin.register(MROInventoryLedger)
class MROInventoryLedgerAdmin(admin.ModelAdmin):
    list_display = ("id", "item", "txn_type", "quantity", "unit", "reason", "created_at")
    search_fields = ("item__name", "reason", "reference_type")
    list_filter = ("txn_type", "created_at")
