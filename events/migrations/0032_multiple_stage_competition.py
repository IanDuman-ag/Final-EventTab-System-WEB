from django.db import migrations, models


def forwards_normalize_formats(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    Event.objects.filter(event_format='multiple_rounds').update(event_format='multiple_stage')
    for event in Event.objects.filter(event_format='preliminary_final'):
        cfg = {}
        if isinstance(event.rounds_config, list) and event.rounds_config:
            first = event.rounds_config[0]
            if isinstance(first, dict):
                cfg = first
        elif isinstance(event.rounds_config, dict):
            cfg = event.rounds_config
        event.event_format = 'multiple_stage'
        event.rounds_config = [
            {
                'stage_number': 1,
                'name': 'Preliminary Round',
                'weight': float(cfg.get('prelim_percentage') or 40),
                'qualification_method': 'top_ranking',
                'qualifiers': int(cfg.get('finalists') or 4),
                'minimum_score': None,
                'carry_previous_scores': False,
                'require_faculty_confirmation': True,
                'is_final': False,
                'status': 'open',
            },
            {
                'stage_number': 2,
                'name': 'Final Round',
                'weight': float(cfg.get('final_percentage') or 60),
                'qualification_method': None,
                'qualifiers': None,
                'minimum_score': None,
                'carry_previous_scores': bool(cfg.get('carry_prelim_scores', True)),
                'require_faculty_confirmation': False,
                'is_final': True,
                'status': 'locked',
            },
        ]
        event.save(update_fields=['event_format', 'rounds_config'])


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0031_adminreporthistory'),
    ]

    operations = [
        migrations.AlterField(
            model_name='event',
            name='event_format',
            field=models.CharField(
                blank=True,
                choices=[
                    ('single_performance', 'Single Performance'),
                    ('multiple_stage', 'Multiple Stage Competition'),
                    ('multiple_rounds', 'Multiple Rounds'),
                    ('preliminary_final', 'Preliminary and Final Round'),
                ],
                default='single_performance',
                max_length=40,
            ),
        ),
        migrations.RunPython(forwards_normalize_formats, backwards_noop),
    ]
