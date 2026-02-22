from django.urls import path

from . import views

app_name = "production"

urlpatterns = [
    path("products/", views.product_bom_page, name="products"),
    path("products/<int:product_id>/delete/", views.delete_finished_product, name="delete_product"),
    path("products/bom/csv/template/", views.bom_csv_template, name="bom_csv_template"),
    path("products/<int:product_id>/bom/export/excel/", views.export_product_bom_excel, name="export_bom_excel"),
    path("products/<int:product_id>/bom/export/pdf/", views.export_product_bom_pdf, name="export_bom_pdf"),
    path("products/bom/<int:bom_id>/update/", views.update_bom_item, name="update_bom"),
    path("products/bom/<int:bom_id>/delete/", views.delete_bom_item, name="delete_bom"),
    path("orders/", views.production_orders_page, name="orders"),
    path("orders/status/", views.update_production_status, name="update_status"),
    path("orders/<int:order_id>/cancel/", views.cancel_production_order_action, name="cancel_order"),
]
