"""Per-assignment readiness and own-history counts, with no private score output."""
import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from core.views import get_assignment_role
from events.mobile_judge import assigned_events, detail_payload, history_payload


class Command(BaseCommand):
    help = 'Read-only Judge assignment readiness audit; never creates mappings or changes round state.'

    def add_arguments(self, parser):
        parser.add_argument('--judge-id', required=True, type=int)

    def handle(self, *args, **options):
        top_level = not connection.in_atomic_block
        with transaction.atomic():
            if connection.vendor == 'postgresql' and top_level:
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION READ ONLY')
            user = get_user_model().objects.filter(pk=options['judge_id'], is_active=True).first()
            if user is None or get_assignment_role(user) != 'Judge':
                raise CommandError('Select an existing active Judge account.')
            history = history_payload(user)
            reports = []
            for event in assigned_events(user):
                detail = detail_payload(user, event)
                own = [row for row in history if row['judging_event_id'] == event.pk]
                reports.append({key: detail[key] for key in (
                    'judging_event_id', 'portal_event_id', 'title', 'participation_type',
                    'status', 'selected_round_id', 'current_round', 'can_score', 'configuration_required',
                    'scoring_unavailable_reasons', 'participants_total', 'participants_scored',
                )})
                reports[-1]['history_counts'] = {state: sum(row['status'] == state for row in own)
                    for state in ('draft', 'submitted', 'returned')}
            self.stdout.write(json.dumps({'read_only': True, 'assignments': reports}, indent=2))
