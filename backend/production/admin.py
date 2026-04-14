from django.contrib import admin

from .models import (
    BOMItem,
    FinishedProduct,
    FinishedStock,
    FinishedStockLedger,
    Marker,
    MarkerOutput,
    PartProduction,
    ProductionConsumption,
    ProductionOrder,
)


@admin.register(FinishedProduct)
class FinishedProductAdmin(admin.ModelAdmin):
    list_display = ("name", "colour", "sku", "item_type", "created_at")
    search_fields = ("name", "colour", "sku")


@admin.register(BOMItem)
class BOMItemAdmin(admin.ModelAdmin):
    list_display = ("product", "material", "part", "qty_per_unit")
    list_filter = ("product",)
    search_fields = ("product__name", "material__name", "part__name")


class MarkerOutputInline(admin.TabularInline):
    model = MarkerOutput
    extra = 0


@admin.register(Marker)
class MarkerAdmin(admin.ModelAdmin):
    list_display = ("marker_id", "sku_id", "material", "colour", "length_per_layer", "sets_per_layer")
    search_fields = ("marker_id", "sku_id", "material__name", "colour")
    inlines = [MarkerOutputInline]


class ProductionConsumptionInline(admin.TabularInline):
    model = ProductionConsumption
    extra = 0


@admin.register(ProductionOrder)
class ProductionOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "target_type", "product", "marker", "planned_qty", "produced_qty", "scrap_qty", "status", "created_by", "created_at")
    list_filter = ("target_type", "status")
    search_fields = ("product__name", "marker__marker_id")
    inlines = [ProductionConsumptionInline]


@admin.register(FinishedStock)
class FinishedStockAdmin(admin.ModelAdmin):
    list_display = ("product", "current_stock", "updated_at")
    search_fields = ("product__name", "product__sku")


@admin.register(FinishedStockLedger)
class FinishedStockLedgerAdmin(admin.ModelAdmin):
    list_display = ("id", "product", "txn_type", "quantity", "reference_type", "reference_id", "created_at")
    list_filter = ("txn_type",)
    search_fields = ("product__name", "product__sku", "reason")


@admin.register(PartProduction)
class PartProductionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "marker",
        "production_order",
        "actual_length",
        "actual_layers",
        "actual_sets_per_layer",
        "total_sets",
        "actual_fabric_issued",
        "good_sets",
        "rejected_sets",
        "created_at",
    )
    search_fields = ("marker__marker_id", "production_order__id")
