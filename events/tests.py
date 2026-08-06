import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from .match_event_service import (
    build_match_blueprint,
    build_match_event_name,
    count_actual_matches,
    count_automatic_advances,
    ensure_unique_match_event_name,
    MatchEventValidationError,
)
from .models import BracketMatch, BracketTeam, Department, Event, SystemSettings, Team


class MatchEventWorkflowTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='admin-test',
            password='test-password',
            email='admin@example.com',
            is_staff=True,
        )
        self.faculty = User.objects.create_user(
            username='faculty-test',
            password='test-password',
            email='faculty@example.com',
            is_staff=True,
        )
        faculty_group, _ = Group.objects.get_or_create(name='Faculty')
        self.faculty.groups.add(faculty_group)
        department = Department.objects.create(name='Engineering', code='ENG')
        self.team_a = Team.objects.create(name='Blue Falcons', code='BF', department=department)
        self.team_b = Team.objects.create(name='Gold Hawks', code='GH', department=department)
        self.team_c = Team.objects.create(name='Red Kites', code='RK', department=department)
        self.client.force_login(self.admin)

    def payload(self, **overrides):
        data = {
            'event_name': 'Basketball Championship',
            'sport_type': 'Basketball',
            'event_classification': 'major',
            'division': 'Mixed',
            'venue': 'University Gym',
            'start_date': '2026-08-10',
            'end_date': '2026-08-11',
            'publication_status': 'draft',
            'tournament_type': 'single_elimination',
            'pairing_method': 'random_draw',
            'result_entry_format': 'team_final_score',
            'team_ids': json.dumps([self.team_a.id, self.team_b.id]),
            'draw_order': json.dumps([self.team_a.id, self.team_b.id]),
            'schedule_mode': 'auto',
            'daily_start_time': '08:00',
            'daily_end_time': '17:00',
            'match_duration_minutes': '60',
            'break_between_matches_minutes': '10',
            'min_rest_minutes': '30',
            'playing_area_count': '1',
            'playing_areas': json.dumps(['Court 1']),
            'tie_break_rules': json.dumps(['head_to_head', 'score_difference']),
            'faculty_account': str(self.faculty.id),
            'auto_update_bracket': 'on',
            'allow_result_editing': 'on',
            'apply_championship_points': 'on',
            'points_config': json.dumps([
                {'label': '1st Place', 'points': 100},
                {'label': '2nd Place', 'points': 75},
            ]),
        }
        data.update(overrides)
        return data

    def test_create_draft_persists_teams_bracket_and_schedule(self):
        response = self.client.post(reverse('admin_match_events'), self.payload())
        self.assertRedirects(response, reverse('admin_match_events'))
        event = Event.objects.get(name='Basketball Championship')
        self.assertEqual(event.publication_status, Event.PUBLICATION_DRAFT)
        self.assertEqual(event.scoring_method, 'match')
        self.assertEqual(BracketTeam.objects.filter(event=event).count(), 2)
        match = BracketMatch.objects.get(event=event)
        self.assertEqual(match.match_date.isoformat(), '2026-08-10')
        self.assertEqual(match.match_time.strftime('%H:%M'), '08:00')

    def test_publish_persists_published_status(self):
        self.client.post(
            reverse('admin_match_events'),
            self.payload(publication_status='published'),
        )
        self.assertEqual(
            Event.objects.get(name='Basketball Championship').publication_status,
            Event.PUBLICATION_PUBLISHED,
        )

    def test_invalid_date_range_is_rejected(self):
        response = self.client.post(
            reverse('admin_match_events'),
            self.payload(start_date='2026-08-12', end_date='2026-08-10'),
            follow=True,
        )
        self.assertContains(response, 'End Date cannot precede Start Date.')
        self.assertFalse(Event.objects.filter(name='Basketball Championship').exists())

    def test_match_list_filters_out_criteria_events(self):
        Event.objects.create(
            name='Dance Contest',
            category='Socio-cultural',
            scoring_method='criteria',
            event_date='2026-08-10',
            venue='Auditorium',
            created_by=self.admin,
        )
        self.client.post(reverse('admin_match_events'), self.payload())
        response = self.client.get(reverse('admin_match_events'))
        self.assertContains(response, 'Basketball Championship')
        self.assertNotContains(response, 'Dance Contest')

    def test_delete_match_event_requires_post_and_cascades(self):
        self.client.post(reverse('admin_match_events'), self.payload())
        event = Event.objects.get(name='Basketball Championship')
        delete_url = reverse('admin_delete_match_event', args=[event.id])
        self.assertEqual(self.client.get(delete_url).status_code, 405)
        response = self.client.post(delete_url)
        self.assertRedirects(response, reverse('admin_match_events'))
        self.assertFalse(Event.objects.filter(pk=event.id).exists())

    def test_double_elimination_creates_winners_losers_and_grand_final(self):
        team_d = Team.objects.create(
            name='Silver Wolves',
            code='SW',
            department=self.team_a.department,
        )
        team_ids = [self.team_a.id, self.team_b.id, self.team_c.id, team_d.id]
        response = self.client.post(
            reverse('admin_match_events'),
            self.payload(
                tournament_type='double_elimination',
                team_ids=json.dumps(team_ids),
                draw_order=json.dumps(team_ids),
            ),
        )
        self.assertRedirects(response, reverse('admin_match_events'))
        event = Event.objects.get(name='Basketball Championship')
        self.assertEqual(event.tournament_type, 'double_elimination')
        matches = list(event.bracket_matches.order_by('match_number', 'id'))
        rounds = {match.round_name for match in matches}
        self.assertIn('Winners Semifinals', rounds)
        self.assertIn('Winners Final', rounds)
        self.assertIn('Losers Round 1', rounds)
        self.assertIn('Losers Final', rounds)
        self.assertIn('Grand Final', rounds)
        actual = [match for match in matches if not match.is_automatic_advance]
        self.assertEqual(len(actual), 6)  # 2N-2 for N=4
        winners_semi = [match for match in matches if match.round_name == 'Winners Semifinals']
        self.assertTrue(any(match.next_match_loser_id for match in winners_semi))
        grand_final = event.bracket_matches.get(client_key='gf')
        self.assertIsNone(grand_final.next_match_winner_id)

    def test_double_elimination_preview_is_available(self):
        response = self.client.post(
            reverse('admin_match_event_preview'),
            data=json.dumps({
                'team_ids': [self.team_a.id, self.team_b.id, self.team_c.id],
                'tournament_type': 'double_elimination',
                'include_third_place': False,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['success'])
        rounds = {match['round'] for match in payload['matches']}
        self.assertIn('Winners Final', rounds)
        self.assertIn('Losers Final', rounds)
        self.assertIn('Grand Final', rounds)
        self.assertTrue(any(match['next_loser_key'] for match in payload['matches']))

    def test_edit_does_not_delete_completed_match_result(self):
        self.client.post(reverse('admin_match_events'), self.payload())
        event = Event.objects.get(name='Basketball Championship')
        match = event.bracket_matches.get()
        match.status = BracketMatch.STATUS_COMPLETED
        match.winner = match.team_a
        match.score_a = '2'
        match.score_b = '0'
        match.save()
        response = self.client.post(
            reverse('admin_edit_match_event', args=[event.id]),
            self.payload(event_name='Basketball Championship Updated'),
        )
        self.assertRedirects(response, reverse('admin_match_events'))
        preserved = BracketMatch.objects.get(pk=match.pk)
        self.assertEqual(preserved.winner_id, match.team_a_id)
        self.assertEqual(preserved.score_a, '2')

    def test_preview_draw_is_reused_by_persistence(self):
        response = self.client.post(
            reverse('admin_match_event_preview'),
            data=json.dumps({
                'team_ids': [self.team_a.id, self.team_b.id, self.team_c.id],
                'tournament_type': 'round_robin',
                'include_third_place': False,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        preview = response.json()
        payload = self.payload(
            tournament_type='round_robin',
            team_ids=json.dumps([self.team_a.id, self.team_b.id, self.team_c.id]),
            draw_order=json.dumps(preview['draw_order']),
        )
        self.client.post(reverse('admin_match_events'), payload)
        event = Event.objects.get(name='Basketball Championship')
        self.assertEqual(event.bracket_draw_order, preview['draw_order'])
        persisted = list(event.bracket_matches.values_list('client_key', flat=True))
        self.assertEqual(persisted, [match['key'] for match in preview['matches']])

    def test_single_elimination_preview_labels_empty_opponent_as_automatic_advance(self):
        response = self.client.post(
            reverse('admin_match_event_preview'),
            data=json.dumps({
                'team_ids': [self.team_a.id, self.team_b.id, self.team_c.id],
                'tournament_type': 'Single Elimination',
                'include_third_place': False,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['automatic_advance_count'], 1)
        self.assertEqual(payload['actual_match_count'], 2)
        self.assertTrue(any(match.get('is_automatic_advance') for match in payload['matches']))
        self.assertIn('Automatic Advance', [match['team_b'] for match in payload['matches']])

    def test_bye_slots_are_not_counted_or_scheduled_as_actual_matches(self):
        team_d = Team.objects.create(name='Silver Wolves', code='SW', department=self.team_a.department)
        team_e = Team.objects.create(name='Iron Bears', code='IB', department=self.team_a.department)
        team_ids = [self.team_a.id, self.team_b.id, self.team_c.id, team_d.id, team_e.id]
        response = self.client.post(
            reverse('admin_match_events'),
            self.payload(
                sport_type='Basketball',
                tournament_type='single_elimination',
                team_ids=json.dumps(team_ids),
                draw_order=json.dumps(team_ids),
            ),
        )
        self.assertRedirects(response, reverse('admin_match_events'))
        event = Event.objects.get(name='Basketball Championship')
        actual = event.bracket_matches.filter(is_automatic_advance=False)
        advances = event.bracket_matches.filter(is_automatic_advance=True)
        # Compressed bracket seats BYEs directly; synthetic advances are preview-only.
        self.assertEqual(actual.count(), 4)  # N-1
        self.assertEqual(advances.count(), 0)
        self.assertTrue(all(match.match_date for match in actual))
        self.assertTrue(all(match.match_number > 0 for match in actual))

    def test_double_elimination_five_teams_has_eight_actual_matches(self):
        team_d = Team.objects.create(name='Silver Wolves', code='SW', department=self.team_a.department)
        team_e = Team.objects.create(name='Iron Bears', code='IB', department=self.team_a.department)
        team_ids = [self.team_a.id, self.team_b.id, self.team_c.id, team_d.id, team_e.id]
        blueprint = build_match_blueprint(
            team_ids,
            'double_elimination',
            draw_order=team_ids,
        )
        self.assertEqual(blueprint['actual_match_count'], 8)  # 2N-2
        self.assertEqual(count_actual_matches(blueprint['matches']), 8)
        self.assertEqual(count_automatic_advances(blueprint['matches']), 3)
        self.assertFalse(any(row['key'] == 'gf-reset' for row in blueprint['matches']))

    def test_round_robin_match_count_formula(self):
        team_ids = [self.team_a.id, self.team_b.id, self.team_c.id]
        blueprint = build_match_blueprint(team_ids, 'round_robin', draw_order=team_ids)
        self.assertEqual(blueprint['actual_match_count'], 3)  # N*(N-1)/2

    def test_event_name_is_built_from_sport_and_division(self):
        self.assertEqual(build_match_event_name('Basketball', '', 'Men'), "Men's Basketball")
        self.assertEqual(build_match_event_name('Chess', '', 'Open'), 'Chess – Open Division')
        self.assertEqual(build_match_event_name('Custom', 'Arnis', 'Women'), "Women's Arnis")

    def test_event_name_must_be_unique_in_same_season(self):
        settings = SystemSettings.load()
        settings.academic_year = '2025-2026'
        settings.save()
        self.client.post(
            reverse('admin_match_events'),
            self.payload(event_name="Men's Basketball", sport_type='Basketball', division='Men'),
        )
        with self.assertRaises(MatchEventValidationError):
            ensure_unique_match_event_name("Men's Basketball", academic_year='2025-2026')
        # Same name is allowed in a different season.
        ensure_unique_match_event_name("Men's Basketball", academic_year='2026-2027')

    def test_manual_schedule_maps_by_stable_match_key(self):
        schedule_rows = [
            {'match_key': 'rr-3', 'date': '2026-08-12', 'time': '10:00', 'venue': 'Court C'},
            {'match_key': 'rr-1', 'date': '2026-08-10', 'time': '08:00', 'venue': 'Court A'},
            {'match_key': 'rr-2', 'date': '2026-08-11', 'time': '09:00', 'venue': 'Court B'},
        ]
        response = self.client.post(
            reverse('admin_match_events'),
            self.payload(
                tournament_type='round_robin',
                team_ids=json.dumps([self.team_a.id, self.team_b.id, self.team_c.id]),
                draw_order=json.dumps([self.team_a.id, self.team_b.id, self.team_c.id]),
                schedule_mode='manual',
                end_date='2026-08-12',
                schedule_rows=json.dumps(schedule_rows),
            ),
        )
        self.assertRedirects(response, reverse('admin_match_events'))
        event = Event.objects.get(name='Basketball Championship')
        self.assertEqual(event.bracket_matches.get(client_key='rr-3').venue, 'Court C')
        self.assertEqual(event.bracket_matches.get(client_key='rr-1').venue, 'Court A')

    def test_criteria_event_creation_is_reachable(self):
        response = self.client.post(reverse('admin_criteria_events'), {
            'event_name': 'Academic Quiz',
            'category': 'Academic',
            'division': 'Open',
            'venue': 'Library Hall',
            'event_date': '2026-08-20',
        })
        self.assertRedirects(response, reverse('admin_criteria_events'))
        event = Event.objects.get(name='Academic Quiz')
        self.assertEqual(event.scoring_method, 'criteria')
        self.assertEqual(event.publication_status, Event.PUBLICATION_DRAFT)

    def test_legacy_double_elimination_is_flagged_for_safe_editing(self):
        event = Event.objects.create(
            name='Legacy Double',
            category='Sports',
            scoring_method='match',
            tournament_type='Double Elimination',
            event_date='2026-08-10',
            venue='Old Gym',
            created_by=self.admin,
        )
        snapshot = BracketTeam.objects.create(
            event=event,
            source_team=self.team_a,
            name=self.team_a.name,
            department=self.team_a.department,
            seed=1,
        )
        BracketMatch.objects.create(
            event=event,
            match_number=1,
            round_name='Winner Bracket Finals',
            team_a=snapshot,
        )
        response = self.client.get(reverse('admin_match_events'))
        row = next(row for row in response.context['event_rows_json'] if row['id'] == event.id)
        self.assertTrue(row['legacy_double_elimination'])
        self.assertEqual(row['tournament_type'], 'double_elimination')

        team_ids = [self.team_a.id, self.team_b.id]
        response = self.client.post(
            reverse('admin_edit_match_event', args=[event.id]),
            self.payload(
                event_name='Legacy Double',
                tournament_type='double_elimination',
                team_ids=json.dumps(team_ids),
                draw_order=json.dumps(team_ids),
            ),
        )
        self.assertRedirects(response, reverse('admin_match_events'))
        event.refresh_from_db()
        self.assertEqual(event.tournament_type, 'double_elimination')
        rounds = set(event.bracket_matches.values_list('round_name', flat=True))
        self.assertIn('Grand Final', rounds)
        self.assertTrue(
            event.bracket_matches.filter(next_match_loser__isnull=False).exists()
        )
