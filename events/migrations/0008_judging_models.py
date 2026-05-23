import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0007_scoresheet'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EventCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('category_type', models.CharField(choices=[('academic', 'Academic Event'), ('esports', 'Esports Event'), ('sports', 'Sports Event'), ('socio_cultural', 'Socio Cultural')], default='academic', max_length=30)),
                ('description', models.TextField(blank=True)),
                ('icon', models.CharField(default='school', max_length=50)),
                ('color', models.CharField(default='#2196F3', max_length=7)),
            ],
            options={
                'verbose_name_plural': 'Event Categories',
            },
        ),
        migrations.CreateModel(
            name='JudgingEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200)),
                ('date', models.DateField()),
                ('time', models.TimeField()),
                ('venue', models.CharField(max_length=200)),
                ('status', models.CharField(choices=[('upcoming', 'Upcoming'), ('active', 'Active'), ('completed', 'Completed')], default='upcoming', max_length=20)),
                ('description', models.TextField(blank=True)),
                ('assigned_judges', models.ManyToManyField(blank=True, related_name='assigned_judging_events', to=settings.AUTH_USER_MODEL)),
                ('category', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='events.eventcategory')),
            ],
            options={
                'ordering': ['date', 'time'],
            },
        ),
        migrations.CreateModel(
            name='Criterion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('description', models.CharField(blank=True, max_length=200)),
                ('max_score', models.DecimalField(decimal_places=1, max_digits=5)),
                ('weight_percent', models.DecimalField(decimal_places=1, max_digits=5)),
                ('order', models.PositiveIntegerField(default=0)),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='criteria', to='events.judgingevent')),
            ],
            options={
                'ordering': ['order'],
            },
        ),
        migrations.CreateModel(
            name='Candidate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('number', models.PositiveIntegerField()),
                ('photo', models.ImageField(blank=True, null=True, upload_to='candidates/')),
                ('description', models.TextField(blank=True)),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='candidates', to='events.judgingevent')),
            ],
            options={
                'ordering': ['number'],
                'unique_together': {('event', 'number')},
            },
        ),
        migrations.CreateModel(
            name='JudgeScore',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('score', models.DecimalField(decimal_places=1, max_digits=5)),
                ('is_locked', models.BooleanField(default=False)),
                ('submitted_at', models.DateTimeField(blank=True, null=True)),
                ('verification_id', models.CharField(blank=True, max_length=50)),
                ('candidate', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='scores', to='events.candidate')),
                ('criterion', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='scores', to='events.criterion')),
                ('judge', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='judge_scores', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('judge', 'candidate', 'criterion')},
            },
        ),
    ]
