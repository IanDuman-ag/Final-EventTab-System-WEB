from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from events import test_mobile_submit as fixtures
from events.test_mobile_api import read_only_queries
from events.models import AuditLog, JudgeActivityLog, JudgeScore, CriteriaScoreSubmission
from events.monitoring_activity import participant_judging_activity


class ParticipantActivityTests(TestCase):
    def setUp(self):
        fixtures.MobileSubmitTests.setUp(self)

    def judge_rows(self):
        return [row for row in participant_judging_activity(self.portal) if row['judge']=='judge-57']

    def open_candidate(self, index=0, rid='prelim'):
        return JudgeActivityLog.log(judge=self.judges[-1], action=JudgeActivityLog.ACTION_VIEW_SCORES,
            event=self.mobile, candidate=self.candidates[index],
            details='Started scoring contestant for monitoring.',
            metadata={'judging_event_id':self.mobile.pk, 'candidate_id':self.candidates[index].pk,'round_id':rid})

    def test_opened_participant_is_visible_and_monitoring_does_not_write_scores(self):
        self.open_candidate()
        before=list(JudgeScore.objects.order_by('pk').values())
        with read_only_queries(): rows=self.judge_rows()
        self.assertEqual(rows[0]['participant'],'Participant 1')
        self.assertEqual(rows[0]['state'],'opened')
        self.assertEqual(rows[0]['status'],'Scoring opened')
        self.assertEqual(rows[1]['state'],'pending')
        self.assertEqual(list(JudgeScore.objects.order_by('pk').values()),before)
        self.assertNotIn('score',rows[0])

    def test_latest_opened_participant_replaces_previous_and_expires(self):
        self.open_candidate(0); latest=self.open_candidate(1)
        rows=self.judge_rows()
        self.assertEqual([row['number'] for row in rows if row['state']=='opened'],[2])
        old=timezone.now()-timedelta(minutes=3)
        AuditLog.objects.filter(action='Started Scoring Contestant').update(created_at=old)
        JudgeActivityLog.objects.filter(action=JudgeActivityLog.ACTION_VIEW_SCORES).update(timestamp=old)
        self.assertFalse(any(row['state']=='opened' for row in self.judge_rows()))

    def test_foreign_round_opening_does_not_appear_in_current_round(self):
        self.open_candidate(rid='final')
        self.assertFalse(any(row['state']=='opened' for row in self.judge_rows()))

    def test_submission_state_is_participant_specific_and_reuses_mobile_projection(self):
        self.open_candidate()
        response=self.api.post(self.url,self.payload,format='json')
        self.assertEqual(response.status_code,200,response.content)
        rows=self.judge_rows()
        self.assertEqual(rows[0]['state'],'submitted')
        self.assertEqual(rows[1]['state'],'pending')
        self.assertEqual(rows[0]['round'],'Preliminary Round')

    def test_normalized_partial_draft_is_visible_without_legacy_scores(self):
        definition=self.portal.scoring_categories.first().criteria.first()
        entry_id=int(next(iter(self.portal.result_processing_config['mobile_round_mapping']['candidates'])))
        CriteriaScoreSubmission.objects.create(event=self.portal,assigned_round_id='prelim',
            judge=self.judges[-1],participant_type='entry',participant_id=entry_id,category=definition.category,
            criterion=definition,status='DRAFT',raw_score=80,normalized_score=40,is_active=True)
        rows=self.judge_rows()
        self.assertEqual(rows[0]['state'],'draft')
        self.assertEqual(rows[0]['status'],'Saved draft')

    def test_superadmin_page_is_private_uncached_and_contains_participant_panel(self):
        owner=get_user_model().objects.create_superuser('monitor-owner','owner@example.test','Owner!78321')
        url=reverse('superadmin_event_detail',args=[self.portal.pk])
        self.assertEqual(self.client.get(url).status_code,302)
        self.client.force_login(owner)
        response=self.client.get(url)
        self.assertContains(response,'Participant Scoring Activity')
        self.assertContains(response,'Participant 1')
        self.assertIn('no-store',response['Cache-Control'])
