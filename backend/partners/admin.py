from django.contrib import admin

from .models import Partner


@admin.register(Partner)
class PartnerAdmin(admin.ModelAdmin):
    list_display = ("vendor_id", "name", "partner_type", "gst_number", "city", "state")
    search_fields = ("vendor_id", "name", "gst_number", "city")
    list_filter = ("partner_type", "state")
