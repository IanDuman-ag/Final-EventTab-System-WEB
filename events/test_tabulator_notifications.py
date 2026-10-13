from datetime import date
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from .models import Event


class TabulatorNotificationTests(TestCase):
    def setUp(self):
        users=get_user_model()
        self.tabulator=users.objects.create_user(username='notification-tab')
        self.tabulator.groups.add(Group.objects.create(name='Tabulator'))
        self.other=users.objects.create_user(username='other-tab')
        self.other.groups.add(Group.objects.get(name='Tabulator'))
        self.viewer=users.objects.create_user(username='notification-viewer')
        self.match=Event.objects.create(name='Assigned Match',category='Sports',scoring_method='match',event_date=date.today(),venue='Court')
        self.criteria=Event.objects.create(name='Assigned Criteria',category='Special Event',scoring_method='criteria',event_date=date.today(),venue='Hall')
        self.private=Event.objects.create(name='Private Other Event',category='Sports',scoring_method='match',event_date=date.today(),venue='Private')
        self.match.assigned_tabulators.add(self.tabulator)
        self.criteria.assigned_tabulators.add(self.tabulator)
        self.private.assigned_tabulators.add(self.other)
        self.url=reverse('tabulator_notifications')

    def test_assignments_scoped_to_current_user(self):
        self.client.force_login(self.tabulator)
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        self.assertTemplateUsed(response,'tabulator/tabnotification.html')
        self.assertContains(response,'Assigned Match')
        self.assertContains(response,'Assigned Criteria')
        self.assertNotContains(response,'Private Other Event')
        self.assertEqual({event['id'] for event in response.context['events']},{self.match.pk,self.criteria.pk})
        self.assertContains(response,reverse('tabulator_dashboard')+'?event_id='+str(self.match.pk))

    def test_empty_and_removed_assignments(self):
        self.client.force_login(self.tabulator)
        self.match.assigned_tabulators.remove(self.tabulator)
        self.criteria.assigned_tabulators.remove(self.tabulator)
        response=self.client.get(self.url)
        self.assertEqual(response.context['events'],[])
        self.assertNotContains(response,'Assigned Match')
        self.assertNotContains(response,'Assigned Criteria')
        self.assertEqual(response.status_code,200)

    def test_authentication_role_and_read_only(self):
        self.assertEqual(self.client.get(self.url).status_code,302)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.url).status_code,302)
        self.client.force_login(self.tabulator)
        self.assertEqual(self.client.post(self.url).status_code,405)

    def test_bell_shows_assignment_links_in_dashboard(self):
        self.client.force_login(self.tabulator)
        response=self.client.get(reverse('tabulator_dashboard'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,reverse('tabulator_notifications'))
        self.assertNotContains(response,'Private Other Event')
