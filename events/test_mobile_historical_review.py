"""Historical evidence cannot authorize changes to scored events."""
from io import StringIO
import json
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test import TestCase
from django.utils import timezone
from . import test_mobile_configuration as fixtures
from .mobile_configuration import configuration_plan
from .mobile_historical_review import historical_identity_report
from .models import (Candidate, Criterion, CriteriaEventEntryMember, Department,
                     EventScoringSubcriterion, JudgeScore, RegistryCandidate)
from .test_mobile_api import read_only_queries


class HistoricalIdentityTests(TestCase):
    def setUp(self):
        # Explicit fixture owner avoids advancing auth_user's sequence into the
        # existing production-shaped explicit judge IDs in other test classes.
        with patch.object(fixtures, 'get_user_model') as user_model:
            user_model.return_value.objects.create_user.side_effect = lambda **kwargs: get_user_model().objects.create_user(pk=69, **kwargs)
            fixtures.MobileConfigurationTests.setUp(self)
        self.legacy = []
        # Official max=100 is an illustrative mismatch, not a production claim.
        for index, (definition, maximum) in enumerate(zip(self.definitions, (30, 30, 30, 10))):
            definition.weight_percent = maximum
            definition.save()
            self.legacy.append(Criterion.objects.create(pk=1197 + index, event=self.duet,
                name=definition.name, order=index, max_score=maximum, weight_percent=maximum))
        for index, entry in enumerate(self.entries):
            entry.display_name = ('Ianskie + Kirbs', 'Gellyn + Sparrow')[index]
            entry.department = Department.objects.create(name=f'Official Department {index}', code=f'DUET{index}')
            entry.save()
            for position, name in enumerate(entry.display_name.split(' + '), 1):
                member = RegistryCandidate.objects.create(number=f'D{index}-{position}', name=name,
                    department=entry.department)
                CriteriaEventEntryMember.objects.create(entry=entry, individual=member, position=position)
            candidate = Candidate.objects.create(pk=372 + index, event=self.duet, name=entry.display_name,
                department=('Bachelor of Food Industry', 'Bachelor of Prime Association of Future Educators')[index],
                number=index + 1)
            for judge in self.judges[:2]:
                for criterion in self.legacy:
                    JudgeScore.objects.create(judge=judge, candidate=candidate, criterion=criterion,
                        score=24 if criterion.order < 3 else 8, is_locked=judge.pk == 16,
                        submitted_at=timezone.now() if judge.pk == 16 else None)

    def report(self, **kwargs):
        return historical_identity_report(self.event, self.round_id, **kwargs)

    def test_all_sixteen_rows_read_only_preserving_other_events(self):
        before = list(JudgeScore.objects.order_by('pk').values())
        with read_only_queries():
            result = self.report()
        self.assertEqual(result['counts']['legacy_scores'], 16)
        self.assertEqual(result['counts']['normalized_submissions'], 0)
        self.assertEqual({row['judge_id'] for row in result['legacy_scores']}, {16, 21})
        self.assertEqual(sum(row['is_locked'] for row in result['legacy_scores']), 8)
        self.assertEqual(sum(row['submitted_at'] is not None for row in result['legacy_scores']), 8)
        self.assertEqual(len(result['official_pair_comparisons'][0]['members']), 2)
        self.assertFalse(result['assessment']['can_apply'])
        self.assertEqual(list(JudgeScore.objects.order_by('pk').values()), before)
        self.event.refresh_from_db()
        self.assertEqual(self.event.result_processing_config, {})
        self.assertEqual(JudgeScore.objects.filter(candidate__event=self.mobile).count(), 60)

    def test_scale_and_department_mismatches_are_not_approved(self):
        result = self.report()
        comparison = result['official_criterion_comparisons'][0]['comparisons'][0]
        self.assertFalse(comparison['maximum_equal'])
        self.assertFalse(comparison['same_raw_score_preserves_weighted_contribution'])
        self.assertIn('maximum', comparison['runtime_compatibility_issue'])
        pair = result['official_pair_comparisons'][0]['comparisons'][0]
        self.assertTrue(pair['exact_name'])
        self.assertFalse(pair['department_exact'])
        self.assertFalse(pair['identity_verified'])
        self.assertFalse(configuration_plan(self.event)['can_apply'])

    def test_zero_based_global_order_can_be_compatible_without_approving_identity(self):
        for definition, legacy in zip(self.definitions, self.legacy):
            definition.max_score = legacy.max_score
            definition.save()
        result = self.report()
        self.assertTrue(result['assessment']['criterion_mapping_structurally_compatible'])
        self.assertTrue(result['assessment']['same_raw_scores_preserve_legacy_weighted_contributions'])
        self.assertFalse(result['assessment']['mapping_only_verified_safe'])
        self.assertFalse(result['assessment']['can_apply'])
        self.assertEqual(result['official_criterion_comparisons'][0]['category_local_order'], 1)
        self.assertEqual(result['mobile_criteria'][0]['global_order'], 0)
        self.assertFalse(configuration_plan(self.event)['can_apply'])

    def test_duplicate_name_remains_ambiguous(self):
        Candidate.objects.create(event=self.duet, name=self.entries[0].display_name, number=3)
        result = self.report()
        self.assertEqual(len(result['official_pair_comparisons'][0]['comparisons']), 2)
        self.assertFalse(result['assessment']['mapping_only_verified_safe'])

    def test_child_definition_difference_stays_blocked(self):
        for definition, legacy in zip(self.definitions, self.legacy):
            definition.max_score = legacy.max_score
            definition.save()
        EventScoringSubcriterion.objects.create(criterion=self.definitions[0], name='Technique',
            max_score=30, display_order=1)
        result = self.report()
        self.assertEqual(result['official_criterion_comparisons'][0]['children'][0]['name'], 'Technique')
        self.assertFalse(result['assessment']['criterion_mapping_structurally_compatible'])

    def test_category_weight_changes_contribution_even_when_local_weight_matches(self):
        self.category.overall_weight_percent = 50
        self.category.save()
        self.definitions[0].max_score = 30
        self.definitions[0].save()
        result = self.report()
        comparison = result['official_criterion_comparisons'][0]['comparisons'][0]
        self.assertTrue(comparison['weight_matches_category_local'])
        self.assertFalse(comparison['same_raw_score_preserves_weighted_contribution'])
        self.assertFalse(result['assessment']['criterion_mapping_structurally_compatible'])

    def test_private_values_require_explicit_opt_in_and_no_apply_option_exists(self):
        self.assertNotIn('raw_score', json.dumps(self.report()['legacy_scores']))
        self.assertTrue(all('raw_score' in row for row in self.report(include_score_values=True)['legacy_scores']))
        output = StringIO()
        with read_only_queries():
            call_command('inspect_judge_mobile_history', judging_event_id=64, round_id=self.round_id, stdout=output)
        self.assertFalse(json.loads(output.getvalue())['score_values_included'])
        with self.assertRaises(CommandError):
            call_command('inspect_judge_mobile_history', judging_event_id=64, round_id='wrong', stdout=StringIO())
        with self.assertRaises(TypeError):
            call_command('inspect_judge_mobile_history', judging_event_id=64, round_id=self.round_id, apply=True)

    def test_missing_owner_is_not_hidden_by_inner_join(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Deferred PostgreSQL constraint fixture')
        with transaction.atomic():
            row = JudgeScore.objects.filter(candidate__event=self.duet).first()
            JudgeScore.objects.filter(pk=row.pk).update(judge_id=999999)
            result = self.report()
            self.assertEqual(len(result['legacy_scores']), 16)
            self.assertFalse(next(item for item in result['legacy_scores'] if item['id'] == row.pk)['owner_exists'])
            transaction.set_rollback(True)

    def test_unknown_postgresql_columns_are_fingerprinted_without_disclosure(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row JSON')
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
                cursor.execute("ALTER TABLE events_judgescore ADD COLUMN review_note text NOT NULL DEFAULT 'private-review'")
                cursor.execute("ALTER TABLE events_judgescore ADD COLUMN approval_status text NOT NULL DEFAULT 'pending'")
            first = self.report()
            self.assertEqual(first['snapshots'], self.report()['snapshots'])
            self.assertNotIn('private-review', json.dumps(first, default=str))
            with connection.cursor() as cursor:
                cursor.execute("UPDATE events_judgescore SET review_note = 'changed-fixture' WHERE candidate_id = 372")
            self.assertNotEqual(first['snapshots']['judge_scores_full_rows_sha256'], self.report()['snapshots']['judge_scores_full_rows_sha256'])
            transaction.set_rollback(True)
