from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from events.superadmin_service import dashboard_distributions


class SuperadminDashboardTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('owner', 'owner@example.test', 'test-password')

    def test_distributions_count_each_user_once_and_require_official_publication(self):
        user = get_user_model().objects.create_user('multi-role')
        user.groups.add(Group.objects.create(name='Judge'), Group.objects.create(name='Tabulator'))
        faculty = get_user_model().objects.create_user('faculty')
        faculty.groups.add(Group.objects.create(name='Faculty'))
        get_user_model().objects.create_user('other')
        common = dict(event_date=timezone.localdate(), category='Sports', scoring_method='match')
        Event.objects.create(name='Public event without results', status='upcoming', publication_status='published', **common)
        Event.objects.create(name='Approved event', status='completed', publication_status='draft', result_processing_config={'approved': True}, **common)
        Event.objects.create(name='Published results', status='completed', publication_status='published', results_finalized=True,
                             result_processing_config={'published_at': timezone.now().isoformat()}, **common)
        result = dashboard_distributions()
        roles = {row['label']: row['value'] for row in result['roles']}
        self.assertEqual(sum(roles.values()), 4)
        self.assertEqual(roles['Tabulators'], 1)
        self.assertEqual(roles['Judges'], 0)
        self.assertEqual(roles['Faculty'], 1)
        self.assertEqual(sum(row['value'] for row in result['status']), 3)
        self.assertEqual({row['label']: row['value'] for row in result['publication']},
                         {'Pending': 1, 'Approved': 1, 'Published': 1})
        self.client.force_login(self.admin)
        response = self.client.get(reverse('superadmin_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['cards']['published_results'], 1)
        self.assertContains(response, 'data-sa-chart=', count=7)
        self.assertContains(response, 'User activity · last 30 days')
        self.assertContains(response, reverse('superadmin_reports'))

    def test_permissions_and_chart_json_remain_safe(self):
        url = reverse('superadmin_dashboard')
        self.assertEqual(self.client.get(url).status_code, 302)
        member = get_user_model().objects.create_user('member')
        self.client.force_login(member)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.admin)
        Event.objects.create(name='Hostile label', category='</script><script>alert(1)</script>',
                             event_date=timezone.localdate(), status='upcoming')
        response = self.client.get(url)
        self.assertNotContains(response, '</script><script>alert(1)</script>')
        self.assertContains(response, '\\u003C/script\\u003E')
        self.assertContains(response, 'No recent activity.')