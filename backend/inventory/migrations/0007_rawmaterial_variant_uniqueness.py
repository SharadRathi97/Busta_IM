from django.db import migrations, models


def backfill_raw_material_variant_fields(apps, schema_editor):
    RawMaterial = apps.get_model("inventory", "RawMaterial")
    for material in RawMaterial.objects.all().only("id", "rm_id", "colour", "colour_code"):
        resolved_rm_id = (material.rm_id or "").strip().upper()
        resolved_colour_code = (material.colour_code or "").strip().upper()

        if not resolved_rm_id:
            resolved_rm_id = f"RM-AUTO-{material.id:06d}"

        if not resolved_colour_code:
            colour = (material.colour or "").strip()
            resolved_colour_code = colour[:12].upper() if colour else "NA"

        if resolved_rm_id != material.rm_id or resolved_colour_code != material.colour_code:
            RawMaterial.objects.filter(pk=material.pk).update(
                rm_id=resolved_rm_id,
                colour_code=resolved_colour_code,
            )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0006_rawmaterial_colour_code_rawmaterial_rm_id"),
    ]

    operations = [
        migrations.RunPython(backfill_raw_material_variant_fields, noop_reverse),
        migrations.AlterField(
            model_name="rawmaterial",
            name="colour_code",
            field=models.CharField(max_length=30),
        ),
        migrations.AlterField(
            model_name="rawmaterial",
            name="rm_id",
            field=models.CharField(max_length=50),
        ),
        migrations.AddConstraint(
            model_name="rawmaterial",
            constraint=models.UniqueConstraint(
                fields=("rm_id", "colour_code"),
                name="uniq_raw_material_rm_id_colour_code",
            ),
        ),
    ]
