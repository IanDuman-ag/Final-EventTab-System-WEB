from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0014_bracketteam_is_champion_bracketteam_points_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='candidate',
            name='department',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
    ]
