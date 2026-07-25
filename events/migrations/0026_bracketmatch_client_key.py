from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('events', '0025_match_event_workflow'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='bracket_draw_order',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='bracketteam',
            name='source_team',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='event_snapshots',
                to='events.team',
            ),
        ),
        migrations.AddField(
            model_name='bracketmatch',
            name='client_key',
            field=models.CharField(blank=True, db_index=True, default='', max_length=40),
        ),
    ]
