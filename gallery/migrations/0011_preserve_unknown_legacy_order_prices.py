from django.db import migrations, models


def clear_estimated_legacy_values(apps, schema_editor):
    """Do not present today's listing price as a historical agreed price."""
    Order = apps.get_model('gallery', 'Order')
    database = schema_editor.connection.alias
    batch = []
    for order in Order.objects.using(database).all().iterator():
        snapshot = order.product_snapshot or {}
        if snapshot.get('captured_at'):
            continue
        order.unit_price = None
        order.total_amount = None
        order.product_snapshot = {}
        batch.append(order)
        if len(batch) >= 500:
            Order.objects.using(database).bulk_update(
                batch,
                ['unit_price', 'total_amount', 'product_snapshot'],
            )
            batch.clear()
    if batch:
        Order.objects.using(database).bulk_update(
            batch,
            ['unit_price', 'total_amount', 'product_snapshot'],
        )


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0010_order_price_snapshot_and_quote_conversion'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='unit_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AlterField(
            model_name='order',
            name='total_amount',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=22, null=True),
        ),
        migrations.RunPython(clear_estimated_legacy_values, migrations.RunPython.noop),
    ]
