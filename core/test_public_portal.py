from datetime import time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.faculty_service import leaderboard_rows
from events.models import (
    BracketMatch, BracketTeam, CriteriaEventEntry, CriteriaScoreSubmission,
    Department, Event, EventScoringCategory, EventScoringCriterion,
)
from events.public_service import PUBLIC_TIMEZONE, result_is_published


class PublicPortalTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate(timezone=PUBLIC_TIMEZONE)
        self.department = Department.objects.create(name='Engineering', code='ENG')
        self.event = self.create_event('Basketball')
        self.team_a = BracketTeam.objects.create(
            event=self.event, name='Falcons', department=self.department,
            members='PRIVATE MEMBER INFORMATION', points=25, is_champion=True,
        )
        self.team_b = BracketTeam.objects.create(event=self.event, name='Eagles')
        self.match = BracketMatch.objects.create(
            event=self.event, match_number=1, round_name='Final',
            team_a=self.team_a, team_b=self.team_b, winner=self.team_a,
            match_date=self.today, match_time=time(10), venue='Court A',
            status='completed', score_a='87', score_b='76',
            remarks='PRIVATE STAFF NOTES',
        )

    def create_event(self, name, **kwargs):
        values = dict(name=name, category='Sports', scoring_method='match',
                      event_date=self.today, event_time=time(9), venue='Gym',
                      publication_status='published', status='upcoming')
        values.update(kwargs)
        return Event.objects.create(**values)

    def publish(self, event=None, **config):
        event = event or self.event
        event.status = 'completed'
        event.results_finalized = True
        event.result_processing_config = {'published_at': timezone.now().isoformat(), **config}
        event.save()
        return event

    def details(self, event=None):
        return self.client.get(reverse('public_event_detail', args=[(event or self.event).pk]))

    def test_navigation_is_anonymous_and_read_only(self):
        for name in ('home', 'public_schedules', 'public_games', 'public_leaderboard', 'public_results', 'login'):
            with self.subTest(route=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        self.assertEqual(self.details().status_code, 200)
        self.publish()
        self.assertEqual(self.client.get(reverse('public_result_detail', args=[self.event.pk])).status_code, 200)
        for name in ('home', 'public_games', 'public_results', 'public_schedules', 'public_leaderboard'):
            self.assertEqual(self.client.post(reverse(name)).status_code, 405)

    def test_database_failure_shows_safe_retry_state(self):
        with patch('core.public_views.public_events', side_effect=DatabaseError('private database detail')):
            with self.assertLogs('core.public_views', level='ERROR'):
                response = self.client.get(reverse('public_games'))
        self.assertEqual(response.status_code, 503)
        self.assertIn('temporarily unavailable', response.context['data_error'])
        self.assertNotContains(response, 'private database detail', status_code=503)

    def test_event_content_is_html_escaped(self):
        self.event.name = '<script>alert("unsafe")</script>'
        self.event.mechanics = '<img src=x onerror=alert(1)>'
        self.event.save()
        response = self.details()
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert("unsafe")</script>')
        self.assertNotContains(response, '<img src=x onerror=alert(1)>')

    def test_existing_staff_login_errors_and_role_redirects_are_preserved(self):
        response = self.client.post(reverse('login'), {'username': 'missing', 'password': 'wrong'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['error'], 'Invalid username or password.')
        roles = [('Admin', 'admin_dashboard'), ('Faculty', 'faculty_dashboard'),
                 ('Tabulator', 'tabulator_dashboard'), ('Judge', None)]
        for role, destination in roles:
            with self.subTest(role=role):
                self.client.logout()
                user = get_user_model().objects.create_user(
                    username='login-' + role.lower(), password='test-login-password', is_staff=role == 'Admin',
                )
                if role != 'Admin':
                    user.groups.add(Group.objects.create(name=role))
                response = self.client.post(reverse('login'), {'username': user.username, 'password': 'test-login-password'})
                if destination:
                    self.assertRedirects(response, reverse(destination), fetch_redirect_response=False)
                else:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('judge mobile app', response.context['success'])
                self.assertEqual(self.client.session['_auth_user_id'], str(user.pk))

    def test_staff_dashboards_remain_protected(self):
        for name in ('admin_dashboard', 'faculty_dashboard', 'tabulator_dashboard'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(reverse('login')))

    def test_draft_and_inactive_events_are_inaccessible(self):
        for values in ({'publication_status': 'draft'}, {'status': 'inactive'}):
            hidden = self.create_event('Hidden', results_finalized=True,
                                       result_processing_config={'published_at': timezone.now().isoformat()}, **values)
            for name in ('public_event_detail', 'public_result_detail'):
                self.assertEqual(self.client.get(reverse(name, args=[hidden.pk])).status_code, 404)
        ids = {row['id'] for row in self.client.get(reverse('public_games')).context['events']}
        self.assertEqual(ids, {self.event.pk})

    def test_listing_and_internal_finalization_never_publish_match_scores(self):
        for finalized in (False, True):
            self.event.results_finalized = finalized
            self.event.status = 'completed'
            self.event.save()
            response = self.details()
            fixture = response.context['fixtures'][0]
            self.assertEqual((fixture['score_a'], fixture['score_b'], fixture['winner']), ('', '', ''))
            self.assertFalse(response.context['event']['has_results'])
            self.assertEqual(self.client.get(reverse('public_result_detail', args=[self.event.pk])).status_code, 404)

    def test_unknown_workflow_and_invalid_publication_marker_are_denied(self):
        for config in ({'verification_mode': 'unknown'}, {'published_at': 'invalid'},
                       {'published_at': '2026-02-30T10:00:00Z'}, {'published_at': None}):
            with self.subTest(config=config):
                self.publish(**config)
                self.assertFalse(result_is_published(self.event))
                self.assertEqual(self.client.get(reverse('public_result_detail', args=[self.event.pk])).status_code, 404)
                self.assertFalse(self.details().context['fixtures'][0]['is_official'])

    def test_match_changes_after_publication_require_republication(self):
        self.publish()
        self.assertTrue(self.details().context['fixtures'][0]['is_official'])
        self.match.score_a = '99'
        self.match.winner = self.team_b
        self.match.save()
        fixture = self.details().context['fixtures'][0]
        self.assertFalse(fixture['is_official'])
        self.assertEqual((fixture['score_a'], fixture['score_b'], fixture['winner']), ('', '', ''))
        result = self.client.get(reverse('public_result_detail', args=[self.event.pk]))
        self.assertEqual(result.context['fixtures'], [])
        self.assertNotContains(result, 'Eagles')
        self.assertNotContains(result, '>99<')
        self.assertIn('republication', result.context['result_notice'])
        self.assertEqual(result.context['result_rows'], [])
        self.assertEqual(self.client.get(reverse('public_leaderboard')).context['standings'], [])
        self.publish()
        fixture = self.details().context['fixtures'][0]
        self.assertTrue(fixture['is_official'])
        self.assertEqual((fixture['score_a'], fixture['winner']), ('99', 'Eagles'))
        self.assertEqual(self.client.get(reverse('public_leaderboard')).context['standings'][0]['points'], 25)

    def test_approval_and_tabulation_are_required_by_configured_workflow(self):
        cases = [
            ({'verification_mode': 'tabulator_admin_approval'}, False),
            ({'verification_mode': 'tabulator_admin_approval', 'approved': True}, False),
            ({'verification_mode': 'tabulator_admin_approval', 'tabulation_confirmed_at': 'confirmed'}, False),
            ({'verification_mode': 'tabulator_verification'}, False),
            ({'verification_mode': 'tabulator_verification', 'tabulation_confirmed_at': ''}, False),
            ({'verification_mode': 'tabulator_verification', 'tabulation_confirmed_at': None}, False),
            ({'verification_mode': 'tabulator_verification', 'tabulation_confirmed_at': 'confirmed'}, True),
            ({'verification_mode': 'tabulator_admin_approval', 'approved': True,
              'tabulation_confirmed_at': 'confirmed'}, True),
        ]
        for config, allowed in cases:
            with self.subTest(config=config):
                self.publish(**config)
                self.assertEqual(result_is_published(self.event), allowed)
                response = self.client.get(reverse('public_result_detail', args=[self.event.pk]))
                self.assertEqual(response.status_code, 200 if allowed else 404)
                self.assertEqual(self.details().context['fixtures'][0]['is_official'], allowed)

    def test_public_context_contains_only_whitelisted_projections(self):
        self.publish()
        response = self.details()
        self.assertEqual(set(response.context['participants'][0]), {'id', 'name', 'department', 'logo_url'})
        fixture = response.context['fixtures'][0]
        self.assertEqual(fixture['winner'], 'Falcons')
        self.assertEqual(fixture['score_a'], '87')
        self.assertNotIn('remarks', fixture)
        self.assertIsInstance(response.context['event'], dict)
        for key in ('result_processing_config', 'assigned_judges', 'faculty_account', 'score_settings'):
            self.assertNotIn(key, response.context['event'])
        self.assertNotContains(response, 'PRIVATE MEMBER INFORMATION')
        self.assertNotContains(response, 'PRIVATE STAFF NOTES')
        self.match.status = 'pending'
        self.match.save()
        self.assertFalse(self.details().context['fixtures'][0]['is_official'])

    def test_games_filters_use_existing_event_fields(self):
        singing = self.create_event('Singing', category='Special Event', scoring_method='criteria',
                                    status='active', venue='Hall')
        cases = [({'type': 'criteria'}, [singing.pk]), ({'type': 'match'}, [self.event.pk]),
                 ({'q': 'SING'}, [singing.pk]), ({'category': 'Special Event'}, [singing.pk]),
                 ({'status': 'ongoing'}, [singing.pk]), ({'venue': 'Gym'}, [self.event.pk]),
                 ({'date': (self.today + timedelta(days=1)).isoformat()}, [])]
        for query, expected in cases:
            with self.subTest(query=query):
                response = self.client.get(reverse('public_games'), query)
                self.assertEqual([row['id'] for row in response.context['events']], expected)
        for date_value in ('invalid', '2026-02-30', '2026-13-10'):
            for name, key in (('public_games', 'events'), ('public_schedules', 'schedules')):
                response = self.client.get(reverse(name), {'date': date_value, 'type': 'invalid'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.context[key]), 2)

    def test_home_featured_filters_search_all_public_competitions(self):
        for index in range(7):
            self.create_event(f'Game {index}')
        singing = self.create_event('Singing Showcase', category='Special Event', scoring_method='criteria',
                                    event_date=self.today + timedelta(days=2), venue='Hall', event_time=time(14))
        self.create_event('Private Singing', category='Special Event', scoring_method='criteria', publication_status='draft')
        response = self.client.get(reverse('home'), {'featured_type': 'criteria', 'featured_q': 'Singing'})
        cards = response.context['featured_events']
        self.assertEqual([card['id'] for card in cards], [singing.pk])
        self.assertEqual(cards[0]['banner_key'], 'singing')
        self.assertEqual((cards[0]['time'], cards[0]['venue']), (time(14), 'Hall'))
        response = self.client.get(reverse('home'), {'featured_q': 'does not exist'})
        self.assertEqual(response.context['featured_events'], [])

    def test_home_featured_sort_and_pagination_preserve_selected_filters(self):
        for index in range(7):
            self.create_event(f'Game {index}')
        first = self.client.get(reverse('home'), {'featured_type': 'match', 'featured_sort': 'name'})
        second = self.client.get(reverse('home'), {'featured_type': 'match', 'featured_sort': 'name', 'featured_page': 2})
        self.assertEqual(len(first.context['featured_events']), 6)
        self.assertEqual(len(second.context['featured_events']), 2)
        self.assertEqual(first.context['featured_events'][0]['name'], 'Basketball')
        self.assertEqual(second.context['featured_page_obj'].paginator.count, 8)
        self.assertIn('featured_type=match', second.context['featured_query_string'])
        self.assertIn('featured_sort=name', second.context['featured_query_string'])
        invalid = self.client.get(reverse('home'), {'featured_type': 'bad', 'featured_sort': 'bad', 'featured_page': 'bad'})
        self.assertEqual(invalid.status_code, 200)
        self.assertEqual(invalid.context['featured_filters']['sort'], 'date')

    def test_schedule_union_uses_fixtures_without_duplicate_event_entries(self):
        singing = self.create_event('Singing', scoring_method='criteria', category='Special Event', venue='Hall')
        BracketMatch.objects.create(event=self.event, match_number=2, round_name='Bye',
                                    match_date=self.today, is_automatic_advance=True)
        response = self.client.get(reverse('public_schedules'))
        rows = response.context['schedules']
        self.assertEqual(len(rows), 2)
        self.assertEqual({row['event_id'] for row in rows}, {self.event.pk, singing.pk})
        basketball = next(row for row in rows if row['event_id'] == self.event.pk)
        self.assertEqual(basketball['participants'], 'Falcons vs Eagles')
        self.assertEqual(basketball['venue'], 'Court A')
        self.assertNotIn('score_a', basketball)
        for query in ({'venue': 'Court A'}, {'status': 'completed'}, {'type': 'match'},
                      {'q': 'Basketball', 'date': self.today.isoformat()}):
            self.assertEqual([row['event_id'] for row in self.client.get(
                reverse('public_schedules'), query).context['schedules']], [self.event.pk])

    def test_directory_and_schedule_pagination_preserve_filters(self):
        for index in range(14):
            self.create_event(f'Tournament {index:02d}')
        for name, key in (('public_games', 'events'), ('public_schedules', 'schedules')):
            first = self.client.get(reverse(name), {'type': 'match'})
            second = self.client.get(reverse(name), {'type': 'match', 'page': 2})
            self.assertEqual(len(first.context[key]), 12)
            self.assertEqual(len(second.context[key]), 3)
            self.assertIn('type=match', second.context['query_string'])
            self.assertEqual(second.context['page_obj'].paginator.count, 15)

    def test_public_and_staff_standings_exclude_unpublished_points(self):
        self.publish()
        hidden = self.create_event('Draft tournament', publication_status='draft')
        BracketTeam.objects.create(event=hidden, name='Private team', department=self.department, points=999)
        self.assertEqual(leaderboard_rows()[0]['points'], 25)
        response = self.client.get(reverse('public_leaderboard'))
        self.assertEqual(response.context['standings'][0]['points'], 25)
        self.assertEqual(leaderboard_rows(event_ids=[]), [])
        self.match.status = 'pending'
        self.match.save()
        self.assertEqual(self.client.get(reverse('public_leaderboard')).context['standings'], [])

    def test_multiround_results_are_round_specific_and_never_expose_raw_scores(self):
        event = self.create_event('Dance', category='Special Event', scoring_method='criteria',
                                  event_format='multiple_stage',
                                  rounds_config=[{'id': 'first', 'name': 'Preliminary'},
                                                 {'id': 'final', 'name': 'Final'}])
        entry = CriteriaEventEntry.objects.create(event=event, entry_type='team', display_name='Dance Crew',
                                                 department=self.department, display_order=1)
        event.participant_ids = [entry.pk]
        event.save()
        judge = get_user_model().objects.create_user(username='public-test-judge')
        event.assigned_judges.add(judge)
        category = EventScoringCategory.objects.create(event=event, assigned_round_id='first', name='Performance',
                                                       judge_mode='scoring', display_order=1, overall_weight_percent=100)
        criterion = EventScoringCriterion.objects.create(category=category, name='Execution', display_order=1,
                                                         weight_percent=100, min_score=0, max_score=100)
        submission = CriteriaScoreSubmission.objects.create(event=event, assigned_round_id='first', category=category,
                                                             criterion=criterion, judge=judge, participant_id=entry.pk,
                                                             source='MANUAL', status='APPROVED', raw_score='93.1234')
        self.publish(event)
        url = reverse('public_result_detail', args=[event.pk])
        response = self.client.get(url, {'round_id': 'first'})
        row = response.context['result_rows'][0]
        self.assertEqual(row['placement'], 'Round rank 1')
        self.assertEqual(row['score'], '')
        self.assertNotIn('raw_score', row)
        self.assertNotContains(response, '93.1234')
        self.assertIn('Overall standings are unavailable', response.context['result_notice'])
        self.assertEqual(self.client.get(url, {'round_id': 'final'}).context['result_rows'], [])
        self.assertEqual(self.client.get(url, {'round_id': 'unknown'}).status_code, 404)
        submission.raw_score = '81'
        submission.save()
        response = self.client.get(url, {'round_id': 'first'})
        self.assertEqual(response.context['result_rows'], [])
        self.publish(event)
        response = self.client.get(url, {'round_id': 'first'})
        self.assertEqual(response.context['result_rows'][0]['placement'], 'Round rank 1')
        self.assertEqual(response.context['result_rows'][0]['score'], '')
        BracketTeam.objects.create(event=event, name='Dance Crew', department=self.department, points=700)
        self.assertEqual(self.client.get(reverse('public_leaderboard')).context['standings'], [])
        submission.status = 'SUBMITTED'
        submission.save()
        self.assertEqual(self.client.get(url, {'round_id': 'first'}).context['result_rows'], [])
