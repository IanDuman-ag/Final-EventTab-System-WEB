# Generated manually for tabulator event assignment scoping

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0023_team_image_and_candidate_upload_path'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='assigned_tabulators',
            field=models.ManyToManyField(
                blank=True,
                limit_choices_to=models.Q(('groups__name__iexact', 'Tabulator')),
                related_name='tabulating_events',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
