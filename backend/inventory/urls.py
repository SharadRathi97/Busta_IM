from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.material_list, name="list"),
    path("production-requests/<int:order_id>/release/", views.release_production_request, name="release_production_request"),
    path("production-requests/<int:order_id>/reject/", views.reject_production_request, name="reject_production_request"),
    path("mro/", views.mro_list, name="mro_list"),
    path("mro/<int:item_id>/edit/", views.mro_edit, name="mro_edit"),
    path("mro/<int:item_id>/delete/", views.mro_delete, name="mro_delete"),
    path("mro/adjust/", views.adjust_mro_item_stock, name="mro_adjust"),
    path("csv/template/", views.raw_material_csv_template, name="csv_template"),
    path("<int:material_id>/edit/", views.material_edit, name="edit"),
    path("<int:material_id>/delete/", views.material_delete, name="delete"),
    path("adjust/", views.adjust_material_stock, name="adjust"),
]
