"""Preview by default; explicit authorized application is a separate operator step."""
import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from events.mobile_configuration import MobileConfigurationError, apply_configuration, configuration_plan
from events.models import Event, JudgingEvent


class Command(BaseCommand):
    help = 'Preview missing mobile setup for an unscored criteria event. Never unlocks or publishes.'

    def add_arguments(self, parser):
        parser.add_argument('--judging-event-id', required=True, type=int)
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--actor-id', type=int)
        parser.add_argument('--approve-plan')

    def handle(self, *args, **options):
        mobile_id = options['judging_event_id']
        if options['apply']:
            actor = get_user_model().objects.filter(pk=options.get('actor_id')).first()
            if actor is None or not options.get('approve_plan'):
                raise CommandError('--apply requires an authorized --actor-id and the reviewed --approve-plan fingerprint.')
            try:
                result = apply_configuration(mobile_id, actor, options['approve_plan'])
            except (MobileConfigurationError, Event.DoesNotExist, JudgingEvent.DoesNotExist) as exc:
                raise CommandError(str(exc)) from exc
        else:
            top_level = not connection.in_atomic_block
            with transaction.atomic():
                if connection.vendor == 'postgresql' and top_level:
                    with connection.cursor() as cursor:
                        cursor.execute('SET TRANSACTION READ ONLY')
                event = Event.objects.filter(judging_event_id=mobile_id, scoring_method='criteria').select_related('judging_event').first()
                if event is None:
                    raise CommandError('Select an existing linked criteria JudgingEvent ID.')
                result = {'read_only': True, **configuration_plan(event)}
        self.stdout.write(json.dumps(result, indent=2))
