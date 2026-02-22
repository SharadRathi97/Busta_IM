from __future__ import annotations

from django.db import models
from django.db.models import Sum
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from inventory.models import InventoryLedger, RawMaterial
from partners.models import Partner
from purchasing.models import PurchaseOrder
from production.models import FinishedProduct, FinishedStock, ProductionOrder


@login_required
def home(request):
    total_materials = RawMaterial.objects.count()
    total_products = FinishedProduct.objects.count()
    total_partners = Partner.objects.count()
    total_finished_stock = FinishedStock.objects.aggregate(total=Sum("current_stock"))["total"] or 0
    in_progress = ProductionOrder.objects.filter(
        status__in=[ProductionOrder.Status.PLANNED, ProductionOrder.Status.IN_PROGRESS]
    ).count()
    completed_production_orders = ProductionOrder.objects.filter(status=ProductionOrder.Status.COMPLETED).count()
    total_production_scrap = (
        ProductionOrder.objects.filter(status=ProductionOrder.Status.COMPLETED).aggregate(total=Sum("scrap_qty"))["total"]
        or 0
    )
    open_purchase_orders = PurchaseOrder.objects.filter(
        status__in=[PurchaseOrder.Status.OPEN, PurchaseOrder.Status.PARTIALLY_RECEIVED]
    ).count()
    low_stock_items = RawMaterial.objects.filter(current_stock__lte=models.F("reorder_level")).order_by("current_stock")[:10]
    recent_ledger = InventoryLedger.objects.select_related("material", "created_by")[:10]

    context = {
        "total_materials": total_materials,
        "total_products": total_products,
        "total_partners": total_partners,
        "total_finished_stock": total_finished_stock,
        "in_progress": in_progress,
        "completed_production_orders": completed_production_orders,
        "total_production_scrap": total_production_scrap,
        "open_purchase_orders": open_purchase_orders,
        "low_stock_items": low_stock_items,
        "recent_ledger": recent_ledger,
    }
    return render(request, "dashboard/home.html", context)
