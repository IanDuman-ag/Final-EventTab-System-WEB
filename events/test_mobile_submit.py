"""Exact Flutter body and production-shaped ownership/locked historical cells."""
from datetime import date, time
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TransactionTestCase
from django.db import connection
from django.core.management import call_command
from io import StringIO
import json
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (Candidate, CriteriaEventEntry, CriteriaScoreSubmission, Criterion,
                     Event, EventCategory, EventScoringCategory, EventScoringCriterion,
                     JudgeScore, JudgingEvent)


class MobileSubmitTests(TransactionTestCase):
    def setUp(self):
        group = Group.objects.create(name='Judge')
        self.judges = [get_user_model().objects.create_user(pk=pk, username=f'judge-{pk}')
                       for pk in (16, 21, 22, 57)]
        for judge in self.judges:
            judge.groups.add(group)
        category = EventCategory.objects.create(name='Pageants')
        self.mobile = JudgingEvent.objects.create(pk=63, title='Pageants', category=category,
            date=date.today(), time=time(9), status='active')
        self.portal = Event.objects.create(pk=77, name='Pageants', category='Cultural', event_date=date.today(),
            scoring_method='criteria', status='active', judging_event=self.mobile,
            rounds_config=[{'id': 'prelim', 'name': 'Preliminary Round', 'status': 'open'}])
        self.mobile.assigned_judges.set(self.judges)
        self.portal.assigned_judges.set(self.judges)
        mapping = {'criteria': {}, 'candidates': {}}
        self.candidates, self.criteria = [], []
        for index in range(8):
            entry = CriteriaEventEntry.objects.create(event=self.portal, entry_type='individual',
                display_name=f'Participant {index + 1}', display_order=index)
            candidate = Candidate.objects.create(event=self.mobile, name=entry.display_name, number=index + 1)
            self.candidates.append(candidate)
            mapping['candidates'][str(entry.pk)] = candidate.pk
        for position, weights in enumerate(((50, 50), (30, 30, 40)), 1):
            category = EventScoringCategory.objects.create(event=self.portal, assigned_round_id='prelim',
                name=f'Category {position}', judge_mode='scoring', display_order=position, overall_weight_percent=50)
            for order, weight in enumerate(weights, 1):
                definition = EventScoringCriterion.objects.create(category=category, name=f'Technique {position}-{order}',
                    display_order=order, weight_percent=weight, max_score=100)
                mobile = Criterion.objects.create(event=self.mobile, name=definition.name, order=len(self.criteria) + 1,
                    weight_percent=weight, max_score=100)
                self.criteria.append(mobile)
                mapping['criteria'][f'prelim:{definition.pk}'] = mobile.pk
        self.portal.participant_ids = [int(key) for key in mapping['candidates']]
        self.portal.result_processing_config = {'mobile_round_mapping': mapping}
        self.portal.save()
        for judge in self.judges[:3]:
            for candidate in self.candidates[:4]:
                for criterion in self.criteria:
                    JudgeScore.objects.create(judge=judge, candidate=candidate, criterion=criterion, score=80,
                        submitted_at=timezone.now(), is_locked=True, verification_id='HISTORICAL')
        self.api = APIClient()
        self.api.force_authenticate(self.judges[-1])
        self.url = '/api/events/judging-events/63/submit_scores/'
        self.payload = {'candidate_id': self.candidates[0].pk, 'round_id': 'prelim',
            'scores': [{'criterion_id': row.pk, 'score': 80.0, 'comment': ''} for row in self.criteria]}
        self.before = list(JudgeScore.objects.order_by('pk').values())

    def test_judge_57_flutter_request_saves_without_touching_60_historical_cells(self):
        # APIClient re-raises actual server exceptions; do not mask a 500.
        response = self.api.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['total_score'], 80)
        self.assertEqual(JudgeScore.objects.filter(judge_id=57).count(), 5)
        self.assertEqual(CriteriaScoreSubmission.objects.filter(judge_id=57).count(), 5)
        self.assertEqual(list(JudgeScore.objects.exclude(judge_id=57).order_by('pk').values()), self.before)

    def test_invalid_proxy_ip_cannot_roll_back_valid_scores(self):
        response = self.api.post(self.url, self.payload, format='json', HTTP_X_FORWARDED_FOR='unknown')
        self.assertEqual(response.status_code, 200, response.content)

    @skipUnless(connection.vendor == 'postgresql', 'Native PostgreSQL transaction failure')
    def test_optional_audit_database_failure_cannot_silently_discard_scores(self):
        def failed_audit(**kwargs):
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1 / 0')
        with patch('events.audit_service.record_audit', side_effect=failed_audit):
            response = self.api.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(JudgeScore.objects.filter(judge_id=57).count(), 5)

    def test_exact_retry_returns_same_receipt_without_duplicate_rows_or_audits(self):
        from .models import JudgeActivityLog
        first = self.api.post(self.url, self.payload, format='json')
        snapshot = list(JudgeScore.objects.order_by('pk').values())
        normalized = list(CriteriaScoreSubmission.objects.order_by('pk').values())
        again = self.api.post(self.url, self.payload, format='json')
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json(), first.json())
        self.assertEqual(list(JudgeScore.objects.order_by('pk').values()), snapshot)
        self.assertEqual(list(CriteriaScoreSubmission.objects.order_by('pk').values()), normalized)
        self.assertEqual(JudgeActivityLog.objects.filter(judge_id=57).count(), 1)
        self.payload['scores'][0]['score'] = 70.0
        self.assertEqual(self.api.post(self.url, self.payload, format='json').status_code, 400)
        self.assertEqual(list(JudgeScore.objects.order_by('pk').values()), snapshot)

    def test_audit_failure_rolls_back_all_cells_then_retry_succeeds(self):
        with self.settings(MOBILE_SCORE_DIAGNOSTICS=True), self.assertLogs('events.mobile_submit', level='ERROR') as logs:
            with patch('events.judging_views.JudgeActivityLog.log', side_effect=RuntimeError('PRIVATE_PAYLOAD_SENTINEL')):
                response = self.api.post(self.url, self.payload, format='json', HTTP_AUTHORIZATION='Token PRIVATE_TOKEN_SENTINEL')
        self.assertEqual(response.status_code, 500)
        self.assertIn('reference', response.json())
        self.assertNotIn('PRIVATE', response.content.decode())
        self.assertNotIn('PRIVATE', '\n'.join(logs.output))
        self.assertIn("'stage': 'audit'", logs.output[0])
        self.assertFalse(JudgeScore.objects.filter(judge_id=57).exists())
        self.assertFalse(CriteriaScoreSubmission.objects.exists())
        self.assertEqual(list(JudgeScore.objects.order_by('pk').values()), self.before)
        self.assertEqual(self.api.post(self.url, self.payload, format='json').status_code, 200)

    def test_invalid_requests_are_400_without_any_saved_cells(self):
        for changes in ({'scores': []}, {'scores': [{'score': 80}]},
                        {'scores': [{'criterion_id': {}, 'score': 80}]},
                        {'scores': [{'criterion_id': self.criteria[0].pk, 'score': 'NaN'}]},
                        {'candidate_id': -1}, {'scores': [{'criterion_id': self.criteria[0].pk, 'score': 80.123}]}):
            with self.subTest(changes=changes):
                self.assertEqual(self.api.post(self.url, {**self.payload, **changes}, format='json').status_code, 400)
        self.assertEqual(list(JudgeScore.objects.order_by('pk').values()), self.before)
        self.assertFalse(CriteriaScoreSubmission.objects.exists())

    def test_auth_role_and_missing_event_remain_safe(self):
        self.assertEqual(self.api.post('/api/events/judging-events/99999/submit_scores/', self.payload, format='json').status_code, 404)
        self.portal.assigned_judges.remove(self.judges[-1])
        self.assertEqual(self.api.post(self.url, self.payload, format='json').status_code, 403)
        self.api.force_authenticate(None)
        self.assertEqual(self.api.post(self.url, self.payload, format='json').status_code, 401)
        self.assertEqual(list(JudgeScore.objects.order_by('pk').values()), self.before)

    def test_diagnostics_are_opt_in_and_never_return_exception_details(self):
        with patch('events.mobile_submit_support.logger.error') as log:
            with patch('events.judging_views.JudgeActivityLog.log', side_effect=RuntimeError('PRIVATE_DETAILS')):
                response = self.api.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('PRIVATE', response.content.decode())
        log.assert_not_called()
        self.assertFalse(JudgeScore.objects.filter(judge_id=57).exists())

    @skipUnless(connection.vendor == 'postgresql', 'Native PostgreSQL schema metadata')
    def test_read_only_schema_diagnostic_matches_current_models(self):
        from .test_mobile_api import read_only_queries
        output = StringIO()
        with read_only_queries():
            call_command('inspect_mobile_submit_schema', stdout=output)
        self.assertTrue(json.loads(output.getvalue())['ok'], output.getvalue())

    @skipUnless(connection.vendor == 'postgresql', 'Native PostgreSQL foreign-key reproduction')
    def test_native_legacy_user_fk_failure_is_captured_and_diagnosed(self):
        # Simulate the legacy wrong-user-table FK documented by the repository's
        # old repair script. This is a disposable test DB, not a production diagnosis.
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE submit_test_legacy_user (id bigint PRIMARY KEY)')
            cursor.execute('INSERT INTO submit_test_legacy_user VALUES (16), (21), (22)')
            cursor.execute('''ALTER TABLE events_judgescore ADD CONSTRAINT submit_test_wrong_user_fk
                FOREIGN KEY (judge_id) REFERENCES submit_test_legacy_user(id)''')
        try:
            with self.settings(MOBILE_SCORE_DIAGNOSTICS=True), self.assertLogs('events.mobile_submit', level='ERROR') as logs:
                response = self.api.post(self.url, self.payload, format='json')
            self.assertEqual(response.status_code, 500)
            self.assertIn('ForeignKeyViolation', logs.output[0])
            self.assertIn('23503', logs.output[0])
            self.assertIn('submit_test_wrong_user_fk', logs.output[0])
            self.assertFalse(JudgeScore.objects.filter(judge_id=57).exists())
            self.assertFalse(CriteriaScoreSubmission.objects.exists())
            self.assertEqual(list(JudgeScore.objects.order_by('pk').values()), self.before)
            output = StringIO()
            call_command('inspect_mobile_submit_schema', stdout=output)
            self.assertIn('foreign_key_mismatch', output.getvalue())
            self.assertIn('submit_test_wrong_user_fk', output.getvalue())
        finally:
            with connection.cursor() as cursor:
                cursor.execute('ALTER TABLE events_judgescore DROP CONSTRAINT submit_test_wrong_user_fk')
                cursor.execute('DROP TABLE submit_test_legacy_user')
