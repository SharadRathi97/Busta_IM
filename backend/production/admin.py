from django.contrib import admin

from .models import BOMItem, FinishedProduct, FinishedStock, FinishedStockLedger, ProductionConsumption, ProductionOrder


@admin.register(FinishedProduct)
class FinishedProductAdmin(admin.ModelAdmin):
    list_display = ("name", "sku", "created_at")
    search_fields = ("name", "sku")


@admin.register(BOMItem)
class BOMItemAdmin(admin.ModelAdmin):
    list_display = ("product", "material", "qty_per_unit")
    list_filter = ("product",)
    search_fields = ("product__name", "material__name")


class ProductionConsumptionInline(admin.TabularInline):
    model = ProductionConsumption
    extra = 0


@admin.register(ProductionOrder)
class ProductionOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "product", "planned_qty", "produced_qty", "scrap_qty", "status", "created_by", "created_at")
    list_filter = ("status",)
    search_fields = ("product__name",)
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
