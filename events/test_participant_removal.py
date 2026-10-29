"""Reversible participant exclusions preserve scores and approved round history."""
from copy import deepcopy
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse

from .criteria_faculty_service import (approve_criteria_round, criteria_faculty_portal,
    round_participant_ids, submit_criteria_publication, unlock_criteria_round)
from .criteria_scoring import compute_official_criteria_rankings
from .faculty_service import FacultyServiceError
from .models import CriteriaEventEntry, CriteriaScoreSubmission, Event, EventScoringCategory, EventScoringCriterion
from .ocr_service import definition
from .participant_removal_service import remove_criteria_participant, restore_criteria_participant


class ParticipantRemovalTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.faculty = users.objects.create_user('removal-faculty')
        group = Group.objects.get_or_create(name='Faculty')[0]
        self.faculty.groups.add(group)
        self.other = users.objects.create_user('removal-other')
        self.other.groups.add(group)
        self.judge = users.objects.create_user('removal-judge')
        self.event = Event.objects.create(name='Contest', event_date=date.today(), scoring_method='criteria',
            faculty_account=self.faculty, rounds_config=[
                {'id': 'first', 'name': 'Round 1', 'status': 'open', 'qualification_method': 'advance_all'},
                {'id': 'second', 'name': 'Round 2', 'status': 'locked'}])
        self.entries = [CriteriaEventEntry.objects.create(event=self.event, entry_type='individual',
                        display_name=f'Contestant {i}', display_order=i) for i in range(2)]
        self.event.participant_ids = [row.pk for row in self.entries]
        self.event.save()
        self.event.assigned_judges.add(self.judge)
        self.criteria = {}
        for index, rid in enumerate(['first', 'second']):
            category = EventScoringCategory.objects.create(event=self.event, assigned_round_id=rid,
                name=rid, purpose='official', judge_mode='scoring', overall_weight_percent=100, display_order=index)
            self.criteria[rid] = EventScoringCriterion.objects.create(category=category,
                name='Skill', weight_percent=100, max_score=100, display_order=0)
        self.url = reverse('faculty_event_manage', args=[self.event.pk])
        self.client.force_login(self.faculty)

    def scores(self, rid):
        criterion = self.criteria[rid]
        for i, entry in enumerate(self.entries):
            CriteriaScoreSubmission.objects.create(event=self.event, assigned_round_id=rid,
                category=criterion.category, criterion=criterion, judge=self.judge,
                participant_id=entry.pk, raw_score=90-i*10, status='SUBMITTED', source='MOBILE')

    def remove(self, entry=None, **kwargs):
        return remove_criteria_participant(self.event, self.faculty,
            (entry or self.entries[0]).pk, kwargs.get('reason', 'disqualified'), kwargs.get('explanation', ''))

    def second_round(self):
        self.scores('first')
        approve_criteria_round(self.event, self.faculty, 0)
        unlock_criteria_round(self.event, self.faculty, 1)
        self.scores('second')

    def test_round_two_removal_preserves_all_scores_and_round_one_snapshot(self):
        self.second_round()
        before = list(CriteriaScoreSubmission.objects.order_by('pk').values())
        snapshot = deepcopy(self.event.result_processing_config['confirmed_stages']['0'])
        record = self.remove(explanation='Rule violation')
        self.assertEqual(record['round_index'], 1)
        self.assertEqual(list(CriteriaScoreSubmission.objects.order_by('pk').values()), before)
        self.assertEqual(self.event.result_processing_config['confirmed_stages']['0'], snapshot)
        self.assertEqual(round_participant_ids(self.event, 0), self.event.participant_ids)
        self.assertEqual(round_participant_ids(self.event, 1), [self.entries[1].pk])
        portal = criteria_faculty_portal(self.event, self.faculty, 1)
        self.assertEqual(len(portal['rounds'][0]['results']), 2)
        self.assertEqual([row['participant_id'] for row in portal['results']], [self.entries[1].pk])
        approve_criteria_round(self.event, self.faculty, 1)
        submit_criteria_publication(self.event, self.faculty)
        from .criteria_faculty_service import approved_final_rankings
        self.assertEqual([row['participant_id'] for row in approved_final_rankings(self.event)], [self.entries[1].pk])

    def test_restore_in_same_round_recovers_eligibility_and_existing_scores(self):
        self.second_round()
        before = list(CriteriaScoreSubmission.objects.order_by('pk').values())
        record = self.remove()
        restore_criteria_participant(self.event, self.faculty, record['id'])
        self.assertEqual(round_participant_ids(self.event, 1), self.event.participant_ids)
        self.assertEqual(len(criteria_faculty_portal(self.event, self.faculty, 1)['results']), 2)
        self.assertEqual(list(CriteriaScoreSubmission.objects.order_by('pk').values()), before)
        with self.assertRaises(FacultyServiceError):
            restore_criteria_participant(self.event, self.faculty, record['id'])

    def test_reason_participant_explanation_and_duplicates_are_validated(self):
        for reason in ['', 'invalid']:
            with self.assertRaises(FacultyServiceError):
                self.remove(reason=reason)
        with self.assertRaises(FacultyServiceError):
            self.remove(explanation='x' * 2001)
        with self.assertRaises(FacultyServiceError):
            remove_criteria_participant(self.event, self.faculty, 999999, 'absent')
        self.remove()
        with self.assertRaises(FacultyServiceError):
            self.remove()

    def test_remove_and_restore_are_authorized_and_csrf_protected(self):
        with self.assertRaises(FacultyServiceError):
            remove_criteria_participant(self.event, self.other, self.entries[0].pk, 'other')
        record = self.remove()
        with self.assertRaises(FacultyServiceError):
            restore_criteria_participant(self.event, self.other, record['id'])
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.faculty)
        self.assertEqual(csrf_client.post(self.url, {'action': 'restore_participant',
            'removal_id': record['id'], 'confirm': 'yes'}).status_code, 403)

    def test_forms_history_and_confirmation(self):
        response = self.client.get(self.url, {'tab': 'participants'})
        self.assertContains(response, 'Reason for removal')
        self.assertContains(response, 'Voluntary withdrawal')
        self.client.post(self.url, {'action': 'remove_participant', 'participant_id': self.entries[0].pk,
                                   'reason': 'absent'})
        self.event.refresh_from_db()
        self.assertNotIn('participant_removals', self.event.result_processing_config)
        self.client.post(self.url, {'action': 'remove_participant', 'participant_id': self.entries[0].pk,
            'reason': 'absent', 'explanation': '<script>alert(1)</script>', 'confirm': 'yes', 'tab': 'participants'})
        self.event.refresh_from_db()
        record = self.event.result_processing_config['participant_removals'][0]
        response = self.client.get(self.url, {'tab': 'removal_history'})
        self.assertEqual(response.context['tab'], 'removal_history')
        self.assertContains(response, 'Absent / No-show')
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        response = self.client.post(self.url, {'action': 'restore_participant', 'removal_id': record['id'],
                                              'confirm': 'yes', 'tab': 'removal_history'})
        self.assertRedirects(response, self.url + '?tab=removal_history&round=0')
        self.event.refresh_from_db()
        self.assertTrue(self.event.result_processing_config['participant_removals'][0]['restored_at'])

    def test_ocr_definition_and_direct_ranking_exclude_removed_only_in_later_round(self):
        self.second_round()
        self.remove()
        self.assertEqual(len(definition(self.event, self.faculty, 'first', self.judge.pk)['participants']), 2)
        self.assertEqual([row['id'] for row in definition(self.event, self.faculty, 'second', self.judge.pk)['participants']], [self.entries[1].pk])
        results = compute_official_criteria_rankings(self.event, round_id='second', accepted_statuses=['SUBMITTED'])
        self.assertEqual([row['participant_id'] for row in results], [self.entries[1].pk])

    def test_removal_between_rounds_starts_at_next_round(self):
        self.scores('first')
        approve_criteria_round(self.event, self.faculty, 0)
        record = self.remove()
        self.assertEqual(record['round_index'], 1)
        unlock_criteria_round(self.event, self.faculty, 1)
        self.assertEqual(round_participant_ids(self.event, 0), self.event.participant_ids)
        self.assertEqual(round_participant_ids(self.event, 1), [self.entries[1].pk])

    def test_restore_does_not_bypass_qualification_or_change_approved_round(self):
        record = self.remove()
        self.scores('first')
        approve_criteria_round(self.event, self.faculty, 0)
        snapshot = deepcopy(self.event.result_processing_config['confirmed_stages']['0'])
        restore_criteria_participant(self.event, self.faculty, record['id'])
        unlock_criteria_round(self.event, self.faculty, 1)
        self.assertEqual(round_participant_ids(self.event, 0), [self.entries[1].pk])
        self.assertEqual(round_participant_ids(self.event, 1), [self.entries[1].pk])
        self.assertEqual(self.event.result_processing_config['confirmed_stages']['0'], snapshot)

    def test_final_approved_and_published_rosters_are_read_only(self):
        self.second_round()
        approve_criteria_round(self.event, self.faculty, 1)
        with self.assertRaises(FacultyServiceError):
            self.remove()
        self.event.results_finalized = True
        self.event.save()
        with self.assertRaises(FacultyServiceError):
            self.remove()

    def test_removal_preserves_ordinal_scores_with_gaps(self):
        self.event.criteria_score_method = 'ranking_based'
        self.event.result_processing_config = {'ranking_aggregation': 'mean_rank'}
        self.event.save()
        category = self.criteria['first'].category
        category.judge_mode = 'ranking'
        category.save()
        self.scores('first')
        for i, score in enumerate(CriteriaScoreSubmission.objects.order_by('pk'), 1):
            score.raw_score = i
            score.save()
        self.remove()
        portal = criteria_faculty_portal(self.event, self.faculty)
        self.assertTrue(portal['selected_round']['can_approve'], portal['errors'])
        self.assertEqual(portal['results'][0]['final_score'], 2)

    def test_mobile_roster_and_stale_submission_respect_removal(self):
        from datetime import time
        from .criteria_mobile_rounds import mobile_round_context, read_mobile_round_context
        from .models import EventCategory, JudgingEvent
        category = EventCategory.objects.create(name='Arts', category_type='academic')
        mobile = JudgingEvent.objects.create(title='Contest', category=category,
            date=date.today(), time=time(9), venue='Hall', status='active')
        mobile.assigned_judges.add(self.judge)
        self.judge.groups.add(Group.objects.get_or_create(name='Judge')[0])
        self.event.judging_event = mobile
        self.event.save()
        original = mobile_round_context(mobile)
        removed_candidate = next(key for key, entry_id in original['candidate_entries'].items()
                                 if entry_id == self.entries[0].pk)
        record = self.remove()
        current = read_mobile_round_context(mobile)
        self.assertNotIn(removed_candidate, current['candidate_entries'])
        from .mobile_judge import detail_payload
        detail = detail_payload(self.judge, mobile)
        self.assertNotIn(self.entries[0].pk, [row['entry_id'] for row in detail['participants']])
        self.client.force_login(self.judge)
        response = self.client.post(reverse('judgingevent-submit-scores', args=[mobile.pk]),
            {'candidate_id': removed_candidate,
             'scores': [{'criterion_id': original['criteria'][0].pk, 'score': 85}]},
            content_type='application/json')
        self.assertGreaterEqual(response.status_code, 400)
        self.assertFalse(CriteriaScoreSubmission.objects.exists())
        restore_criteria_participant(self.event, self.faculty, record['id'])
        self.assertIn(removed_candidate, read_mobile_round_context(mobile)['candidate_entries'])
        self.assertIn(self.entries[0].pk, [row['entry_id'] for row in detail_payload(self.judge, mobile)['participants']])

    def test_public_roster_faculty_details_and_report_counts_restore_together(self):
        from .public_service import public_participants
        from .faculty_service import serialize_event_detail
        from .admin_report_service import build_report_preview, _count_participants
        record = self.remove()
        self.assertEqual([row['id'] for row in public_participants(self.event)], [self.entries[1].pk])
        self.assertNotIn(self.entries[0].display_name, serialize_event_detail(self.event)['participants'])
        report = build_report_preview('contestants')
        self.assertNotIn(self.entries[0].display_name, [row[0] for row in report['rows']])
        self.assertEqual(_count_participants([self.event]), 1)
        restore_criteria_participant(self.event, self.faculty, record['id'])
        self.assertEqual(len(public_participants(self.event)), 2)
        self.assertIn(self.entries[0].display_name, serialize_event_detail(self.event)['participants'])
        report = build_report_preview('contestants')
        self.assertIn(self.entries[0].display_name, [row[0] for row in report['rows']])
        self.assertEqual(_count_participants([self.event]), 2)

    def test_removing_all_entries_does_not_resurrect_legacy_roster(self):
        from .public_service import public_participants
        from .faculty_service import serialize_event_detail
        from .admin_report_service import build_report_preview, _count_participants
        self.remove()
        self.remove(self.entries[1])
        self.assertEqual(public_participants(self.event), [])
        self.assertEqual(serialize_event_detail(self.event)['participants'], [])
        self.assertEqual(build_report_preview('contestants')['rows'], [])
        self.assertEqual(_count_participants([self.event]), 0)

    def test_tabulator_breakdown_and_completion_keep_only_historical_removed_scores(self):
        from core.faculty_views import _judge_completion
        from core.tabulator_result_page import build_results_page
        from django.test import RequestFactory
        self.second_round()
        self.remove()
        CriteriaScoreSubmission.objects.filter(event=self.event).update(status='APPROVED')
        completion = _judge_completion(self.event)
        self.assertEqual(completion['expected_per_judge'], 3)
        self.assertEqual(completion['submitted'], 1)
        factory = RequestFactory()
        first = build_results_page(factory.get('/', {'round_id': 'first'}), self.event, {'event_kind': 'criteria'})
        second = build_results_page(factory.get('/', {'round_id': 'second'}), self.event, {'event_kind': 'criteria'})
        self.assertIn(self.entries[0].display_name, [row['name'] for row in first['breakdown_groups']])
        self.assertNotIn(self.entries[0].display_name, [row['name'] for row in second['breakdown_groups']])
        self.assertTrue(second['round_complete'])
