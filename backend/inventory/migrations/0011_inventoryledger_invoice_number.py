from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0010_remove_rawmaterial_uniq_raw_material_rm_id_colour_code_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventoryledger",
            name="invoice_number",
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
