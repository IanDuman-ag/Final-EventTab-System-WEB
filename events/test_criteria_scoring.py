from datetime import date, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from .criteria_scoring import compute_official_criteria_rankings, normalized_criterion_score
from .models import (
    Candidate, CriteriaScoreSubmission, Criterion, Event, EventCategory,
    EventScoringCategory, EventScoringCriterion, JudgeScore, JudgingEvent,
    RegistryCandidate,
)
from .mobile_sync import sync_event_to_mobile


class NormalizedCriteriaScoringTests(TestCase):
    def setUp(self):
        self.judge = get_user_model().objects.create_user(username='criteria-judge')
        self.participant = RegistryCandidate.objects.create(name='Performer', number='1')
        self.event = Event.objects.create(
            name='Singing', category='Special Event', event_date=date.today(),
            venue='Hall', scoring_method='criteria', participation_type='individual',
            participant_ids=[self.participant.id],
        )
        self.event.assigned_judges.add(self.judge)
        self.category = EventScoringCategory.objects.create(
            event=self.event, assigned_round_id='main', name='Performance',
            judge_mode='scoring', display_order=1, overall_weight_percent=100,
        )
        self.criterion = EventScoringCriterion.objects.create(
            category=self.category, name='Delivery', weight_percent=100,
            min_score=0, max_score=100, display_order=1,
        )

    def score(self, **overrides):
        values = dict(
            event=self.event, assigned_round_id='main', category=self.category,
            criterion=self.criterion, judge=self.judge, participant_id=self.participant.id,
            source='MOBILE', status='APPROVED', raw_score=80,
        )
        values.update(overrides)
        return CriteriaScoreSubmission.objects.create(**values)

    def rankings(self):
        return compute_official_criteria_rankings(self.event, round_id='main')

    def test_decimal_normalization_and_invalid_values(self):
        self.assertEqual(normalized_criterion_score('8.5', '10', '30'), Decimal('25.5'))
        for values in [('NaN', 100, 100), (90, 0, 100), (101, 100, 100), (-1, 100, 100)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                normalized_criterion_score(*values)

    def test_only_approved_active_scores_contribute(self):
        row = self.score(status='SUBMITTED')
        for status in ['DRAFT', 'SUBMITTED', 'NEEDS_REVIEW', 'VERIFIED', 'REOPENED', 'REJECTED', 'SUPERSEDED']:
            row.status = status
            row.save()
            self.assertEqual(self.rankings(), [])
        row.status = 'APPROVED'
        row.save()
        self.assertEqual(self.rankings()[0]['final_score'], Decimal(80))
        row.is_active = False
        row.save()
        self.assertEqual(self.rankings(), [])

    def test_mobile_and_ocr_duplicate_constraint(self):
        self.score()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.score(source='OCR')

    def test_multiple_judges_are_averaged(self):
        self.score()
        second = get_user_model().objects.create_user(username='second-judge')
        self.event.assigned_judges.add(second)
        self.score(judge=second, source='OCR', raw_score=90)
        self.assertEqual(self.rankings()[0]['final_score'], Decimal(85))
        self.assertTrue(self.rankings()[0]['complete'])

    def test_unassigned_judge_is_excluded(self):
        self.score()
        self.event.assigned_judges.clear()
        self.assertEqual(self.rankings(), [])

    def test_inactive_participant_is_excluded(self):
        self.score()
        self.participant.status = 'inactive'
        self.participant.save()
        self.assertEqual(self.rankings(), [])

    def test_special_award_is_excluded(self):
        self.score()
        award = EventScoringCategory.objects.create(
            event=self.event, assigned_round_id='main', name='Audience Award',
            purpose='special_award', judge_mode='scoring', display_order=2,
            overall_weight_percent=0,
        )
        criterion = EventScoringCriterion.objects.create(
            category=award, name='Impact', weight_percent=100, max_score=100, display_order=1,
        )
        self.score(category=award, criterion=criterion, raw_score=100)
        self.assertEqual(self.rankings()[0]['final_score'], Decimal(80))

    def test_mobile_sync_preserves_existing_score_rows(self):
        mobile_category = EventCategory.objects.create(name='Cultural')
        mobile = JudgingEvent.objects.create(
            title='Singing', category=mobile_category, date=date.today(),
            time=time(9), venue='Hall',
        )
        candidate = Candidate.objects.create(event=mobile, number=1, name='Performer')
        criterion = Criterion.objects.create(event=mobile, name='Delivery', max_score=100, weight_percent=100)
        score = JudgeScore.objects.create(judge=self.judge, candidate=candidate, criterion=criterion, score=80)
        self.event.judging_event = mobile
        self.event.save()
        sync_event_to_mobile(self.event)
        score.refresh_from_db()
        self.assertEqual(score.candidate_id, candidate.id)
        self.assertEqual(score.criterion_id, criterion.id)
        self.assertEqual(score.score, Decimal(80))
