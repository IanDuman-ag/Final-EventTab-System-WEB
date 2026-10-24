from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse

from events.models import BracketMatch, BracketTeam, Department, Event, SystemSettings
from events.schedule_navigation import ScheduleNavigationForm, competition_day


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

    def test_day_number_uses_official_start_and_excludes_breaks(self):
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

    def test_empty_competition_days_are_selectable_without_compressing_numbering(self):
        self.settings.non_competition_dates = []
        self.settings.save()
        self.event('Opening')
        self.event('Final', event_date=date(2026, 11, 3))
        response = self.client.get(self.url, {'day': '2026-11-02'})
        self.assertEqual([tab['number'] for tab in response.context['day_tabs']], [1, 2, 3])
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
        # Excluding a non-competition day never silently erases an actual schedule.
        self.assertNotIn(date(2026, 11, 2), [tab['date'] for tab in response.context['day_tabs']])
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

    def test_missing_start_does_not_invent_day_numbers_or_write_settings(self):
        SystemSettings.objects.all().delete()
        self.event()
        response = self.client.get(self.url)
        self.assertFalse(SystemSettings.objects.exists())
        self.assertEqual(response.context['selected_day'], 'all')
        self.assertTrue(all(tab['number'] is None for tab in response.context['day_tabs']))
        self.assertEqual(len(response.context['schedules']), 1)

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
        self.assertIsNone(competition_day(date(2026, 10, 31), self.start, set()))


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
