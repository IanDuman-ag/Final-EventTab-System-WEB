"""Regression tests for optional subcriteria in the legacy judge API."""
from datetime import date, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from .judging_serializers import JudgingEventDetailSerializer
from .mobile_serializers import MobileJudgingEventSerializer
from .mobile_sync import sync_event_to_mobile
from .models import (Candidate, Criterion, CriterionSubcriterion, Event,
                     EventCategory, JudgeScore, JudgingEvent)


class MobileSubcriteriaTests(TestCase):
    def setUp(self):
        self.judge = get_user_model().objects.create_user(username='child-score-judge', password='test-password')
        judge_group, _ = Group.objects.get_or_create(name='Judge')
        self.judge.groups.add(judge_group)
        category = EventCategory.objects.create(name='Vocal Performance', category_type='socio_cultural')
        self.event = JudgingEvent.objects.create(
            title='Vocal Duet', category=category, date=date.today(),
            time=time(9, 0), venue='Hall', status='active',
        )
        self.event.assigned_judges.add(self.judge)
        self.candidate = Candidate.objects.create(event=self.event, number=1, name='John + Maria')
        self.criterion = Criterion.objects.create(
            event=self.event, name='Tone and Technique', max_score=Decimal('30.00'),
            weight_percent=Decimal('30.0'), order=1,
        )
        self.tone = CriterionSubcriterion.objects.create(
            criterion=self.criterion, name='Tone', max_score=Decimal('15.00'), display_order=1,
        )
        self.technique = CriterionSubcriterion.objects.create(
            criterion=self.criterion, name='Technique', max_score=Decimal('15.00'), display_order=2,
        )
        self.url = reverse('judgingevent-submit-scores', args=[self.event.id])
        self.client.force_login(self.judge)

    def _submit(self, score):
        return self.client.post(
            self.url, data={'candidate_id': self.candidate.id, 'scores': [score]},
            content_type='application/json',
        )

    def test_subcriterion_scores_set_parent_subtotal_and_weight_once(self):
        response = self._submit({
            'criterion_id': self.criterion.id,
            'subcriteria': [
                {'subcriterion_id': self.tone.id, 'score': '13.25'},
                {'subcriterion_id': self.technique.id, 'score': '14.75'},
            ],
        })
        self.assertEqual(response.status_code, 200, response.content)
        parent = JudgeScore.objects.get(judge=self.judge, candidate=self.candidate, criterion=self.criterion)
        self.assertEqual(parent.score, Decimal('28.00'))
        self.assertEqual(list(parent.subcriterion_scores.values_list('score', flat=True)),
                         [Decimal('13.25'), Decimal('14.75')])
        self.assertEqual(response.json()['breakdown'][0]['weighted_score'], 28)
        self.assertEqual(response.json()['total_score'], 28)

        saved = self.client.get(reverse('judgingevent-my-scores', args=[self.event.id]),
                                {'candidate_id': self.candidate.id})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(len(saved.json()[0]['subcriterion_scores']), 2)

    def test_parent_only_and_invalid_child_scores_are_rejected_without_writes(self):
        for score in (
            {'criterion_id': self.criterion.id, 'score': 28},
            {'criterion_id': self.criterion.id, 'subcriteria': [
                {'subcriterion_id': self.tone.id, 'score': 13}]},
            {'criterion_id': self.criterion.id, 'subcriteria': [
                {'subcriterion_id': self.tone.id, 'score': 16},
                {'subcriterion_id': self.technique.id, 'score': 14}]},
            {'criterion_id': self.criterion.id, 'subcriteria': [
                {'subcriterion_id': self.tone.id, 'score': '13.251'},
                {'subcriterion_id': self.technique.id, 'score': 14}]},
        ):
            with self.subTest(score=score):
                self.assertEqual(self._submit(score).status_code, 400)
                self.assertFalse(JudgeScore.objects.exists())

    def test_simple_criterion_keeps_direct_score_payload(self):
        self.criterion.subcriteria.all().delete()
        response = self._submit({'criterion_id': self.criterion.id, 'score': '27.50'})
        self.assertEqual(response.status_code, 200, response.content)
        parent = JudgeScore.objects.get(criterion=self.criterion)
        self.assertEqual(parent.score, Decimal('27.50'))
        self.assertFalse(parent.subcriterion_scores.exists())

    def test_mobile_detail_serializers_expose_nested_definitions(self):
        for serializer in (JudgingEventDetailSerializer, MobileJudgingEventSerializer):
            with self.subTest(serializer=serializer.__name__):
                criterion = serializer(self.event).data['criteria'][0]
                self.assertEqual([child['name'] for child in criterion['subcriteria']],
                                 ['Tone', 'Technique'])
                self.assertEqual(criterion['weight_percent'], '30.0')

    def test_sync_exposes_children_and_keeps_scored_mobile_rows(self):
        portal = Event.objects.create(
            name='Synced Duet', category='Vocal Performance', event_date=date.today(),
            venue='Hall', scoring_method='criteria',
            judging_criteria_config=[{
                'name': 'Tone and Technique', 'weight': 100, 'max_score': 30,
                'subcriteria': [
                    {'name': 'Tone', 'max_score': 15},
                    {'name': 'Technique', 'max_score': 15},
                ],
            }],
        )
        mobile = sync_event_to_mobile(portal)
        synced = mobile.criteria.get()
        self.assertEqual(list(synced.subcriteria.values_list('name', flat=True)), ['Tone', 'Technique'])
        candidate = mobile.candidates.first()
        JudgeScore.objects.create(judge=self.judge, candidate=candidate, criterion=synced, score=28)
        portal.judging_criteria_config = [{'name': 'Replacement', 'weight': 100, 'max_score': 100}]
        portal.save(update_fields=['judging_criteria_config'])
        sync_event_to_mobile(portal)
        self.assertEqual(mobile.criteria.get().id, synced.id)
        self.assertEqual(mobile.criteria.get().subcriteria.count(), 2)
