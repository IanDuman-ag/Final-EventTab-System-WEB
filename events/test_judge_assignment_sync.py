from datetime import date, time

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient

from .mobile_sync import sync_event_to_mobile, sync_judge_to_mobile_events
from .models import Event
from .test_mobile_api import read_only_queries


class JudgeAssignmentSyncTests(TestCase):
    def setUp(self):
        self.judge = get_user_model().objects.create_user(username='assigned-judge')
        self.other = get_user_model().objects.create_user(username='unassigned-judge')
        group = Group.objects.create(name='Judge')
        self.judge.groups.add(group)
        self.other.groups.add(group)
        self.api = APIClient()
        self.api.force_authenticate(self.judge)
        self.portal = Event.objects.create(name='New event', category='Academic',
            scoring_method='criteria', event_date=date.today(), event_time=time(10),
            venue='Hall', status=Event.STATUS_UPCOMING)
        self.portal.assigned_judges.add(self.judge)

    def test_created_upcoming_event_syncs_only_assigned_judges(self):
        mobile = sync_event_to_mobile(self.portal)
        self.assertEqual(list(mobile.assigned_judges.all()), [self.judge])
        with read_only_queries():
            home = self.api.get('/api/events/judge/dashboard/').json()
            events = self.api.get('/api/events/judge/assignments/').json()
        self.assertEqual([row['portal_event_id'] for row in home['assigned_events']], [self.portal.pk])
        home_row = dict(home['assigned_events'][0])
        self.assertEqual(home_row.pop('candidate_count'), home_row['participant_count'])
        self.assertEqual(home_row.pop('time'), '10:00:00')
        self.assertEqual(home_row, events['assignments'][0])
        self.assertFalse(home['assigned_events'][0]['can_score'])

    def test_dashboard_reads_portal_assignments_when_mirror_is_empty(self):
        mobile = sync_event_to_mobile(self.portal)
        mobile.assigned_judges.clear()
        with read_only_queries():
            response = self.api.get('/api/events/judge/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['assigned_events'][0]['portal_event_id'], self.portal.pk)

    def test_new_judge_sync_does_not_grant_unassigned_active_events(self):
        self.portal.status = Event.STATUS_ACTIVE
        self.portal.save(update_fields=['status'])
        mobile = sync_event_to_mobile(self.portal)
        sync_judge_to_mobile_events(self.other)
        self.assertFalse(mobile.assigned_judges.filter(pk=self.other.pk).exists())
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.get('/api/events/judge/dashboard/').json()['assigned_events'], [])

    def test_new_judge_sync_includes_explicit_upcoming_assignments(self):
        mobile = sync_event_to_mobile(self.portal)
        self.portal.assigned_judges.add(self.other)
        sync_judge_to_mobile_events(self.other)
        self.assertTrue(mobile.assigned_judges.filter(pk=self.other.pk).exists())
        self.api.force_authenticate(self.other)
        self.assertEqual(len(self.api.get('/api/events/judge/dashboard/').json()['assigned_events']), 1)

    def test_removal_hides_event_even_with_stale_mirror(self):
        mobile = sync_event_to_mobile(self.portal)
        self.portal.assigned_judges.remove(self.judge)
        self.assertTrue(mobile.assigned_judges.filter(pk=self.judge.pk).exists())
        with read_only_queries():
            self.assertEqual(self.api.get('/api/events/judge/dashboard/').json()['assigned_events'], [])
            self.assertEqual(self.api.get('/api/events/judge/assignments/').json()['assignments'], [])
            self.assertEqual(self.api.get(f'/api/events/judge/assignments/{mobile.pk}/').status_code, 404)
        sync_event_to_mobile(self.portal)
        self.assertFalse(mobile.assigned_judges.exists())

    def test_active_event_without_assignees_does_not_assign_every_judge(self):
        self.portal.assigned_judges.clear()
        self.portal.status = Event.STATUS_ACTIVE
        self.portal.save(update_fields=['status'])
        mobile = sync_event_to_mobile(self.portal)
        self.assertFalse(mobile.assigned_judges.exists())

    def test_sync_with_mobile_only_required_image_url_column(self):
        # Reproduce the live shared DB drift omitted by clean migration tests.
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE events_judgingevent ADD COLUMN image_url varchar(500) NOT NULL DEFAULT ''")
            cursor.execute("ALTER TABLE events_judgingevent ALTER COLUMN image_url DROP DEFAULT")
        mobile = sync_event_to_mobile(self.portal)
        self.portal.refresh_from_db()
        self.assertEqual(self.portal.judging_event_id, mobile.pk)
        with connection.cursor() as cursor:
            cursor.execute('SELECT image_url FROM events_judgingevent WHERE id = %s', [mobile.pk])
            self.assertEqual(cursor.fetchone()[0], '')
        response = self.api.get('/api/events/judge/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['assigned_events'][0]['portal_event_id'], self.portal.pk)
