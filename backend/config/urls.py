from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("dashboard.urls")),
    path("", include("accounts.urls")),
    path("vendors/", include("partners.urls")),
    path("materials/", include("inventory.urls")),
    path("", include("production.urls")),
    path("purchase-orders/", include("purchasing.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
