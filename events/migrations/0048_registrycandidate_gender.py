from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('events', '0047_schedule_day_navigation')]

    operations = [
        migrations.AddField(
            model_name='registrycandidate',
            name='gender',
            field=models.CharField(blank=True, choices=[('', 'Not specified'), ('male', 'Male'),
                ('female', 'Female'), ('other', 'Other')], default='', max_length=10),
        ),
    ]
