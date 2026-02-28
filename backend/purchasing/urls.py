from django.urls import path

from . import views

app_name = "purchasing"

urlpatterns = [
    path("", views.purchase_order_page, name="list"),
    path("low-stock/create-batch-po-pdf/", views.create_low_stock_vendor_purchase_order_pdf, name="create_low_stock_batch_po_pdf"),
    path("<int:po_id>/approve/inventory/", views.approve_purchase_order_inventory_action, name="approve_inventory"),
    path("<int:po_id>/approve/admin/", views.approve_purchase_order_admin_action, name="approve_admin"),
    path("<int:po_id>/delete-pending/", views.delete_pending_purchase_order_action, name="delete_pending"),
    path("<int:po_id>/receive/", views.receive_purchase_order_page, name="receive"),
    path("<int:po_id>/cancel/", views.cancel_purchase_order_action, name="cancel"),
    path("<int:po_id>/reopen/", views.reopen_purchase_order_action, name="reopen"),
    path("<int:po_id>/export/excel/", views.export_purchase_order_excel, name="export_excel"),
    path("<int:po_id>/export/pdf/", views.export_purchase_order_pdf, name="export_pdf"),
]
