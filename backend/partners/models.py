from django.core.validators import RegexValidator
from django.db import models


gst_validator = RegexValidator(
    regex=r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[A-Z0-9]{1}Z[A-Z0-9]{1}$",
    message="Enter a valid GSTIN.",
)


class Partner(models.Model):
    class PartnerType(models.TextChoices):
        SUPPLIER = "supplier", "Supplier"
        BUYER = "buyer", "Buyer"
        BOTH = "both", "Both"

    vendor_id = models.CharField(max_length=50, unique=True, null=True)
    name = models.CharField(max_length=150, unique=True)
    partner_type = models.CharField(max_length=16, choices=PartnerType.choices)
    gst_number = models.CharField(max_length=15, validators=[gst_validator])
    address_line1 = models.CharField(max_length=255)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    pincode = models.CharField(max_length=6, validators=[RegexValidator(r"^[0-9]{6}$", "Enter a valid 6-digit pincode.")])
    contact_person = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        if self.vendor_id:
            return f"{self.vendor_id} - {self.name}"
        return self.name

    @property
    def full_address(self) -> str:
        parts = [self.address_line1]
        if self.address_line2:
            parts.append(self.address_line2)
        parts.extend([self.city, self.state, self.pincode])
        return ", ".join(part for part in parts if part)
