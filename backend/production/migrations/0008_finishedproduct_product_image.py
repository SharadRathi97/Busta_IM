from django.db import migrations, models

import production.models


class Migration(migrations.Migration):
    dependencies = [
        ("production", "0007_finishedproduct_colour"),
    ]

    operations = [
        migrations.AddField(
            model_name="finishedproduct",
            name="product_image",
            field=models.ImageField(blank=True, upload_to=production.models.finished_product_image_upload_path),
        ),
    ]
