import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0008_judging_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='judging_event',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='portal_event',
                to='events.judgingevent',
            ),
        ),
    ]
