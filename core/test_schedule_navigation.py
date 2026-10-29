from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse

from events.models import BracketMatch, BracketTeam, Department, Event, SystemSettings
from events.schedule_navigation import ScheduleNavigationForm, active_schedule_days


class ScheduleDayNavigationTests(TestCase):
    def setUp(self):
        self.start = date(2026, 11, 1)
        self.settings = SystemSettings.objects.create(intramurals_start_date=self.start,
            non_competition_dates=['2026-11-02'])
        self.url = reverse('public_schedules')

    def event(self, name='Singing', **kwargs):
        values = dict(name=name, category='Arts', scoring_method='criteria',
            event_date=self.start, event_time=time(9), venue='Hall',
            status='upcoming', publication_status='published')
        values.update(kwargs)
        return Event.objects.create(**values)

    def test_day_number_uses_unique_published_schedule_dates(self):
        first = self.event()
        second = self.event('Debate', event_date=self.start + timedelta(days=2))
        Event.objects.filter(pk__in=[first.pk, second.pk]).update(
            created_at=datetime(2020, 1, 1, tzinfo=datetime_timezone.utc))
        response = self.client.get(self.url, {'day': '2026-11-03'})
        self.assertEqual([row['event_id'] for row in response.context['schedules']], [second.pk])
        days = {tab['date']: tab['number'] for tab in response.context['day_tabs']}
        self.assertEqual(days[self.start], 1)
        self.assertEqual(days[date(2026, 11, 3)], 2)
        self.assertContains(response, 'aria-current="date"')
        self.assertEqual(response.context['schedule_groups'][0]['number'], 2)

    def test_filters_intersect_selected_day_and_navigation_retains_them(self):
        wanted = self.event('Vocal Solo')
        self.event('Vocal Duet', venue='Court')
        self.event('Vocal next day', event_date=date(2026, 11, 3))
        params = dict(day='2026-11-01', q='Vocal', type='criteria', category='Arts',
                      date='2026-11-01', venue='Hall', status='upcoming')
        response = self.client.get(self.url, params)
        self.assertEqual([row['event_id'] for row in response.context['schedules']], [wanted.pk])
        next_tab = next(tab for tab in response.context['day_tabs'] if tab['date'] == date(2026, 11, 3))
        query = parse_qs(urlparse(next_tab['url']).query)
        for key in ('q', 'type', 'category', 'venue', 'status'):
            self.assertEqual(query[key], [params[key]])
        self.assertEqual(query['day'], ['2026-11-03'])
        self.assertEqual(query['date'], ['2026-11-03'])
        self.assertNotContains(response, 'Vocal Duet</a>')

    def test_empty_dates_do_not_create_tabs_or_advance_numbering(self):
        self.settings.non_competition_dates = []
        self.settings.save()
        self.event('Opening')
        self.event('Final', event_date=date(2026, 11, 3))
        response = self.client.get(self.url, {'day': '2026-11-02'})
        self.assertEqual([tab['number'] for tab in response.context['day_tabs']], [1, 2])
        self.assertNotIn(date(2026, 11, 2), [tab['date'] for tab in response.context['day_tabs']])
        self.assertEqual(response.context['schedules'], [])
        self.assertContains(response, 'No schedules for this day.')

    def test_criteria_sessions_use_actual_dates_times_and_venues_without_parent_duplicates(self):
        event = self.event('Pageant', event_date=date(2026, 10, 20), end_date=date(2026, 11, 3),
            rounds_config=[
                {'name': 'Preliminary', 'schedule': {'date': '2026-11-01', 'start_time': '10:30', 'end_time': '12:00', 'venue': 'Stage A'}},
                {'name': 'Final', 'schedule': {'date': '2026-11-03', 'start_time': '14:00', 'venue': 'Stage B'}},
                {'name': 'Disabled', 'enabled': False, 'schedule': {'date': '2026-11-04'}},
            ])
        response = self.client.get(self.url, {'day': 'all'})
        rows = response.context['schedules']
        self.assertEqual(len(rows), 2)
        self.assertEqual([row['event_id'] for row in rows], [event.pk, event.pk])
        self.assertEqual([row['date'] for row in rows], [self.start, date(2026, 11, 3)])
        self.assertEqual(rows[0]['time'], time(10, 30))
        self.assertEqual(rows[0]['end_time'], time(12))
        filtered = self.client.get(self.url, {'day': '2026-11-03', 'venue': 'Stage B', 'date': '2026-11-03'})
        self.assertEqual(filtered.context['schedules'][0]['round'], 'Final')
        self.assertIn('Stage B', filtered.context['venues'])

    def test_multiday_span_appears_each_day_and_all_dates_are_grouped(self):
        self.event('Art exhibit', end_date=date(2026, 11, 3))
        response = self.client.get(self.url, {'day': 'all'})
        self.assertEqual([row['date'] for row in response.context['schedules']],
                         [self.start, date(2026, 11, 2), date(2026, 11, 3)])
        self.assertEqual(len(response.context['schedule_groups']), 3)
        # Actual public schedules determine active days, including dated event spans.
        self.assertIn(date(2026, 11, 2), [tab['date'] for tab in response.context['day_tabs']])
        self.assertEqual([tab['number'] for tab in response.context['day_tabs']], [1, 2, 3])
        self.assertEqual(len(self.client.get(self.url, {'day': '2026-11-02'}).context['schedules']), 1)

    def test_match_reschedule_moves_only_its_fixture_and_keeps_team_logos(self):
        event = self.event('Basketball', scoring_method='match', category='Sports', end_date=date(2026, 11, 5))
        department = Department.objects.create(name='Engineering', code='ENG', logo='department_logos/eng.png')
        a = BracketTeam.objects.create(event=event, name='Falcons', department=department)
        b = BracketTeam.objects.create(event=event, name='Eagles')
        match = BracketMatch.objects.create(event=event, match_number=1, team_a=a, team_b=b,
            match_date=self.start, match_time=time(10), venue='Court', status='pending')
        BracketMatch.objects.create(event=event, match_number=2, team_a=a, team_b=b,
            match_date=date(2026, 11, 3), match_time=time(11), status='pending')
        response = self.client.get(self.url, {'day': self.start.isoformat()})
        self.assertEqual(len(response.context['schedules']), 1)
        self.assertContains(response, 'matchup-vs')
        self.assertTrue(response.context['schedules'][0]['matchup'][0]['logo_url'].endswith('/eng.png'))
        match.match_date = date(2026, 11, 4)
        match.save(update_fields=['match_date'])
        self.assertEqual(self.client.get(self.url, {'day': self.start.isoformat()}).context['schedules'], [])
        self.assertEqual(len(self.client.get(self.url, {'day': '2026-11-04'}).context['schedules']), 1)
        self.assertEqual(len(self.client.get(self.url, {'day': 'all'}).context['schedules']), 2)

    def test_active_numbering_does_not_require_or_write_settings(self):
        SystemSettings.objects.all().delete()
        self.event()
        response = self.client.get(self.url)
        self.assertFalse(SystemSettings.objects.exists())
        self.assertEqual(response.context['selected_day'], 'all')
        self.assertEqual(response.context['day_tabs'][0]['number'], 1)
        self.assertEqual(len(response.context['schedules']), 1)

    def test_automatic_day_numbers_ignore_creation_dates_filters_and_private_events(self):
        self.settings.intramurals_start_date = None
        self.settings.non_competition_dates = []
        self.settings.save()
        first = self.event('Opening')
        self.event('Basketball', category='Sports', event_date=date(2026, 11, 3))
        self.event('Private earlier event', publication_status='draft', event_date=date(2026, 10, 1))
        Event.objects.filter(pk=first.pk).update(created_at=datetime(2020, 1, 1, tzinfo=datetime_timezone.utc))
        response = self.client.get(self.url, {'day': '2026-11-03', 'category': 'Sports', 'q': 'Basketball'})
        self.assertEqual(response.context['schedule_groups'][0]['number'], 2)
        self.assertContains(response, '<strong>Day 2</strong>')
        # Moving an active date preserves chronological, consecutive numbering.
        first.event_date = date(2026, 11, 2)
        first.save(update_fields=['event_date'])
        response = self.client.get(self.url, {'day': '2026-11-03', 'category': 'Sports'})
        self.assertEqual(response.context['schedule_groups'][0]['number'], 2)
        self.assertEqual(response.context['day_tabs'][0]['date'], date(2026, 11, 2))

    def test_pagination_keeps_day_and_hidden_events_never_create_tabs(self):
        for index in range(14):
            self.event(f'Event {index}')
        self.event('Private', publication_status='draft', event_date=date(2026, 12, 1))
        response = self.client.get(self.url, {'day': '2026-11-01', 'page': '2'})
        self.assertEqual(len(response.context['schedules']), 2)
        self.assertIn('day=2026-11-01', response.context['query_string'])
        self.assertNotIn('page=', response.context['query_string'])
        self.assertNotIn(date(2026, 12, 1), [tab['date'] for tab in response.context['day_tabs']])

    def test_manila_midnight_selects_manila_competition_day(self):
        self.event()
        self.event('Evening', event_date=date(2026, 11, 3))
        # UTC Nov 2 16:30 is Nov 3 00:30 in Manila.
        with patch('django.utils.timezone.now', return_value=datetime(2026, 11, 2, 16, 30, tzinfo=datetime_timezone.utc)):
            response = self.client.get(self.url)
        self.assertEqual(response.context['selected_day'], '2026-11-03')
        self.assertContains(response, 'Asia/Manila')

    def test_empty_invalid_and_conflicting_date_filters_are_safe(self):
        self.event()
        for day in ('invalid', '2026-02-30', '2026-11-07'):
            self.assertEqual(self.client.get(self.url, {'day': day}).status_code, 200)
        response = self.client.get(self.url, {'day': '2026-11-01', 'date': '2026-11-03'})
        self.assertEqual(response.context['schedules'], [])
        self.assertContains(response, 'No schedules for this day.')
        self.assertEqual(active_schedule_days([]), {})

    def test_sparse_example_counts_and_day_mapping_stay_fixed_under_every_filter(self):
        self.settings.intramurals_start_date = date(2026, 1, 1)
        self.settings.non_competition_dates = ['2026-11-05']
        self.settings.save()
        self.event('Opening', event_date=date(2026, 10, 22))
        self.event('Speech', event_date=date(2026, 11, 2))
        self.event('Painting', event_date=date(2026, 11, 5))
        self.event('Closing', event_date=date(2026, 11, 10))
        self.event('Private', event_date=date(2026, 10, 1), publication_status='draft')
        match_event = self.event('Basketball', event_date=date(2026, 10, 10), scoring_method='match', category='Sports')
        a = BracketTeam.objects.create(event=match_event, name='Falcons')
        b = BracketTeam.objects.create(event=match_event, name='Eagles')
        match = BracketMatch.objects.create(event=match_event, match_number=1, team_a=a, team_b=b,
            match_date=date(2026, 11, 5), match_time=time(10), venue='Court', status='pending')
        expected = [(date(2026, 10, 22), 1, 1), (date(2026, 11, 2), 2, 1),
                    (date(2026, 11, 5), 3, 2), (date(2026, 11, 10), 4, 1)]
        for filters in ({}, {'q': 'Basketball'}, {'category': 'Sports'}, {'type': 'match'},
                        {'venue': 'Court'}, {'status': 'completed'}, {'team': str(a.pk)},
                        {'team': 'Falcons'}, {'date': '2026-11-05'}):
            with self.subTest(filters=filters):
                response = self.client.get(self.url, {'day': 'all', **filters})
                self.assertEqual([(tab['date'], tab['number'], tab['count']) for tab in response.context['day_tabs']], expected)
                self.assertContains(response, '2 scheduled events')
                if filters.get('team'):
                    self.assertEqual(len(response.context['schedules']), 1)
                    self.assertEqual(response.context['schedule_groups'][0]['number'], 3)
        # Rescheduling moves the last match off its old date without keeping a ghost day.
        match.match_date = date(2026, 11, 1)
        match.save(update_fields=['match_date'])
        response = self.client.get(self.url, {'day': 'all'})
        self.assertEqual([(tab['date'], tab['number']) for tab in response.context['day_tabs']],
            [(date(2026, 10, 22), 1), (date(2026, 11, 1), 2), (date(2026, 11, 2), 3),
             (date(2026, 11, 5), 4), (date(2026, 11, 10), 5)])


class AdminScheduleSettingsTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user('calendar-admin', is_staff=True)
        self.url = reverse('admin_settings')
        self.data = {'operation': 'schedule_navigation', 'intramurals_start_date': '2026-11-01',
                     'non_competition_dates': '2026-11-02, 2026-11-04'}

    def test_save_calendar_does_not_mutate_scoring_results_or_event_configuration(self):
        settings = SystemSettings.objects.create(championship_points_config={'major': 99, 'minor': 10})
        event = Event.objects.create(name='Published', category='Sports', event_date=date(2026, 11, 1),
            results_finalized=True, result_processing_config={'published_at': 'unchanged'})
        self.client.force_login(self.admin)
        response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 302)
        settings.refresh_from_db(); event.refresh_from_db()
        self.assertEqual(settings.intramurals_start_date, date(2026, 11, 1))
        self.assertEqual(settings.non_competition_dates, ['2026-11-02', '2026-11-04'])
        self.assertEqual(settings.championship_points_config, {'major': 99, 'minor': 10})
        self.assertEqual(event.result_processing_config, {'published_at': 'unchanged'})
        self.assertTrue(event.results_finalized)
        self.assertContains(self.client.get(self.url), 'Save schedule days')

    def test_invalid_dates_preserve_saved_calendar(self):
        self.client.force_login(self.admin)
        self.client.post(self.url, self.data)
        for invalid in ('2026-02-30', 'not-a-date', '2026-11-01', '2026-10-31'):
            response = self.client.post(self.url, {**self.data, 'non_competition_dates': invalid})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(SystemSettings.objects.get(pk=1).non_competition_dates, ['2026-11-02', '2026-11-04'])
        self.assertFalse(ScheduleNavigationForm({'non_competition_dates': '2026-11-02'}).is_valid())

    def test_calendar_requires_admin_and_csrf(self):
        self.assertEqual(self.client.post(self.url, self.data).status_code, 302)
        faculty = get_user_model().objects.create_user('calendar-faculty', is_staff=True)
        faculty.groups.add(Group.objects.create(name='Faculty'))
        self.client.force_login(faculty)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 403)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(self.url, self.data).status_code, 403)
        self.assertFalse(SystemSettings.objects.exists())
