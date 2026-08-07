from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0036_event_pageant_config'),
    ]

    operations = [
        migrations.CreateModel(
            name='EventScoringCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('judge_mode', models.CharField(choices=[('scoring', 'Scoring Mode'), ('ranking', 'Ranking Mode')], max_length=12)),
                ('display_order', models.PositiveIntegerField()),
                ('overall_weight_percent', models.DecimalField(decimal_places=2, max_digits=5)),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='scoring_categories', to='events.event')),
            ],
            options={'ordering': ['display_order', 'id']},
        ),
        migrations.CreateModel(
            name='EventScoringCriterion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('weight_percent', models.DecimalField(decimal_places=2, max_digits=5)),
                ('max_score', models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True)),
                ('display_order', models.PositiveIntegerField()),
                ('category', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='criteria', to='events.eventscoringcategory')),
            ],
            options={'ordering': ['display_order', 'id']},
        ),
        migrations.AddConstraint(
            model_name='eventscoringcategory',
            constraint=models.UniqueConstraint(fields=('event', 'name'), name='unique_event_scoring_category_name'),
        ),
        migrations.AddConstraint(
            model_name='eventscoringcategory',
            constraint=models.UniqueConstraint(fields=('event', 'display_order'), name='unique_event_scoring_category_order'),
        ),
        migrations.AddConstraint(
            model_name='eventscoringcriterion',
            constraint=models.UniqueConstraint(fields=('category', 'name'), name='unique_category_criterion_name'),
        ),
        migrations.AddConstraint(
            model_name='eventscoringcriterion',
            constraint=models.UniqueConstraint(fields=('category', 'display_order'), name='unique_category_criterion_order'),
        ),
    ]
