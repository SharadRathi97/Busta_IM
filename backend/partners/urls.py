from django.urls import path

from . import views

app_name = "partners"

urlpatterns = [
    path("", views.partner_list_create, name="list"),
    path("csv/template/", views.partner_csv_template, name="csv_template"),
    path("<int:partner_id>/edit/", views.partner_edit, name="edit"),
    path("<int:partner_id>/delete/", views.partner_delete, name="delete"),
]
