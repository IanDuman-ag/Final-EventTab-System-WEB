import json
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from events.models import Event


class AdminEventBulkDeleteTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user('bulk-admin', is_staff=True)
        self.client.force_login(self.admin)
        self.url = reverse('admin_bulk_delete_events')

    def event(self, name, kind='match'):
        return Event.objects.create(name=name, category='Sports' if kind == 'match' else 'Arts', scoring_method=kind, event_date=timezone.localdate())

    def post(self, ids, kind):
        return self.client.post(self.url, json.dumps({'ids': ids, 'event_type': kind}), content_type='application/json')

    def test_deletes_selected_events_and_preserves_unselected_and_other_type(self):
        for kind in ('match', 'criteria'):
            first, second = self.event('First', kind), self.event('Second', kind)
            untouched = self.event('Keep', kind)
            response = self.post([first.pk, second.pk, first.pk], kind)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['deleted_count'], 2)
            self.assertTrue(Event.objects.filter(pk=untouched.pk).exists())
            self.assertFalse(Event.objects.filter(pk__in=[first.pk, second.pk]).exists())

    def test_rejects_cross_type_and_malformed_requests_without_deleting(self):
        match, criteria = self.event('Match'), self.event('Criteria', 'criteria')
        for payload in ([], {'ids': [match.pk, criteria.pk], 'event_type': 'match'}, {'ids': [True], 'event_type': 'match'}, {'ids': ['bad'], 'event_type': 'match'}, {'ids': [], 'event_type': 'criteria'}):
            response = self.client.post(self.url, json.dumps(payload), content_type='application/json')
            self.assertEqual(response.status_code, 400)
        self.assertEqual(Event.objects.count(), 2)

    def test_rollback_on_protected_records(self):
        first, second = self.event('First'), self.event('Second')
        with patch('core.views.delete_mobile_for_event', side_effect=[None, IntegrityError('protected')]):
            response = self.post([first.pk, second.pk], 'match')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Event.objects.count(), 2)

    def test_staff_post_and_csrf_are_required(self):
        event = self.event('Keep')
        self.assertNotEqual(self.client.get(self.url).status_code, 200)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.admin)
        self.assertEqual(protected.post(self.url, json.dumps({'ids': [event.pk]}), content_type='application/json').status_code, 403)
        user = get_user_model().objects.create_user('viewer')
        self.client.force_login(user)
        self.assertEqual(self.post([event.pk], 'match').status_code, 302)
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())

    def test_criteria_select_all_includes_filtered_events_across_pages(self):
        events = [self.event('Chosen ' + str(index), 'criteria') for index in range(12)]
        other = self.event('Other', 'criteria')
        response = self.client.get(reverse('admin_criteria_events'), {'q': 'Chosen'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context['bulk_event_ids']), {event.pk for event in events})
        self.assertNotIn(other.pk, response.context['bulk_event_ids'])
        self.assertEqual(len(response.context['event_rows']), 10)
        self.assertContains(response, 'events-select-all')
