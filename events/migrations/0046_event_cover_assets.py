from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('events', '0045_ocr_score_sheets')]

    operations = [
        migrations.CreateModel(
            name='EventCoverAsset',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=40, unique=True)),
                ('image', models.ImageField(blank=True, upload_to='event_covers/')),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('generating', 'Generating'), ('ready', 'Ready'), ('failed', 'Failed')], default='pending', max_length=12)),
                ('source', models.CharField(blank=True, max_length=12)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('error', models.CharField(blank=True, max_length=200)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='EventCoverBudget',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('day', models.DateField(unique=True)),
                ('attempts', models.PositiveIntegerField(default=0)),
            ],
        ),
    ]
