# Generated for first-party marketplace recommendation signals.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0008_sellerbanner_seller_role'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductInteraction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_type', models.CharField(choices=[('PRODUCT_VIEW', 'Product view'), ('SEARCH', 'Search'), ('CLICK', 'Recommendation click'), ('QUOTE_REQUEST', 'Quote request')], max_length=20)),
                ('category', models.CharField(blank=True, max_length=30)),
                ('search_hash', models.CharField(blank=True, max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='interactions', to='gallery.product')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='product_interactions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['user', 'event_type', 'created_at'], name='gallery_pro_user_id_ef7dc2_idx')],
            },
        ),
    ]
