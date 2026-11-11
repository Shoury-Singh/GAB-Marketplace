from django.db import migrations


def create_early_adopter_plan(apps, schema_editor):
    CommissionPlan = apps.get_model("gallery", "CommissionPlan")
    CommissionPlan.objects.using(schema_editor.connection.alias).get_or_create(
        code="early-adopter",
        defaults={
            "name": "Early adopter",
            "commission_rate_bps": 100,
            "gst_rate_bps": None,
            "is_default": True,
            "is_active": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("gallery", "0012_transactions_and_commission_ledger"),
    ]

    operations = [
        migrations.RunPython(create_early_adopter_plan, migrations.RunPython.noop),
    ]
