import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import BracketMatch, BracketTeam, Department, Event, Team


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
        department = Department.objects.create(name='Engineering', code='ENG')
        self.team_a = Team.objects.create(name='Blue Falcons', code='BF', department=department)
        self.team_b = Team.objects.create(name='Gold Hawks', code='GH', department=department)
        self.team_c = Team.objects.create(name='Red Kites', code='RK', department=department)
        self.client.force_login(self.admin)

    def payload(self, **overrides):
        data = {
            'event_name': 'Basketball Championship',
            'event_classification': 'major',
            'division': 'Mixed',
            'venue': 'University Gym',
            'start_date': '2026-08-10',
            'end_date': '2026-08-11',
            'publication_status': 'draft',
            'tournament_type': 'single_elimination',
            'team_ids': json.dumps([self.team_a.id, self.team_b.id]),
            'draw_order': json.dumps([self.team_a.id, self.team_b.id]),
            'schedule_mode': 'auto',
            'daily_start_time': '08:00',
            'daily_end_time': '17:00',
            'faculty_account': str(self.faculty.id),
            'auto_update_bracket': 'on',
            'allow_result_editing': 'on',
            'apply_championship_points': 'on',
            'points_config': json.dumps([
                {'label': '1st Place', 'points': 15},
                {'label': '2nd Place', 'points': 10},
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

    def test_double_elimination_is_explicitly_rejected(self):
        response = self.client.post(
            reverse('admin_match_events'),
            self.payload(tournament_type='double_elimination'),
            follow=True,
        )
        self.assertContains(response, 'does not support a complete loser bracket')
        self.assertFalse(Event.objects.filter(name='Basketball Championship').exists())

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

    def test_single_elimination_preview_labels_empty_opponent_as_bye(self):
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
        self.assertIn('Bye', [match['team_b'] for match in response.json()['matches']])

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

        response = self.client.post(
            reverse('admin_edit_match_event', args=[event.id]),
            self.payload(
                event_name='Legacy Double',
                tournament_type='double_elimination',
            ),
            follow=True,
        )
        self.assertContains(response, 'current engine does not support a complete loser bracket')
        event.refresh_from_db()
        self.assertEqual(event.tournament_type, 'Double Elimination')
