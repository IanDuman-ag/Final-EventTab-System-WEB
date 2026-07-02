from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0021_registrycandidate'),
    ]

    operations = [
        migrations.AddField(
            model_name='registrycandidate',
            name='image',
            field=models.ImageField(blank=True, null=True, upload_to='registry_candidates/'),
        ),
    ]
