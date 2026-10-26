"""Focused coverage of event-local divisions, stable ordering and round gates."""
import json
from datetime import date
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from . import test_faculty_criteria_workflow as fixtures
from .criteria_event_service import (save_criteria_event, serialize_criteria_event,
    _validated_rounds, CriteriaEventValidationError)
from .criteria_faculty_service import (approve_criteria_round, unlock_criteria_round,
    criteria_faculty_portal, round_participant_ids, _qualifiers)
from .criteria_scoring import compute_official_criteria_rankings
from .divisions import validate_division_setup
from .models import CriteriaEventEntry, CriteriaScoreSubmission, RegistryCandidate


class EventDivisionTests(TransactionTestCase):
    reset_sequences = True
    def setUp(self):
        fixtures.FacultyCriteriaWorkflowTests.setUp(self)
        # Four existing participants in two custom divisions.
        self.people = [RegistryCandidate.objects.create(number=str(10 + i), name=f'Person {i}') for i in range(4)]
        for index, entry in enumerate(self.entries):
            entry.entry_type = 'individual'; entry.source_individual = self.people[index]; entry.save()
        for index in range(2, 4):
            self.entries.append(CriteriaEventEntry.objects.create(event=self.event, entry_type='individual',
                source_individual=self.people[index], display_name=self.people[index].name, display_order=index))
        self.event.participation_type = 'individual'
        self.event.participant_ids = [entry.pk for entry in self.entries]
        self.event.result_processing_config = {'event_divisions': {'enabled': True, 'names': ['Junior', 'Senior'],
            'assignments': {str(entry.pk): 'Junior' if index < 2 else 'Senior' for index, entry in enumerate(self.entries)}}}
        self.event.save()

    def complete(self):
        for entry, value in zip(self.entries, [80, 70, 99, 90]):
            fixtures.FacultyCriteriaWorkflowTests.score(self, entry=entry, value=value)

    def test_custom_names_validation_and_missing_assignment(self):
        for raw, entries in [({'enabled': True, 'names': ['Junior', 'junior']}, []),
                             ({'enabled': True, 'names': ['Custom']}, [{'division': 'Other'}])]:
            with self.assertRaises(ValueError): validate_division_setup(raw, entries)
        self.assertEqual(validate_division_setup({}, [])['enabled'], False)
        self.assertEqual(validate_division_setup({'enabled': True, 'names': ['Open Class']}, [{'division': 'Open Class'}])['names'], ['Open Class'])

    def test_rank_and_advance_independently_preserving_scores(self):
        self.complete()
        portal = criteria_faculty_portal(self.event, self.faculty)
        self.assertEqual([(row['division'], row['rank']) for row in portal['results']],
                         [('Junior', 1), ('Junior', 2), ('Senior', 1), ('Senior', 2)])
        before = list(CriteriaScoreSubmission.objects.values_list('pk', 'raw_score'))
        approve_criteria_round(self.event, self.faculty, 0)
        unlock_criteria_round(self.event, self.faculty, 1)
        self.assertEqual(round_participant_ids(self.event, 1), [self.entries[0].pk, self.entries[2].pk])
        self.assertEqual(list(CriteriaScoreSubmission.objects.values_list('pk', 'raw_score')), before)
        self.assertFalse(self.event.results_finalized)

    def test_tie_in_one_division_blocks_approval(self):
        self.complete()
        CriteriaScoreSubmission.objects.filter(participant_id=self.entries[1].pk).update(raw_score=80)
        self.assertIn('Junior: A tie', ' '.join(criteria_faculty_portal(self.event, self.faculty)['errors']))

    def test_oversized_qualification_rejected_per_division(self):
        rounds = [{'id': 'a', 'name': 'First', 'weight': 50, 'qualifiers': 3, 'qualification_method': 'top_ranking'},
                  {'id': 'b', 'name': 'Final', 'weight': 50}]
        with self.assertRaisesMessage(CriteriaEventValidationError, 'smallest division'):
            _validated_rounds('multiple_stage', {'rounds_config': json.dumps(rounds)}, participant_count=4,
                              division_counts={'Junior': 2, 'Senior': 2})
        rounds[0]['qualifiers'] = 1
        self.assertEqual(_validated_rounds('multiple_stage', {'rounds_config': json.dumps(rounds)}, participant_count=4,
                         division_counts={'Junior': 2, 'Senior': 2})[0]['qualifiers'], 1)

    def test_manual_qualification_requires_correct_count_per_division(self):
        self.complete()
        results = criteria_faculty_portal(self.event, self.faculty)['results']
        cfg = {'is_final': False, 'qualification_method': 'manual_selection', 'qualifiers': 1}
        from .faculty_service import FacultyServiceError
        with self.assertRaises(FacultyServiceError):
            _qualifiers(cfg, results, [self.entries[0].pk, self.entries[1].pk], event=self.event)
        ids, errors = _qualifiers(cfg, results, [self.entries[1].pk, self.entries[3].pk], event=self.event)
        self.assertFalse(errors); self.assertEqual(ids, [self.entries[1].pk, self.entries[3].pk])

    def payload(self, order=None):
        order = order or list(range(4))
        return {'criteria_wizard_version': '4', 'event_name': 'Divided event', 'category': 'Special Event',
            'event_classification': 'minor', 'participation_type': 'individual', 'venue': 'Hall',
            'start_date': date.today().isoformat(), 'end_date': date.today().isoformat(),
            'publication_status': 'draft', 'event_format': 'multiple_stage', 'criteria_score_method': 'weighted_percentage',
            'rounds_config': json.dumps([dict(row, weight=50) for row in self.event.rounds_config]),
            'event_entries': json.dumps([{'source_individual_id': self.people[index].pk, 'division': 'Junior' if index < 2 else 'Senior'} for index in order]),
            'result_processing_config': json.dumps({'event_divisions': {'enabled': True, 'names': ['Junior', 'Senior']}})}

    @patch('events.criteria_event_service._schedule_assignment_notifications')
    def test_order_swap_keeps_ids_numbers_and_mobile_identity(self, notify):
        admin = get_user_model().objects.create_superuser('division-admin', 'division@example.test', 'local-password')
        event = save_criteria_event(self.payload(), admin, instance=self.event)
        mobile_ids = set(event.judging_event.candidates.values_list('pk', flat=True))
        ids = {entry.source_individual_id: entry.pk for entry in event.criteria_entries.all()}
        event = save_criteria_event(self.payload([1, 0, 3, 2]), admin, instance=event)
        self.assertEqual(event.participant_ids, [ids[self.people[i].pk] for i in [1, 0, 3, 2]])
        self.assertEqual(set(event.judging_event.candidates.values_list('pk', flat=True)), mobile_ids)
        self.assertEqual([row['candidate_number'] for row in serialize_criteria_event(event)['entries']], ['11', '10', '13', '12'])

    @patch('events.criteria_event_service._schedule_assignment_notifications')
    def test_scored_assignment_change_rejected_without_touching_scores(self, notify):
        self.complete()
        admin = get_user_model().objects.create_superuser('division-admin', 'division@example.test', 'local-password')
        payload = self.payload()
        entries = json.loads(payload['event_entries']); entries[0]['division'] = 'Senior'
        payload['event_entries'] = json.dumps(entries)
        with self.assertRaisesMessage(CriteriaEventValidationError, 'cannot change after scoring'):
            save_criteria_event(payload, admin, instance=self.event)
        self.assertEqual(CriteriaScoreSubmission.objects.count(), 4)

    def test_ordinal_ranks_can_restart_in_each_division(self):
        category = self.criteria['prelim'].category
        category.judge_mode = 'ranking'; category.save()
        self.event.result_processing_config['ranking_aggregation'] = 'weighted_mean_rank'; self.event.save()
        for entry, value in zip(self.entries, [1, 2, 1, 2]):
            fixtures.FacultyCriteriaWorkflowTests.score(self, entry=entry, value=value)
        rows = compute_official_criteria_rankings(self.event, round_id='prelim', accepted_statuses=['SUBMITTED'], configured=True)
        self.assertEqual([row['rank'] for row in rows], [1, 2, 1, 2])

    def test_mobile_payload_grouped_order_and_original_numbers_and_submission(self):
        from types import SimpleNamespace
        from . import test_mobile_submit as mobile_fixture
        from .mobile_judge import detail_payload
        from .judging_serializers import JudgingEventDetailSerializer
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory, force_authenticate
        data = SimpleNamespace()
        mobile_fixture.MobileSubmitTests.setUp(data)
        entries = list(data.portal.criteria_entries.all())
        # Deliberately interleave divisions; within each division keep the saved order.
        names = ['Senior' if index % 2 else 'Junior' for index in range(8)]
        for index, entry in enumerate(entries):
            person = RegistryCandidate.objects.create(number=str(101 + index), name=entry.display_name)
            entry.source_individual = person; entry.save()
        data.portal.result_processing_config['event_divisions'] = {'enabled': True, 'names': ['Junior', 'Senior'],
            'assignments': {str(entry.pk): name for entry, name in zip(entries, names)}}
        segment_id = data.portal.scoring_categories.first().pk
        data.portal.result_processing_config['segment_performance_orders'] = {str(segment_id): [entry.pk for entry in reversed(entries)]}
        data.portal.save()
        detail = detail_payload(data.judges[-1], data.mobile)
        self.assertEqual(detail['segment_performance_orders'][0]['candidate_ids'], [data.candidates[i].pk for i in [6, 4, 2, 0, 7, 5, 3, 1]])
        self.assertEqual([row['division'] for row in detail['participants']], ['Junior'] * 4 + ['Senior'] * 4)
        self.assertEqual([row['appearance_order'] for row in detail['participants']], [1, 2, 3, 4] * 2)
        self.assertEqual([row['number'] for row in detail['participants']], [101, 103, 105, 107, 102, 104, 106, 108])
        self.assertEqual(detail['participants'][0]['candidate_number'], '101')
        request = APIRequestFactory().get('/')
        force_authenticate(request, data.judges[-1])
        serialized = JudgingEventDetailSerializer(data.mobile, context={'request': Request(request)}).data
        self.assertEqual([row['division'] for row in serialized['candidates']], ['Junior'] * 4 + ['Senior'] * 4)
        response = data.api.post(data.url, data.payload, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        from .models import JudgeScore
        self.assertEqual(list(JudgeScore.objects.exclude(judge_id=57).order_by('pk').values()), data.before)

    def test_final_publication_and_points_keep_divisions_separate(self):
        from django.utils import timezone
        from .criteria_faculty_service import submit_criteria_publication
        from .public_service import criteria_results
        from .points_settings_service import criteria_placements
        self.complete()
        approve_criteria_round(self.event, self.faculty, 0)
        unlock_criteria_round(self.event, self.faculty, 1)
        for entry in [self.entries[0], self.entries[2]]:
            fixtures.FacultyCriteriaWorkflowTests.score(self, round_id='final', entry=entry, value=95)
        approve_criteria_round(self.event, self.faculty, 1)
        submit_criteria_publication(self.event, self.faculty)
        placements, error = criteria_placements(self.event, {'tie_policy': 'reject'})
        self.assertFalse(error)
        self.assertEqual([(row['division'], row['rank']) for row in placements], [('Junior', 1), ('Senior', 1)])
        # Public readers still see nothing until the existing publication gate is satisfied.
        self.assertEqual(criteria_results(self.event)[0], [])
        self.event.publication_status = 'published'; self.event.status = 'completed'; self.event.results_finalized = True
        self.event.result_processing_config['published_at'] = timezone.now().isoformat()
        self.event.save()
        results, message = criteria_results(self.event)
        self.assertEqual([(row['division'], row['rank']) for row in results], [('Junior', 1), ('Senior', 1)])

    @patch('events.criteria_event_service._schedule_assignment_notifications')
    def test_segment_order_persists_without_changing_default_ids_or_scores(self, notify):
        admin = get_user_model().objects.create_superuser('segment-admin', 'segment@example.test', 'local-password')
        payload = self.payload()
        config = json.loads(payload['result_processing_config'])
        key = str(self.criteria['prelim'].category_id)
        config['segment_performance_orders'] = {key: [self.people[i].pk for i in [1, 0, 3, 2]]}
        payload['result_processing_config'] = json.dumps(config)
        original_ids = list(self.event.participant_ids)
        saved = save_criteria_event(payload, admin, instance=self.event)
        self.assertEqual(saved.participant_ids, original_ids)
        self.assertEqual(saved.result_processing_config['segment_performance_orders'][key], [original_ids[i] for i in [1, 0, 3, 2]])
        from .divisions import segment_participant_order
        self.assertEqual(segment_participant_order(saved, key, original_ids), [original_ids[i] for i in [1, 0, 3, 2]])
        self.assertEqual(segment_participant_order(saved, key, [original_ids[0], original_ids[2]]), [original_ids[0], original_ids[2]])
        self.assertFalse(CriteriaScoreSubmission.objects.exists())

    @patch('events.criteria_event_service._schedule_assignment_notifications')
    def test_segment_order_rejects_missing_duplicate_and_foreign_entries(self, notify):
        admin = get_user_model().objects.create_superuser('segment-admin', 'segment@example.test', 'local-password')
        key = str(self.criteria['prelim'].category_id)
        ids = [person.pk for person in self.people]
        for orders in [{key: ids[:2]}, {key: [ids[0]] * 4}, {'999999': ids}, {key: ids[:3] + [999999]}]:
            payload = self.payload()
            config = json.loads(payload['result_processing_config']); config['segment_performance_orders'] = orders
            payload['result_processing_config'] = json.dumps(config)
            with self.assertRaisesMessage(CriteriaEventValidationError, 'exactly once'):
                save_criteria_event(payload, admin, instance=self.event)
