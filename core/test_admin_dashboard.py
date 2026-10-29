from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from events.models import Event, Team, RegistryCandidate, CriteriaEventEntry


class AdminDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('dashboard-admin', is_staff=True)
        self.client.force_login(self.user)

    def test_counts_use_registrations_not_event_capacity(self):
        event = Event.objects.create(name='Singing', category='Arts', event_date=timezone.localdate(), max_participants=500, num_teams=100)
        participant = RegistryCandidate.objects.create(number='1', name='Singer')
        Team.objects.create(name='Team A', code='A')
        CriteriaEventEntry.objects.create(event=event, entry_type='individual', display_name='Singer', source_individual=participant, display_order=1)
        response = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_participants'], 1)
        self.assertEqual(response.context['total_teams'], 1)
        self.assertEqual(response.context['recent_events'][0]['max_participants'], 1)
        self.assertNotContains(response, '20% vs last month')

    def test_activity_uses_creation_date_and_calendar_uses_schedule(self):
        today = timezone.localdate(timezone=ZoneInfo('Asia/Manila'))
        start = today + timedelta(days=12)
        event = Event.objects.create(name='</script><script>alert(1)</script>', category='Arts', event_date=start, end_date=start + timedelta(days=2))
        local_midnight = datetime.combine(today, datetime.min.time(), tzinfo=ZoneInfo('Asia/Manila'))
        Event.objects.filter(pk=event.pk).update(created_at=local_midnight.astimezone(dt_timezone.utc))
        response = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(response.context['activity_days'][-1]['events'], 1)
        calendar = response.context['dashboard_events'][0]
        self.assertEqual(calendar['date'], start.isoformat())
        self.assertEqual(calendar['end'], (start + timedelta(days=2)).isoformat())
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_empty_dashboard_and_access_control(self):
        response = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['dashboard_events'], [])
        self.assertEqual(response.context['total_events_count'], 0)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('admin_dashboard')).status_code, 302)
