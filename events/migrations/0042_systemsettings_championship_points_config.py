from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('events', '0041_mobile_score_precision')]

    operations = [
        migrations.AddField(
            model_name='systemsettings',
            name='championship_points_config',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
