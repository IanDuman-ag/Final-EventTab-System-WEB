from contextlib import contextmanager
from datetime import date, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .models import (
    BracketMatch, BracketTeam, Candidate, CriteriaEventEntry, CriteriaScoreSubmission, Criterion, Department, Event,
    EventCategory, EventScoringCategory, EventScoringCriterion, JudgeScore, JudgingEvent,
)


@contextmanager
def read_only_queries():
    def guard(execute, sql, params, many, context):
        if sql.lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'CREATE', 'ALTER', 'DROP'}:
            raise AssertionError(f'GET attempted a database mutation: {sql}')
        return execute(sql, params, many, context)
    with connection.execute_wrapper(guard):
        yield


class MobileApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.judge = User.objects.create_user(username='mobile-judge')
        self.other = User.objects.create_user(username='other-judge')
        judge_group = Group.objects.create(name='Judge')
        self.judge.groups.add(judge_group)
        self.other.groups.add(judge_group)
        self.faculty = User.objects.create_user(username='faculty', is_staff=True)
        self.faculty.groups.add(Group.objects.create(name='Faculty'))
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=self.judge).key}')
        self.public = APIClient()
        self.category = EventCategory.objects.create(name='Academic', category_type='academic')
        self.event = JudgingEvent.objects.create(title='Quiz', category=self.category,
                                                 date=date.today(), time=time(10), venue='Hall', status='active')
        self.event.assigned_judges.add(self.judge, self.other)
        self.portal = Event.objects.create(name='Quiz', category='Academic', scoring_method='criteria',
                                           event_date=date.today(), venue='Hall', status='active', judging_event=self.event)
        self.portal.assigned_judges.add(self.judge, self.other)
        self.candidate = Candidate.objects.create(event=self.event, name='Alice', number=1)
        self.criterion = Criterion.objects.create(event=self.event, name='Quality', max_score=100, weight_percent=100)

    def judge_paths(self):
        return ['dashboard/', 'assignments/', f'assignments/{self.event.pk}/', 'score-history/', 'profile/']

    def test_authentication_and_role_enforced_for_every_judge_endpoint(self):
        for suffix in self.judge_paths():
            path = '/api/events/judge/' + suffix
            with self.subTest(path=path):
                self.assertEqual(self.public.get(path).status_code, 401)
                self.public.force_authenticate(self.faculty)
                self.assertEqual(self.public.get(path).status_code, 403)
                self.public.force_authenticate(None)
                self.assertEqual(self.api.get(path).status_code, 200)

    def test_assignment_contract_and_own_scores_only(self):
        JudgeScore.objects.create(judge=self.other, candidate=self.candidate, criterion=self.criterion,
                                  score=99, is_locked=True)
        response = self.api.get(f'/api/events/judge/assignments/{self.event.pk}/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        for field in ('participants', 'criteria', 'all_criteria', 'stages'):
            self.assertIsInstance(data[field], list)
        self.assertIsInstance(data['assignment'], dict)
        self.assertEqual(data['participants'][0]['scoring_status'], 'pending')
        self.assertEqual(data['contestants_scored'], 0)
        self.assertIsInstance(data['criteria'][0]['max_score'], (int, float))
        self.assertEqual(data['status'], 'ongoing')
        self.assertEqual(self.api.get('/api/events/judge/score-history/').json()['entries'], [])
        JudgeScore.objects.create(judge=self.judge, candidate=self.candidate, criterion=self.criterion,
                                  score=80, is_locked=True, submitted_at=timezone.now(), verification_id='OWN')
        history = self.api.get('/api/events/judge/score-history/').json()
        self.assertEqual(history['counts']['submitted'], 1)
        self.assertEqual(history['entries'][0]['score'], 80)
        self.assertEqual(history['entries'][0]['verification_id'], 'OWN')
        self.assertEqual(self.api.get('/api/events/judge/profile/').json()['stats']['completed'], 1)

    def test_unassigned_missing_and_inactive_assignments(self):
        self.portal.assigned_judges.remove(self.judge)
        self.assertEqual(self.api.get(f'/api/events/judge/assignments/{self.event.pk}/').status_code, 404)
        self.assertEqual(self.api.get('/api/events/judge/assignments/999999/').status_code, 404)
        self.assertEqual(self.api.get('/api/events/judge/assignments/').json()['assignments'], [])
        self.assertEqual(self.api.get('/api/events/judge/score-history/').json()['entries'], [])
        self.assertEqual(self.api.get('/api/events/judge/profile/').json()['stats']['assignments'], 0)
        self.portal.assigned_judges.add(self.judge)
        self.portal.status = Event.STATUS_INACTIVE
        self.portal.save()
        self.assertEqual(self.api.get('/api/events/judge/assignments/').json()['assignments'], [])

    def test_empty_public_contracts_and_post_rejected(self):
        self.portal.publication_status = Event.PUBLICATION_DRAFT
        self.portal.save()
        expected = {'dashboard': 'ongoing', 'events': 'events', 'rankings': 'rankings',
                    'announcements': 'announcements', 'notifications': 'notifications'}
        for endpoint, field in expected.items():
            with self.subTest(endpoint=endpoint):
                response = self.public.get(f'/api/events/viewer/{endpoint}/')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()[field], [])
        for endpoint in ('about', 'profile', 'live'):
            self.assertEqual(self.public.get(f'/api/events/viewer/{endpoint}/').status_code, 200)
        self.assertEqual(self.api.post('/api/events/judge/assignments/', {}).status_code, 405)
        self.assertEqual(self.public.post('/api/events/viewer/events/', {}).status_code, 405)

    def test_unpublished_scores_and_private_events_never_public(self):
        JudgeScore.objects.create(judge=self.judge, candidate=self.candidate, criterion=self.criterion,
                                  score=Decimal('87.31'), is_locked=True, verification_id='PRIVATE-KEY')
        for suffix in ('dashboard/', 'events/', 'rankings/', f'criteria-events/{self.event.pk}/'):
            response = self.public.get('/api/events/viewer/' + suffix)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('87.31', response.content.decode())
            self.assertNotIn('PRIVATE-KEY', response.content.decode())
        detail = self.public.get(f'/api/events/viewer/criteria-events/{self.event.pk}/').json()
        self.assertEqual(detail['results'], [])
        self.assertFalse(detail['show_scores'])
        self.portal.publication_status = Event.PUBLICATION_DRAFT
        self.portal.save()
        self.assertEqual(self.public.get(f'/api/events/viewer/criteria-events/{self.event.pk}/').status_code, 404)
        self.assertEqual(self.public.get('/api/events/viewer/events/').json()['events'], [])

    def make_match(self):
        event = Event.objects.create(name='Basketball', category='Sports', scoring_method='match',
                                     event_date=date.today(), venue='Court', status='completed')
        department = Department.objects.create(name='Engineering', code='ENG')
        a = BracketTeam.objects.create(event=event, name='Blue', department=department, points=15)
        b = BracketTeam.objects.create(event=event, name='Gold')
        match = BracketMatch.objects.create(event=event, match_number=1, round_name='Final',
                                            team_a=a, team_b=b, winner=a, score_a=90, score_b=70, status='completed')
        return event, match

    def test_match_scores_and_rankings_require_publication_and_hide_later_edits(self):
        event, match = self.make_match()
        path = f'/api/events/viewer/match-events/{match.pk}/'
        data = self.public.get(path).json()
        self.assertIsInstance(data['schedule'], list)
        self.assertIsNone(data['schedule'][0]['score_a'])
        self.assertEqual(data['results'], [])
        self.assertEqual(self.public.get('/api/events/viewer/rankings/').json()['rankings'], [])
        event.results_finalized = True
        event.result_processing_config = {'published_at': (timezone.now() + timedelta(seconds=1)).isoformat()}
        event.save()
        data = self.public.get(path).json()
        self.assertEqual(data['results'][0]['score_a'], '90')
        rows = self.public.get('/api/events/viewer/rankings/').json()['rankings']
        self.assertEqual(rows[0]['name'], 'Engineering')
        self.assertEqual(rows[0]['points'], 15)
        self.assertEqual(rows[0]['color'], '#022068')
        match.updated_at = timezone.now() + timedelta(seconds=2)
        BracketMatch.objects.filter(pk=match.pk).update(updated_at=match.updated_at)
        self.assertEqual(self.public.get(path).json()['results'], [])
        self.assertEqual(self.public.get('/api/events/viewer/rankings/').json()['rankings'], [])

    def test_publication_gates_require_all_approvals(self):
        event, match = self.make_match()
        event.results_finalized = True
        event.result_processing_config = {'published_at': (timezone.now() + timedelta(seconds=1)).isoformat(),
                                          'verification_mode': 'tabulator_admin_approval'}
        event.save()
        self.assertEqual(self.public.get(f'/api/events/viewer/match-events/{match.pk}/').json()['results'], [])
        self.assertEqual(self.public.get('/api/events/viewer/rankings/').json()['rankings'], [])

    def configure_rounds(self, mapped=True):
        entry = CriteriaEventEntry.objects.create(event=self.portal, entry_type='individual', display_name='Alice')
        self.portal.participant_ids = [entry.pk]
        self.portal.rounds_config = [{'id': 'prelim', 'name': 'Preliminary', 'status': 'open'},
                                     {'id': 'final', 'name': 'Final', 'status': 'locked'}]
        category = EventScoringCategory.objects.create(event=self.portal, name='Talent', assigned_round_id='prelim',
                                                       judge_mode='scoring', display_order=1, overall_weight_percent=100)
        definition = EventScoringCriterion.objects.create(category=category, name='Quality', weight_percent=100,
                                                          max_score=100, display_order=1)
        self.portal.result_processing_config = {'mobile_round_mapping': {
            'criteria': {f'prelim:{definition.pk}': self.criterion.pk} if mapped else {},
            'candidates': {str(entry.pk): self.candidate.pk} if mapped else {}}}
        self.portal.save()

    def test_round_selection_and_faculty_locks(self):
        self.configure_rounds()
        path = f'/api/events/judge/assignments/{self.event.pk}/'
        data = self.api.get(path + '?round_id=prelim').json()
        self.assertTrue(data['can_score'])
        self.assertEqual(data['current_round'], 'Preliminary')
        data = self.api.get(path + '?round_id=final').json()
        self.assertFalse(data['can_score'])
        self.assertEqual(data['criteria'], [])
        self.assertEqual(self.api.get(path + '?round_id=invalid').status_code, 400)
        self.portal.results_finalized = True
        self.portal.save()
        self.assertFalse(self.api.get(path + '?round_id=prelim').json()['can_score'])

    def test_gets_are_read_only_even_with_missing_mappings(self):
        self.configure_rounds(mapped=False)
        self.make_match()
        with read_only_queries():
            for suffix in self.judge_paths():
                self.assertEqual(self.api.get('/api/events/judge/' + suffix).status_code, 200)
            for suffix in ('dashboard/', 'events/', 'rankings/', 'live/', 'profile/', 'about/',
                           'notifications/', 'announcements/', f'criteria-events/{self.event.pk}/'):
                self.assertEqual(self.public.get('/api/events/viewer/' + suffix).status_code, 200)
        data = self.api.get(f'/api/events/judge/assignments/{self.event.pk}/').json()
        self.assertFalse(data['can_score'])
        self.assertTrue(data['configuration_required'])
        self.portal.refresh_from_db()
        self.assertEqual(self.portal.result_processing_config['mobile_round_mapping']['criteria'], {})
        self.assertEqual(self.event.candidates.count(), 1)
        self.assertEqual(self.event.criteria.count(), 1)

    def test_normalized_history_uses_only_own_active_submissions(self):
        self.configure_rounds()
        category = self.portal.scoring_categories.get()
        definition = category.criteria.get()
        participant = self.portal.criteria_entries.get()
        values = {'event': self.portal, 'assigned_round_id': 'prelim', 'category': category,
                  'criterion': definition, 'participant_id': participant.pk, 'participant_type': 'entry',
                  'source': 'MOBILE', 'status': 'SUBMITTED', 'submitted_at': timezone.now()}
        CriteriaScoreSubmission.objects.create(judge=self.judge, raw_score=75, **values)
        CriteriaScoreSubmission.objects.create(judge=self.other, raw_score=99, **values)
        JudgeScore.objects.create(judge=self.judge, candidate=self.candidate, criterion=self.criterion,
                                  score=75, is_locked=True)
        with read_only_queries():
            rows = self.api.get('/api/events/judge/score-history/').json()['entries']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['score'], 75)
        self.assertEqual(rows[0]['status'], 'submitted')
        self.assertEqual(rows[0]['round_id'], 'prelim')
        CriteriaScoreSubmission.objects.filter(judge=self.judge).update(status='REJECTED')
        rows = self.api.get('/api/events/judge/score-history/?status=returned').json()['entries']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['review_status'], 'rejected')

    def test_published_criteria_only_returns_approved_placements(self):
        self.configure_rounds()
        participant = self.portal.criteria_entries.get()
        self.portal.status = 'completed'
        self.portal.results_finalized = True
        self.portal.result_processing_config.update({
            'published_at': timezone.now().isoformat(), 'faculty_submitted_at': timezone.now().isoformat(),
            'qualified_participant_ids': {'1': [participant.pk]},
            'confirmed_stages': {'1': {'approved_at': timezone.now().isoformat(), 'results': [
                {'participant_id': participant.pk, 'complete': True, 'rank': 1, 'final_score': 88.1234}]}}})
        self.portal.save()
        with read_only_queries():
            response = self.public.get(f'/api/events/viewer/criteria-events/{self.event.pk}/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['results'][0]['contestant_name'], 'Alice')
        self.assertEqual(data['results'][0]['rank'], 1)
        self.assertIsNone(data['results'][0]['score'])
        self.assertFalse(data['show_scores'])
        self.assertNotIn('88.1234', response.content.decode())

    def test_empty_candidates_and_invalid_round_state_fail_closed(self):
        self.candidate.delete()
        data = self.api.get(f'/api/events/judge/assignments/{self.event.pk}/').json()
        self.assertEqual(data['participants'], [])
        self.assertFalse(data['can_score'])
        self.configure_rounds(mapped=False)
        self.portal.result_processing_config['active_stage_index'] = 'invalid'
        self.portal.save()
        self.assertFalse(self.api.get(f'/api/events/judge/assignments/{self.event.pk}/').json()['can_score'])

    def test_filters_dates_and_existing_router_preserved(self):
        self.assertEqual(self.api.get('/api/events/judge/assignments/?status=completed').json()['assignments'], [])
        self.assertEqual(self.api.get('/api/events/judge/score-history/?date_from=wrong').status_code, 400)
        self.assertEqual(self.public.get('/api/events/viewer/events/?q=unmatched').json()['events'], [])
        self.assertEqual(self.public.get('/api/events/viewer/events/?type=match').json()['events'], [])
        self.assertEqual(self.api.get('/api/events/categories/').status_code, 200)
        self.assertEqual(self.api.get('/api/events/judging-events/').status_code, 200)

    def test_submission_reads_are_round_scoped_and_do_not_write(self):
        self.configure_rounds()
        JudgeScore.objects.create(judge=self.judge, candidate=self.candidate, criterion=self.criterion,
                                  score=80, is_locked=True)
        detail = f'/api/events/judging-events/{self.event.pk}/'
        scores = detail + f'my_scores/?candidate_id={self.candidate.pk}'
        with read_only_queries():
            response = self.api.get(detail + '?round_id=prelim')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['round_id'], 'prelim')
            self.assertEqual(len(self.api.get(scores + '&round_id=prelim').json()), 1)
            self.assertEqual(self.api.get(scores + '&round_id=final').json(), [])
            self.assertEqual(self.api.get('/api/events/judge-notifications/').status_code, 200)
        self.assertEqual(self.api.get(detail + 'my_scores/?candidate_id=bad').status_code, 400)
        self.portal.assigned_judges.remove(self.judge)
        self.assertEqual(self.api.get(detail).status_code, 404)
        self.assertEqual(self.api.get(scores).status_code, 404)
        self.assertEqual(self.api.get(f'/api/events/categories/{self.category.pk}/events/').json(), [])
