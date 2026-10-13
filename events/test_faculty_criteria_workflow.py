from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from .criteria_faculty_service import (approve_criteria_round, criteria_faculty_portal,
    round_is_scoreable, submit_criteria_publication, unlock_criteria_round)
from .faculty_service import FacultyServiceError
from .models import CriteriaEventEntry, CriteriaScoreSubmission, Event, EventScoringCategory, EventScoringCriterion


class FacultyCriteriaWorkflowTests(TestCase):
    def setUp(self):
        self.faculty = get_user_model().objects.create_user(username='workflow-faculty')
        self.faculty.groups.add(Group.objects.get_or_create(name='Faculty')[0])
        self.judge = get_user_model().objects.create_user(username='workflow-judge')
        self.other = get_user_model().objects.create_user(username='other-faculty')
        self.other.groups.add(Group.objects.get(name='Faculty'))
        self.event = Event.objects.create(name='Dance', category='Arts', venue='Hall', event_date=date.today(), scoring_method='criteria', faculty_account=self.faculty, rounds_config=[{'id': 'prelim', 'name': 'Preliminary', 'status': 'open', 'qualifiers': 1, 'qualification_method': 'top_ranking'}, {'id': 'final', 'name': 'Final', 'status': 'locked'}])
        self.event.assigned_judges.add(self.judge)
        self.entries = [CriteriaEventEntry.objects.create(event=self.event, entry_type='team', display_name=f'Team {index}', display_order=index) for index in range(2)]
        self.event.participant_ids = [row.pk for row in self.entries]
        self.event.save()
        self.criteria = {}
        for index, round_id in enumerate(['prelim', 'final']):
            category = EventScoringCategory.objects.create(event=self.event, assigned_round_id=round_id, name=round_id, purpose='official', judge_mode='scoring', overall_weight_percent=100, display_order=index)
            self.criteria[round_id] = EventScoringCriterion.objects.create(category=category, name='Skill', weight_percent=100, min_score=0, max_score=100, display_order=0)

    def score(self, round_id='prelim', entry=None, value=90, status='SUBMITTED'):
        criterion = self.criteria[round_id]
        return CriteriaScoreSubmission.objects.create(event=self.event, assigned_round_id=round_id, category=criterion.category, criterion=criterion, judge=self.judge, participant_type='entry', participant_id=(entry or self.entries[0]).pk, source='MOBILE', status=status, raw_score=value)

    def complete(self):
        self.score(value=90)
        self.score(entry=self.entries[1], value=80)

    def test_partial_and_draft_submissions_do_not_enable_approval(self):
        self.score()
        self.score(entry=self.entries[1], status='DRAFT')
        portal = criteria_faculty_portal(self.event, self.faculty)
        self.assertFalse(portal['selected_round']['can_approve'])
        self.assertEqual(portal['judges'][0]['progress_percent'], 50)
        with self.assertRaises(FacultyServiceError):
            approve_criteria_round(self.event, self.faculty, 0)

    def test_approval_does_not_unlock_and_explicit_unlock_advances_only_qualifier(self):
        self.complete()
        approve_criteria_round(self.event, self.faculty, 0)
        self.assertFalse(round_is_scoreable(self.event, 1))
        self.assertEqual(self.event.result_processing_config['active_stage_index'] if 'active_stage_index' in self.event.result_processing_config else 0, 0)
        unlock_criteria_round(self.event, self.faculty, 1)
        self.assertTrue(round_is_scoreable(self.event, 1))
        self.assertEqual(self.event.result_processing_config['qualified_participant_ids']['1'], [self.entries[0].pk])
        with self.assertRaises(FacultyServiceError):
            unlock_criteria_round(self.event, self.faculty, 1)

    def test_unauthorized_actions_and_unlock_before_approval(self):
        self.complete()
        with self.assertRaises(FacultyServiceError):
            approve_criteria_round(self.event, self.other, 0)
        with self.assertRaises(FacultyServiceError):
            unlock_criteria_round(self.event, self.faculty, 1)

    def test_invalid_score_and_boundary_tie_block_approval(self):
        first = self.score(value=101)
        self.score(entry=self.entries[1], value=90)
        self.assertFalse(criteria_faculty_portal(self.event, self.faculty)['selected_round']['can_approve'])
        first.raw_score = 90
        first.save()
        self.assertIn('tie', ' '.join(criteria_faculty_portal(self.event, self.faculty)['errors']).lower())

    def test_final_approval_and_submission_do_not_publish(self):
        self.complete()
        approve_criteria_round(self.event, self.faculty, 0)
        unlock_criteria_round(self.event, self.faculty, 1)
        self.score(round_id='final')
        approve_criteria_round(self.event, self.faculty, 1)
        submit_criteria_publication(self.event, self.faculty)
        self.event.refresh_from_db()
        self.assertFalse(self.event.results_finalized)
        self.assertTrue(self.event.result_processing_config['ready_for_publication'])

    def test_tabulator_verification_required_and_never_implicitly_approved(self):
        self.complete()
        self.event.result_processing_config = {'verification_mode': 'tabulator_verification'}
        self.event.save()
        with self.assertRaises(FacultyServiceError):
            approve_criteria_round(self.event, self.faculty, 0)
        self.assertFalse(CriteriaScoreSubmission.objects.filter(status='APPROVED').exists())

    def test_single_round_has_no_unlock_and_can_submit_after_approval(self):
        self.event.rounds_config = [self.event.rounds_config[0]]
        self.event.save()
        self.complete()
        approve_criteria_round(self.event, self.faculty, 0)
        self.assertTrue(criteria_faculty_portal(self.event, self.faculty)['publication']['can_submit'])

    def test_approval_snapshot_remains_visible(self):
        self.complete()
        approve_criteria_round(self.event, self.faculty, 0)
        CriteriaScoreSubmission.objects.filter(event=self.event).update(raw_score=0)
        self.assertEqual(criteria_faculty_portal(self.event, self.faculty)['results'][0]['final_score'], 90)

    def test_ordinal_ranking_uses_lowest_rank_and_rejects_duplicate_ranks(self):
        self.event.criteria_score_method = 'ranking_based'
        self.event.result_processing_config = {'ranking_aggregation': 'weighted_mean_rank'}
        self.event.save()
        self.criteria['prelim'].category.judge_mode = 'ranking'
        self.criteria['prelim'].category.save()
        self.score(value=1)
        second = self.score(entry=self.entries[1], value=2)
        portal = criteria_faculty_portal(self.event, self.faculty)
        self.assertTrue(portal['selected_round']['can_approve'])
        self.assertEqual(portal['results'][0]['participant_id'], self.entries[0].pk)
        second.raw_score = 1
        second.save()
        self.assertFalse(criteria_faculty_portal(self.event, self.faculty)['selected_round']['can_approve'])

    def test_admin_approval_flag_is_not_granted_by_faculty_submission(self):
        self.event.rounds_config = [self.event.rounds_config[0]]
        self.event.result_processing_config = {'verification_mode': 'tabulator_admin_approval', 'tabulation_confirmed_at': '2026-01-01T00:00:00+00:00', 'approved': False}
        self.event.save()
        self.score(value=90, status='VERIFIED')
        self.score(entry=self.entries[1], value=80, status='VERIFIED')
        approve_criteria_round(self.event, self.faculty, 0)
        submit_criteria_publication(self.event, self.faculty)
        self.assertFalse(self.event.result_processing_config['approved'])

    def test_carryover_uses_accumulated_weights_and_final_score_tiebreak(self):
        self.event.rounds_config[0].update(weight=50, qualifiers=2, qualification_method='advance_all')
        self.event.rounds_config[1].update(weight=50, carry_previous_scores=True)
        self.event.tie_break_rules = [{'method': 'highest_final_round'}]
        self.event.save()
        self.complete()
        approve_criteria_round(self.event, self.faculty, 0)
        unlock_criteria_round(self.event, self.faculty, 1)
        self.score(round_id='final', value=80)
        self.score(round_id='final', entry=self.entries[1], value=90)
        results = criteria_faculty_portal(self.event, self.faculty, 1)['results']
        self.assertEqual(results[0]['participant_id'], self.entries[1].pk)
        self.assertEqual(results[0]['rank'], 1)
        self.assertEqual(results[1]['rank'], 2)

    def test_ranking_is_blocked_without_explicit_aggregation_configuration(self):
        self.event.criteria_score_method = 'ranking_based'
        self.event.save()
        category = self.criteria['prelim'].category
        category.judge_mode = 'ranking'
        category.save()
        self.score(value=1)
        self.score(entry=self.entries[1], value=2)
        self.assertFalse(criteria_faculty_portal(self.event, self.faculty)['selected_round']['can_approve'])
