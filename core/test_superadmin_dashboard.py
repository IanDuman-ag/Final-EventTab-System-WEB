from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from urllib.parse import parse_qs, urlsplit
from datetime import timedelta

from events.models import AdminReportHistory, AuditLog, Event
from events.superadmin_service import dashboard_distributions


class SuperadminDashboardTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('owner', 'owner@example.test', 'test-password')

    def test_notification_feed_is_authenticated_and_role_scoped(self):
        url = reverse('portal_notifications')
        self.assertEqual(self.client.get(url).status_code, 401)
        faculty = get_user_model().objects.create_user('notification-faculty')
        faculty.groups.add(Group.objects.create(name='Faculty'))
        own = Event.objects.create(name='Assigned event', event_date=timezone.localdate(), faculty_account=faculty)
        Event.objects.create(name='Private other event', event_date=timezone.localdate())
        AuditLog.objects.create(action='Private system activity', description='Administrator details')
        self.client.force_login(faculty)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(response.json()['notifications'][0]['url'], reverse('faculty_event_manage', args=[own.pk]))
        self.assertNotContains(response, 'Private')
        self.assertIn('no-store', response['Cache-Control'])
        member = get_user_model().objects.create_user('no-portal-role')
        self.client.force_login(member)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_admin_and_superadmin_notifications_link_to_authorized_pages(self):
        event = Event.objects.create(name='Basketball update', event_date=timezone.localdate(), scoring_method='match')
        staff = get_user_model().objects.create_user('notification-admin', is_staff=True)
        self.client.force_login(staff)
        note = self.client.get(reverse('portal_notifications')).json()['notifications'][0]
        self.assertEqual(note['url'], reverse('admin_view_match_event', args=[event.pk]))
        self.client.force_login(self.admin)
        AuditLog.objects.create(action='Updated <event>', description='Real system change')
        response = self.client.get(reverse('portal_notifications'))
        notes = response.json()['notifications']
        self.assertTrue(any(note['title'] == 'Updated <event>' for note in notes))
        self.assertTrue(any(note['url'] == reverse('superadmin_event_detail', args=[event.pk]) for note in notes))

    def test_notification_feed_empty_state_and_new_updates(self):
        staff = get_user_model().objects.create_user('empty-admin', is_staff=True)
        self.client.force_login(staff)
        url = reverse('portal_notifications')
        self.assertEqual(self.client.get(url).json(), {'notifications': [], 'count': 0})
        event = Event.objects.create(name='New real event', event_date=timezone.localdate())
        self.assertEqual(self.client.get(url).json()['notifications'][0]['title'], event.name)
        for number in range(10):
            Event.objects.create(name=f'Notification event {number}', event_date=timezone.localdate())
        self.assertEqual(self.client.get(url).json()['count'], 8)

    def test_reports_preview_system_wide_exports_and_saved_download(self):
        Event.objects.create(name='Report Test Event', event_date=timezone.localdate(), category='Sports', scoring_method='match')
        self.client.force_login(self.admin)
        generate = reverse('generate_superadmin_report')
        payload = {'report_type': 'summary', 'format': 'pdf', 'action': 'preview'}
        response = self.client.post(generate, payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['preview']['rows'][0][0], 'Report Test Event')
        self.assertFalse(AdminReportHistory.objects.exists())
        with_metrics = self.client.post(generate, {**payload, 'metrics': ['event_summary', 'publication_status']})
        self.assertIn('Publication Status', with_metrics.json()['preview']['headers'])
        self.assertIn('Event Venue', with_metrics.json()['preview']['headers'])
        for fmt, prefix in [('pdf', b'%PDF'), ('excel', b'PK'), ('csv', b'Event Name')]:
            response = self.client.post(generate, {'report_type': 'summary', 'format': fmt})
            self.assertEqual(response.status_code, 200, response.content[:200])
            self.assertTrue(response.content.startswith(prefix))
        self.assertEqual(AdminReportHistory.objects.count(), 3)
        report = AdminReportHistory.objects.first()
        response = self.client.get(reverse('superadmin_reports'), {'download': report.pk})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'Event Name'))
        page = self.client.get(reverse('superadmin_reports'))
        self.assertEqual(page.context['reports_generated_count'], 3)
        self.assertContains(page, 'Reports &amp; Analytics')
        self.assertContains(page, '?download=')
        export = self.client.get(reverse('superadmin_reports'), {'export': 'all'})
        self.assertIn('Report,Created By,Created At,Format,Status', export.content.decode())

    def test_reports_preserve_event_exports_and_validate_requests(self):
        event = Event.objects.create(name='Basketball Report', event_date=timezone.localdate(), category='Sports', scoring_method='match')
        self.client.force_login(self.admin)
        url = reverse('generate_superadmin_report')
        self.assertEqual(self.client.get(url).status_code, 405)
        for payload in [{'report_type': 'invalid'}, {'format': 'invalid'}, {'event': 'Missing Event'},
                        {'start_date': 'bad date'}, {'start_date': '2026-10-20', 'end_date': '2026-10-01'}]:
            self.assertEqual(self.client.post(url, payload).status_code, 400)
        for fmt, prefix in [('pdf', b'%PDF'), ('excel', b'PK')]:
            response = self.client.post(url, {'event': event.name, 'report_type': 'summary', 'format': fmt,
                                              'metrics': ['rankings', 'participants', 'scores']})
            self.assertEqual(response.status_code, 200, response.content[:200])
            self.assertTrue(response.content.startswith(prefix))
        self.client.logout()
        self.assertEqual(self.client.get(reverse('superadmin_reports')).status_code, 302)
        self.assertEqual(self.client.post(url, {'action': 'preview'}).status_code, 302)

    def test_audit_quick_filters_insights_and_pagination_preserve_query(self):
        for number in range(12):
            AuditLog.objects.create(user=self.admin, user_name='owner', user_role='Super Admin',
                                    module='Authentication', action='Logged In',
                                    description='reference audit & review', status='failed' if number < 4 else 'success')
        self.client.force_login(self.admin)
        url = reverse('superadmin_activity_logs')
        response = self.client.get(url, {'q': 'reference audit & review', 'page': 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['filtered_count'], 12)
        self.assertEqual(len(response.context['rows']), 4)
        self.assertEqual(response.context['insight_count'], 12)
        self.assertEqual(response.context['insight_failed'], 4)
        self.assertEqual(response.context['top_modules'][0]['count'], 12)
        self.assertContains(response, 'Showing 9–12 of 12 entries')
        for item in response.context['quick_filters']:
            self.assertEqual(parse_qs(item['query'])['q'], ['reference audit & review'])
            self.assertNotIn('page', parse_qs(item['query']))
        response = self.client.get(url, {'q': 'reference audit & review', 'result': 'failed'})
        self.assertEqual(response.context['filtered_count'], 4)
        self.assertEqual(response.context['success_percent'], 0)
        self.assertEqual(response.context['failed_percent'], 100)
        self.assertContains(response, 'al-quick-failed is-active')
        older = AuditLog.objects.create(action='Logged In', description='reference audit & review')
        AuditLog.objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(days=20))
        recent = self.client.get(url, {'q': 'reference audit & review', 'insights': '7'})
        all_time = self.client.get(url, {'q': 'reference audit & review', 'insights': 'all'})
        self.assertEqual(recent.context['insight_count'], 12)
        self.assertEqual(all_time.context['insight_count'], 13)
        response = self.client.get(url, {'q': 'no matching audit rows', 'insights': 'invalid'})
        self.assertEqual(response.context['insight_count'], 0)
        self.assertEqual(response.context['success_percent'], 0)
        self.assertContains(response, 'No audit logs match your filters.')

    def test_audit_export_keeps_result_filter_and_read_only_protection(self):
        AuditLog.objects.create(action='Logged In', description='reference export success', status='success')
        AuditLog.objects.create(action='Logged In', description='reference export failure', status='failed')
        url = reverse('superadmin_activity_logs')
        self.assertEqual(self.client.get(url, {'export': 'excel'}).status_code, 302)
        self.client.force_login(self.admin)
        response = self.client.get(url, {'q': 'reference export', 'result': 'failed', 'export': 'csv'})
        self.assertIn('reference export failure', response.content.decode())
        self.assertNotIn('reference export success', response.content.decode())
        response = self.client.get(url, {'q': 'reference export', 'result': 'failed', 'export': 'pdf'})
        self.assertTrue(response.content.startswith(b'%PDF'))
        response = self.client.get(url, {'q': 'reference export', 'result': 'failed', 'export': 'excel'})
        self.assertTrue(response.content.startswith(b'PK'))

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
        self.assertContains(response, 'data-sa-chart=', count=5)
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

    def test_live_audit_reads_new_records_and_preserves_filters_without_writing(self):
        self.client.force_login(self.admin)
        url = reverse('superadmin_activity_logs')
        params = {'q': 'Live judge', 'role': 'Judge', 'page': '1'}
        first = self.client.get(url, params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(first.status_code, 200)
        self.assertIn('no-store', first['Cache-Control'])
        self.assertEqual(first.context['filtered_count'], 0)
        AuditLog.objects.create(user_name='Live judge', user_role='Judge',
            action='Submitted Final Score', module='Criteria-Based Event', description='Live judge submitted scores.')
        AuditLog.objects.create(user_name='Other admin', user_role='Admin', action='Updated Event')
        before = AuditLog.objects.count()
        second = self.client.get(url, params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(second.context['filtered_count'], 1)
        self.assertContains(second, 'Live judge submitted scores.')
        self.assertNotContains(second, 'Other admin')
        self.assertEqual(AuditLog.objects.count(), before)
        self.client.logout()
        self.assertEqual(self.client.get(url, params).status_code, 302)
