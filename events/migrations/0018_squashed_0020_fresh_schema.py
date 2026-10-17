"""Fresh-schema replacement; retain 0018/0019/0020 for existing histories.

0018 already defines the canonical Team. The following two migrations repair
pre-Django legacy tables and cannot be replayed on a fresh database. Django
uses this replacement only when all or none of those migrations are applied.
Already-migrated installations require no SQL from this replacement.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    replaces = [
        ('events', '0018_team_model'),
        ('events', '0019_align_team_schema'),
        ('events', '0020_team_delete_set_null_on_legacy_match'),
    ]

    dependencies = [('events', '0017_assignment_account_profile')]

    operations = [
        migrations.CreateModel(
            name='Team',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('code', models.CharField(max_length=50, unique=True)),
                ('members', models.TextField(blank=True, default='')),
                ('coach', models.CharField(blank=True, default='', max_length=200)),
                ('status', models.CharField(choices=[('active', 'Active'), ('inactive', 'Inactive')], default='active', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('department', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='teams', to='events.department')),
            ],
            options={'ordering': ['name']},
        ),
    ]
