from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from urllib.parse import parse_qs, urlsplit

from events.models import Event
from events.superadmin_service import dashboard_distributions


class SuperadminDashboardTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('owner', 'owner@example.test', 'test-password')

    def test_event_monitoring_filters_summary_progress_and_export(self):
        today = timezone.localdate()
        Event.objects.create(name='=Unsafe spreadsheet title', event_date=today, scoring_method='match',
                             status='completed', event_classification='major')
        Event.objects.create(name='Vocal Duet', event_date=today, scoring_method='criteria',
                             status='active', event_classification='minor')
        self.client.force_login(self.admin)
        url = reverse('superadmin_event_monitoring')
        response = self.client.get(url, {'classification': 'major', 'period': 'month'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_count'], 1)
        self.assertEqual(response.context['rows'][0]['progress'], 100)
        cards = {card['key']: card for card in response.context['summary']}
        self.assertEqual(cards['total']['count'], 2)
        self.assertEqual(cards['criteria']['count'], 1)
        self.assertContains(response, '<progress max="100" value="100"')
        self.assertNotContains(response, 'Vocal Duet')
        export = self.client.get(url, {'classification': 'major', 'period': 'month', 'export': 'csv'})
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn("'=Unsafe spreadsheet title", export.content.decode())
        self.assertNotIn('Vocal Duet', export.content.decode())

    def test_event_monitoring_export_is_protected(self):
        response = self.client.get(reverse('superadmin_event_monitoring'), {'export': 'csv'})
        self.assertEqual(response.status_code, 302)

    def test_user_management_summary_stays_global_when_filtered(self):
        judge_group = Group.objects.create(name='Judge')
        for number in range(12):
            user = get_user_model().objects.create_user(f'judge{number}', is_active=number != 0)
            user.groups.add(judge_group)
        self.client.force_login(self.admin)
        response = self.client.get(reverse('superadmin_users'), {'role': 'judge', 'page': 2, 'sort': 'name', 'dir': 'asc'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['summary'], {'total': 13, 'admins': 1, 'judges': 12, 'active': 12})
        self.assertEqual(response.context['total_count'], 12)
        self.assertEqual(len(response.context['rows']), 2)
        header = response.context['sort_headers'][0]
        query = parse_qs(urlsplit(header['url']).query)
        self.assertEqual(query['role'], ['judge'])
        self.assertEqual(query['dir'], ['desc'])
        self.assertNotIn('page', query)
        self.assertEqual(header['aria_sort'], 'ascending')
        self.assertContains(response, 'aria-current="page">2')
        self.assertContains(response, 'id="sa-open-create"')
        self.assertContains(response, 'sa-reset', count=None)

    def test_user_management_search_and_sort_urls_escape_input(self):
        self.client.force_login(self.admin)
        query = '<script>A & B</script>'
        response = self.client.get(reverse('superadmin_users'), {'q': query})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No users found.')
        self.assertNotContains(response, query)
        for header in response.context['sort_headers']:
            self.assertEqual(parse_qs(urlsplit(header['url']).query)['q'], [query])

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
