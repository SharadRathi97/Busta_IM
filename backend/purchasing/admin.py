from django.contrib import admin

from .models import PurchaseOrder, PurchaseOrderItem


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 0
    readonly_fields = ("received_quantity",)


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "vendor", "order_date", "status", "received_at", "cancelled_at", "created_by", "created_at")
    search_fields = ("vendor__name",)
    list_filter = ("status", "order_date")
    inlines = [PurchaseOrderItemInline]
