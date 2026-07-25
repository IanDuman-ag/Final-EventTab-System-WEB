from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('events', '0026_bracketmatch_client_key'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='participation_type',
            field=models.CharField(
                blank=True,
                choices=[('team', 'Team'), ('individual', 'Individual')],
                default='team',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='event',
            name='event_format',
            field=models.CharField(
                blank=True,
                choices=[
                    ('single_performance', 'Single Performance'),
                    ('multiple_rounds', 'Multiple Rounds'),
                    ('preliminary_final', 'Preliminary and Final Round'),
                ],
                default='single_performance',
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name='event',
            name='criteria_score_method',
            field=models.CharField(
                blank=True,
                choices=[
                    ('weighted_percentage', 'Weighted Percentage'),
                    ('raw_score', 'Raw Score'),
                    ('average_score', 'Average Score'),
                    ('ranking_based', 'Ranking-Based Score'),
                ],
                default='weighted_percentage',
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name='event',
            name='rules_guidelines',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='event',
            name='rules_file',
            field=models.FileField(blank=True, null=True, upload_to='event_rules/'),
        ),
        migrations.AddField(
            model_name='event',
            name='rounds_config',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='event',
            name='judging_criteria_config',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='event',
            name='score_settings',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='event',
            name='deductions_enabled',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='event',
            name='deductions_config',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='event',
            name='result_processing_config',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='event',
            name='judge_settings',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='event',
            name='tie_break_rules',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='event',
            name='participant_ids',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='event',
            name='chief_judge',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='chief_judge_events',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='event',
            name='results_finalized',
            field=models.BooleanField(default=False),
        ),
    ]
