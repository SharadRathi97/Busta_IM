from django.urls import path

from . import views

app_name = "purchasing"

urlpatterns = [
    path("", views.purchase_order_page, name="list"),
    path("<int:po_id>/receive/", views.receive_purchase_order_page, name="receive"),
    path("<int:po_id>/cancel/", views.cancel_purchase_order_action, name="cancel"),
    path("<int:po_id>/reopen/", views.reopen_purchase_order_action, name="reopen"),
    path("<int:po_id>/export/excel/", views.export_purchase_order_excel, name="export_excel"),
    path("<int:po_id>/export/pdf/", views.export_purchase_order_pdf, name="export_pdf"),
]
