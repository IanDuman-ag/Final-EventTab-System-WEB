from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('events', '0048_registrycandidate_gender')]

    operations = [
        migrations.AddConstraint(
            model_name='eventscoringcategory',
            constraint=models.UniqueConstraint(
                fields=('event', 'assigned_round_id', 'display_order'),
                name='unique_round_scoring_category_order',
            ),
        ),
        migrations.RemoveConstraint(
            model_name='eventscoringcategory',
            name='unique_event_scoring_category_order',
        ),
    ]
