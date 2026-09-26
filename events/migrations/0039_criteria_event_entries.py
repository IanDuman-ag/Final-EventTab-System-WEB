from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q


def forwards(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    Candidate = apps.get_model('events', 'RegistryCandidate')
    Team = apps.get_model('events', 'Team')
    Entry = apps.get_model('events', 'CriteriaEventEntry')
    Score = apps.get_model('events', 'CriteriaScoreSubmission')
    for event in Event.objects.filter(scoring_method='criteria').iterator():
        source_model = Candidate if event.participation_type == 'individual' else Team
        entry_type = 'individual' if event.participation_type == 'individual' else 'team'
        mapping = {}
        for order, source_id in enumerate(event.participant_ids or []):
            source = source_model.objects.filter(pk=source_id).first()
            defaults = {'event_id': event.id, 'entry_type': entry_type, 'display_order': order,
                        'display_name': str(source) if source else f'Legacy participant {source_id}'}
            if source:
                defaults['department_id'] = source.department_id
                defaults['source_individual_id' if entry_type == 'individual' else 'source_team_id'] = source.id
            entry = Entry.objects.create(**defaults)
            mapping[int(source_id)] = entry.id
        event.participant_ids = [mapping.get(int(value), value) for value in (event.participant_ids or [])]
        cfg = dict(event.result_processing_config or {})
        qualified = dict(cfg.get('qualified_participant_ids') or {})
        for key, values in qualified.items():
            qualified[key] = [mapping.get(int(value), value) for value in (values or [])]
        if qualified:
            cfg['qualified_participant_ids'] = qualified
            event.result_processing_config = cfg
        event.save(update_fields=['participant_ids', 'result_processing_config'])
        for score in Score.objects.filter(event_id=event.id).iterator():
            if score.participant_id in mapping:
                score.participant_id = mapping[score.participant_id]
                score.participant_type = 'event_entry'
                score.save(update_fields=['participant_id', 'participant_type'])


class Migration(migrations.Migration):
    dependencies = [('events', '0038_criteria_workflow_rounds_sources')]
    operations = [
        migrations.AlterField(model_name='event', name='participation_type', field=models.CharField(blank=True, choices=[('team', 'Team'), ('group', 'Group'), ('individual', 'Individual'), ('pair', 'Pair')], default='team', max_length=20)),
        migrations.CreateModel(name='CriteriaEventEntry', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('entry_type', models.CharField(choices=[('individual', 'Individual'), ('pair', 'Pair'), ('team', 'Team / Group')], max_length=12)),
            ('display_name', models.CharField(max_length=420)), ('display_order', models.PositiveIntegerField(default=0)),
            ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
            ('department', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='criteria_event_entries', to='events.department')),
            ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='criteria_entries', to='events.event')),
            ('source_individual', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='criteria_event_entries', to='events.registrycandidate')),
            ('source_team', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='criteria_event_entries', to='events.team')),
        ], options={'ordering': ['display_order', 'id']}),
        migrations.CreateModel(name='CriteriaEventEntryMember', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('position', models.PositiveSmallIntegerField()),
            ('entry', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='member_links', to='events.criteriaevententry')),
            ('individual', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='criteria_pair_memberships', to='events.registrycandidate')),
        ], options={'ordering': ['position', 'id']}),
        migrations.AddConstraint(model_name='criteriaevententry', constraint=models.UniqueConstraint(fields=('event', 'display_order'), name='unique_criteria_entry_order')),
        migrations.AddConstraint(model_name='criteriaevententry', constraint=models.UniqueConstraint(condition=Q(source_individual__isnull=False), fields=('event', 'source_individual'), name='unique_event_individual_entry')),
        migrations.AddConstraint(model_name='criteriaevententry', constraint=models.UniqueConstraint(condition=Q(source_team__isnull=False), fields=('event', 'source_team'), name='unique_event_team_entry')),
        migrations.AddConstraint(model_name='criteriaevententry', constraint=models.CheckConstraint(condition=(Q(entry_type='individual', source_team__isnull=True) | Q(entry_type='team', source_individual__isnull=True) | Q(entry_type='pair', source_individual__isnull=True, source_team__isnull=True)), name='criteria_entry_source_shape')),
        migrations.AddConstraint(model_name='criteriaevententrymember', constraint=models.UniqueConstraint(fields=('entry', 'individual'), name='unique_criteria_entry_member')),
        migrations.AddConstraint(model_name='criteriaevententrymember', constraint=models.UniqueConstraint(fields=('entry', 'position'), name='unique_criteria_entry_member_position')),
        migrations.AddConstraint(model_name='criteriaevententrymember', constraint=models.CheckConstraint(condition=Q(position__in=[1, 2]), name='criteria_entry_member_position_1_2')),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
