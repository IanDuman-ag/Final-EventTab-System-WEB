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

    def test_legacy_published_criteria_contributes_partial_department_tally(self):
        event = self.create_event('Published Singing', scoring_method='criteria',
            event_classification='major', apply_championship_points=True,
            championship_points_config=[{'points': 100}, {'points': 75}])
        entries = [CriteriaEventEntry.objects.create(event=event, entry_type='individual',
            display_name=name, department=self.department, display_order=index)
            for index, name in enumerate(['Singer A', 'Singer B'], 1)]
        event.participant_ids = [entry.pk for entry in entries]
        event.save()
        stamp = timezone.now().isoformat()
        self.publish(event, faculty_submitted_at=stamp, confirmed_stages={'0': {
            'approved_at': stamp, 'results': [
                {'participant_id': entry.pk, 'rank': index, 'complete': True, 'final_score': 90 - index}
                for index, entry in enumerate(entries, 1)]}})
        self.assertEqual(leaderboard_rows(), [
            {'department': 'Engineering', 'points': 175, 'rank': 1}])
        for params in ({}, {'type': 'criteria'}, {'event_id': str(event.pk)}):
            response = self.client.get(reverse('public_leaderboard'), params)
            self.assertEqual(response.context['standings'][0]['points'], 175)
        # Reads do not change publication data or accumulate awards.
        event.refresh_from_db()
        self.assertNotIn('published_championship_awards', event.result_processing_config)
        self.assertEqual(leaderboard_rows()[0]['points'], 175)
        event.apply_championship_points = False
        event.save()
        self.assertEqual(leaderboard_rows(), [])
        event.apply_championship_points = True
        event.results_finalized = False
        event.save()
        self.assertEqual(leaderboard_rows(), [])

    def test_approved_multiround_final_is_previewed_and_counts_published_awards(self):
        event = self.create_event('Approved Pageant', scoring_method='criteria',
            event_format='multiple_stage', rounds_config=[
                {'id': 'first', 'name': 'Preliminary'}, {'id': 'final', 'name': 'Final'}])
        entry = CriteriaEventEntry.objects.create(event=event, entry_type='individual',
            display_name='Approved Finalist', department=self.department, display_order=1)
        event.participant_ids = [entry.pk]
        event.save()
        stamp = timezone.now().isoformat()
        self.publish(event, faculty_submitted_at=stamp,
            championship_scoring={'approval_required': False}, published_championship_awards={str(self.department.pk): 100},
            qualified_participant_ids={'1': [entry.pk]},
            confirmed_stages={'1': {'approved_at': stamp, 'results': [
                {'participant_id': entry.pk, 'rank': 1, 'complete': True, 'final_score': 'PRIVATE_SCORE'}]}})
        response = self.client.get(reverse('public_results'))
        card = next(row for row in response.context['events'] if row['id'] == event.pk)
        self.assertEqual(card['official_placements'][0]['name'], 'Approved Finalist')
        self.assertNotContains(response, 'PRIVATE_SCORE')
        response = self.client.get(reverse('public_leaderboard'))
        self.assertEqual(response.context['standings'][0]['points'], 100)
        self.assertEqual(response.context['standings'][0]['gold'], 1)
        # Published placements alone cannot invent department championship points.
        event.result_processing_config.pop('published_championship_awards')
        event.save(update_fields=['result_processing_config'])
        response = self.client.get(reverse('public_leaderboard'))
        self.assertEqual(response.context['standings'], [])
        self.assertContains(response, 'Awaiting published championship points.')
        # Publication does not make an unapproved final eligible.
        event.result_processing_config['confirmed_stages'] = {}
        event.save(update_fields=['result_processing_config'])
        self.assertEqual(self.client.get(reverse('public_leaderboard')).context['standings'], [])



    def test_schedule_shows_real_opponents_and_department_logo(self):
        self.department.logo = 'department_logos/engineering.png'
        self.department.save(update_fields=['logo'])
        response = self.client.get(reverse('public_schedules'))
        matchup = response.context['schedules'][0]['matchup']
        self.assertEqual([side['name'] for side in matchup], ['Falcons', 'Eagles'])
        self.assertEqual(matchup[0]['department'], 'Engineering')
        self.assertTrue(matchup[0]['logo_url'].endswith('department_logos/engineering.png'))
        self.assertContains(response, 'matchup-vs')
        self.assertNotContains(response, 'PRIVATE MEMBER INFORMATION')
        self.assertNotContains(response, 'PRIVATE STAFF NOTES')
        self.assertNotContains(self.client.get(reverse('home')), 'schedule-row--match')

    def test_schedule_handles_unknown_opponent_without_inventing_a_team(self):
        self.match.team_b = None
        self.match.save(update_fields=['team_b'])
        response = self.client.get(reverse('public_schedules'))
        self.assertEqual(response.context['schedules'][0]['matchup'][1]['name'], 'TBD')
        self.assertEqual(response.context['schedules'][0]['matchup'][1]['logo_url'], '')


    def test_home_classification_filter_combines_with_type_and_search(self):
        major = self.create_event('Major Vocal Duet', scoring_method='criteria', event_classification='major')
        self.create_event('Minor Vocal Duet', scoring_method='criteria', event_classification='minor')
        response = self.client.get(reverse('home'), {
            'featured_type': 'criteria', 'featured_q': 'Vocal',
            'featured_classification': 'major', 'featured_sort': 'name',
        })
        self.assertEqual([row['id'] for row in response.context['featured_events']], [major.pk])
        self.assertIn('featured_classification=major', response.context['featured_query_string'])
        self.assertContains(response, 'home-leaderboard-title')
        self.assertContains(response, 'home-schedule-layout')
        self.assertContains(response, 'id="home-reference-styles"')
        self.assertEqual(self.client.get(reverse('home'), {
            'featured_classification': 'invalid',
        }).context['featured_filters']['classification'], '')

    def test_managed_photo_covers_do_not_override_restored_svg_artwork(self):
        self.event.name = 'Photo Contest'
        self.event.sport_type = ''
        self.event.special_event_type = ''
        for prefix in ('event_covers/', 'cloudinary/event_covers/', 'cloudinary/assets/event_covers/'):
            with self.subTest(prefix=prefix):
                self.event.image = prefix + 'photography_123'
                self.event.save()
                response = self.client.get(reverse('public_games'))
                self.assertEqual(response.context['events'][0]['image_url'], '')
                self.assertContains(response, 'data:image/svg+xml;base64,')
                self.assertNotContains(response, 'photography_123')

    def test_games_category_filter_uses_major_and_minor_classification(self):
        self.event.event_classification = 'major'
        self.event.save(update_fields=['event_classification'])
        minor = self.create_event('Minor Quiz Bee', event_classification='minor')
        for classification, expected in [('major', self.event.pk), ('minor', minor.pk)]:
            response = self.client.get(reverse('public_games'), {'category': classification})
            self.assertEqual([row['id'] for row in response.context['events']], [expected])
            self.assertContains(response, f'value="{classification}" selected')
        self.assertContains(response, '>Major</option>')
        self.assertContains(response, '>Minor</option>')

    def test_games_artwork_matches_event_and_preserves_uploaded_image(self):
        from pathlib import Path
        from django.conf import settings
        from core.public_views import game_artwork_path
        for name, asset in [('Spoken Poetry', 'speech.svg'), ('Charcoal Painting', 'art.svg'),
                            ('Film on Stage', 'film.svg'), ('Photo Contest', 'photography.svg'),
                            ('Live Band Competition', 'band.svg'), ('Quiz Bee', 'quiz.svg'),
                            ('Radio Drama', 'radio.svg'), ('Short & Sweet Play', 'theater.svg'),
                            ('Most Disciplined Unit', 'unit.svg'), ('Amazing Race', 'race.svg'),
                            ('Mr. & Ms. USTP Ambassador 2026', 'pageant.webp')]:
            with self.subTest(name=name):
                self.event.name = name
                self.event.sport_type = ''
                self.event.special_event_type = ''
                path = game_artwork_path(self.event)
                self.assertTrue(path.endswith('/' + asset))
                self.assertTrue((Path(settings.BASE_DIR) / 'frontend' / path).is_file())
        self.event.name = 'Photo Contest'
        self.event.image = 'events/uploaded-photo.jpg'
        self.event.save()
        response = self.client.get(reverse('public_games'))
        self.assertContains(response, f'src="{self.event.image.url}"')
        self.assertContains(response, 'data-banner-fallback="data:image/svg+xml;base64,')

    def test_classification_tags_use_saved_values_on_public_pages(self):
        for value, label in [('major', 'Major Event'), ('minor', 'Minor Event'), ('', '')]:
            self.event.event_classification = value
            self.event.save(update_fields=['event_classification'])
            for route, args in [('home', []), ('public_games', []), ('public_schedules', []),
                                ('public_event_detail', [self.event.pk])]:
                with self.subTest(classification=value, route=route):
                    response = self.client.get(reverse(route, args=args))
                    marker = 'aria-label="Event classification"'
                    if label:
                        self.assertContains(response, marker + '>' + label + '</span>')
                    else:
                        self.assertNotContains(response, marker)
        self.event.event_classification = 'major'
        self.event.save(update_fields=['event_classification'])
        self.publish()
        for route, args in [('public_results', []), ('public_result_detail', [self.event.pk])]:
            self.assertContains(self.client.get(reverse(route, args=args)),
                                'aria-label="Event classification">Major Event</span>')

    def test_schedule_union_preserves_event_and_match_classifications(self):
        self.event.event_classification = 'major'
        self.event.save(update_fields=['event_classification'])
        self.create_event('Minor Singing', scoring_method='criteria', event_classification='minor')
        response = self.client.get(reverse('public_schedules'))
        classifications = {row['event_name']: row['classification_label'] for row in response.context['schedules']}
        self.assertEqual(classifications['Basketball'], 'Major Event')
        self.assertEqual(classifications['Minor Singing'], 'Minor Event')

    def test_leaderboard_tabs_scope_categories_and_actual_event_names(self):
        singing = self.create_event('Singing', category='Arts', scoring_method='criteria')
        badminton = self.create_event('Badminton', category='Indoor', scoring_method='match')
        self.create_event('Private scoring event', category='Private', publication_status='draft')
        url = reverse('public_leaderboard')
        match = self.client.get(url, {'type': 'match'})
        self.assertEqual(match.context['categories'], ['Indoor', 'Sports'])
        self.assertEqual({option['id'] for option in match.context['event_options']}, {str(self.event.pk), str(badminton.pk)})
        self.assertContains(match, 'id="standings-event"')
        self.assertNotContains(match, 'All event types')
        criteria = self.client.get(url, {'type': 'criteria', 'category': 'Sports', 'event_id': self.event.pk})
        self.assertEqual(criteria.context['categories'], ['Arts'])
        self.assertEqual(criteria.context['filters']['category'], '')
        self.assertEqual(criteria.context['selected_event_id'], '')
        self.assertEqual(criteria.context['event_options'], [{'id': str(singing.pk), 'name': 'Singing'}])
        overall = self.client.get(url)
        self.assertEqual(overall.context['categories'], ['Arts', 'Indoor', 'Sports'])
        self.assertEqual(len(overall.context['event_options']), 3)
        self.assertNotContains(overall, 'Private scoring event')

    def test_leaderboard_category_and_event_filters_limit_official_points(self):
        self.publish()
        badminton = self.create_event('Badminton', category='Indoor')
        winner = BracketTeam.objects.create(event=badminton, name='Shuttlers', department=self.department, points=75)
        opponent = BracketTeam.objects.create(event=badminton, name='Opponents')
        BracketMatch.objects.create(event=badminton, match_number=1, round_name='Final',
            team_a=winner, team_b=opponent, winner=winner, status='completed')
        self.publish(badminton)
        pending = self.create_event('Awaiting confirmation', category='Sports')
        BracketTeam.objects.create(event=pending, name='Pending', department=self.department, points=999)
        url = reverse('public_leaderboard')
        self.assertEqual(self.client.get(url).context['standings'][0]['points'], 100)
        response = self.client.get(url, {'type': 'match', 'category': 'Indoor'})
        self.assertEqual(response.context['event_options'], [{'id': str(badminton.pk), 'name': 'Badminton'}])
        self.assertEqual(response.context['standings'][0]['points'], 75)
        response = self.client.get(url, {'event_id': self.event.pk})
        self.assertEqual(response.context['selected_event_id'], str(self.event.pk))
        self.assertEqual(response.context['standings'][0]['points'], 25)
        self.assertEqual(response.context['standings'][0]['result_url'], reverse('public_result_detail', args=[self.event.pk]))
        response = self.client.get(url, {'event_id': pending.pk})
        self.assertEqual(response.context['selected_event_id'], str(pending.pk))
        self.assertEqual(response.context['standings'], [])
        self.assertNotContains(response, '999')

    def test_leaderboard_reference_layout_and_department_search(self):
        self.publish(calculated_placements={str(self.team_a.pk): 1})
        response = self.client.get(reverse('public_leaderboard'), {'q': 'engineer'})
        self.assertEqual(response.context['standings'][0]['department'], 'Engineering')
        self.assertEqual(response.context['standings'][0]['gold'], 1)
        self.assertEqual(response.context['standings'][0]['events_joined'], 1)
        self.assertContains(response, 'standings-podium')
        self.assertContains(response, '>Match Based</button>')
        self.assertNotContains(response, '>Sports</button>')
        empty = self.client.get(reverse('public_leaderboard'), {'q': 'missing department'})
        self.assertEqual(empty.context['standings'], [])
        self.assertEqual(len(empty.context['podium']), 1)

    def test_leaderboard_progress_uses_unsearched_official_leader(self):
        rows = [{'department': name, 'points': points, 'rank': rank}
                for rank, (name, points) in enumerate([('Technology', 340), ('Information', 280), ('Food Processing', 220)], 1)]
        with patch('core.public_views.leaderboard_rows', return_value=rows):
            response = self.client.get(reverse('public_leaderboard'))
        self.assertEqual(response.context['leader_points'], 340)
        self.assertEqual(response.context['points_ticks'], [0, 85, 170, 255, 340])
        self.assertEqual([row['progress_percent'] for row in response.context['standings']], [100, 82, 65])
        self.assertEqual([row['points_gap'] for row in response.context['standings']], [0, 60, 120])
        self.assertEqual(response.context['standings'][0]['lead_margin'], 60)
        with patch('core.public_views.leaderboard_rows', return_value=rows):
            searched = self.client.get(reverse('public_leaderboard'), {'q': 'Information'})
        self.assertEqual(searched.context['leader_points'], 340)
        self.assertEqual(searched.context['standings'][0]['progress_percent'], 82)
        self.assertEqual(searched.context['standings'][0]['rank'], 2)

    def test_leaderboard_progress_handles_zero_ties_and_single_department(self):
        for values in ([0, 0], [100, 100], [100]):
            rows = [{'department': f'Department {rank}', 'points': points, 'rank': rank}
                    for rank, points in enumerate(values, 1)]
            with self.subTest(values=values), patch('core.public_views.leaderboard_rows', return_value=rows):
                response = self.client.get(reverse('public_leaderboard'))
            standings = response.context['standings']
            self.assertEqual(standings[0]['progress_percent'], 100 if values[0] else 0)
            self.assertEqual(standings[0]['is_tied_leader'], len(values) > 1)
            self.assertEqual(standings[0]['lead_margin'], 0 if len(values) > 1 else None)
        with patch('core.public_views.leaderboard_rows', return_value=[]):
            response = self.client.get(reverse('public_leaderboard'))
        self.assertEqual(response.context['leader_points'], 0)
        self.assertEqual(response.context['standings'], [])

    def test_leaderboard_school_year_limits_published_points(self):
        self.event.academic_year = '2026'
        self.event.save()
        self.publish()
        older = self.create_event('Old event', academic_year='2025')
        winner = BracketTeam.objects.create(event=older, name='Old Falcons', department=self.department, points=100)
        opponent = BracketTeam.objects.create(event=older, name='Old Eagles')
        BracketMatch.objects.create(event=older, match_number=1, round_name='Final',
            team_a=winner, team_b=opponent, winner=winner, status='completed')
        self.publish(older)
        self.create_event('Future event', academic_year='2027')
        url = reverse('public_leaderboard')
        self.assertEqual(self.client.get(url).context['standings'][0]['points'], 25)
        self.assertIn('2027', self.client.get(url).context['school_years'])
        self.assertEqual(self.client.get(url, {'year': '2025'}).context['standings'][0]['points'], 100)
        self.assertEqual(self.client.get(url, {'year': ''}).context['standings'][0]['points'], 125)

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
        self.assertEqual(result.context['bracket_lanes'], [])
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

    def test_public_result_bracket_preserves_links_and_all_published_matches(self):
        semifinal = BracketMatch.objects.create(
            event=self.event, match_number=2, round_name='Winners Semifinals',
            team_a=self.team_a, team_b=self.team_b, winner=self.team_a,
            status='completed', score_a='21', score_b='12', next_match_winner=self.match,
        )
        losers = BracketMatch.objects.create(
            event=self.event, match_number=3, round_name='Losers Final',
            team_a=self.team_a, team_b=self.team_b, winner=self.team_b,
            status='completed', score_a='10', score_b='21',
        )
        semifinal.next_match_loser = losers
        semifinal.save()
        for number in range(4, 17):
            BracketMatch.objects.create(
                event=self.event, match_number=number, round_name='Pool Play',
                team_a=self.team_a, team_b=self.team_b, winner=self.team_a,
                status='completed', score_a='21', score_b='8',
            )
        advance = BracketMatch.objects.create(
            event=self.event, match_number=0, round_name='Winners Semifinals',
            team_a=self.team_a, winner=self.team_a, is_automatic_advance=True,
            status='completed', next_match_winner=semifinal,
        )
        self.publish()
        response = self.client.get(reverse('public_result_detail', args=[self.event.pk]))
        nodes = [match for lane in response.context['bracket_lanes']
                 for column in lane['rounds'] for match in column['matches']]
        self.assertEqual(len(nodes), 17)
        self.assertTrue(next(match for match in nodes if match['id'] == advance.pk)['automatic_advance'])
        node = next(match for match in nodes if match['id'] == semifinal.pk)
        self.assertEqual(node['next_winner'], self.match.pk)
        self.assertEqual(node['next_loser'], losers.pk)
        self.assertContains(response, 'class="official-match-card"', count=17)
        self.assertNotContains(response, 'PRIVATE STAFF NOTES')
        self.assertNotContains(response, 'PRIVATE MEMBER INFORMATION')
        self.match.save()
        response = self.client.get(reverse('public_result_detail', args=[self.event.pk]))
        nodes = [match for lane in response.context['bracket_lanes']
                 for column in lane['rounds'] for match in column['matches']]
        node = next(match for match in nodes if match['id'] == semifinal.pk)
        self.assertEqual(node['next_winner'], '')
        self.assertNotContains(response, f'data-match-id="{self.match.pk}"')

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

    def test_home_cultural_and_arts_tabs_use_public_event_categories(self):
        cultural = self.create_event('Pageants', category='Cultural', scoring_method='criteria')
        arts = self.create_event('Singing Showcase', category='Arts', scoring_method='criteria')
        combined = self.create_event('Vocal Duet', category='Socio-Cultural & Literary Arts', scoring_method='criteria')
        self.create_event('Private Cultural Event', category='Cultural', publication_status='draft')
        self.create_event('Uncategorized Criteria', category='Special Event', scoring_method='criteria')
        home = reverse('home')
        cultural_response = self.client.get(home, {'featured_type': 'cultural'})
        self.assertEqual({row['id'] for row in cultural_response.context['featured_events']}, {cultural.pk, combined.pk})
        arts_response = self.client.get(home, {'featured_type': 'arts', 'featured_q': 'Singing', 'featured_sort': 'name'})
        self.assertEqual([row['id'] for row in arts_response.context['featured_events']], [arts.pk])
        self.assertIn('featured_type=arts', arts_response.context['featured_query_string'])
        all_arts = self.client.get(home, {'featured_type': 'arts'})
        self.assertEqual({row['id'] for row in all_arts.context['featured_events']}, {arts.pk, combined.pk})

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
