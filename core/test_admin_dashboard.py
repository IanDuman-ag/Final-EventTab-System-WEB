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

    def test_classification_progress_requires_finalized_results(self):
        Event.objects.create(name='Finalized major', event_date=timezone.localdate(), event_classification='major', results_finalized=True)
        Event.objects.create(name='Unfinalized major', event_date=timezone.localdate(), event_classification='major', status='completed')
        Event.objects.create(name='Minor', event_date=timezone.localdate(), event_classification='minor')
        response = self.client.get(reverse('admin_dashboard'))
        major, minor = response.context['classification_progress']
        self.assertEqual((major['completed'], major['total'], major['percent']), (1, 2, 50))
        self.assertEqual((minor['remaining'], minor['percent']), (1, 0))
        self.assertEqual(response.context['classification_remaining'], 2)
        self.assertContains(response, 'Major and Minor Event Progress')
        self.assertContains(response, 'Judge Submissions')
        self.assertNotContains(response, 'categorySort')

    def test_judge_submission_counts_require_complete_finalized_sheets(self):
        from events.models import EventScoringCategory, EventScoringCriterion, CriteriaScoreSubmission
        judge = get_user_model().objects.create_user('overview-judge')
        event = Event.objects.create(name='Judged event', event_date=timezone.localdate(), scoring_method='criteria')
        entries = [CriteriaEventEntry.objects.create(event=event, entry_type='team', display_name=f'Entry {i}', display_order=i) for i in range(2)]
        event.participant_ids = [entry.pk for entry in entries]
        event.save()
        event.assigned_judges.add(judge)
        category = EventScoringCategory.objects.create(event=event, assigned_round_id='main', name='Performance', purpose='official', judge_mode='scoring', overall_weight_percent=100, display_order=0)
        criterion = EventScoringCriterion.objects.create(category=category, name='Skill', weight_percent=100, max_score=100, display_order=0)
        rows = [CriteriaScoreSubmission.objects.create(event=event, assigned_round_id='main', category=category, criterion=criterion, judge=judge, participant_id=entry.pk, source='MOBILE', status='DRAFT', raw_score=90) for entry in entries]
        def counts():
            return self.client.get(reverse('admin_dashboard')).context['judge_submissions']
        self.assertEqual(counts(), {'submitted': 0, 'expected': 1, 'pending': 1, 'percent': 0})
        rows[0].status = 'SUBMITTED'
        rows[0].save()
        self.assertEqual(counts()['submitted'], 0)
        rows[1].status = 'SUBMITTED'
        rows[1].save()
        self.assertEqual(counts(), {'submitted': 1, 'expected': 1, 'pending': 0, 'percent': 100})
