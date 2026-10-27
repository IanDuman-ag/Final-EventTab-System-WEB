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
from .divisions import assign_gender_divisions, validate_division_setup
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

    def test_gender_assignment_only_matches_named_divisions_and_unassigned_individuals(self):
        from types import SimpleNamespace
        rows = [
            {'entry_type': 'individual', 'source': SimpleNamespace(gender=gender), 'division': division}
            for gender, division in [('male', ''), ('female', ''), ('other', ''), ('', ''), ('male', 'Junior')]]
        rows.append({'entry_type': 'team', 'source': SimpleNamespace(gender='male'), 'division': ''})
        assign_gender_divisions({'enabled': True, 'names': ['MALE', 'Female', 'Junior']}, rows)
        self.assertEqual([row['division'] for row in rows], ['MALE', 'Female', '', '', 'Junior', ''])
        unassigned = [{'entry_type': 'individual', 'source': SimpleNamespace(gender='male'), 'division': ''}]
        assign_gender_divisions({'enabled': True, 'names': ['Male Division', 'Junior']}, unassigned)
        self.assertEqual(unassigned[0]['division'], '')
        assign_gender_divisions({'enabled': False, 'names': ['Male']}, unassigned)
        self.assertEqual(unassigned[0]['division'], '')
        assign_gender_divisions({'enabled': True, 'names': ['Male']}, unassigned)
        self.assertEqual(unassigned[0]['division'], 'Male')

    def gender_payload(self):
        for person, gender in zip(self.people, ['male', 'female', 'male', 'female']):
            person.gender = gender
            person.save(update_fields=['gender'])
        payload = self.payload()
        rows = json.loads(payload['event_entries'])
        for row in rows:
            row['division'] = ''
        payload['event_entries'] = json.dumps(rows)
        payload['result_processing_config'] = json.dumps({'event_divisions': {'enabled': True, 'names': ['Male', 'Female']}})
        return payload

    @patch('events.criteria_event_service._schedule_assignment_notifications')
    def test_gender_assignment_persists_without_renumbering_or_replacing_entries(self, notify):
        admin = get_user_model().objects.create_superuser('gender-division-admin', 'gd@example.test', 'local-password')
        ids = list(self.event.participant_ids)
        event = save_criteria_event(self.gender_payload(), admin, instance=self.event)
        data = serialize_criteria_event(event)
        self.assertEqual([row['division'] for row in data['entries']], ['Male', 'Female', 'Male', 'Female'])
        self.assertEqual(event.participant_ids, ids)
        self.assertFalse(data['division_setup_locked'])
        self.complete()
        self.assertTrue(serialize_criteria_event(event)['division_setup_locked'])

    @patch('events.criteria_event_service._schedule_assignment_notifications')
    def test_custom_divisions_still_require_manual_assignment(self, notify):
        admin = get_user_model().objects.create_superuser('custom-division-admin', 'custom@example.test', 'local-password')
        payload = self.gender_payload()
        payload['result_processing_config'] = json.dumps({'event_divisions': {'enabled': True, 'names': ['Junior', 'Senior']}})
        with self.assertRaisesMessage(CriteriaEventValidationError, 'Assign each selected participant'):
            save_criteria_event(payload, admin, instance=self.event)

    @patch('events.criteria_event_service._schedule_assignment_notifications')
    def test_removing_division_after_scores_is_rejected_without_score_loss(self, notify):
        admin = get_user_model().objects.create_superuser('remove-division-admin', 'remove@example.test', 'local-password')
        payload = self.payload()
        self.complete()
        before = list(CriteriaScoreSubmission.objects.order_by('pk').values_list('pk', 'raw_score'))
        rows = json.loads(payload['event_entries'])
        for row in rows:
            row['division'] = 'Senior'
        payload['event_entries'] = json.dumps(rows)
        payload['result_processing_config'] = json.dumps({'event_divisions': {'enabled': True, 'names': ['Senior']}})
        with self.assertRaisesMessage(CriteriaEventValidationError, 'cannot change after scoring'):
            save_criteria_event(payload, admin, instance=self.event)
        self.assertEqual(list(CriteriaScoreSubmission.objects.order_by('pk').values_list('pk', 'raw_score')), before)

    @patch('events.criteria_event_service._schedule_assignment_notifications')
    def test_turning_off_divisions_preserves_unscored_participant_ids(self, notify):
        admin = get_user_model().objects.create_superuser('no-division-admin', 'none@example.test', 'local-password')
        payload = self.payload()
        payload['result_processing_config'] = json.dumps({'event_divisions': {'enabled': False, 'names': []}})
        rows = json.loads(payload['event_entries'])
        for row in rows:
            row['division'] = ''
        payload['event_entries'] = json.dumps(rows)
        ids = list(self.event.participant_ids)
        event = save_criteria_event(payload, admin, instance=self.event)
        self.assertEqual(event.participant_ids, ids)
        self.assertFalse(event.result_processing_config['event_divisions']['enabled'])

    @patch('events.criteria_event_service._schedule_assignment_notifications')
    def test_individual_workspace_also_persists_gender_assignment(self, notify):
        from .criteria_event_service import save_criteria_participant_setup
        admin = get_user_model().objects.create_superuser('workspace-gender-admin', 'workspace-gender@example.test', 'local-password')
        event = save_criteria_participant_setup(self.event, self.gender_payload(), admin)
        self.assertEqual([row['division'] for row in serialize_criteria_event(event)['entries']], ['Male', 'Female', 'Male', 'Female'])

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

    def test_top_five_advances_five_from_each_gender_division(self):
        for index in range(4, 12):
            self.entries.append(CriteriaEventEntry.objects.create(event=self.event, entry_type='individual',
                display_name=f'Candidate {index}', display_order=index))
        self.event.participant_ids = [row.pk for row in self.entries]
        assignments = {str(row.pk): 'Male' if index < 6 else 'Female'
                       for index, row in enumerate(self.entries)}
        self.event.result_processing_config = {'event_divisions': {
            'enabled': True, 'names': ['Male', 'Female'], 'assignments': assignments}}
        self.event.rounds_config[0]['qualifiers'] = 5
        self.event.save()
        # All Male scores exceed Female scores, so an overall Top 5 would fail this test.
        for index, entry in enumerate(self.entries):
            fixtures.FacultyCriteriaWorkflowTests.score(self, entry=entry, value=100 - index)
        before = list(CriteriaScoreSubmission.objects.order_by('pk').values_list('pk', 'raw_score'))
        approve_criteria_round(self.event, self.faculty, 0)
        unlock_criteria_round(self.event, self.faculty, 1)
        self.event.refresh_from_db()
        qualified = round_participant_ids(self.event, 1)
        self.assertEqual(len(qualified), 10)
        for name in ['Male', 'Female']:
            self.assertEqual(sum(assignments[str(pk)] == name for pk in qualified), 5)
        self.assertNotIn(self.entries[5].pk, qualified)
        self.assertNotIn(self.entries[11].pk, qualified)
        self.assertEqual(list(CriteriaScoreSubmission.objects.order_by('pk').values_list('pk', 'raw_score')), before)

    def test_award_submission_does_not_block_main_round_review(self):
        from .models import EventScoringCategory, EventScoringCriterion
        self.complete()
        category = EventScoringCategory.objects.create(event=self.event, name='Performance Award', assigned_round_id='prelim', purpose='special_award', judge_mode='scoring', overall_weight_percent=0, display_order=10)
        criterion = EventScoringCriterion.objects.create(category=category, name='Award', weight_percent=100, min_score=0, max_score=100, display_order=0)
        CriteriaScoreSubmission.objects.create(event=self.event, assigned_round_id='prelim', category=category, criterion=criterion, judge=self.judge, participant_type='entry', participant_id=self.entries[0].pk, source='OCR', status='SUBMITTED', raw_score=95)
        portal = criteria_faculty_portal(self.event, self.faculty)
        self.assertTrue(portal['selected_round']['can_approve'], portal['errors'])
        self.assertEqual([row['final_score'] for row in portal['results']], [80, 70, 99, 90])

    def test_combined_results_use_approved_round_scores_by_division(self):
        from .criteria_scoring import combine_round_results
        self.complete()
        approve_criteria_round(self.event, self.faculty, 0)
        unlock_criteria_round(self.event, self.faculty, 1)
        self.event.result_processing_config['final_result_basis'] = 'weighted_rounds'
        self.event.rounds_config[0]['weight'] = 25
        self.event.rounds_config[1]['weight'] = 75
        self.event.save()
        finalists = [{'participant_id': entry.pk, 'final_score': 60, 'rank': 1}
                     for entry in [self.entries[0], self.entries[2]]]
        rows = combine_round_results(self.event, finalists, 1)
        self.assertEqual([float(row['final_score']) for row in rows], [65, 69.75])
        self.assertEqual([row['rank'] for row in rows], [1, 1])
        self.event.result_processing_config['final_result_basis'] = 'cumulative_rounds'
        rows = combine_round_results(self.event, finalists, 1)
        self.assertEqual([float(row['final_score']) for row in rows], [70, 79.5])
        self.assertEqual(CriteriaScoreSubmission.objects.count(), 4)

    def test_final_round_only_ignores_previous_scores(self):
        from .criteria_scoring import combine_round_results
        self.event.result_processing_config['final_result_basis'] = 'final_round_only'
        rows = [{'participant_id': self.entries[0].pk, 'final_score': 60, 'rank': 1}]
        self.assertEqual(combine_round_results(self.event, rows, 1), rows)

    def test_combined_final_can_be_approved_and_saved(self):
        self.complete()
        approve_criteria_round(self.event, self.faculty, 0)
        unlock_criteria_round(self.event, self.faculty, 1)
        self.event.result_processing_config['final_result_basis'] = 'cumulative_rounds'
        self.event.save()
        for entry in [self.entries[0], self.entries[2]]:
            fixtures.FacultyCriteriaWorkflowTests.score(self, round_id='final', entry=entry, value=60)
        approve_criteria_round(self.event, self.faculty, 1)
        self.event.refresh_from_db()
        rows = self.event.result_processing_config['confirmed_stages']['1']['results']
        self.assertEqual([row['final_score'] for row in rows], [70, 79.5])

    def test_combined_results_reject_missing_approved_snapshots(self):
        from .criteria_scoring import combine_round_results
        self.event.result_processing_config['final_result_basis'] = 'cumulative_rounds'
        with self.assertRaisesMessage(ValueError, 'approved score'):
            combine_round_results(self.event, [{'participant_id': self.entries[0].pk, 'final_score': 60}], 1)

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
    def test_team_and_pair_divisions_save_with_stable_entry_ids(self, notify):
        from .models import Team
        admin = get_user_model().objects.create_superuser('group-admin', 'group@example.test', 'local-password')
        teams = [Team.objects.create(name=f'Group {index}', code=f'G{index}') for index in range(2)]
        for kind, rows in [
            ('team', [{'source_team_id': team.pk, 'division': name} for team, name in zip(teams, ['Junior', 'Senior'])]),
            ('pair', [{'member_ids': [self.people[0].pk, self.people[1].pk], 'division': 'Junior'},
                      {'member_ids': [self.people[2].pk, self.people[3].pk], 'division': 'Senior'}]),
        ]:
            with self.subTest(kind=kind):
                payload = self.payload()
                payload['participation_type'] = kind
                payload['event_entries'] = json.dumps(rows)
                event = save_criteria_event(payload, admin, instance=self.event)
                ids = list(event.participant_ids)
                payload['event_entries'] = json.dumps(list(reversed(rows)))
                event = save_criteria_event(payload, admin, instance=event)
                self.assertEqual(event.participant_ids, list(reversed(ids)))
                self.assertEqual([row['division'] for row in serialize_criteria_event(event)['entries']], ['Senior', 'Junior'])

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

    def test_individuals_route_opens_workspace_and_registry_remains_available(self):
        from django.test import Client
        admin = get_user_model().objects.create_superuser('workspace-admin', 'workspace@example.test', 'local-password')
        client = Client(); client.force_login(admin)
        response = client.get('/admin/candidates/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="candidates-table"')
        workspace = client.get('/admin/candidates/?performance=1')
        self.assertContains(workspace, 'individual-performance-mount')
        self.assertTrue(workspace.context['individual_workspace'])
        self.assertEqual(client.get('/admin/candidates/?registry=1').status_code, 200)
        self.assertContains(client.get('/admin/candidates/?registry=1'), 'id="candidates-table"')

    def test_workspace_save_preserves_scores_publication_judges_and_rounds(self):
        from .criteria_event_service import save_criteria_participant_setup
        self.complete()
        admin = get_user_model().objects.create_superuser('workspace-admin', 'workspace@example.test', 'local-password')
        before = (self.event.publication_status, self.event.status, self.event.rounds_config,
                  list(self.event.assigned_judges.values_list('pk', flat=True)))
        scores = list(CriteriaScoreSubmission.objects.order_by('pk').values_list('pk', 'raw_score'))
        event = save_criteria_participant_setup(self.event, self.payload([1, 0, 3, 2]), admin)
        self.assertEqual((event.publication_status, event.status, event.rounds_config,
                          list(event.assigned_judges.values_list('pk', flat=True))), before)
        self.assertEqual(list(CriteriaScoreSubmission.objects.order_by('pk').values_list('pk', 'raw_score')), scores)
        self.assertEqual(event.participant_ids, [self.entries[i].pk for i in [1, 0, 3, 2]])

    def test_workspace_save_endpoint_requires_staff_and_rejects_scored_reassignment(self):
        from django.test import Client
        self.complete()
        client = Client(); client.force_login(self.judge)
        payload = self.payload(); payload.update(event_id=self.event.pk, workflow_action='save_participant_setup')
        self.assertEqual(client.post('/admin/events/criteria/', payload).status_code, 302)
        admin = get_user_model().objects.create_superuser('workspace-admin', 'workspace@example.test', 'local-password')
        client.force_login(admin)
        rows = json.loads(payload['event_entries']); rows[0]['division'] = 'Senior'
        payload['event_entries'] = json.dumps(rows)
        response = client.post('/admin/events/criteria/', payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn('cannot change after scoring', response.json()['message'])

    def test_registry_has_no_division_controls_and_ignores_stale_division_filter(self):
        from django.test import Client
        admin = get_user_model().objects.create_superuser('registry-admin', 'registry@example.test', 'local-password')
        client = Client(); client.force_login(admin)
        response = client.get('/admin/candidates/', {'event': self.event.pk, 'division': 'Senior', 'q': 'Person 3'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['id'] for row in response.context['candidate_rows']], [self.people[3].pk])
        self.assertNotContains(response, '<th>Division</th>')
        self.assertNotContains(response, 'name="division"')
        self.assertNotContains(response, 'Junior Division')
        self.assertContains(response, '<th>Gender</th>')
        self.assertEqual(response.context['total_candidate_count'], 4)
        unscoped = client.get('/admin/candidates/', {'event': 'all'})
        self.assertEqual(len(unscoped.context['candidate_rows']), 4)
        self.event.refresh_from_db()
        self.assertTrue(self.event.result_processing_config['event_divisions']['enabled'])
        self.assertContains(response, 'id="open-candidate-modal"', count=1)

    def test_individual_numbers_sort_numerically_before_pagination(self):
        from django.test import Client
        for number in range(1, 10):
            RegistryCandidate.objects.create(number=str(number), name=f'Individual {number}')
        RegistryCandidate.objects.create(number='G-99', name='Custom number')
        before = dict(RegistryCandidate.objects.values_list('pk', 'number'))
        participant_ids = list(self.event.participant_ids)
        admin = get_user_model().objects.create_superuser('number-admin', 'number@example.test', 'local-password')
        client = Client()
        client.force_login(admin)
        first = client.get('/admin/candidates/', {'event': self.event.pk})
        second = client.get('/admin/candidates/', {'event': self.event.pk, 'page': 2})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual([row['number'] for row in first.context['candidate_rows']], [str(number) for number in range(1, 11)])
        self.assertEqual([row['number'] for row in second.context['candidate_rows']], ['11', '12', '13', 'G-99'])
        self.assertEqual(dict(RegistryCandidate.objects.values_list('pk', 'number')), before)
        self.event.refresh_from_db()
        self.assertEqual(self.event.participant_ids, participant_ids)

    def test_participant_crud_still_works_from_individuals_table(self):
        from django.test import Client
        from .models import Department
        admin = get_user_model().objects.create_superuser('registry-admin', 'registry@example.test', 'local-password')
        client = Client(); client.force_login(admin)
        department = Department.objects.create(name='Test Program', code='TEST')
        payload = {'number': 'NEW-99', 'name': 'New Individual', 'department_id': department.pk, 'status': 'active'}
        response = client.post('/admin/candidates/create/', payload)
        self.assertEqual(response.status_code, 200)
        person_id = response.json()['id']
        self.assertEqual(client.get(f'/admin/candidates/{person_id}/view/').json()['name'], 'New Individual')
        payload['name'] = 'Updated Individual'
        self.assertEqual(client.post(f'/admin/candidates/{person_id}/edit/', payload).status_code, 200)
        self.assertEqual(RegistryCandidate.objects.get(pk=person_id).name, 'Updated Individual')
        self.assertEqual(client.post(f'/admin/candidates/{person_id}/delete/').status_code, 200)
        self.assertFalse(RegistryCandidate.objects.filter(pk=person_id).exists())

    def test_gender_create_edit_view_and_table_keep_event_divisions_unchanged(self):
        from django.test import Client
        admin = get_user_model().objects.create_superuser('gender-admin', 'gender@example.test', 'local-password')
        client = Client(); client.force_login(admin)
        before = self.event.result_processing_config
        created = client.post('/admin/candidates/create/', {'number': 'G-99', 'name': 'New Person', 'gender': 'female'})
        self.assertEqual(created.status_code, 200)
        person_id = created.json()['id']
        self.assertEqual(client.get(f'/admin/candidates/{person_id}/view/').json()['gender_label'], 'Female')
        response = client.post(f'/admin/candidates/{person_id}/edit/', {'number': 'G-99', 'name': 'New Person', 'gender': 'male'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RegistryCandidate.objects.get(pk=person_id).gender, 'male')
        table = client.get('/admin/candidates/')
        self.assertContains(table, 'data-label="Gender"')
        self.assertEqual(next(row for row in table.context['candidate_rows'] if row['id'] == person_id)['gender_label'], 'Male')
        self.event.refresh_from_db()
        self.assertEqual(self.event.result_processing_config, before)

    def test_gender_rejects_invalid_values_before_creating_or_editing(self):
        from django.test import Client
        admin = get_user_model().objects.create_superuser('gender-admin', 'gender@example.test', 'local-password')
        client = Client(); client.force_login(admin)
        for gender in ['invalid', ['male']]:
            response = client.post('/admin/candidates/create/', json.dumps({'number': 'G-99', 'name': 'New Person', 'gender': gender}), content_type='application/json')
            self.assertEqual(response.status_code, 400)
            self.assertFalse(RegistryCandidate.objects.filter(number='G-99').exists())
        person = self.people[0]
        response = client.post(f'/admin/candidates/{person.pk}/edit/', {'number': person.number, 'name': 'Bad change', 'gender': 'invalid'})
        self.assertEqual(response.status_code, 400)
        person.refresh_from_db()
        self.assertNotEqual(person.name, 'Bad change')

    def test_existing_gender_is_preserved_when_legacy_edit_omits_it(self):
        from django.test import Client
        self.assertEqual(self.people[0].gender, '')
        self.assertEqual(self.people[0].get_gender_display(), 'Not specified')
        person = self.people[0]; person.gender = 'other'; person.save()
        admin = get_user_model().objects.create_superuser('gender-admin', 'gender@example.test', 'local-password')
        client = Client(); client.force_login(admin)
        response = client.post(f'/admin/candidates/{person.pk}/edit/', {'number': person.number, 'name': person.name})
        self.assertEqual(response.status_code, 200)
        person.refresh_from_db()
        self.assertEqual(person.gender, 'other')
