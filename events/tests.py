import json
from datetime import date, time

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
from .models import BracketMatch, BracketTeam, Department, Event, EventCategory, SystemSettings, Team


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
        self.tabulator = User.objects.create_user(
            username='tabulator-test',
            password='test-password',
            email='tabulator@example.com',
        )
        tabulator_group, _ = Group.objects.get_or_create(name='Tabulator')
        self.tabulator.groups.add(tabulator_group)
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
            'tabulator_account': str(self.tabulator.id),
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
        self.assertEqual(list(event.assigned_tabulators.values_list('id', flat=True)), [self.tabulator.id])

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

    def test_preview_never_renders_numeric_team_ids_as_labels(self):
        from events.match_event_service import serialize_blueprint_matches

        team_ids = [self.team_a.id, self.team_b.id, self.team_c.id]
        blueprint = build_match_blueprint(team_ids, 'single_elimination', draw_order=team_ids)
        names = {
            self.team_a.id: self.team_a.name,
            self.team_b.id: self.team_b.name,
            self.team_c.id: self.team_c.name,
        }
        serialized = serialize_blueprint_matches(blueprint['matches'], names)
        for match in serialized:
            for field in ('team_a', 'team_b', 'display_label', 'label_a', 'label_b', 'dependency_label'):
                value = str(match.get(field) or '')
                self.assertFalse(value.isdigit(), msg=f'{field} leaked raw id: {value}')
                for part in value.replace('—', 'vs').split('vs'):
                    self.assertFalse(part.strip().isdigit(), msg=f'{field} leaked raw id part: {value}')
            if match.get('is_automatic_advance'):
                self.assertIn(match['team_a'], names.values())
                self.assertIn('Automatic Advance', match['display_label'])
            if match.get('participant_a'):
                self.assertEqual(match['participant_a']['team_name'], names[match['team_a_id']])

    def test_preview_api_returns_team_names_not_ids(self):
        response = self.client.post(
            reverse('admin_match_event_preview'),
            data=json.dumps({
                'team_ids': [self.team_a.id, self.team_b.id, self.team_c.id],
                'tournament_type': 'single_elimination',
                'include_third_place': False,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['success'])
        for match in payload['matches']:
            joined = f"{match.get('team_a')} {match.get('team_b')} {match.get('display_label')}"
            self.assertNotRegex(joined, rf'(^|[^\w]){self.team_a.id}([^\w]|$)')
            self.assertNotRegex(joined, rf'(^|[^\w]){self.team_b.id}([^\w]|$)')
            self.assertNotRegex(joined, rf'(^|[^\w]){self.team_c.id}([^\w]|$)')
            if match.get('team_a_id'):
                self.assertIn(match['participant_a']['team_name'], (
                    self.team_a.name, self.team_b.name, self.team_c.name, 'Team information unavailable'
                ))

    def test_missing_team_relationship_uses_safe_fallback(self):
        from events.match_event_service import MISSING_TEAM_LABEL, serialize_blueprint_matches

        blueprint = build_match_blueprint(
            [self.team_a.id, self.team_b.id],
            'single_elimination',
            draw_order=[self.team_a.id, self.team_b.id],
        )
        # Pretend one seated team id is missing from the lookup.
        serialized = serialize_blueprint_matches(blueprint['matches'], {self.team_a.id: self.team_a.name})
        seated = [match for match in serialized if match.get('team_b_id') == self.team_b.id]
        self.assertTrue(seated)
        self.assertEqual(seated[0]['team_b'], MISSING_TEAM_LABEL)
        self.assertNotEqual(seated[0]['team_b'], str(self.team_b.id))

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


class BracketDisplayGraphTests(TestCase):
    """Layout/connector invariants for professional bracket preview rendering."""

    def setUp(self):
        department = Department.objects.create(name='Arts', code='ART')
        self.teams = [
            Team.objects.create(name=f'Team {index}', code=f'T{index}', department=department)
            for index in range(1, 17)
        ]

    def _ids(self, count):
        return [team.id for team in self.teams[:count]]

    def _names(self, team_ids):
        return {team.id: team.name for team in self.teams if team.id in team_ids}

    def _assert_layout(self, count, expected_actual, expected_advances, expected_opening_slots):
        from events.match_event_service import bracket_display_graph, serialize_blueprint_matches

        team_ids = self._ids(count)
        blueprint = build_match_blueprint(
            team_ids,
            'single_elimination',
            draw_order=team_ids,
        )
        self.assertEqual(blueprint['actual_match_count'], expected_actual)
        self.assertEqual(blueprint['automatic_advance_count'], expected_advances)
        self.assertEqual(count_actual_matches(blueprint['matches']), expected_actual)
        self.assertEqual(count_automatic_advances(blueprint['matches']), expected_advances)

        serialized = serialize_blueprint_matches(blueprint['matches'], self._names(team_ids))
        layout = bracket_display_graph(serialized, team_ids)

        self.assertFalse(layout.get('is_round_robin'))
        self.assertEqual(layout['opening_slot_count'], expected_opening_slots)
        self.assertTrue(layout['columns'])
        self.assertEqual(layout['columns'][-1]['name'], 'Champion')

        advance_nodes = [
            node
            for column in layout['columns']
            for node in column['nodes']
            if node.get('kind') == 'advance'
        ]
        self.assertEqual(len(advance_nodes), expected_advances)

        advance_edges = [edge for edge in layout['edges'] if edge['kind'] == 'automatic_advance']
        self.assertEqual(len(advance_edges), expected_advances)
        for edge in advance_edges:
            self.assertTrue(any(
                node['key'] == edge['from_key'] and node.get('kind') == 'advance'
                for column in layout['columns'] for node in column['nodes']
            ))
            self.assertTrue(any(
                node['key'] == edge['to_key'] and node.get('kind') == 'match'
                for column in layout['columns'] for node in column['nodes']
            ))

        for match in serialized:
            if match.get('is_automatic_advance'):
                self.assertEqual(match.get('number'), 0)
                self.assertTrue(match.get('next_winner_key'))
                self.assertNotIn(' vs ', match.get('display_label', ''))
                self.assertIn('Automatic Advance', match.get('display_label', ''))
                self.assertFalse(str(match.get('team_a', '')).isdigit())
            else:
                self.assertGreater(match.get('number') or 0, 0)
                label = match.get('display_label') or ''
                self.assertNotRegex(label, r'(^|\s)\d+(\s+vs\s+|\s*$)')
                if match.get('next_winner_key') is None and 'Final' in (match.get('round') or ''):
                    continue

        # Every automatic advance and non-final actual match connects somewhere.
        edge_from = {edge['from_key'] for edge in layout['edges']}
        for match in serialized:
            if match.get('is_automatic_advance'):
                self.assertIn(match['key'], edge_from)
            elif match.get('next_winner_key'):
                self.assertIn(match['key'], edge_from)

        self.assertTrue(any(edge['kind'] == 'champion' for edge in layout['edges']))
        return blueprint, serialized, layout

    def test_bracket_layout_three_participants(self):
        self._assert_layout(3, expected_actual=2, expected_advances=1, expected_opening_slots=2)

    def test_bracket_layout_five_participants(self):
        blueprint, serialized, layout = self._assert_layout(
            5, expected_actual=4, expected_advances=3, expected_opening_slots=4
        )
        rounds = {match['round'] for match in serialized if not match.get('is_automatic_advance')}
        self.assertIn('Quarterfinals', rounds)
        self.assertIn('Semifinals', rounds)
        self.assertIn('Finals', rounds)
        qf = [m for m in serialized if m.get('round') == 'Quarterfinals' and not m.get('is_automatic_advance')]
        sf = [m for m in serialized if m.get('round') == 'Semifinals' and not m.get('is_automatic_advance')]
        finals = [m for m in serialized if m.get('round') == 'Finals' and not m.get('is_automatic_advance')]
        self.assertEqual(len(qf), 1)
        self.assertEqual(len(sf), 2)
        self.assertEqual(len(finals), 1)
        opening = layout['columns'][0]
        self.assertEqual(len(opening['nodes']), 4)
        self.assertEqual(sum(1 for node in opening['nodes'] if node['kind'] == 'advance'), 3)
        self.assertEqual(sum(1 for node in opening['nodes'] if node['kind'] == 'match'), 1)

    def test_bracket_layout_six_participants(self):
        self._assert_layout(6, expected_actual=5, expected_advances=2, expected_opening_slots=4)

    def test_bracket_layout_seven_participants(self):
        self._assert_layout(7, expected_actual=6, expected_advances=1, expected_opening_slots=4)

    def test_bracket_layout_eight_participants(self):
        self._assert_layout(8, expected_actual=7, expected_advances=0, expected_opening_slots=4)

    def test_bracket_layout_sixteen_participants(self):
        self._assert_layout(16, expected_actual=15, expected_advances=0, expected_opening_slots=8)

    def test_round_robin_uses_standings_graph_not_elimination_tree(self):
        from events.match_event_service import bracket_display_graph, serialize_blueprint_matches

        team_ids = self._ids(4)
        blueprint = build_match_blueprint(team_ids, 'round_robin', draw_order=team_ids)
        serialized = serialize_blueprint_matches(blueprint['matches'], self._names(team_ids))
        layout = bracket_display_graph(serialized, team_ids)
        self.assertTrue(layout['is_round_robin'])
        self.assertEqual(layout['edges'], [])
        self.assertEqual(layout['columns'][0]['name'], 'Pool Play')
        self.assertEqual(len(layout['columns'][0]['nodes']), 6)

    def test_preview_api_includes_layout_and_team_names(self):
        User = get_user_model()
        admin = User.objects.create_user(
            username='layout-admin',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(admin)
        team_ids = self._ids(5)
        response = self.client.post(
            reverse('admin_match_event_preview'),
            data=json.dumps({
                'team_ids': team_ids,
                'tournament_type': 'single_elimination',
                'include_third_place': False,
                'draw_order': team_ids,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['success'])
        self.assertIn('layout', payload)
        self.assertEqual(payload['actual_match_count'], 4)
        self.assertEqual(payload['automatic_advance_count'], 3)
        self.assertEqual(payload['layout']['opening_slot_count'], 4)
        for match in payload['matches']:
            for field in ('display_label', 'team_a', 'team_b', 'label_a'):
                value = str(match.get(field) or '')
                self.assertFalse(value.isdigit(), msg=f'{field} leaked raw id: {value}')

    def test_winner_progression_links_persist_after_save(self):
        User = get_user_model()
        admin = User.objects.create_user(username='persist-admin', password='x', is_staff=True)
        faculty = User.objects.create_user(username='persist-faculty', password='x', is_staff=True)
        faculty_group, _ = Group.objects.get_or_create(name='Faculty')
        faculty.groups.add(faculty_group)
        self.client.force_login(admin)
        team_ids = self._ids(5)
        response = self.client.post(
            reverse('admin_match_events'),
            {
                'event_name': 'Layout Persist Cup',
                'sport_type': 'Basketball',
                'event_classification': 'major',
                'division': 'Open',
                'venue': 'Gym',
                'start_date': '2026-08-10',
                'end_date': '2026-08-11',
                'publication_status': 'draft',
                'tournament_type': 'single_elimination',
                'pairing_method': 'random_draw',
                'result_entry_format': 'team_final_score',
                'team_ids': json.dumps(team_ids),
                'draw_order': json.dumps(team_ids),
                'schedule_mode': 'auto',
                'daily_start_time': '08:00',
                'daily_end_time': '17:00',
                'match_duration_minutes': '60',
                'break_between_matches_minutes': '10',
                'min_rest_minutes': '30',
                'playing_area_count': '1',
                'playing_areas': json.dumps(['Court 1']),
                'tie_break_rules': json.dumps(['head_to_head']),
                'faculty_account': str(faculty.id),
                'points_config': json.dumps([{'label': '1st Place', 'points': 100}]),
            },
        )
        self.assertRedirects(response, reverse('admin_match_events'))
        event = Event.objects.get(name='Layout Persist Cup')
        actual = list(event.bracket_matches.filter(is_automatic_advance=False).order_by('match_number'))
        self.assertEqual(len(actual), 4)
        linked = [match for match in actual if match.next_match_winner_id]
        self.assertEqual(len(linked), 3)
        page = self.client.get(reverse('admin_match_events'))
        row = next(item for item in page.context['event_rows_json'] if item['id'] == event.id)
        for schedule_row in row['schedule_rows']:
            if schedule_row['match_number'] and schedule_row['match_number'] < 4:
                self.assertTrue(schedule_row.get('next_winner_key'))
            self.assertFalse(str(schedule_row.get('team_a') or '').isdigit())
            self.assertFalse(str(schedule_row.get('team_b') or '').isdigit())


class TournamentBracketsListingTests(TestCase):
    """Tournament Brackets page: match-only eligibility, statuses, progress."""

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='brackets-admin',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(self.admin)
        self.department = Department.objects.create(name='Sports Dept', code='SPD')
        self.teams = [
            Team.objects.create(name=f'Club {i}', code=f'C{i}', department=self.department)
            for i in range(1, 6)
        ]

    def _create_match_event(self, name, **kwargs):
        defaults = {
            'name': name,
            'category': 'Sports',
            'scoring_method': 'match',
            'tournament_type': 'single_elimination',
            'sport_type': 'Basketball',
            'division': 'Men',
            'event_date': '2026-08-20',
            'venue': 'Main Gym',
            'created_by': self.admin,
            'publication_status': Event.PUBLICATION_PUBLISHED,
        }
        defaults.update(kwargs)
        return Event.objects.create(**defaults)

    def test_criteria_and_pop_solo_never_appear(self):
        match_event = self._create_match_event('Men Basketball Bracket')
        criteria = Event.objects.create(
            name='Pop Solo',
            category='Cultural',
            scoring_method='criteria',
            event_date='2026-08-21',
            venue='Auditorium',
            created_by=self.admin,
        )
        dance = Event.objects.create(
            name='Dance Sports',
            category='Cultural',
            scoring_method='criteria',
            event_date='2026-08-22',
            venue='Hall',
            created_by=self.admin,
        )
        from core.views import build_tournament_bracket_listing
        listing = build_tournament_bracket_listing()
        names = [row['name'] for row in listing['event_rows']]
        self.assertIn(match_event.name, names)
        self.assertNotIn(criteria.name, names)
        self.assertNotIn(dance.name, names)
        self.assertNotIn('Pop Solo', names)
        response = self.client.get(reverse('admin_brackets'))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('Tournament Brackets', body)
        self.assertNotIn('Create Bracket', body)
        self.assertNotIn('Pop Solo', body)
        self.assertNotIn('Dance Sports', body)
        self.assertIn('Go to Match-Based Events', body)

    def test_summary_cards_exclude_criteria(self):
        self._create_match_event('Generated Later')
        Event.objects.create(
            name='Pageant Night',
            category='Cultural',
            scoring_method='criteria',
            event_date='2026-09-01',
            venue='Hall',
            created_by=self.admin,
        )
        from core.views import build_tournament_bracket_listing
        listing = build_tournament_bracket_listing()
        self.assertEqual(listing['summary']['match_based_events'], 1)
        self.assertEqual(listing['summary']['not_generated'], 1)
        self.assertEqual(listing['summary']['generated_brackets'], 0)

    def test_formats_and_participant_labels(self):
        se = self._create_match_event('SE Cup', tournament_type='single_elimination')
        de = self._create_match_event('DE Cup', tournament_type='double_elimination', sport_type='Volleyball')
        rr = self._create_match_event('RR Cup', tournament_type='round_robin', sport_type='Chess')
        for event in (se, de, rr):
            for index, team in enumerate(self.teams, start=1):
                BracketTeam.objects.create(
                    event=event,
                    source_team=team,
                    name=team.name,
                    department=team.department,
                    seed=index,
                )
        from core.views import build_tournament_bracket_listing
        listing = build_tournament_bracket_listing()
        by_name = {row['name']: row for row in listing['event_rows']}
        self.assertEqual(by_name['SE Cup']['tournament_type_label'], 'Single Elimination')
        self.assertEqual(by_name['DE Cup']['tournament_type_label'], 'Double Elimination')
        self.assertEqual(by_name['RR Cup']['tournament_type_label'], 'Round Robin')
        self.assertTrue(by_name['RR Cup']['is_round_robin'])
        self.assertEqual(by_name['SE Cup']['participant_count'], 5)
        self.assertEqual(by_name['SE Cup']['participant_label'], '5 Teams')
        self.assertEqual(by_name['SE Cup']['sport'], 'Basketball')
        self.assertNotEqual(by_name['SE Cup']['sport'], 'Sports')

    def test_progress_excludes_automatic_advances(self):
        event = self._create_match_event('Progress Cup')
        snapshots = []
        for index, team in enumerate(self.teams[:3], start=1):
            snapshots.append(BracketTeam.objects.create(
                event=event,
                source_team=team,
                name=team.name,
                department=team.department,
                seed=index,
            ))
        BracketMatch.objects.create(
            event=event,
            match_number=0,
            round_name='Quarterfinals',
            team_a=snapshots[0],
            status=BracketMatch.STATUS_COMPLETED,
            winner=snapshots[0],
            is_automatic_advance=True,
        )
        BracketMatch.objects.create(
            event=event,
            match_number=1,
            round_name='Semifinals',
            team_a=snapshots[1],
            team_b=snapshots[2],
            status=BracketMatch.STATUS_PENDING,
            is_automatic_advance=False,
        )
        from core.views import build_tournament_bracket_listing
        row = next(r for r in build_tournament_bracket_listing()['event_rows'] if r['name'] == 'Progress Cup')
        self.assertEqual(row['match_total'], 1)
        self.assertEqual(row['match_completed'], 0)
        self.assertEqual(row['match_progress_pct'], 0)
        self.assertEqual(row['bracket_status'], 'generated')
        self.assertIn('actual matches', row['match_progress_label'])

    def test_five_participants_single_elimination_actual_matches(self):
        team_ids = [team.id for team in self.teams]
        blueprint = build_match_blueprint(team_ids, 'single_elimination', draw_order=team_ids)
        self.assertEqual(blueprint['actual_match_count'], 4)
        self.assertEqual(blueprint['automatic_advance_count'], 3)

    def test_bracket_and_tournament_status_are_separate(self):
        event = self._create_match_event('Status Cup', status=Event.STATUS_INACTIVE)
        from core.views import build_tournament_bracket_listing
        row = next(r for r in build_tournament_bracket_listing()['event_rows'] if r['id'] == event.id)
        self.assertEqual(row['bracket_status'], 'not_generated')
        self.assertEqual(row['tournament_status'], 'cancelled')
        self.assertIn('bracket_status', row)
        self.assertIn('tournament_status', row)
        self.assertNotEqual(row['bracket_status'], row['tournament_status'])

    def test_no_raw_ids_in_listing_labels(self):
        event = self._create_match_event('Label Cup')
        BracketTeam.objects.create(
            event=event,
            source_team=self.teams[0],
            name=self.teams[0].name,
            department=self.teams[0].department,
            seed=1,
        )
        from core.views import build_tournament_bracket_listing
        row = next(r for r in build_tournament_bracket_listing()['event_rows'] if r['id'] == event.id)
        for key in ('name', 'sport', 'division', 'tournament_type_label', 'participant_label', 'bracket_status_label'):
            self.assertFalse(str(row[key]).isdigit())

    def test_regenerate_blocked_when_confirmed_results_exist(self):
        event = self._create_match_event('Locked Results Cup')
        team = BracketTeam.objects.create(
            event=event,
            source_team=self.teams[0],
            name=self.teams[0].name,
            department=self.teams[0].department,
            seed=1,
        )
        team_b = BracketTeam.objects.create(
            event=event,
            source_team=self.teams[1],
            name=self.teams[1].name,
            department=self.teams[1].department,
            seed=2,
        )
        BracketMatch.objects.create(
            event=event,
            match_number=1,
            round_name='Finals',
            team_a=team,
            team_b=team_b,
            status=BracketMatch.STATUS_COMPLETED,
            winner=team,
            score_a='10',
            score_b='8',
            is_automatic_advance=False,
        )
        response = self.client.post(
            reverse('admin_generate_bracket'),
            data=json.dumps({
                'event': {'event_id': event.id, 'tournament_format': 'Single Elimination'},
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 409)
        payload = response.json()
        self.assertTrue(payload.get('blocked'))
        self.assertIn('cannot be regenerated normally', payload.get('message', ''))



class PageantCriteriaEventTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='pageant-admin', password='test-password', is_staff=True,
        )
        self.faculty = User.objects.create_user(
            username='pageant-faculty', password='test-password', is_staff=True,
        )
        self.judge = User.objects.create_user(
            username='pageant-judge', password='test-password', is_staff=True,
        )
        faculty_group, _ = Group.objects.get_or_create(name='Faculty')
        judge_group, _ = Group.objects.get_or_create(name='Judge')
        self.faculty.groups.add(faculty_group)
        self.judge.groups.add(judge_group)
        from .models import RegistryCandidate
        self.cand_a = RegistryCandidate.objects.create(number='1', name='Alex Rivera')
        self.cand_b = RegistryCandidate.objects.create(number='2', name='Blake Torres')

    def _pageant_payload(self, publication='draft', **overrides):
        from events.criteria_event_service import DEFAULT_PAGEANT_SEGMENTS
        segments = []
        for idx, seg in enumerate(DEFAULT_PAGEANT_SEGMENTS):
            row = dict(seg)
            row['id'] = f's{idx+1}'
            row['criteria'] = [
                {**c, 'id': f's{idx+1}c{ci+1}'} for ci, c in enumerate(seg.get('criteria') or [])
            ]
            segments.append(row)
        payload = {
            'event_name': 'Intramural Pageant 2026',
            'category': 'Special Event',
            'special_event_type': 'pageant',
            'event_classification': 'major',
            'participation_type': 'individual',
            'venue': 'Main Auditorium',
            'start_date': '2026-09-01',
            'end_date': '2026-09-02',
            'event_format': 'multiple_stage',
            'criteria_score_method': 'weighted_percentage',
            'publication_status': publication,
            'faculty_account': str(self.faculty.id),
            'chief_judge': str(self.judge.id),
            'judge_ids': json.dumps([self.judge.id]),
            'participant_ids': json.dumps([self.cand_a.id, self.cand_b.id]),
            'rounds_config': json.dumps(segments),
            'judging_criteria_config': json.dumps([]),
            'score_settings': json.dumps({'min_score': 0, 'max_score': 100, 'allow_decimal': True, 'decimal_places': 2}),
            'deductions_config': json.dumps([]),
            'result_processing_config': json.dumps({}),
            'judge_settings': json.dumps({'require_all_judges': True, 'allow_edit_before_submit': True, 'remove_high_low': False}),
            'tie_break_rules': json.dumps([{'method': 'manual_decision'}]),
            'points_config': json.dumps([]),
            'pageant_config': json.dumps({
                'pageant_format': 'male_female',
                'competition_categories': ['Male Category', 'Female Category'],
                'description': 'A guided pageant',
                'rules': '',
                'segment_template': 'standard',
                'advancement_enabled': False,
                'special_awards': [],
                'pair_entries': [],
                'pending_candidates': [],
                'start_time': '18:00',
                'end_time': '21:00',
            }),
            'apply_championship_points': '1',
        }
        payload.update(overrides)
        return payload

    def test_is_pageant_gate(self):
        from events.criteria_event_service import is_pageant_event, recommended_categories_for_format
        self.assertTrue(is_pageant_event({'category': 'Special Event', 'special_event_type': 'pageant'}))
        self.assertFalse(is_pageant_event({'category': 'Special Event', 'special_event_type': ''}))
        self.assertEqual(recommended_categories_for_format('male_female'), ['Male Category', 'Female Category'])
        self.assertEqual(recommended_categories_for_format('individual'), ['Open Category'])
        self.assertEqual(recommended_categories_for_format('pairs'), ['Pair Category'])

    def test_pageant_draft_allows_incomplete(self):
        from events.criteria_event_service import save_criteria_event
        event = save_criteria_event(self._pageant_payload(
            publication='draft',
            venue='',
            faculty_account='',
            chief_judge='',
            judge_ids='[]',
            participant_ids='[]',
        ), self.admin)
        self.assertEqual(event.special_event_type, 'pageant')
        self.assertEqual(event.publication_status, Event.PUBLICATION_DRAFT)
        self.assertTrue(event.pageant_config.get('pageant_format'))

    def test_pageant_publish_requires_complete_setup(self):
        from events.criteria_event_service import CriteriaEventValidationError, save_criteria_event
        with self.assertRaises(CriteriaEventValidationError):
            save_criteria_event(self._pageant_payload(
                publication='published',
                venue='',
            ), self.admin)

    def test_pageant_publish_success(self):
        from events.criteria_event_service import save_criteria_event, serialize_criteria_event
        event = save_criteria_event(self._pageant_payload(publication='published'), self.admin)
        self.assertEqual(event.publication_status, Event.PUBLICATION_PUBLISHED)
        self.assertEqual(event.participation_type, Event.PARTICIPATION_INDIVIDUAL)
        self.assertEqual(event.event_format, Event.FORMAT_MULTIPLE_STAGE)
        self.assertEqual(event.pageant_config.get('competition_categories'), ['Male Category', 'Female Category'])
        data = serialize_criteria_event(event)
        self.assertTrue(data['is_pageant'])
        self.assertEqual(data['pageant_format'], 'male_female')
        self.assertTrue(data['rounds_config'])

    def test_segment_weights_must_total_100_on_publish(self):
        from events.criteria_event_service import CriteriaEventValidationError, save_criteria_event
        bad_segments = [{
            'id': 's1', 'name': 'Talent', 'weight': 40, 'enabled': True,
            'counts_toward_main_ranking': True,
            'criteria': [{'id': 'c1', 'name': 'Skill', 'weight': 100, 'max_score': 100}],
        }]
        with self.assertRaises(CriteriaEventValidationError):
            save_criteria_event(self._pageant_payload(
                publication='published',
                rounds_config=json.dumps(bad_segments),
            ), self.admin)


class CriteriaRoundAdvancementValidationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='criteria-admin', password='test-password', is_staff=True,
        )
        self.tabulator = User.objects.create_user(
            username='criteria-tabulator', password='test-password',
        )
        self.judge = User.objects.create_user(
            username='criteria-judge', password='test-password',
        )
        tabulator_group, _ = Group.objects.get_or_create(name='Tabulator')
        judge_group, _ = Group.objects.get_or_create(name='Judge')
        self.tabulator.groups.add(tabulator_group)
        self.judge.groups.add(judge_group)
        from .models import RegistryCandidate
        self.candidates = [
            RegistryCandidate.objects.create(number=f'CRV-{idx}', name=f'Candidate {idx}')
            for idx in range(1, 11)
        ]

    def _rounds(self, qualifiers, first_method='top_ranking'):
        return [
            {
                'id': 'r1',
                'name': 'Preliminary Round',
                'round_type': 'elimination',
                'scoring_method': 'numerical',
                'score_handling': 'start_zero',
                'weight': 40,
                'qualification_method': first_method,
                'advancement_rule': first_method,
                'qualifiers': qualifiers,
                'minimum_score': None,
                'carry_previous_scores': False,
                'require_faculty_confirmation': True,
                'is_final': False,
            },
            {
                'id': 'r2',
                'name': 'Final Round',
                'round_type': 'final',
                'scoring_method': 'numerical',
                'score_handling': 'carry',
                'weight': 60,
                'qualification_method': None,
                'advancement_rule': None,
                'qualifiers': None,
                'minimum_score': None,
                'carry_previous_scores': True,
                'require_faculty_confirmation': False,
                'is_final': True,
            },
        ]

    def _payload(self, candidate_count, qualifiers=10, **overrides):
        payload = {
            'event_name': 'Criteria Advancement Event',
            'category': 'SOCIO-CULTURAL & LITERARY ARTS COMPETITION',
            'event_classification': 'major',
            'division': 'Open',
            'participation_type': 'individual',
            'venue': 'Main Hall',
            'start_date': '2026-09-01',
            'end_date': '2026-09-01',
            'event_format': 'multiple_stage',
            'criteria_score_method': 'weighted_percentage',
            'publication_status': 'draft',
            'participant_ids': json.dumps([c.id for c in self.candidates[:candidate_count]]),
            'rounds_config': json.dumps(self._rounds(qualifiers)),
            'judging_criteria_config': json.dumps([
                {'id': 'c1', 'name': 'Overall Performance', 'description': '', 'weight': 100, 'max_score': 100},
            ]),
            'score_settings': json.dumps({'min_score': 0, 'max_score': 100, 'allow_decimal': True, 'decimal_places': 2}),
            'deductions_config': json.dumps([]),
            'result_processing_config': json.dumps({}),
            'judge_settings': json.dumps({'require_all_judges': True, 'allow_edit_before_submit': True, 'remove_high_low': False}),
            'tie_break_rules': json.dumps([{'method': 'manual_decision'}]),
            'judge_ids': json.dumps([self.judge.id]),
            'faculty_account': str(self.tabulator.id),
            'chief_judge': str(self.judge.id),
            'points_config': json.dumps([]),
            'apply_championship_points': '1',
        }
        payload.update(overrides)
        return payload

    def test_four_candidates_top_ten_adjusts_to_top_four(self):
        from events.criteria_event_service import save_criteria_event
        event = save_criteria_event(self._payload(4, qualifiers=10), self.admin)
        self.assertEqual(event.rounds_config[0]['qualifiers'], 4)
        self.assertEqual(event.rounds_config[0]['qualification_method'], 'top_ranking')

    def test_four_candidates_top_three_stays_top_three(self):
        from events.criteria_event_service import save_criteria_event
        event = save_criteria_event(self._payload(4, qualifiers=3), self.admin)
        self.assertEqual(event.rounds_config[0]['qualifiers'], 3)

    def test_ten_candidates_top_ten_is_valid(self):
        from events.criteria_event_service import save_criteria_event
        event = save_criteria_event(self._payload(10, qualifiers=10), self.admin)
        self.assertEqual(event.rounds_config[0]['qualifiers'], 10)

    def test_zero_candidates_is_blocked(self):
        from events.criteria_event_service import CriteriaEventValidationError, save_criteria_event
        with self.assertRaisesMessage(CriteriaEventValidationError, 'Select at least one candidate.'):
            save_criteria_event(self._payload(0, qualifiers=10, publication_status='published'), self.admin)

    def test_zero_candidates_can_be_saved_as_draft(self):
        from events.criteria_event_service import save_criteria_event
        event = save_criteria_event(self._payload(0, qualifiers=10), self.admin)
        self.assertEqual(event.participant_ids, [])
        self.assertEqual(event.publication_status, Event.PUBLICATION_DRAFT)

    def test_final_round_without_advancement_is_valid(self):
        from events.criteria_event_service import save_criteria_event
        event = save_criteria_event(self._payload(4, qualifiers=4), self.admin)
        final_round = event.rounds_config[1]
        self.assertTrue(final_round['is_final'])
        self.assertIsNone(final_round['qualifiers'])
        self.assertIsNone(final_round['qualification_method'])

    def test_candidate_count_reduction_revalidates_saved_rounds(self):
        from events.criteria_event_service import save_criteria_event
        event = save_criteria_event(self._payload(10, qualifiers=10), self.admin)
        updated = save_criteria_event(self._payload(4, qualifiers=10), self.admin, instance=event)
        self.assertEqual(updated.rounds_config[0]['qualifiers'], 4)
        self.assertEqual(updated.participant_ids, [c.id for c in self.candidates[:4]])

    def test_stage_confirmation_rejects_duplicate_qualifiers(self):
        from events.criteria_event_service import save_criteria_event
        from events.faculty_service import FacultyServiceError, confirm_stage_advancement
        event = save_criteria_event(self._payload(4, qualifiers=4), self.admin)
        first_id = self.candidates[0].id
        with self.assertRaisesMessage(FacultyServiceError, 'Duplicate qualifier selections are not allowed.'):
            confirm_stage_advancement(
                event,
                self.tabulator,
                0,
                [first_id, first_id, self.candidates[1].id, self.candidates[2].id],
            )

    def test_stage_confirmation_advances_adjusted_four_candidates(self):
        from events.criteria_event_service import save_criteria_event
        from events.faculty_service import confirm_stage_advancement
        event = save_criteria_event(self._payload(4, qualifiers=10), self.admin)
        ids = [c.id for c in self.candidates[:4]]
        result = confirm_stage_advancement(event, self.tabulator, 0, ids)
        event.refresh_from_db()
        self.assertEqual(result['qualifier_ids'], ids)
        self.assertEqual(event.result_processing_config['qualified_participant_ids']['1'], ids)


class EventScoringCategoryTests(TestCase):
    def setUp(self):
        self.event = Event.objects.create(
            name='Category workflow event',
            category='Special Event',
            event_date=date.today(),
            venue='Hall',
            scoring_method='criteria',
        )

    def test_category_and_criteria_are_scoped_to_event(self):
        from .models import EventScoringCategory, EventScoringCriterion
        category = EventScoringCategory.objects.create(
            event=self.event,
            name='Interview',
            judge_mode=EventScoringCategory.JUDGE_MODE_SCORING,
            display_order=1,
            overall_weight_percent=20,
        )
        criterion = EventScoringCriterion.objects.create(
            category=category,
            name='Confidence',
            weight_percent=20,
            max_score=100,
            display_order=1,
        )
        self.assertEqual(category.event_id, self.event.id)
        self.assertEqual(criterion.category_id, category.id)
        self.assertEqual(list(self.event.scoring_categories.values_list('name', flat=True)), ['Interview'])


class TabulatorResultsPortalTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.tabulator = User.objects.create_user(
            username='tab-results',
            password='test-password',
            email='tab-results@example.com',
        )
        tab_group, _ = Group.objects.get_or_create(name='Tabulator')
        self.tabulator.groups.add(tab_group)
        other = User.objects.create_user(
            username='tab-other',
            password='test-password',
            email='tab-other@example.com',
        )
        other.groups.add(tab_group)

        from .models import Candidate, Criterion, JudgingEvent

        category = EventCategory.objects.create(name='Special', category_type='socio_cultural')
        self.judging = JudgingEvent.objects.create(
            title='Pageant Night',
            category=category,
            date=date.today(),
            time=time(18, 0),
            venue='Auditorium',
            status='active',
        )
        self.event = Event.objects.create(
            name='Campus Pageant Finals',
            category='Special Event',
            special_event_type='pageant',
            event_date=date.today(),
            venue='Auditorium',
            scoring_method='criteria',
            participation_type=Event.PARTICIPATION_INDIVIDUAL,
            criteria_score_method=Event.CRITERIA_SCORE_WEIGHTED,
            judging_event=self.judging,
            results_finalized=True,
        )
        self.event.assigned_tabulators.add(self.tabulator)

        Criterion.objects.create(
            event=self.judging,
            name='Poise',
            max_score=100,
            weight_percent=100,
            order=1,
        )
        self.cand_a = Candidate.objects.create(event=self.judging, name='Alice', number=1, department='Eng')
        self.cand_b = Candidate.objects.create(event=self.judging, name='Bianca', number=2, department='Arts')

        self.foreign = Event.objects.create(
            name='Other Event',
            category='Sports',
            event_date=date.today(),
            venue='Gym',
            scoring_method='match',
        )
        self.foreign.assigned_tabulators.add(other)

        self._ranking_rows = [
            {
                'candidate_id': self.cand_a.id,
                'name': 'Alice',
                'number': 1,
                'final_score': 90.0,
                'breakdown': '1 judge(s)',
                'comments': [],
                'penalties': 0,
                'rank': 1,
                'department': 'Eng',
                'qualification_status': 'Finalist',
            },
            {
                'candidate_id': self.cand_b.id,
                'name': 'Bianca',
                'number': 2,
                'final_score': 80.0,
                'breakdown': '1 judge(s)',
                'comments': [],
                'penalties': 0,
                'rank': 2,
                'department': 'Arts',
                'qualification_status': 'Finalist',
            },
        ]

    def test_results_payload_scoped_to_assigned_event(self):
        from unittest.mock import patch
        from core.faculty_views import _tabulator_results_payload

        with patch('core.faculty_views.compute_criteria_rankings', return_value=self._ranking_rows):
            payload = _tabulator_results_payload(self.tabulator, event_id=self.event.id)
        self.assertEqual(payload['event']['id'], self.event.id)
        self.assertEqual(payload['mode'], 'scoring')
        self.assertTrue(payload['is_finalized'])
        self.assertIsNotNone(payload['winner'])
        self.assertEqual(payload['winner']['name'], 'Alice')
        event_ids = {e['id'] for e in payload['events']}
        self.assertIn(self.event.id, event_ids)
        self.assertNotIn(self.foreign.id, event_ids)

    def test_results_csv_exports_ranked_rows(self):
        from unittest.mock import patch

        self.client.force_login(self.tabulator)
        with patch('core.faculty_views.compute_criteria_rankings', return_value=self._ranking_rows):
            response = self.client.get(
                reverse('tabulator_results_csv'),
                {'event_id': self.event.id},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.decode('utf-8')
        self.assertIn('Alice', body)
        self.assertIn('Bianca', body)

    def test_results_page_renders_for_tabulator(self):
        from unittest.mock import patch

        self.client.force_login(self.tabulator)
        with patch('core.faculty_views.compute_criteria_rankings', return_value=self._ranking_rows):
            response = self.client.get(reverse('tabulator_results'), {'event_id': self.event.id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Final Results')
        self.assertContains(response, 'Alice')
