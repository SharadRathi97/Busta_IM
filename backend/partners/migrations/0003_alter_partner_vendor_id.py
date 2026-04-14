from django.db import migrations, models


def backfill_null_vendor_ids(apps, schema_editor):
    Partner = apps.get_model("partners", "Partner")
    for partner in Partner.objects.filter(vendor_id__isnull=True):
        partner.vendor_id = f"AUTO-{partner.pk}"
        partner.save(update_fields=["vendor_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("partners", "0002_partner_vendor_id"),
    ]

    operations = [
        migrations.RunPython(backfill_null_vendor_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="partner",
            name="vendor_id",
            field=models.CharField(max_length=50, unique=True),
        ),
    ]
