from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0037_event_scoring_categories'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='event',
            name='participation_type',
            field=models.CharField(
                blank=True,
                choices=[('team', 'Team'), ('group', 'Group'), ('individual', 'Individual')],
                default='team',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='eventscoringcategory',
            name='assigned_round_id',
            field=models.CharField(blank=True, default='', max_length=80),
        ),
        migrations.AddField(
            model_name='eventscoringcategory',
            name='category_purpose',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='eventscoringcategory',
            name='purpose',
            field=models.CharField(
                choices=[
                    ('official', 'Official Scoring Category'),
                    ('special_award', 'Special Award Only'),
                ],
                default='official',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='eventscoringcriterion',
            name='judging_description',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='eventscoringcriterion',
            name='min_score',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=7),
        ),
        migrations.AddField(
            model_name='eventscoringcriterion',
            name='tie_breaker_priority',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='CriteriaScoreSubmission',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('assigned_round_id', models.CharField(max_length=80)),
                ('participant_type', models.CharField(default='participant', max_length=20)),
                ('participant_id', models.PositiveIntegerField()),
                ('source', models.CharField(choices=[('MOBILE', 'Judge Mobile'), ('OCR', 'Physical Scoresheet OCR'), ('MANUAL', 'Manual Backup')], max_length=10)),
                ('status', models.CharField(choices=[('DRAFT', 'Draft'), ('SUBMITTED', 'Submitted'), ('NEEDS_REVIEW', 'Needs Review'), ('VERIFIED', 'Verified'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected'), ('REOPENED', 'Reopened'), ('SUPERSEDED', 'Superseded')], default='DRAFT', max_length=20)),
                ('raw_score', models.DecimalField(decimal_places=4, max_digits=9)),
                ('normalized_score', models.DecimalField(decimal_places=4, default=0, max_digits=9)),
                ('is_active', models.BooleanField(default=True)),
                ('submission_key', models.CharField(blank=True, db_index=True, default='', max_length=120)),
                ('replacement_reason', models.TextField(blank=True, default='')),
                ('device_session', models.CharField(blank=True, default='', max_length=200)),
                ('submitted_at', models.DateTimeField(blank=True, null=True)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('category', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='score_submissions', to='events.eventscoringcategory')),
                ('criterion', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='score_submissions', to='events.eventscoringcriterion')),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='criteria_score_submissions', to='events.event')),
                ('judge', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='criteria_score_submissions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['event_id', 'assigned_round_id', 'category_id', 'judge_id', 'participant_id', 'criterion_id'],
            },
        ),
        migrations.AddIndex(
            model_name='criteriascoresubmission',
            index=models.Index(fields=['event', 'assigned_round_id', 'category', 'judge', 'participant_id'], name='events_crit_event_i_332f9b_idx'),
        ),
        migrations.AddIndex(
            model_name='criteriascoresubmission',
            index=models.Index(fields=['event', 'status', 'source', 'is_active'], name='events_crit_event_i_4ca988_idx'),
        ),
        migrations.AddConstraint(
            model_name='criteriascoresubmission',
            constraint=models.UniqueConstraint(condition=models.Q(('is_active', True)), fields=('event', 'assigned_round_id', 'category', 'judge', 'participant_id', 'criterion'), name='unique_active_criteria_score_source'),
        ),
    ]
