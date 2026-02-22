from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("users/", views.user_list_create, name="user_list"),
    path("users/<int:user_id>/delete/", views.delete_user, name="delete_user"),
    path(
        "users/transactions/<str:scope>/download/",
        views.download_transaction_history,
        name="download_transactions",
    ),
    path("users/<int:user_id>/deactivate/", views.deactivate_user, name="deactivate_user"),
]
