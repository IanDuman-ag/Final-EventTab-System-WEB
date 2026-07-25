import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('events', '0027_criteria_event_workflow'),
    ]

    operations = [
        migrations.CreateModel(
            name='ScoresheetTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('event_type', models.CharField(choices=[('match', 'Match-Based'), ('criteria', 'Criteria-Based')], default='match', max_length=20)),
                ('category', models.CharField(blank=True, default='', max_length=100)),
                ('status', models.CharField(choices=[('active', 'Active'), ('archived', 'Archived')], default='active', max_length=20)),
                ('paper_size', models.CharField(default='a4', max_length=20)),
                ('orientation', models.CharField(choices=[('portrait', 'Portrait'), ('landscape', 'Landscape')], default='portrait', max_length=20)),
                ('layout', models.JSONField(blank=True, default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='scoresheet_templates', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-updated_at'],
            },
        ),
        migrations.CreateModel(
            name='GeneratedScoresheet',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_label', models.CharField(blank=True, default='', max_length=200)),
                ('item_label', models.CharField(blank=True, default='', max_length=200)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('file', models.FileField(blank=True, upload_to='scoresheets/generated/')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('event', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='generated_scoresheets', to='events.event')),
                ('generated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='generated_scoresheets', to=settings.AUTH_USER_MODEL)),
                ('template', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='generated', to='events.scoresheettemplate')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
