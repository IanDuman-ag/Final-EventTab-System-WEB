from copy import deepcopy
from datetime import date
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from events.faculty_service import FacultyServiceError, leaderboard_rows, publish_results
from events.models import AuditLog, BracketMatch, BracketTeam, Department, Event, SystemSettings
from events.points_settings_service import (
    ScoringRulesError, default_rules, event_snapshot, reconcile_championship_points,
    reopen_match_for_correction, scoring_rules, validate_scoring_rules,
)
from events.superadmin_service import championship_standings


class ChampionshipRulesTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user('championship-admin', is_staff=True)
        self.client.force_login(self.admin)
        self.department = Department.objects.create(name='Engineering', code='CHAMP')
        self.other_department = Department.objects.create(name='Science', code='OTHER')

    def rules(self, method='placement', classification='major'):
        rules = default_rules()
        rules.update(method=method, classification=classification)
        rules['placements'] = [{'rank': 1, 'major': 10, 'minor': 5}, {'rank': 2, 'major': 7, 'minor': 3}]
        return rules

    def fixture(self, method='placement', classification='major'):
        event = Event.objects.create(name='Championship', event_date=date.today(), scoring_method='match',
                                     event_classification=classification, tournament_type='single_elimination',
                                     result_processing_config={'championship_scoring': self.rules(method, classification), 'approved': True})
        a = BracketTeam.objects.create(event=event, name='Team A', department=self.department)
        b = BracketTeam.objects.create(event=event, name='Team B', department=self.other_department)
        match = BracketMatch.objects.create(event=event, match_number=1, round_name='Final', team_a=a, team_b=b,
                                             winner=a, loser=b, status='completed', score_a='10', score_b='7')
        return event, a, b, match

    def payload(self, method='placement'):
        rules = self.rules(method)
        return {'method': method, 'major_win': 3, 'minor_win': 2, 'placements': json.dumps(rules['placements']),
                'approval_required': 'on', 'automatic_calculation': 'on', 'tie_policy': 'official'}

    def test_major_minor_placement_awards(self):
        for classification, expected in [('major', (10, 7)), ('minor', (5, 3))]:
            event, a, b, _ = self.fixture(classification=classification)
            reconcile_championship_points(event)
            a.refresh_from_db(); b.refresh_from_db()
            self.assertEqual((a.points, b.points), expected)

    def test_per_win_and_combined_are_idempotent(self):
        for method, expected in [('per_win', (3, 0)), ('combined', (13, 7))]:
            event, a, b, _ = self.fixture(method=method)
            for _ in range(3):
                reconcile_championship_points(event)
                a.refresh_from_db(); b.refresh_from_db()
                self.assertEqual((a.points, b.points), expected)

    def test_byes_pending_and_draws_do_not_earn_win_points(self):
        event, a, b, final = self.fixture(method='per_win')
        BracketMatch.objects.create(event=event, match_number=0, round_name='Bye', winner=a, status='completed', is_automatic_advance=True)
        BracketMatch.objects.create(event=event, match_number=2, round_name='Pending', winner=a)
        BracketMatch.objects.create(event=event, match_number=3, round_name='Draw', team_a=a, team_b=b, status='completed')
        reconcile_championship_points(event)
        a.refresh_from_db()
        self.assertEqual(a.points, 3)

    def test_placement_waits_for_complete_approved_event(self):
        event, a, _, match = self.fixture()
        event.result_processing_config['approved'] = False
        event.save()
        reconcile_championship_points(event)
        a.refresh_from_db()
        self.assertEqual(a.points, 0)
        with self.assertRaises(FacultyServiceError):
            publish_results(event, self.admin)

    def test_official_leaderboards_only_include_publication_once(self):
        event, a, b, match = self.fixture(method='combined')
        reconcile_championship_points(event)
        self.assertEqual(leaderboard_rows(), [])
        self.assertEqual(championship_standings(), [])
        publish_results(event, self.admin)
        publish_results(event, self.admin)
        self.assertEqual(leaderboard_rows()[0]['points'], 13)
        self.assertEqual(championship_standings()[0]['total_points'], 13)
        event.status = Event.STATUS_INACTIVE
        event.save()
        self.assertEqual(leaderboard_rows(), [])

    def test_correction_reverses_award_and_withdraws_publication(self):
        event, a, b, match = self.fixture(method='per_win')
        publish_results(event, self.admin)
        reopen_match_for_correction(event, match.pk, self.admin, 'Correct entered winner')
        a.refresh_from_db(); match.refresh_from_db(); event.refresh_from_db()
        self.assertEqual(a.points, 0)
        self.assertEqual(match.status, 'pending')
        self.assertFalse(event.results_finalized)
        self.assertTrue(event.result_processing_config['championship_requires_republication'])
        self.assertEqual(leaderboard_rows(), [])
        match.winner, match.loser, match.status = b, a, 'completed'
        match.save()
        event.result_processing_config['approved'] = True
        event.save()
        publish_results(event, self.admin)
        self.assertEqual(leaderboard_rows()[0]['department'], 'Science')
        self.assertEqual(leaderboard_rows()[0]['points'], 3)
        self.assertTrue(AuditLog.objects.filter(action='Reopened Match for Correction').exists())

    def test_manual_calculation_is_explicit(self):
        event, a, _, _ = self.fixture('per_win')
        event.result_processing_config['championship_scoring']['automatic_calculation'] = False
        event.save()
        reconcile_championship_points(event)
        a.refresh_from_db(); self.assertEqual(a.points, 0)
        reconcile_championship_points(event, force=True)
        a.refresh_from_db(); self.assertEqual(a.points, 3)

    def test_validation_unique_nonnegative_and_confirmed_exception(self):
        data = self.payload()
        data['placements'] = json.dumps([{'rank': 1, 'major': 0, 'minor': 0}])
        self.assertEqual(validate_scoring_rules(data)['placements'][0]['major'], 0)
        data['placements'] = json.dumps([{'rank': 1, 'major': 1, 'minor': 1}] * 2)
        with self.assertRaises(ScoringRulesError): validate_scoring_rules(data)
        data['placements'] = json.dumps([{'rank': 1, 'major': 1, 'minor': 1}, {'rank': 2, 'major': 2, 'minor': 2}])
        with self.assertRaises(ScoringRulesError): validate_scoring_rules(data)
        data['confirm_nonmonotonic'] = 'on'
        self.assertEqual(len(validate_scoring_rules(data)['placements']), 2)
        for value in (-1, 'NaN', '1.2', ''):
            with self.assertRaises(ScoringRulesError): validate_scoring_rules({**self.payload('per_win'), 'major_win': value})

    def test_global_save_snapshots_and_audits(self):
        event, _, _, _ = self.fixture('per_win')
        before = deepcopy(event.result_processing_config)
        response = self.client.post(reverse('admin_settings'), self.payload('combined'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(scoring_rules()['method'], 'combined')
        event.refresh_from_db(); self.assertEqual(event.result_processing_config, before)
        self.assertTrue(AuditLog.objects.filter(action='Changed Championship Scoring Rules', metadata__updated__method='combined').exists())

    def test_staff_faculty_cannot_change_global_rules(self):
        group = Group.objects.create(name='Faculty')
        self.admin.groups.add(group)
        self.assertEqual(self.client.post(reverse('admin_settings'), self.payload()).status_code, 403)
        self.assertFalse(SystemSettings.objects.exists())

    def test_event_override_is_audited_and_requires_reason(self):
        event, _, _, _ = self.fixture('per_win')
        publish_results(event, self.admin)
        url = reverse('admin_settings') + f'?event={event.pk}'
        self.assertEqual(self.client.post(url, self.payload('combined')).status_code, 400)
        self.assertEqual(self.client.post(url, {**self.payload('combined'), 'reason': 'Approved tournament rule change'}).status_code, 302)
        event.refresh_from_db()
        self.assertFalse(event.results_finalized)
        self.assertEqual(event.result_processing_config['championship_scoring']['method'], 'combined')

    def test_shared_round_robin_ties_and_manual_resolution(self):
        event, a, b, match = self.fixture()
        event.tournament_type = 'round_robin'
        event.result_processing_config['championship_scoring']['tie_policy'] = 'shared'
        event.save()
        BracketMatch.objects.create(event=event, match_number=2, round_name='Return', team_a=b, team_b=a,
                                   winner=b, loser=a, status='completed')
        reconcile_championship_points(event)
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.points, b.points), (10, 10))
        event.result_processing_config['championship_scoring']['tie_policy'] = 'manual'
        event.save()
        reconcile_championship_points(event)
        self.assertIn('resolution', event.result_processing_config['championship_pending'])

    def test_criteria_judge_totals_stay_separate_from_placement_awards(self):
        from events.criteria_scoring import compute_official_criteria_rankings
        from events.models import CriteriaEventEntry, CriteriaScoreSubmission, EventScoringCategory, EventScoringCriterion
        rules = self.rules('placement', 'minor')
        event = Event.objects.create(name='Singing', category='Special Event', event_date=date.today(), scoring_method='criteria',
                                     event_classification='minor', apply_championship_points=True,
                                     result_processing_config={'championship_scoring': rules, 'approved': True})
        entry = CriteriaEventEntry.objects.create(event=event, entry_type='individual', display_name='Singer', department=self.department)
        event.participant_ids = [entry.pk]; event.save()
        judge = get_user_model().objects.create_user('scoring-judge')
        event.assigned_judges.add(judge)
        category = EventScoringCategory.objects.create(event=event, assigned_round_id='1', name='Performance',
                                                       judge_mode='scoring', display_order=1, overall_weight_percent=100)
        criterion = EventScoringCriterion.objects.create(category=category, name='Voice', display_order=1,
                                                         weight_percent=100, min_score=0, max_score=100)
        score = CriteriaScoreSubmission.objects.create(event=event, assigned_round_id='1', category=category, criterion=criterion,
                                                       judge=judge, participant_id=entry.pk, source='MANUAL', status='APPROVED', raw_score=92)
        before = compute_official_criteria_rankings(event, round_id='1')
        publish_results(event, self.admin)
        self.assertEqual(leaderboard_rows()[0]['points'], 5)
        self.assertEqual(compute_official_criteria_rankings(event, round_id='1'), before)
        self.assertEqual(event.bracket_teams.count(), 0)
        score.status = 'SUBMITTED'; score.save()
        self.assertEqual(leaderboard_rows(), [])

    def test_global_defaults_do_not_overwrite_saved_per_win_settings(self):
        SystemSettings.objects.create(championship_points_config={'major': 8, 'minor': 2})
        self.assertEqual(scoring_rules()['method'], 'per_win')
        self.assertEqual(scoring_rules()['win_points'], {'major': 8, 'minor': 2})

    def test_backend_rejects_incomplete_final_placement_resolution(self):
        event, a, b, match = self.fixture()
        url = reverse('admin_settings') + f'?event={event.pk}'
        data = {**self.payload(), 'reason': 'Resolve final standings', f'placement_{a.pk}': '1'}
        self.assertEqual(self.client.post(url, data).status_code, 400)
        data[f'placement_{b.pk}'] = '2'
        self.assertEqual(self.client.post(url, data).status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.result_processing_config['championship_placements'], {str(a.pk): 1, str(b.pk): 2})

    def test_final_approval_requires_all_matches_to_be_confirmed(self):
        from events.faculty_service import approve_results
        event, a, b, match = self.fixture('combined')
        match.status = 'pending'; match.save()
        with self.assertRaises(FacultyServiceError): approve_results(event, self.admin)

    def test_placement_rank_does_not_depend_on_award_values(self):
        from core.faculty_views import _match_standings
        event, a, b, _ = self.fixture()
        rules = event.result_processing_config['championship_scoring']
        rules['placements'] = [{'rank': 1, 'major': 0, 'minor': 0}, {'rank': 2, 'major': 100, 'minor': 100}]
        event.save()
        reconcile_championship_points(event)
        rows = _match_standings(event)
        self.assertEqual([(row['team_id'], row['rank']) for row in rows], [(a.pk, 1), (b.pk, 2)])

    def test_new_confirmation_invalidates_stale_final_approval(self):
        from events.bracket_progression import apply_scoresheet_to_bracket
        from events.models import ScoreSheet
        event, a, b, match = self.fixture('per_win')
        match.status = 'pending'; match.save()
        sheet = ScoreSheet.objects.create(event=event, match=match, tabulator=self.admin,
                                          score_team_a=10, score_team_b=7, winner=a, status='finalized')
        apply_scoresheet_to_bracket(sheet)
        event.refresh_from_db()
        self.assertNotIn('approved', event.result_processing_config)
        with self.assertRaises(FacultyServiceError): publish_results(event, self.admin)
