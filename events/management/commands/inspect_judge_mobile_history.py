"""Private, read-only evidence; no apply/repair/force option exists."""
import json

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction

from events.mobile_historical_review import historical_identity_report
from events.models import Event


class Command(BaseCommand):
    help = 'Read-only historical criterion/pair/score comparison. Never approves or applies setup.'

    def add_arguments(self, parser):
        parser.add_argument('--judging-event-id', type=int, required=True)
        parser.add_argument('--round-id', required=True)
        parser.add_argument('--include-score-values', action='store_true',
            help='Include private raw values in the operator report; never send this report to application logs.')

    def handle(self, *args, **options):
        top_level = not connection.in_atomic_block
        with transaction.atomic():
            if connection.vendor == 'postgresql' and top_level:
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            event = Event.objects.filter(judging_event_id=options['judging_event_id'], scoring_method='criteria').select_related('judging_event').first()
            if event is None:
                raise CommandError('Select an existing linked criteria JudgingEvent.')
            try:
                report = historical_identity_report(event, options['round_id'],
                    include_score_values=options['include_score_values'])
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(json.dumps(report, indent=2, cls=DjangoJSONEncoder))
