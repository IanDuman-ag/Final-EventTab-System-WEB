from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from events.bracket_progression import recompute_points
from events.faculty_service import _apply_championship_points, leaderboard_rows
from events.models import BracketMatch, BracketTeam, Department, Event, SystemSettings
from events.points_settings_service import points_defaults


class AdminPointsSettingsTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user('points-admin', is_staff=True)
        self.client.force_login(self.admin)

    def test_settings_require_staff_and_csrf(self):
        url = reverse('admin_settings')
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        viewer = get_user_model().objects.create_user('points-viewer')
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(url, {'major': 10, 'minor': 5}).status_code, 403)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(url, {'major': 10, 'minor': 5}).status_code, 403)
        self.assertFalse(SystemSettings.objects.exists())

    def test_settings_save_and_invalid_input_preserves_saved_values(self):
        url = reverse('admin_settings')
        data = {'method': 'per_win', 'major_win': 10, 'minor_win': 5, 'approval_required': 'on', 'automatic_calculation': 'on', 'tie_policy': 'official'}
        self.assertRedirects(self.client.post(url, data), url)
        self.assertEqual(points_defaults(), {'major': 10, 'minor': 5})
        for value in ('-1', '1.5', '1000001', '', 'NaN'):
            self.assertEqual(self.client.post(url, {**data, 'major_win': value}).status_code, 400)
            self.assertEqual(points_defaults(), {'major': 10, 'minor': 5})

    def test_recompute_excludes_byes_pending_and_duplicate_awards(self):
        event = Event.objects.create(
            name='Configured tournament', category='Sports', event_date=date.today(),
            result_processing_config={'match_win_points': 10},
            apply_championship_points=True,
            championship_points_config=[{'label': '1st Place', 'points': 100}],
        )
        dept = Department.objects.create(name='Engineering', code='POINTS')
        a = BracketTeam.objects.create(event=event, name='Team A', department=dept)
        b = BracketTeam.objects.create(event=event, name='Team B')
        match = BracketMatch.objects.create(
            event=event, match_number=1, round_name='Final', team_a=a, team_b=b,
            winner=a, loser=b, status=BracketMatch.STATUS_COMPLETED,
        )
        BracketMatch.objects.create(event=event, match_number=0, round_name='Bye',
                                    winner=a, status='completed', is_automatic_advance=True)
        BracketMatch.objects.create(event=event, match_number=2, round_name='Pending', winner=a)
        for _ in range(2):
            recompute_points(event)
            _apply_championship_points(event)
            a.refresh_from_db()
            self.assertEqual(a.points, 10)
        self.assertEqual(leaderboard_rows(), [])
        match.winner, match.loser = b, a
        match.save()
        recompute_points(event)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((a.points, b.points), (0, 10))
        SystemSettings.objects.create(championship_points_config={'major': 99, 'minor': 1})
        recompute_points(event)
        b.refresh_from_db()
        self.assertEqual(b.points, 10)

    def test_legacy_event_keeps_existing_win_and_champion_rules(self):
        event = Event.objects.create(name='Legacy', event_date=date.today())
        team = BracketTeam.objects.create(event=event, name='Champion')
        BracketMatch.objects.create(event=event, match_number=1, round_name='Final',
                                    winner=team, status='completed')
        recompute_points(event)
        team.refresh_from_db()
        self.assertEqual(team.points, 8)

    def test_new_event_snapshots_classification_award(self):
        from events.tests import MatchEventWorkflowTests
        from events.match_event_service import save_match_event

        fixture = MatchEventWorkflowTests()
        fixture.client = Client()
        fixture.setUp()
        SystemSettings.objects.create(championship_points_config={'major': 12, 'minor': 4})
        event = save_match_event(fixture.payload(event_classification='minor'), fixture.admin)
        self.assertEqual(event.result_processing_config['championship_scoring']['win_points']['minor'], 4)
        self.assertFalse(event.apply_championship_points)
        settings = SystemSettings.load()
        settings.championship_points_config = {'major': 99, 'minor': 88}
        settings.save()
        event = save_match_event(fixture.payload(event_classification='minor'), fixture.admin, instance=event)
        self.assertEqual(event.result_processing_config['championship_scoring']['win_points']['minor'], 4)
