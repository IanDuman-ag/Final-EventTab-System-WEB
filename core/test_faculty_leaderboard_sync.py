from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from events.models import Event


class FacultyLeaderboardSyncTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='faculty-sync',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(self.user)

    def test_response_is_uncached_and_exposes_current_version(self):
        event = Event.objects.create(
            name='Mobile Sync Event',
            category='Sports',
            event_date=date.today(),
            venue='Gym',
        )

        response = self.client.get(reverse('faculty_leaderboard'))

        self.assertEqual(response.status_code, 200)
        self.assertIn('no-cache', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['X-Leaderboard-Version'], event.updated_at.isoformat())
        self.assertContains(
            response,
            f'data-leaderboard-version="{event.updated_at.isoformat()}"',
        )

    def test_head_request_returns_version_without_stale_cache(self):
        event = Event.objects.create(
            name='Published Mobile Event',
            category='Academic',
            event_date=date.today(),
            venue='Hall',
        )

        response = self.client.head(reverse('faculty_leaderboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Leaderboard-Version'], event.updated_at.isoformat())
