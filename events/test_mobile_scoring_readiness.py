"""Production-shaped regression: local weights, global order, timestamped cells."""
from datetime import date, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .criteria_scoring import compute_official_criteria_rankings
from .faculty_service import compute_criteria_rankings
from .models import (Candidate, CriteriaEventEntry, CriteriaScoreSubmission, Criterion,
                     Event, EventCategory, EventScoringCategory, EventScoringCriterion,
                     JudgeScore, JudgingEvent)
from .test_mobile_api import read_only_queries


class MobileScoringReadinessTests(TestCase):
    def setUp(self):
        group = Group.objects.create(name='Judge')
        self.judges = [get_user_model().objects.create_user(pk=pk, username=f'judge-{pk}') for pk in (16, 21, 22)]
        for judge in self.judges:
            judge.groups.add(group)
        category = EventCategory.objects.create(name='Pageants')
        self.mobile = JudgingEvent.objects.create(title='Pageants', category=category, date=date.today(),
                                                  time=time(9), status='active')
        self.portal = Event.objects.create(name='Pageants', category='Cultural', event_date=date.today(),
            scoring_method='criteria', status='active', judging_event=self.mobile,
            rounds_config=[{'id': 'prelim', 'name': 'Preliminary', 'status': 'open'},
                           {'id': 'final', 'name': 'Final', 'status': 'locked'}])
        self.portal.assigned_judges.set(self.judges)
        self.mobile.assigned_judges.set(self.judges)
        self.entries, self.candidates, self.definitions, self.criteria = [], [], [], []
        mapping = {'candidates': {}, 'criteria': {}}
        for index in range(8):
            entry = CriteriaEventEntry.objects.create(event=self.portal, entry_type='individual',
                display_name=f'Participant {index + 1}', display_order=index)
            candidate = Candidate.objects.create(event=self.mobile, name=entry.display_name, number=index + 1)
            self.entries.append(entry)
            self.candidates.append(candidate)
            mapping['candidates'][str(entry.pk)] = candidate.pk
        for cat_index, weights in enumerate(((50, 50), (30, 30, 40)), 1):
            category = EventScoringCategory.objects.create(event=self.portal, assigned_round_id='prelim',
                name=f'Category {cat_index}', judge_mode='scoring', display_order=cat_index, overall_weight_percent=50)
            for local_order, weight in enumerate(weights, 1):
                definition = EventScoringCriterion.objects.create(category=category,
                    name=f'Technique {cat_index}-{local_order}', display_order=local_order, max_score=100, weight_percent=weight)
                criterion = Criterion.objects.create(event=self.mobile, name=definition.name,
                    order=len(self.criteria) + 1, max_score=100, weight_percent=weight)
                self.definitions.append(definition)
                self.criteria.append(criterion)
                mapping['criteria'][f'prelim:{definition.pk}'] = criterion.pk
        category = EventScoringCategory.objects.create(event=self.portal, assigned_round_id='final', name='Final Category',
            judge_mode='scoring', display_order=3, overall_weight_percent=100)
        final = EventScoringCriterion.objects.create(category=category, name='Final impact', display_order=1,
                                                     max_score=100, weight_percent=100)
        legacy_final = Criterion.objects.create(event=self.mobile, name=final.name, order=6, max_score=100, weight_percent=100)
        mapping['criteria'][f'final:{final.pk}'] = legacy_final.pk
        self.final = final
        self.portal.participant_ids = [entry.pk for entry in self.entries]
        self.portal.result_processing_config = {'mobile_round_mapping': mapping}
        self.portal.save()
        self.stamp = timezone.now()
        for judge in self.judges:
            for candidate in self.candidates[:4]:
                for criterion in self.criteria:
                    JudgeScore.objects.create(judge=judge, candidate=candidate, criterion=criterion,
                        score=80, submitted_at=self.stamp, is_locked=False, verification_id='HISTORICAL')
        self.api = APIClient()
        self.api.force_authenticate(self.judges[0])
        self.detail_url = f'/api/events/judge/assignments/{self.mobile.pk}/'
        self.submit_url = f'/api/events/judging-events/{self.mobile.pk}/submit_scores/'

    def payload(self, index=4):
        return {'candidate_id': self.candidates[index].pk, 'round_id': 'prelim',
                'scores': [{'criterion_id': row.pk, 'score': 80} for row in self.criteria]}

    def snapshot(self):
        return list(JudgeScore.objects.order_by('pk').values())

    def test_local_weights_and_global_order_are_compatible_without_mutation(self):
        before = self.snapshot()
        with read_only_queries():
            data = self.api.get(self.detail_url).json()
            scores = self.api.get(f'/api/events/judging-events/{self.mobile.pk}/my_scores/?candidate_id={self.candidates[0].pk}').json()
        self.assertTrue(data['can_score'])
        self.assertFalse(data['configuration_required'])
        self.assertEqual([row['weight_percent'] for row in data['criteria']], [25, 25, 15, 15, 20])
        self.assertEqual([row['order'] for row in data['criteria']], [1, 2, 3, 4, 5])
        self.assertEqual([row['category_order'] for row in data['criteria']], [1, 2, 1, 2, 3])
        self.assertEqual(data['participants_scored'], 4)
        self.assertEqual(data['participants_total'], 8)
        self.assertTrue(all(row['is_locked'] for row in scores))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(JudgeScore.objects.count(), 60)
        self.assertFalse(CriteriaScoreSubmission.objects.exists())
        self.assertEqual(list(Criterion.objects.filter(pk__in=[row.pk for row in self.criteria])
                              .order_by('order').values_list('weight_percent', flat=True)), [50, 50, 30, 30, 40])
        self.assertTrue(all(row['final_score'] == 0 for row in compute_criteria_rankings(self.portal)))

    def test_effective_legacy_weights_are_also_compatible(self):
        for row, weight in zip(self.criteria, (25, 25, 15, 15, 20)):
            row.weight_percent = weight
            row.save()
        self.assertTrue(self.api.get(self.detail_url).json()['can_score'])

    def test_submitted_unlocked_scores_cannot_be_overwritten(self):
        before = self.snapshot()
        response = self.api.post(self.submit_url, self.payload(index=0), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(CriteriaScoreSubmission.objects.exists())

    def test_new_scores_apply_category_weight_once_and_preserve_history(self):
        before = self.snapshot()
        weights = list(Criterion.objects.order_by('pk').values_list('pk', 'weight_percent', 'order'))
        # Existing legacy calculator ignores timestamped-unlocked cells, as before.
        historical_rankings = compute_criteria_rankings(self.portal)
        response = self.api.post(self.submit_url, self.payload(), format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['total_score'], 80)
        self.assertEqual([row['weight'] for row in response.json()['breakdown']], [25, 25, 15, 15, 20])
        self.assertEqual(list(JudgeScore.objects.filter(pk__in=[row['id'] for row in before]).order_by('pk').values()), before)
        self.assertEqual(list(Criterion.objects.order_by('pk').values_list('pk', 'weight_percent', 'order')), weights)
        self.assertEqual(CriteriaScoreSubmission.objects.count(), 5)
        self.assertEqual(sum(CriteriaScoreSubmission.objects.values_list('normalized_score', flat=True)), 160)
        # 160 is the sum of category-local subtotals; official engine applies
        # the two category shares once, yielding 80. Old rows are not bridged.
        results = compute_official_criteria_rankings(self.portal, round_id='prelim',
                    participant_ids=[self.entries[4].pk], accepted_statuses=['SUBMITTED'])
        self.assertEqual(results[0]['final_score'], Decimal('80'))
        self.assertFalse(results[0]['complete'])  # Other Judges have not submitted this participant.
        legacy = next(row for row in compute_criteria_rankings(self.portal) if row['candidate_id'] == self.candidates[4].pk)
        self.assertEqual(legacy['final_score'], 160)  # Legacy absolute-weight interpretation stays unchanged.
        self.assertEqual(self.api.post(self.submit_url, self.payload(), format='json').status_code, 200)
        self.assertEqual(CriteriaScoreSubmission.objects.count(), 5)
        self.assertEqual(len(historical_rankings), 8)

    def test_incompatible_weight_or_alias_mapping_stays_closed_in_get_and_post(self):
        self.criteria[0].weight_percent = 70
        self.criteria[0].save()
        before = self.snapshot()
        self.assertFalse(self.api.get(self.detail_url).json()['can_score'])
        self.assertEqual(self.api.post(self.submit_url, self.payload(), format='json').status_code, 400)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(CriteriaScoreSubmission.objects.exists())
        self.criteria[0].weight_percent = 50
        self.criteria[0].save()
        mapping = self.portal.result_processing_config['mobile_round_mapping']['criteria']
        mapping[f'prelim:{self.definitions[1].pk}'] = self.criteria[0].pk
        self.portal.save()
        self.assertFalse(self.api.get(self.detail_url).json()['can_score'])

    def test_category_totals_must_be_valid(self):
        category = self.portal.scoring_categories.get(name='Category 1')
        category.overall_weight_percent = 40
        category.save()
        self.assertFalse(self.api.get(self.detail_url).json()['can_score'])

    def test_non_scoreable_or_invalid_eligibility_cannot_submit(self):
        self.portal.participant_ids.append(999999)
        self.portal.save()
        before = self.snapshot()
        self.assertFalse(self.api.get(self.detail_url).json()['can_score'])
        self.assertEqual(self.api.post(self.submit_url, self.payload(), format='json').status_code, 400)
        self.assertEqual(self.snapshot(), before)
        self.portal.participant_ids = [entry.pk for entry in self.entries]
        self.portal.rounds_config[0]['status'] = 'locked'
        self.portal.save()
        self.assertFalse(self.api.get(self.detail_url).json()['can_score'])
        self.assertEqual(self.api.post(self.submit_url, self.payload(), format='json').status_code, 400)
        self.assertFalse(CriteriaScoreSubmission.objects.exists())

    def test_duplicate_global_order_is_not_confused_with_category_local_order(self):
        self.criteria[2].order = 1
        self.criteria[2].save()
        self.assertFalse(self.api.get(self.detail_url).json()['can_score'])

    def test_final_requires_faculty_approval_unlock_and_eligible_participants(self):
        before = self.snapshot()
        self.assertFalse(self.api.get(self.detail_url + '?round_id=final').json()['can_score'])
        payload = {'candidate_id': self.candidates[4].pk, 'round_id': 'final',
                   'scores': [{'criterion_id': self.mobile.criteria.get(name='Final impact').pk, 'score': 90}]}
        self.assertEqual(self.api.post(self.submit_url, payload, format='json').status_code, 400)
        self.portal.rounds_config[1]['status'] = 'open'
        self.portal.result_processing_config['active_stage_index'] = 1
        self.portal.save()
        self.assertFalse(self.api.get(self.detail_url + '?round_id=final').json()['can_score'])
        self.portal.result_processing_config.update(confirmed_stages={'0': {'approved_at': self.stamp.isoformat()}},
            qualified_participant_ids={'1': [self.entries[4].pk]})
        self.portal.save()
        self.assertTrue(self.api.get(self.detail_url + '?round_id=final').json()['can_score'])
        self.assertFalse(self.api.get(self.detail_url + '?round_id=prelim').json()['can_score'])
        self.assertEqual(self.api.post(self.submit_url, self.payload(), format='json').status_code, 400)
        payload['candidate_id'] = self.candidates[5].pk
        self.assertEqual(self.api.post(self.submit_url, payload, format='json').status_code, 400)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(CriteriaScoreSubmission.objects.exists())

    def test_unauthorized_judge_cannot_read_or_submit(self):
        self.portal.assigned_judges.remove(self.judges[0])
        before = self.snapshot()
        self.assertEqual(self.api.get(self.detail_url).status_code, 404)
        self.assertEqual(self.api.post(self.submit_url, self.payload(), format='json').status_code, 403)
        self.assertEqual(self.snapshot(), before)

    def test_legacy_only_events_keep_the_existing_lock_contract(self):
        from .mobile_scoring_compatibility import legacy_score_is_submitted
        self.portal.delete()
        score = JudgeScore.objects.first()
        self.assertFalse(legacy_score_is_submitted(score))
        score.is_locked = True
        self.assertTrue(legacy_score_is_submitted(score))
