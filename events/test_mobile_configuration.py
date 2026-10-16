"""Screenshot-shaped missing setup, with no changes to historical Pageants scores."""
from datetime import date, time
from io import StringIO
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from . import test_mobile_submit as fixtures
from .mobile_configuration import MobileConfigurationError, apply_configuration, configuration_plan
from .models import (Candidate, Criterion, CriteriaEventEntry, CriteriaScoreSubmission, Event,
                     EventScoringCategory, EventScoringCriterion, JudgeScore, JudgingEvent)
from .test_mobile_api import read_only_queries


class MobileConfigurationTests(TestCase):
    def setUp(self):
        fixtures.MobileSubmitTests.setUp(self)
        self.faculty = get_user_model().objects.create_user(username='duet-faculty')
        self.faculty.groups.add(Group.objects.create(name='Faculty'))
        self.round_id = 's9bu97fk'
        self.duet = JudgingEvent.objects.create(pk=64, title='Vocal Duet', category=self.mobile.category,
            date=date.today(), time=time(9), status='active')
        self.event = Event.objects.create(pk=79, name='Vocal Duet', event_date=date.today(), scoring_method='criteria',
            status='active', judging_event=self.duet, faculty_account=self.faculty, participation_type='pair',
            rounds_config=[{'id': self.round_id, 'name': 'Vocal Duet Performance', 'status': 'open'}])
        self.event.assigned_judges.add(self.judges[-1])
        self.entries = [CriteriaEventEntry.objects.create(event=self.event, entry_type='pair',
            display_name=f'Pair {index + 1}', display_order=index) for index in range(2)]
        self.category = EventScoringCategory.objects.create(event=self.event, name='Vocal Duet Performance',
            assigned_round_id=self.round_id, judge_mode='scoring', display_order=1, overall_weight_percent=100)
        # Illustrative test weights, not a claim about live Vocal Duet weights.
        names = ['Tone Quality / Vocal Technique', 'Blended / Harmony', 'Musicianship', 'Deportment']
        self.definitions = [EventScoringCriterion.objects.create(category=self.category, name=name,
            max_score=100, weight_percent=weight, display_order=index)
            for index, (name, weight) in enumerate(zip(names, (40, 30, 20, 10)), 1)]
        self.event.participant_ids = [entry.pk for entry in self.entries]
        self.event.save()
        self.detail_url = f'/api/events/judge/assignments/{self.duet.pk}/'

    def existing_rows(self):
        criteria = [Criterion.objects.create(event=self.duet,
            name=(f'{self.category.name}: {row.name}' if index % 2 else row.name),
            max_score=row.max_score, weight_percent=row.weight_percent, order=10 + index)
            for index, row in enumerate(self.definitions)]
        candidates = [Candidate.objects.create(event=self.duet, name=row.display_name, number=index + 1)
                      for index, row in enumerate(self.entries)]
        return criteria, candidates

    def test_preview_is_read_only_and_reproduces_open_round_with_disabled_buttons(self):
        output = StringIO()
        with read_only_queries():
            before = self.api.get(self.detail_url).json()
            call_command('prepare_judge_mobile', judging_event_id=self.duet.pk, stdout=output)
        self.assertFalse(before['can_score'])
        self.assertTrue(before['configuration_required'])
        self.assertEqual(before['participants_total'], 2)
        plan = json.loads(output.getvalue())
        self.assertTrue(plan['read_only'])
        self.assertTrue(plan['can_apply'], plan['blocked_reasons'])
        self.assertEqual(len(plan['criteria']), 4)
        self.assertEqual(len(plan['participants']), 2)
        self.assertTrue(all(row['action'] == 'create' for row in plan['criteria'] + plan['participants']))
        self.assertEqual(self.duet.criteria.count(), 0)
        self.assertEqual(self.duet.candidates.count(), 0)
        self.assertEqual(JudgeScore.objects.count(), 60)
        self.event.refresh_from_db()
        self.assertEqual(self.event.result_processing_config, {})

    def test_explicit_apply_prepares_four_criteria_two_pairs_and_submission_without_touching_pageants(self):
        plan = configuration_plan(self.event)
        original_rounds = self.event.rounds_config
        official = list(self.category.criteria.values())
        output = StringIO()
        call_command('prepare_judge_mobile', judging_event_id=self.duet.pk, apply=True,
            actor_id=self.faculty.pk, approve_plan=plan['fingerprint'], stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual(len(result['created']['criteria']), 4)
        self.assertEqual(len(result['created']['candidates']), 2)
        with read_only_queries():
            detail = self.api.get(self.detail_url).json()
        self.assertTrue(detail['can_score'], detail['scoring_unavailable_reasons'])
        self.assertEqual(detail['participant_label'], 'Pairs')
        self.assertEqual([row['weight_percent'] for row in detail['criteria']], [40, 30, 20, 10])
        payload = {'candidate_id': detail['participants'][0]['id'], 'round_id': self.round_id,
            'scores': [{'criterion_id': row['id'], 'score': 80} for row in detail['criteria']]}
        url = f'/api/events/judging-events/{self.duet.pk}/submit_scores/'
        first = self.api.post(url, payload, format='json')
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()['total_score'], 80)
        self.assertEqual(self.api.post(url, payload, format='json').json(), first.json())
        self.assertEqual(list(JudgeScore.objects.exclude(judge_id=57).order_by('pk').values()), self.before)
        self.assertEqual(CriteriaScoreSubmission.objects.filter(event=self.event).count(), 4)
        self.event.refresh_from_db()
        self.assertEqual(self.event.rounds_config, original_rounds)
        self.assertEqual(list(self.category.criteria.values()), official)

    def test_existing_bare_or_qualified_rows_are_reused_without_snapshot_or_reordering(self):
        criteria, candidates = self.existing_rows()
        snapshot = list(self.duet.criteria.values())
        plan = configuration_plan(self.event)
        self.assertTrue(plan['can_apply'], plan['blocked_reasons'])
        self.assertTrue(all(row['action'] == 'reuse' for row in plan['criteria'] + plan['participants']))
        result = apply_configuration(self.duet.pk, self.faculty, plan['fingerprint'])
        self.assertEqual(result['created'], {'criteria': [], 'candidates': []})
        self.assertEqual(list(self.duet.criteria.values()), snapshot)
        self.event.refresh_from_db()
        repeat = configuration_plan(self.event)
        result = apply_configuration(self.duet.pk, self.faculty, repeat['fingerprint'])
        self.assertEqual(result['created'], {'criteria': [], 'candidates': []})
        self.assertEqual(self.duet.criteria.count(), len(criteria))
        self.assertEqual(self.duet.candidates.count(), len(candidates))
        self.assertTrue(self.api.get(self.detail_url).json()['can_score'])

    def test_ambiguous_existing_criteria_or_participants_never_create_duplicates(self):
        criteria, _ = self.existing_rows()
        original = criteria[0]
        Criterion.objects.create(event=self.duet, name=original.name, max_score=original.max_score,
            weight_percent=original.weight_percent, order=90)
        Candidate.objects.create(event=self.duet, name=self.entries[0].display_name, number=99)
        plan = configuration_plan(self.event)
        self.assertFalse(plan['can_apply'])
        self.assertTrue(any('multiple existing mobile rows' in row for row in plan['blocked_reasons']))
        self.assertTrue(any('ambiguous existing identity' in row for row in plan['blocked_reasons']))
        with self.assertRaises(MobileConfigurationError):
            apply_configuration(self.duet.pk, self.faculty, plan['fingerprint'])
        self.assertEqual(self.duet.criteria.count(), 5)
        self.assertEqual(self.duet.candidates.count(), 3)

    def test_incompatible_weight_and_stale_explicit_mapping_are_not_repaired(self):
        criteria, _ = self.existing_rows()
        criteria[0].weight_percent = 99
        criteria[0].save()
        self.event.result_processing_config = {'mobile_round_mapping': {'criteria': {
            f'{self.round_id}:{self.definitions[1].pk}': -1}}}
        self.event.save()
        plan = configuration_plan(self.event)
        self.assertFalse(plan['can_apply'])
        self.assertTrue(any('incompatible existing mobile row' in row for row in plan['blocked_reasons']))
        self.assertTrue(any('stale or incompatible explicit mapping' in row for row in plan['blocked_reasons']))
        self.assertEqual(Criterion.objects.get(pk=criteria[0].pk).weight_percent, 99)

    def test_pageants_and_any_event_with_saved_scores_are_blocked(self):
        plan = configuration_plan(self.portal)
        self.assertFalse(plan['can_apply'])
        self.assertEqual(plan['existing_score_rows']['legacy'], 60)
        self.assertTrue(any('saved scores' in row for row in plan['blocked_reasons']))
        CriteriaScoreSubmission.objects.create(event=self.event, assigned_round_id=self.round_id, category=self.category,
            criterion=self.definitions[0], judge=self.judges[-1], participant_id=self.entries[0].pk,
            participant_type='entry', source='MANUAL', status='DRAFT', raw_score=10)
        self.assertFalse(configuration_plan(self.event)['can_apply'])
        self.assertEqual(list(JudgeScore.objects.order_by('pk').values()), self.before)

    def test_unauthorized_stale_plan_and_command_missing_approval_make_no_changes(self):
        plan = configuration_plan(self.event)
        with self.assertRaises(MobileConfigurationError):
            apply_configuration(self.duet.pk, self.judges[-1], plan['fingerprint'])
        with self.assertRaises(CommandError):
            call_command('prepare_judge_mobile', judging_event_id=self.duet.pk, apply=True, stdout=StringIO())
        self.entries[0].display_name = 'Changed since review'
        self.entries[0].save()
        with self.assertRaisesMessage(MobileConfigurationError, 'changed since preview'):
            apply_configuration(self.duet.pk, self.faculty, plan['fingerprint'])
        self.assertEqual(self.duet.criteria.count(), 0)
        self.assertEqual(self.duet.candidates.count(), 0)

    def test_closed_round_bad_category_weights_and_mid_apply_failure_roll_back(self):
        self.event.rounds_config[0]['status'] = 'locked'
        self.event.save()
        self.assertFalse(configuration_plan(self.event)['can_apply'])
        self.event.rounds_config[0]['status'] = 'open'
        self.event.save()
        self.category.overall_weight_percent = 90
        self.category.save()
        self.assertFalse(configuration_plan(self.event)['can_apply'])
        self.category.overall_weight_percent = 100
        self.category.save()
        plan = configuration_plan(self.event)
        with patch('events.mobile_configuration.Candidate.objects.create', side_effect=RuntimeError('local test failure')):
            with self.assertRaises(RuntimeError):
                apply_configuration(self.duet.pk, self.faculty, plan['fingerprint'])
        self.assertEqual(self.duet.criteria.count(), 0)
        self.assertEqual(self.duet.candidates.count(), 0)
        self.event.refresh_from_db()
        self.assertEqual(self.event.result_processing_config, {})

    def test_differently_named_existing_records_require_review_not_duplicate_creation(self):
        criteria, candidates = self.existing_rows()
        criteria[0].name = 'Different legacy label'
        criteria[0].save()
        candidates[0].name = 'Different legacy pair label'
        candidates[0].save()
        plan = configuration_plan(self.event)
        self.assertFalse(plan['can_apply'])
        self.assertTrue(any('possible existing identity' in row for row in plan['blocked_reasons']))
        self.assertTrue(any('unmatched existing mobile candidates' in row for row in plan['blocked_reasons']))
        self.assertEqual(self.duet.criteria.count(), 4)
        self.assertEqual(self.duet.candidates.count(), 2)

    def test_completed_pageants_receipts_stay_unchanged_after_duet_setup(self):
        dance = JudgingEvent.objects.create(pk=65, title='Modern Dance Contemporary', category=self.mobile.category,
            date=date.today(), time=time(9), status='active')
        portal = Event.objects.create(pk=78, name=dance.title, event_date=date.today(), scoring_method='criteria',
            status='active', judging_event=dance, participation_type='team',
            rounds_config=[{'id': 's22ecy0y', 'name': 'Final Round', 'status': 'open'}])
        portal.assigned_judges.add(self.judges[-1])
        category = EventScoringCategory.objects.create(event=portal, name='Dance', assigned_round_id='s22ecy0y',
            judge_mode='scoring', display_order=1, overall_weight_percent=100)
        definition = EventScoringCriterion.objects.create(category=category, name='Technique', display_order=1,
            max_score=100, weight_percent=100)
        mobile_criterion = Criterion.objects.create(event=dance, name='Technique', order=1, max_score=100, weight_percent=100)
        mapping = {'criteria': {f's22ecy0y:{definition.pk}': mobile_criterion.pk}, 'candidates': {}}
        for index in range(6):
            entry = CriteriaEventEntry.objects.create(event=portal, entry_type='team', display_name=f'Team {index + 1}', display_order=index)
            candidate = Candidate.objects.create(event=dance, name=entry.display_name, number=index + 1)
            mapping['candidates'][str(entry.pk)] = candidate.pk
        portal.participant_ids = [int(key) for key in mapping['candidates']]
        portal.result_processing_config = {'mobile_round_mapping': mapping}
        portal.save()
        dance_before = self.api.get('/api/events/judge/assignments/65/').json()
        self.assertTrue(dance_before['can_score'])
        self.assertEqual(dance_before['participants_total'], 6)
        for candidate in self.candidates:
            payload = {**self.payload, 'candidate_id': candidate.pk}
            self.assertEqual(self.api.post(self.url, payload, format='json').status_code, 200)
        legacy = list(JudgeScore.objects.filter(candidate__event=self.mobile).order_by('pk').values())
        normalized = list(CriteriaScoreSubmission.objects.filter(event=self.portal).order_by('pk').values())
        before = self.api.get('/api/events/judge/assignments/63/').json()
        self.assertEqual(before['participants_scored'], 8)
        plan = configuration_plan(self.event)
        apply_configuration(self.duet.pk, self.faculty, plan['fingerprint'])
        self.assertEqual(list(JudgeScore.objects.filter(candidate__event=self.mobile).order_by('pk').values()), legacy)
        self.assertEqual(list(CriteriaScoreSubmission.objects.filter(event=self.portal).order_by('pk').values()), normalized)
        self.assertEqual(self.api.get('/api/events/judge/assignments/63/').json(), before)
        self.assertEqual(self.api.get('/api/events/judge/assignments/65/').json(), dance_before)
