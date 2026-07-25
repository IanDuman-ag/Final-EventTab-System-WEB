# Generated for the match-based event management workflow.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('events', '0024_event_assigned_tabulators'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='allow_result_editing',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='event',
            name='apply_championship_points',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='event',
            name='auto_update_bracket',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='event',
            name='championship_points_config',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='event',
            name='daily_end_time',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='event',
            name='daily_start_time',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='event',
            name='end_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='event',
            name='event_classification',
            field=models.CharField(blank=True, choices=[('major', 'Major Event'), ('minor', 'Minor Event')], default='', max_length=10),
        ),
        migrations.AddField(
            model_name='event',
            name='faculty_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='faculty_events', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='event',
            name='include_third_place',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='event',
            name='publication_status',
            field=models.CharField(choices=[('draft', 'Draft'), ('published', 'Published')], default='published', max_length=12),
        ),
        migrations.AddField(
            model_name='event',
            name='require_faculty_confirmation',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='event',
            name='schedule_mode',
            field=models.CharField(choices=[('auto', 'Auto Generate Schedule'), ('manual', 'Manual Scheduling')], default='auto', max_length=10),
        ),
        migrations.AddField(
            model_name='event',
            name='seeding_method',
            field=models.CharField(default='random_draw', max_length=20),
        ),
    ]
