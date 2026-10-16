"""Own round-scoped history and read-only compatibility for Admin-synced events."""
from copy import deepcopy
from datetime import date, time
from io import StringIO
import json

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import test_mobile_submit as fixtures
from .test_mobile_api import read_only_queries
from .models import (Candidate, Criterion, CriteriaEventEntry, CriteriaScoreSubmission,
                     Event, EventScoringCategory, EventScoringCriterion, JudgeScore, JudgingEvent)


class JudgePortalFlowTests(TestCase):
    def setUp(self):
        fixtures.MobileSubmitTests.setUp(self)
        self.history_url = '/api/events/judge/score-history/?status=all'
        self.detail_url = '/api/events/judge/assignments/63/'

    def duet(self, entry_type='pair'):
        mobile = JudgingEvent.objects.create(title='Vocal Duet', category=self.mobile.category,
            date=date.today(), time=time(9), status='active')
        portal = Event.objects.create(name='Vocal Duet', category='Cultural', scoring_method='criteria',
            event_date=date.today(), status='active', judging_event=mobile, participation_type=entry_type,
            rounds_config=[{'id': 'main', 'name': 'Duet Round', 'status': 'open'}])
        portal.assigned_judges.add(self.judges[-1])
        # The Web assignment is authoritative even if its legacy M2M was not refreshed.
        entry = CriteriaEventEntry.objects.create(event=portal, entry_type=entry_type, display_name='Singers A + B')
        candidate = Candidate.objects.create(event=mobile, name=entry.display_name, number=1)
        category = EventScoringCategory.objects.create(event=portal, name='Performance', assigned_round_id='main',
            judge_mode='scoring', overall_weight_percent=100, display_order=1)
        definition = EventScoringCriterion.objects.create(category=category, name='Voice', max_score=100,
            weight_percent=100, display_order=1)
        criterion = Criterion.objects.create(event=mobile, name='Voice', max_score=100, weight_percent=100, order=1)
        portal.participant_ids = [entry.pk]
        portal.judging_criteria_config = [{'id': f'esc_{definition.pk}', 'round_id': 'main',
            'name': 'Voice', 'max_score': 100, 'weight': 100, 'order': 1}]
        portal.save()
        return portal, mobile, entry, candidate, definition, criterion

    def normalized(self, criterion, participant, score=80, status='SUBMITTED', judge=None):
        return CriteriaScoreSubmission.objects.create(event=criterion.category.event, category=criterion.category,
            criterion=criterion, assigned_round_id=criterion.category.assigned_round_id,
            participant_id=participant.pk, participant_type='entry', judge=judge or self.judges[-1],
            raw_score=score, status=status, source='MANUAL',
            submitted_at=timezone.now() if status != 'DRAFT' else None)

    def test_pageants_saved_history_deduplicates_bridge_and_refreshes_dashboard(self):
        first = self.api.post(self.url, self.payload, format='json')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.api.post(self.url, self.payload, format='json').json(), first.json())
        with read_only_queries():
            history = self.api.get(self.history_url).json()
            detail = self.api.get(self.detail_url).json()
            dashboard = self.api.get('/api/events/judge/dashboard/').json()
        self.assertEqual(history['counts'], {'draft': 0, 'submitted': 1, 'returned': 0, 'all': 1})
        row = history['entries'][0]
        self.assertEqual(row['title'], 'Pageants')
        self.assertEqual(row['score'], 80)
        self.assertEqual(row['round_id'], 'prelim')
        self.assertEqual(len(row['receipt_scores']), 5)
        self.assertEqual(sum(item['weight_percent'] for item in row['receipt_criteria']), 100)
        self.assertEqual(detail['participants_scored'], 1)
        self.assertEqual(dashboard['stats']['submitted'], 1)
        self.assertEqual(dashboard['last_submission']['candidate_id'], self.candidates[0].pk)
        self.assertEqual(list(JudgeScore.objects.exclude(judge_id=57).order_by('pk').values()), self.before)

    def test_admin_synced_duet_uses_own_web_assignment_without_provisioning(self):
        portal, mobile, entry, candidate, definition, criterion = self.duet()
        before = deepcopy(portal.result_processing_config)
        with read_only_queries():
            assignments = self.api.get('/api/events/judge/assignments/').json()['assignments']
            detail = self.api.get(f'/api/events/judge/assignments/{mobile.pk}/').json()
        self.assertEqual({row['title'] for row in assignments}, {'Pageants', 'Vocal Duet'})
        self.assertTrue(detail['can_score'], detail['scoring_unavailable_reasons'])
        self.assertEqual(detail['participant_label'], 'Pairs')
        self.assertEqual(detail['criteria'][0]['id'], criterion.pk)
        payload = {'candidate_id': candidate.pk, 'round_id': 'main',
            'scores': [{'criterion_id': criterion.pk, 'score': 90.0, 'comment': ''}]}
        response = self.api.post(f'/api/events/judging-events/{mobile.pk}/submit_scores/', payload, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['total_score'], 90)
        portal.refresh_from_db()
        self.assertEqual(portal.result_processing_config, before)
        self.assertEqual(mobile.criteria.count(), 1)
        self.assertEqual(mobile.candidates.count(), 1)
        self.assertEqual(self.api.get(self.history_url).json()['entries'][0]['title'], 'Vocal Duet')
        portal.assigned_judges.remove(self.judges[-1])
        mobile.assigned_judges.add(self.judges[-1])
        self.assertEqual(self.api.get(f'/api/events/judge/assignments/{mobile.pk}/').status_code, 404)
        self.assertEqual(self.api.post(f'/api/events/judging-events/{mobile.pk}/submit_scores/', payload, format='json').status_code, 403)

    def test_team_individual_pair_rosters_preserve_participant_identity(self):
        for kind in ('team', 'individual', 'pair'):
            portal, mobile, entry, candidate, _, _ = self.duet(kind)
            data = self.api.get(f'/api/events/judge/assignments/{mobile.pk}/').json()
            self.assertTrue(data['can_score'], data)
            self.assertEqual(data['participants'][0]['entry_id'], entry.pk)
            self.assertEqual(data['participants'][0]['id'], candidate.pk)

    def test_ambiguous_or_stale_mapping_is_not_repaired_or_enabled(self):
        portal, mobile, entry, candidate, definition, criterion = self.duet()
        duplicate = Candidate.objects.create(event=mobile, name=candidate.name, number=2)
        data = self.api.get(f'/api/events/judge/assignments/{mobile.pk}/').json()
        self.assertFalse(data['can_score'])
        self.assertIn('candidate mappings', data['scoring_unavailable_reason'])
        duplicate.name = 'Other'
        duplicate.save()
        portal.result_processing_config = {'mobile_round_mapping': {'criteria': {f'main:{definition.pk}': -1}}}
        portal.save()
        data = self.api.get(f'/api/events/judge/assignments/{mobile.pk}/').json()
        self.assertFalse(data['can_score'])
        self.assertIn('incompatible mobile mapping', data['scoring_unavailable_reason'])

    def test_normalized_only_submissions_and_partial_drafts_are_own_and_viewable(self):
        definitions = list(EventScoringCriterion.objects.filter(category__event=self.portal))
        entries = list(self.portal.criteria_entries.all())
        for criterion in definitions:
            self.normalized(criterion, entries[0])
            self.normalized(criterion, entries[1], judge=self.judges[0], score=99)
        self.normalized(definitions[0], entries[2], score=70, status='DRAFT')
        with read_only_queries():
            history = self.api.get(self.history_url).json()
            scores = self.api.get('/api/events/judging-events/63/my_scores/',
                {'candidate_id': self.candidates[0].pk, 'round_id': 'prelim'}).json()
        self.assertEqual(history['counts']['all'], 2)
        self.assertEqual(history['counts']['submitted'], 1)
        self.assertEqual(history['counts']['draft'], 1)
        self.assertEqual(len(scores), 5)
        self.assertTrue(all(row['is_locked'] for row in scores))
        self.assertNotIn(99, [row['score'] for row in history['entries']])
        draft = next(row for row in history['entries'] if row['status'] == 'draft')
        self.assertIsNone(draft['submitted_at'])
        self.assertTrue(draft['submitted_at_display'])
        self.assertEqual(draft['max_score'], 100)

    def test_legacy_draft_and_normalized_submitted_sheets_do_not_double_count(self):
        JudgeScore.objects.create(judge=self.judges[-1], candidate=self.candidates[0], criterion=self.criteria[0], score=30)
        history = self.api.get(self.history_url).json()
        self.assertEqual(history['counts']['draft'], 1)
        entry = self.portal.criteria_entries.first()
        for criterion in EventScoringCriterion.objects.filter(category__event=self.portal):
            self.normalized(criterion, entry, score=90)
        history = self.api.get(self.history_url).json()
        self.assertEqual(history['counts']['all'], 1)
        self.assertEqual(history['entries'][0]['score'], 90)

    def test_closed_round_invalid_weights_and_incomplete_setup_report_reasons(self):
        portal, mobile, _, candidate, definition, criterion = self.duet()
        path = f'/api/events/judge/assignments/{mobile.pk}/'
        portal.rounds_config[0]['status'] = 'locked'
        portal.save()
        data = self.api.get(path).json()
        self.assertFalse(data['can_score'])
        self.assertIn('is locked', data['scoring_unavailable_reason'])
        payload = {'candidate_id': candidate.pk, 'round_id': 'main', 'scores': [{'criterion_id': criterion.pk, 'score': 80}]}
        self.assertEqual(self.api.post(f'/api/events/judging-events/{mobile.pk}/submit_scores/', payload, format='json').status_code, 400)
        portal.rounds_config[0]['status'] = 'open'
        portal.save()
        definition.weight_percent = 90
        definition.save()
        data = self.api.get(path).json()
        self.assertFalse(data['can_score'])
        self.assertIn('criterion weights must total 100%', data['scoring_unavailable_reason'])
        self.assertFalse(JudgeScore.objects.filter(candidate=candidate).exists())
        portal.status = Event.STATUS_COMPLETED
        portal.save()
        data = self.api.get(path).json()
        self.assertEqual(data['status'], 'completed')
        self.assertFalse(data['can_score'])

    def test_final_round_requires_faculty_approved_previous_round(self):
        self.portal.rounds_config.append({'id': 'final', 'name': 'Final Round', 'status': 'open'})
        category = EventScoringCategory.objects.create(event=self.portal, name='Final', assigned_round_id='final',
            judge_mode='scoring', overall_weight_percent=100, display_order=3)
        criterion = EventScoringCriterion.objects.create(category=category, name='Final Voice', max_score=100, weight_percent=100, display_order=1)
        mobile = Criterion.objects.create(event=self.mobile, name='Final Voice', max_score=100, weight_percent=100, order=6)
        self.portal.result_processing_config['active_stage_index'] = 1
        self.portal.result_processing_config['mobile_round_mapping']['criteria'][f'final:{criterion.pk}'] = mobile.pk
        self.portal.result_processing_config['qualified_participant_ids'] = {'1': self.portal.participant_ids[:2]}
        self.portal.save()
        self.assertFalse(self.api.get(self.detail_url).json()['can_score'])
        self.portal.result_processing_config['confirmed_stages'] = {'0': {'approved_at': timezone.now().isoformat()}}
        self.portal.save()
        data = self.api.get(self.detail_url).json()
        self.assertTrue(data['can_score'], data)
        self.assertEqual(data['selected_round_id'], 'final')
        self.assertEqual(data['participants_total'], 2)
        self.assertEqual(data['criteria'][0]['id'], mobile.pk)
        self.assertFalse(self.api.get(self.detail_url + '?round_id=prelim').json()['can_score'])

    def test_read_only_audit_reports_each_assignment_without_score_payloads(self):
        portal, mobile, *_ = self.duet()
        portal.rounds_config[0]['status'] = 'locked'
        portal.save()
        output = StringIO()
        with read_only_queries():
            call_command('inspect_judge_portal', judge_id=57, stdout=output)
        report = json.loads(output.getvalue())
        self.assertTrue(report['read_only'])
        self.assertEqual(len(report['assignments']), 2)
        duet = next(row for row in report['assignments'] if row['title'] == 'Vocal Duet')
        self.assertFalse(duet['can_score'])
        self.assertIn('is locked', duet['scoring_unavailable_reasons'][0])
        self.assertNotIn('receipt_scores', output.getvalue())
        with self.assertRaises(CommandError):
            call_command('inspect_judge_portal', judge_id=99999, stdout=StringIO())

    def test_receipt_history_and_retry_respect_configured_raw_or_average_method(self):
        for index, (method, expected, maximum) in enumerate((('raw_score', 400, 500), ('average_score', 80, 100))):
            self.portal.criteria_score_method = method
            self.portal.save()
            payload = {**self.payload, 'candidate_id': self.candidates[index].pk}
            first = self.api.post(self.url, payload, format='json')
            self.assertEqual(first.status_code, 200, first.content)
            self.assertEqual(first.json()['total_score'], expected)
            self.assertEqual(self.api.post(self.url, payload, format='json').json(), first.json())
            history = self.api.get(self.history_url).json()['entries']
            own = next(row for row in history if row['candidate_id'] == self.candidates[index].pk)
            self.assertEqual(own['score'], expected)
            self.assertEqual(own['max_score'], maximum)

    def test_history_display_and_date_filters_use_manila_submission_date(self):
        JudgeScore.objects.create(judge=self.judges[-1], candidate=self.candidates[0], criterion=self.criteria[0],
            score=80, is_locked=True, submitted_at=parse_datetime('2026-10-16T17:00:00Z'))
        entry = self.api.get(self.history_url).json()['entries'][0]
        self.assertEqual(entry['submitted_at_display'], 'Oct 17, 2026')
        self.assertEqual(len(self.api.get(self.history_url + '&date_from=2026-10-17').json()['entries']), 1)
        self.assertEqual(self.api.get(self.history_url + '&date_to=2026-10-16').json()['entries'], [])
