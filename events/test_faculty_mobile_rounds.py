"""Round-scoped mobile submissions share faculty review data."""
from datetime import date, time

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from .criteria_mobile_rounds import mobile_round_context
from .models import (CriteriaEventEntry, CriteriaScoreSubmission, Event, EventCategory,
                     EventScoringCategory, EventScoringCriterion, JudgingEvent)


class FacultyMobileRoundTests(TestCase):
    def setUp(self):
        self.judge = get_user_model().objects.create_user(username='round-judge')
        group, _ = Group.objects.get_or_create(name='Judge')
        self.judge.groups.add(group)
        category = EventCategory.objects.create(name='Arts', category_type='academic')
        self.mobile = JudgingEvent.objects.create(title='Dance', category=category, date=date.today(),
                                                 time=time(9), venue='Hall', status='active')
        self.event = Event.objects.create(name='Dance', event_date=date.today(), scoring_method='criteria',
                                         judging_event=self.mobile, rounds_config=[{'id': 'r1'}, {'id': 'r2'}])
        self.entry = CriteriaEventEntry.objects.create(event=self.event, entry_type='individual', display_name='A')
        self.event.participant_ids = [self.entry.id]
        self.event.save()
        self.event.assigned_judges.add(self.judge)
        self.mobile.assigned_judges.add(self.judge)
        for index in (1, 2):
            segment = EventScoringCategory.objects.create(event=self.event, assigned_round_id=f'r{index}',
                name=f'Segment {index}', judge_mode='scoring', display_order=index, overall_weight_percent=100)
            EventScoringCriterion.objects.create(category=segment, name='Technique', weight_percent=100,
                                                  max_score=100, display_order=1)
        self.client.force_login(self.judge)
        self.url = reverse('judgingevent-submit-scores', args=[self.mobile.id])

    def payload(self):
        scoped = mobile_round_context(self.mobile)
        return {'candidate_id': scoped['candidates'][0].id,
                'scores': [{'criterion_id': scoped['criteria'][0].id, 'score': 85}]}

    def test_legacy_payload_writes_normalized_submitted_scores_once(self):
        payload = self.payload()
        response = self.client.post(self.url, payload, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        row = CriteriaScoreSubmission.objects.get()
        self.assertEqual(row.assigned_round_id, 'r1')
        self.assertEqual(row.status, 'SUBMITTED')
        self.assertEqual(row.participant_id, self.entry.id)
        self.assertEqual(self.client.post(self.url, payload, content_type='application/json').status_code, 400)

    def test_locked_round_and_out_of_range_rejected(self):
        payload = self.payload()
        payload['round_id'] = 'r2'
        self.assertEqual(self.client.post(self.url, payload, content_type='application/json').status_code, 400)
        payload.pop('round_id')
        payload['scores'][0]['score'] = 101
        self.assertEqual(self.client.post(self.url, payload, content_type='application/json').status_code, 400)
        self.assertFalse(CriteriaScoreSubmission.objects.exists())

    def test_unassigned_judge_cannot_submit(self):
        payload = self.payload()
        self.event.assigned_judges.clear()
        self.assertEqual(self.client.post(self.url, payload, content_type='application/json').status_code, 403)

    def test_missing_criteria_rejected_without_writes(self):
        payload = self.payload()
        payload['scores'] = []
        self.assertEqual(self.client.post(self.url, payload, content_type='application/json').status_code, 400)
        self.assertFalse(CriteriaScoreSubmission.objects.exists())

    def test_next_round_preserves_previous_mobile_and_normalized_scores(self):
        payload = self.payload()
        self.assertEqual(self.client.post(self.url, payload, content_type='application/json').status_code, 200)
        self.event.refresh_from_db()
        self.event.result_processing_config.update(active_stage_index=1,
            confirmed_stages={'0': {'approved_at': '2026-10-13'}}, qualified_participant_ids={'1': [self.entry.id]})
        self.event.rounds_config[1]['status'] = 'open'
        self.event.save()
        second = self.payload()
        self.assertNotEqual(payload['scores'][0]['criterion_id'], second['scores'][0]['criterion_id'])
        self.assertEqual(self.client.post(self.url, second, content_type='application/json').status_code, 200)
        self.assertEqual(set(CriteriaScoreSubmission.objects.values_list('assigned_round_id', flat=True)), {'r1', 'r2'})
        self.assertEqual(self.mobile.criteria.filter(scores__judge=self.judge).count(), 2)

    def test_advancement_excludes_nonqualifier_and_uses_distinct_criteria(self):
        first = self.payload()
        self.event.result_processing_config.update(active_stage_index=1,
            confirmed_stages={'0': {'approved_at': '2026-10-13'}}, qualified_participant_ids={'1': []})
        self.event.rounds_config[1]['status'] = 'open'
        self.event.save()
        scoped = mobile_round_context(self.mobile)
        self.assertNotEqual(first['scores'][0]['criterion_id'], scoped['criteria'][0].id)
        self.assertEqual(scoped['candidates'], [])
        first['scores'][0]['criterion_id'] = scoped['criteria'][0].id
        self.assertEqual(self.client.post(self.url, first, content_type='application/json').status_code, 400)

    def test_mobile_subcriteria_are_preserved_in_normalized_breakdown(self):
        from .models import EventScoringSubcriterion, CriteriaSubcriterionScore
        definition = EventScoringCriterion.objects.get(category__assigned_round_id='r1')
        for index, name in enumerate(['Tone','Technique'], 1):
            EventScoringSubcriterion.objects.create(criterion=definition,name=name,max_score=50,display_order=index)
        scoped = mobile_round_context(self.mobile)
        children = list(scoped['criteria'][0].subcriteria.all())
        payload = {'candidate_id':scoped['candidates'][0].id,
                   'scores':[{'criterion_id':scoped['criteria'][0].id,
                              'subcriteria':[{'subcriterion_id':child.id,'score':40} for child in children]}]}
        response=self.client.post(self.url,payload,content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(CriteriaSubcriterionScore.objects.count(),2)
        self.assertEqual(CriteriaScoreSubmission.objects.get().raw_score,80)

    def test_duplicate_display_names_get_distinct_stable_candidate_mappings(self):
        for display_order in (1, 2):
            CriteriaEventEntry.objects.create(event=self.event, entry_type='individual', display_name='A', display_order=display_order)
        first=mobile_round_context(self.mobile)
        second=mobile_round_context(self.mobile)
        self.event.refresh_from_db()
        mapping=self.event.result_processing_config['mobile_round_mapping']['candidates']
        self.assertEqual(len(set(mapping.values())),3)
        self.assertEqual([c.id for c in first['candidates']],[c.id for c in second['candidates']])

    def test_existing_first_round_mobile_ids_and_finalized_scores_are_preserved(self):
        from .models import Candidate, Criterion, JudgeScore
        from django.utils import timezone
        definition=EventScoringCriterion.objects.get(category__assigned_round_id='r1')
        self.event.judging_criteria_config=[{'id':f'esc_{definition.pk}','round_id':'r1',
            'name':'Technique','order':1,'max_score':100,'weight':100}]
        self.event.save()
        old_criterion=Criterion.objects.create(event=self.mobile,name='Technique',order=1,max_score=100,weight_percent=100)
        candidate=Candidate.objects.create(event=self.mobile,name='A',number=1)
        JudgeScore.objects.create(judge=self.judge,candidate=candidate,criterion=old_criterion,
            score=82,is_locked=True,submitted_at=timezone.now())
        context=mobile_round_context(self.mobile)
        self.assertEqual(context['criteria'][0].pk,old_criterion.pk)
        self.assertEqual(context['candidates'][0].pk,candidate.pk)
        self.assertEqual(CriteriaScoreSubmission.objects.get().raw_score,82)
        self.assertEqual(CriteriaScoreSubmission.objects.get().status,'SUBMITTED')
        mobile_round_context(self.mobile)
        self.assertEqual(CriteriaScoreSubmission.objects.count(),1)

    def test_authorized_correction_preserves_old_normalized_scores_and_allows_resubmission(self):
        from unittest.mock import patch
        from .faculty_service import return_for_correction
        faculty=get_user_model().objects.create_user(username='correction-faculty')
        faculty.groups.add(Group.objects.get_or_create(name='Faculty')[0])
        self.event.faculty_account=faculty
        self.event.save()
        payload=self.payload()
        self.assertEqual(self.client.post(self.url,payload,content_type='application/json').status_code,200)
        admin=get_user_model().objects.create_user(username='correction-admin',is_staff=True)
        with patch('events.faculty_service._notify_judges'):
            return_for_correction(self.event,admin,[self.judge.pk])
        payload['scores'][0]['score']=88
        response=self.client.post(self.url,payload,content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(CriteriaScoreSubmission.objects.filter(is_active=True).get().raw_score,88)
        self.assertEqual(CriteriaScoreSubmission.objects.filter(is_active=False).get().raw_score,85)


    def test_category_event_list_does_not_expose_unassigned_criteria_events(self):
        other=get_user_model().objects.create_user(username='category-unassigned')
        other.groups.add(Group.objects.get(name='Judge'))
        self.client.force_login(other)
        response=self.client.get(reverse('eventcategory-events',args=[self.mobile.category_id]))
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,self.mobile.title)
        self.client.force_login(self.judge)
        self.assertContains(self.client.get(reverse('eventcategory-events',args=[self.mobile.category_id])),self.mobile.title)
