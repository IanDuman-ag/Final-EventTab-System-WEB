"""Admin-synced multi-round scoring, qualification and explicit round locks."""
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from .criteria_faculty_service import (
    approve_criteria_round, criteria_faculty_portal, unlock_criteria_round,
)
from .faculty_service import FacultyServiceError
from .mobile_sync import sync_event_to_mobile
from .models import (
    CriteriaEventEntry, CriteriaScoreSubmission, Event,
    EventScoringCategory, EventScoringCriterion,
)


class JudgeMultiRoundWorkflowTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.faculty = users.objects.create_user(username='round-owner', is_staff=True)
        self.judges = [users.objects.create_user(username=f'round-judge-{i}') for i in range(2)]
        group, _ = Group.objects.get_or_create(name='Judge')
        for judge in self.judges:
            judge.groups.add(group)
        self.event = Event.objects.create(
            name='Multi-round Pageant', event_date=date.today(), status='active',
            scoring_method='criteria', faculty_account=self.faculty,
            rounds_config=[
                {'id': 'prelim', 'name': 'Preliminary', 'status': 'open',
                 'qualification_method': 'top_ranking', 'qualifiers': 1},
                {'id': 'final', 'name': 'Final', 'status': 'locked'},
            ],
        )
        self.event.assigned_judges.set(self.judges)
        self.entries = [CriteriaEventEntry.objects.create(
            event=self.event, entry_type='individual', display_name=f'Candidate {i}', display_order=i,
        ) for i in range(2)]
        self.event.participant_ids = [entry.pk for entry in self.entries]
        definitions = []
        for order, rid in enumerate(('prelim', 'final'), 1):
            category = EventScoringCategory.objects.create(
                event=self.event, assigned_round_id=rid, name=rid,
                judge_mode='scoring', overall_weight_percent=100, display_order=order,
            )
            criterion = EventScoringCriterion.objects.create(
                category=category, name=f'{rid} performance', max_score=100,
                weight_percent=100, display_order=1,
            )
            definitions.append({'id': f'esc_{criterion.pk}', 'round_id': rid,
                                'name': criterion.name, 'max_score': 100, 'weight': 100, 'order': order})
        self.event.judging_criteria_config = definitions
        self.event.save()
        self.mobile = sync_event_to_mobile(self.event)
        self.event.refresh_from_db()
        self.detail_url = f'/api/events/judge/assignments/{self.mobile.pk}/'
        self.submit_url = f'/api/events/judging-events/{self.mobile.pk}/submit_scores/'

    def detail(self, judge=None, rid='prelim'):
        self.client.force_login(judge or self.judges[0])
        response = self.client.get(self.detail_url, {'round_id': rid})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def submit(self, detail, index, score, rid=None):
        payload = {'round_id': rid or detail['selected_round_id'],
                   'candidate_id': detail['participants'][index]['id'],
                   'scores': [{'criterion_id': detail['criteria'][0]['id'], 'score': score}]}
        return self.client.post(self.submit_url, payload, content_type='application/json')

    def submit_preliminary(self, scores=(90, 60)):
        for judge in self.judges:
            detail = self.detail(judge)
            self.assertTrue(detail['can_score'], detail['scoring_unavailable_reasons'])
            for index, score in enumerate(scores):
                response = self.submit(detail, index, score)
                self.assertEqual(response.status_code, 200, response.content)

    def test_admin_sync_scores_first_round_and_advances_only_approved_qualifiers_after_unlock(self):
        first = self.detail()
        self.assertTrue(first['can_score'], first['scoring_unavailable_reasons'])
        final = self.detail(rid='final')
        self.assertFalse(final['can_score'])
        self.assertEqual(self.submit(final, 0, 95).status_code, 400)
        for index, score in enumerate((90, 60)):
            self.assertEqual(self.submit(first, index, score).status_code, 200)
        self.event.refresh_from_db()
        pending = criteria_faculty_portal(self.event, self.faculty)['selected_round']
        self.assertFalse(pending['can_approve'])
        self.assertEqual(pending['results'], [])
        with self.assertRaises(FacultyServiceError):
            unlock_criteria_round(self.event, self.faculty, 1)
        second = self.detail(self.judges[1])
        for index, score in enumerate((80, 70)):
            self.assertEqual(self.submit(second, index, score).status_code, 200)
        self.event.refresh_from_db()
        result = criteria_faculty_portal(self.event, self.faculty)['selected_round']
        self.assertTrue(result['can_approve'], result['blockers'])
        self.assertEqual([row['final_score'] for row in result['results']], [85, 65])
        approve_criteria_round(self.event, self.faculty, 0)
        self.assertFalse(self.detail(rid='final')['can_score'])
        unlock_criteria_round(self.event, self.faculty, 1)
        final = self.detail(rid='final')
        self.assertTrue(final['can_score'], final['scoring_unavailable_reasons'])
        self.assertEqual([p['is_qualified'] for p in final['participants']], [True, False])
        self.assertEqual(self.submit(final, 1, 99).status_code, 400)
        self.assertEqual(self.submit(final, 0, 95).status_code, 200)
        self.assertFalse(self.detail()['can_score'])
        self.assertEqual(CriteriaScoreSubmission.objects.filter(assigned_round_id='prelim').count(), 4)
        self.assertEqual(CriteriaScoreSubmission.objects.filter(assigned_round_id='final').count(), 1)

    def test_minimum_score_rule_controls_qualification(self):
        self.event.rounds_config[0].update(qualification_method='minimum_score', minimum_score=80)
        self.event.save(update_fields=['rounds_config'])
        self.submit_preliminary()
        self.event.refresh_from_db()
        approve_criteria_round(self.event, self.faculty, 0)
        unlock_criteria_round(self.event, self.faculty, 1)
        final = self.detail(rid='final')
        self.assertEqual([p['is_qualified'] for p in final['participants']], [True, False])

    def test_tie_at_cutoff_does_not_silently_eliminate_a_candidate(self):
        self.submit_preliminary(scores=(80, 80))
        self.event.refresh_from_db()
        with self.assertRaisesMessage(FacultyServiceError, 'tie'):
            approve_criteria_round(self.event, self.faculty, 0)
        self.assertFalse(self.detail(rid='final')['can_score'])
