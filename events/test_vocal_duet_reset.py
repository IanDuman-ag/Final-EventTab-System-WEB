"""Production-shaped, explicitly TEST data; never use production settings."""
import hashlib
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, transaction
from django.test import TestCase

from . import test_mobile_historical_review as fixtures
from .criteria_mobile_rounds import read_mobile_round_context
from .models import (AuditLog, Candidate, Criterion, CriterionSubcriterion, CriteriaScoreSubmission,
                     EventScoringSubcriterion, JudgeScore, JudgeSubcriterionScore)
from .test_mobile_api import read_only_queries
from .vocal_duet_reset import CONFIRMATION, ResetError, _digest, _preimage, apply_reset, reset_plan


class VocalDuetResetTests(TestCase):
    def setUp(self):
        # Explicit score IDs cannot share a sequence with unrelated fixture rows.
        with connection.cursor() as cursor:
            cursor.execute("SELECT setval(pg_get_serial_sequence('events_judgescore','id'), 1, false)")
        fixtures.HistoricalIdentityTests.setUp(self)
        for index, row in enumerate(JudgeScore.objects.filter(candidate__event=self.duet).order_by('pk')):
            JudgeScore.objects.filter(pk=row.pk).update(id=175 + index)
        for definition in self.definitions:
            for order, maximum in enumerate((60, 40), 1):
                EventScoringSubcriterion.objects.create(criterion=definition, name=f'Detail {order}',
                    display_order=order, max_score=maximum)
        self.review = {'verified_by': self.faculty.pk, 'all_16_scores_are_test_data': True,
            'evidence_reference': 'TEST signed operator approval, not a production attestation',
            'criteria': [{'official_id': definition.pk, 'mobile_id': legacy.pk}
                for definition, legacy in zip(self.definitions, self.legacy)],
            'pairs': [{'entry_id': entry.pk, 'mobile_id': 372 + index,
                'member_ids': list(entry.member_links.values_list('individual_id', flat=True)),
                'identity_evidence': 'TEST signed pair member roster',
                'snapshot_discrepancy_explanation': 'TEST obsolete department snapshot verified against roster'}
                for index, entry in enumerate(self.entries)]}
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.backup = self.directory / 'backup.dump'
        # Restore verification is an operator attestation. Local fixture only.
        self.backup.write_bytes(b'PGDMP-local-test-backup-fixture')
        self.backup_hash = hashlib.sha256(self.backup.read_bytes()).hexdigest()
        self.snapshot = self.directory / 'private-preimage.json'
        self.receipt = self.directory / 'restore-receipt.json'

    def plan(self):
        return reset_plan(self.faculty, self.review)

    def apply(self, plan=None, confirmation=CONFIRMATION):
        plan = plan or self.plan()
        self.receipt.write_text(json.dumps({'backup_sha256': self.backup_hash,
            'plan_fingerprint': plan['fingerprint'], 'verified_by': self.faculty.pk,
            'restore_succeeded': True, 'verified_at': '2026-10-16T00:00:00Z',
            'source_database': connection.settings_dict['NAME'],
            'scoped_rows_sha256': _digest(_preimage(plan)['tables']),
            'isolated_restore_database': 'isolated_restore_fixture'}))
        return apply_reset(self.faculty.pk, self.review, plan['fingerprint'], confirmation,
            str(self.backup), self.backup_hash, str(self.receipt), str(self.snapshot))

    def test_preview_is_read_only_exact_and_auditable(self):
        before = list(JudgeScore.objects.order_by('pk').values())
        with read_only_queries():
            plan = self.plan()
        self.assertTrue(plan['can_apply'], plan['blocked_reasons'])
        self.assertEqual(plan['delete_score_ids'], list(range(175, 191)))
        self.assertEqual(len(plan['test_scores']), 16)
        self.assertEqual(len(plan['criteria']), 4)
        self.assertTrue(all(len(item['after']['subcriteria']) == 2 for item in plan['criteria']))
        self.assertEqual(plan['dependent_subscore_ids'], [])
        self.assertEqual(list(JudgeScore.objects.order_by('pk').values()), before)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_apply_preserves_ids_and_pageants_and_makes_read_only_context_ready(self):
        before = list(JudgeScore.objects.filter(candidate__event=self.mobile).order_by('pk').values())
        rounds = self.event.rounds_config
        official = list(self.category.criteria.values())
        result = self.apply()
        self.assertTrue(result['applied'])
        self.assertFalse(JudgeScore.objects.filter(pk__range=(175, 190)).exists())
        self.assertEqual(list(JudgeScore.objects.filter(candidate__event=self.mobile).order_by('pk').values()), before)
        self.assertEqual(list(self.duet.criteria.order_by('pk').values_list('pk', flat=True)), [1197, 1198, 1199, 1200])
        self.assertEqual(list(self.duet.candidates.order_by('pk').values_list('pk', flat=True)), [372, 373])
        self.assertEqual(CriterionSubcriterion.objects.filter(criterion__event=self.duet).count(), 8)
        self.assertEqual(list(self.category.criteria.values()), official)
        self.event.refresh_from_db()
        self.assertEqual(self.event.rounds_config, rounds)
        self.assertFalse(self.event.results_finalized)
        self.assertEqual(CriteriaScoreSubmission.objects.filter(event=self.event).count(), 0)
        with read_only_queries():
            context = read_mobile_round_context(self.duet)
            response = self.api.get(f'/api/events/judge/assignments/{self.duet.pk}/')
        self.assertTrue(context['compatible'])
        self.assertTrue(response.json()['can_score'], response.json())
        self.assertEqual(len(context['candidates']), 2)
        self.assertEqual(AuditLog.objects.exclude(action='Opened Assigned Event').count(), 1)
        self.assertEqual(AuditLog.objects.filter(action='Opened Assigned Event').count(), 1)
        preimage = json.loads(self.snapshot.read_text())
        self.assertEqual(len(preimage['tables']['events_judgescore']), 16)
        self.assertNotIn('raw_score', json.dumps(list(AuditLog.objects.values_list('metadata', flat=True))))

    def test_repaired_subcriteria_support_submission_receipt_and_exact_retry(self):
        self.apply()
        payload = {'candidate_id': 372, 'round_id': self.round_id, 'scores': [
            {'criterion_id': row.pk, 'subcriteria': [{'subcriterion_id': child.pk, 'score': str(child.max_score)}
                for child in row.subcriteria.all()]} for row in self.duet.criteria.all()]}
        url = '/api/events/judging-events/64/submit_scores/'
        response = self.api.post(url, payload, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['total_score'], 100)
        self.assertEqual(self.api.post(url, payload, format='json').json(), response.json())
        self.assertEqual(JudgeScore.objects.filter(judge=self.judges[-1], candidate_id=372).count(), 4)
        self.assertEqual(CriteriaScoreSubmission.objects.filter(event=self.event).count(), 4)
        history = self.api.get('/api/events/judge/score-history/')
        self.assertEqual(history.status_code, 200)
        self.assertNotIn('No scoring history', str(history.json()))

    def test_retry_does_not_delete_or_overwrite_new_scores(self):
        plan = self.plan()
        self.apply(plan)
        JudgeScore.objects.create(judge=self.judges[-1], candidate_id=372, criterion_id=1197, score=80)
        with self.assertRaises(ResetError):
            self.apply(plan)
        self.assertEqual(JudgeScore.objects.filter(candidate__event=self.duet).count(), 1)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_identity_requires_members_evidence_and_department_discrepancy_review(self):
        self.review['pairs'][0]['member_ids'].reverse()
        self.assertFalse(self.plan()['can_apply'])
        self.review['pairs'][0]['member_ids'].reverse()
        del self.review['pairs'][0]['snapshot_discrepancy_explanation']
        self.assertFalse(self.plan()['can_apply'])
        self.review['criteria'] = []
        self.assertFalse(self.plan()['can_apply'])

    def test_wrong_role_or_unassigned_faculty_cannot_preview_or_apply(self):
        with self.assertRaises(ResetError):
            reset_plan(self.judges[-1], self.review)
        other = get_user_model().objects.create_user(pk=101, username='other-faculty')
        other.groups.set(self.faculty.groups.all())
        with self.assertRaises(ResetError):
            reset_plan(other, self.review)
        self.faculty.is_active = False
        self.faculty.save()
        with self.assertRaises(ResetError):
            self.apply()

    def test_staff_admin_requires_their_own_explicit_review(self):
        admin = get_user_model().objects.create_user(pk=102, username='reviewing-admin', is_staff=True)
        self.assertFalse(reset_plan(admin, self.review)['can_apply'])
        self.review['verified_by'] = admin.pk
        self.assertTrue(reset_plan(admin, self.review)['can_apply'])

    def test_matching_existing_child_ids_are_preserved(self):
        for definition, legacy in zip(self.definitions, self.legacy):
            for child in definition.subcriteria.all():
                CriterionSubcriterion.objects.create(criterion=legacy, name=child.name,
                    display_order=child.display_order, max_score=child.max_score)
        ids = list(CriterionSubcriterion.objects.filter(criterion__event=self.duet).values_list('pk', flat=True))
        self.apply()
        self.assertEqual(list(CriterionSubcriterion.objects.filter(criterion__event=self.duet).values_list('pk', flat=True)), ids)

    def test_missing_confirmation_and_backup_tamper_are_blocked(self):
        with self.assertRaises(ResetError):
            self.apply(confirmation='yes')
        plan = self.plan()
        self.backup.write_bytes(b'tampered')
        with self.assertRaises(ResetError):
            self.apply(plan)
        self.assertEqual(JudgeScore.objects.filter(candidate__event=self.duet).count(), 16)

    def test_new_score_or_wrong_event_row_blocks_entire_cleanup(self):
        JudgeScore.objects.create(pk=191, judge=self.judges[-1], candidate_id=372, criterion_id=1197, score=10)
        self.assertFalse(self.plan()['can_apply'])
        JudgeScore.objects.filter(pk=191).delete()
        JudgeScore.objects.filter(pk=175).update(criterion=self.criteria[0])
        self.assertFalse(self.plan()['can_apply'])

    def test_normalized_draft_and_published_or_locked_round_block_cleanup(self):
        row = CriteriaScoreSubmission.objects.create(event=self.event, category=self.category, criterion=self.definitions[0],
            assigned_round_id=self.round_id, participant_id=self.entries[0].pk, judge=self.judges[0],
            source='MOBILE', status='DRAFT', raw_score=10)
        self.assertFalse(self.plan()['can_apply'])
        row.delete()
        self.event.results_finalized = True
        self.event.save()
        self.assertFalse(self.plan()['can_apply'])
        self.event.results_finalized = False
        self.event.rounds_config[0]['status'] = 'locked'
        self.event.save()
        self.assertFalse(self.plan()['can_apply'])

    def test_dependent_subscores_block_instead_of_cascading(self):
        child = CriterionSubcriterion.objects.create(criterion=self.legacy[0], name='Historical', max_score=30, display_order=1)
        JudgeSubcriterionScore.objects.create(judge_score_id=175, subcriterion=child, score=10)
        plan = self.plan()
        self.assertFalse(plan['can_apply'])
        self.assertEqual(len(plan['dependent_subscore_ids']), 1)

    def test_unknown_database_dependency_is_reported_and_blocked(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
                cursor.execute('CREATE TABLE vocal_duet_test_dependency (id integer PRIMARY KEY, cell_id integer REFERENCES events_judgescore(id))')
                cursor.execute('INSERT INTO vocal_duet_test_dependency VALUES (1, 175)')
            plan = self.plan()
            self.assertFalse(plan['can_apply'])
            self.assertEqual(next(row for row in plan['dependencies'] if row['table'] == 'vocal_duet_test_dependency')['ids'], [1])
            transaction.set_rollback(True)

    def test_post_validation_failure_rolls_back_cleanup_and_configuration(self):
        before = list(JudgeScore.objects.order_by('pk').values())
        with patch('events.vocal_duet_reset.read_mobile_round_context', return_value=None):
            with self.assertRaises(ResetError):
                self.apply()
        self.assertEqual(list(JudgeScore.objects.order_by('pk').values()), before)
        self.assertFalse(CriterionSubcriterion.objects.filter(criterion__event=self.duet).exists())
        self.assertEqual(AuditLog.objects.count(), 0)
        self.event.refresh_from_db()
        self.assertEqual(self.event.result_processing_config, {})

    def test_stale_official_child_rubric_requires_fresh_preview(self):
        plan = self.plan()
        self.definitions[0].subcriteria.filter(display_order=1).update(name='Changed Detail')
        with self.assertRaises(ResetError):
            self.apply(plan)
        self.assertEqual(JudgeScore.objects.filter(candidate__event=self.duet).count(), 16)

    def test_required_production_columns_survive_in_private_preimage(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
                cursor.execute("ALTER TABLE events_judgescore ADD COLUMN review_note text NOT NULL DEFAULT 'test-only-note'")
                cursor.execute("ALTER TABLE events_judgescore ADD COLUMN approval_status text NOT NULL DEFAULT 'test-approved'")
            self.apply()
            restored_evidence = json.loads(self.snapshot.read_text())['tables']['events_judgescore']
            self.assertEqual(len(restored_evidence), 16)
            self.assertTrue(all(row['review_note'] == 'test-only-note' and row['approval_status'] == 'test-approved' for row in restored_evidence))
            self.assertEqual(JudgeScore.objects.filter(candidate__event=self.mobile).count(), 60)
            transaction.set_rollback(True)

    def test_unrelated_modern_dance_round_and_saved_score_stay_unchanged(self):
        from .models import Event, JudgingEvent, CriteriaEventEntry, EventScoringCategory, EventScoringCriterion
        mobile = JudgingEvent.objects.create(pk=65, title='Modern Dance', category=self.mobile.category,
            date=self.mobile.date, time=self.mobile.time, status='active')
        event = Event.objects.create(pk=78, name='Modern Dance', event_date=self.mobile.date,
            scoring_method='criteria', participation_type='team', status='active', judging_event=mobile,
            rounds_config=[{'id': 'dance-final', 'status': 'open', 'name': 'Final'}])
        entry = CriteriaEventEntry.objects.create(event=event, entry_type='team', display_name='Dance Team', display_order=1)
        candidate = Candidate.objects.create(event=mobile, name='Dance Team', number=1)
        category = EventScoringCategory.objects.create(event=event, name='Dance', assigned_round_id='dance-final',
            display_order=1, overall_weight_percent=100, judge_mode='scoring')
        definition = category.criteria.create(name='Performance', display_order=1, max_score=100, weight_percent=100)
        criterion = Criterion.objects.create(event=mobile, name='Performance', max_score=100, weight_percent=100, order=1)
        event.participant_ids = [entry.pk]
        event.result_processing_config = {'mobile_round_mapping': {'criteria': {f'dance-final:{definition.pk}': criterion.pk},
            'candidates': {str(entry.pk): candidate.pk}}}
        event.save()
        JudgeScore.objects.create(pk=191, judge=self.judges[0], candidate=candidate, criterion=criterion, score=85, is_locked=True)
        before = list(Event.objects.filter(pk=78).values()), list(JudgeScore.objects.filter(pk=191).values())
        self.assertTrue(read_mobile_round_context(mobile)['compatible'])
        self.apply()
        self.assertEqual((list(Event.objects.filter(pk=78).values()), list(JudgeScore.objects.filter(pk=191).values())), before)
        self.assertTrue(read_mobile_round_context(mobile)['compatible'])

    def test_cli_preview_has_no_mutations_or_implicit_apply(self):
        review_path = self.directory / 'review.json'
        review_path.write_text(json.dumps(self.review))
        output = StringIO()
        with read_only_queries():
            call_command('reset_vocal_duet_test_scores', actor_id=69, review_file=str(review_path), stdout=output)
        self.assertTrue(json.loads(output.getvalue())['read_only'])
        self.assertEqual(JudgeScore.objects.filter(candidate__event=self.duet).count(), 16)

    def orphan_fixture(self, approve=True):
        # Deferred FK fixture in disposable PostgreSQL only. Each caller rolls
        # back this preparation before TestCase checks constraints at teardown.
        JudgeScore.objects.filter(pk__range=(175, 190), judge_id=16).update(judge_id=24)
        JudgeScore.objects.filter(pk__range=(175, 190), judge_id=21).update(judge_id=25)
        reviewer = get_user_model().objects.create_user(pk=103, username='independent-admin', is_staff=True)
        if approve:
            self.review['orphan_owner_review'] = {'verified_by': reviewer.pk, 'confirmed_test_data': True,
                'missing_judge_ids': [24, 25], 'orphan_score_ids': list(range(175, 191)),
                'evidence_reference': 'TEST independent signed historical test-data verification'}
        return reviewer

    def test_orphan_owner_preview_requires_independent_exact_approval(self):
        with transaction.atomic():
            self.orphan_fixture(approve=False)
            with read_only_queries():
                plan = self.plan()
            self.assertFalse(plan['can_apply'])
            self.assertEqual(plan['owner_integrity']['missing_judge_ids'], [24, 25])
            self.assertEqual(plan['owner_integrity']['orphan_score_ids'], list(range(175, 191)))
            self.assertFalse(plan['owner_integrity']['independently_verified'])
            transaction.set_rollback(True)

    def test_verified_orphan_cleanup_preserves_other_events_and_does_not_create_users(self):
        with transaction.atomic():
            self.orphan_fixture()
            before = list(JudgeScore.objects.filter(candidate__event=self.mobile).order_by('pk').values())
            users = list(get_user_model().objects.order_by('pk').values())
            self.assertTrue(self.plan()['can_apply'], self.plan()['blocked_reasons'])
            self.apply()
            self.assertEqual(list(get_user_model().objects.order_by('pk').values()), users)
            self.assertEqual(list(JudgeScore.objects.filter(candidate__event=self.mobile).order_by('pk').values()), before)
            self.assertEqual(AuditLog.objects.get().metadata['owner_integrity']['missing_judge_ids'], [24, 25])
            preimage = json.loads(self.snapshot.read_text())['tables']['events_judgescore']
            self.assertEqual({row['judge_id'] for row in preimage}, {24, 25})
            transaction.set_rollback(True)

    def test_orphan_cleanup_rollback_retains_original_owner_ids_and_all_rows(self):
        with transaction.atomic():
            self.orphan_fixture()
            before = list(JudgeScore.objects.order_by('pk').values())
            with patch('events.vocal_duet_reset.read_mobile_round_context', return_value=None):
                with self.assertRaises(ResetError):
                    self.apply()
            self.assertEqual(list(JudgeScore.objects.order_by('pk').values()), before)
            self.assertFalse(CriterionSubcriterion.objects.filter(criterion__event=self.duet).exists())
            self.assertEqual(AuditLog.objects.count(), 0)
            transaction.set_rollback(True)

    def test_orphan_approval_cannot_bypass_incomplete_review_or_cross_event_scope(self):
        with transaction.atomic():
            self.orphan_fixture()
            self.review['pairs'][0]['identity_evidence'] = ''
            self.assertFalse(self.plan()['can_apply'])
            self.review['pairs'][0]['identity_evidence'] = 'TEST signed roster'
            JudgeScore.objects.filter(pk=175).update(criterion=self.criteria[0])
            self.assertFalse(self.plan()['can_apply'])
            transaction.set_rollback(True)

    def test_self_review_wrong_role_inexact_lists_and_revoked_reviewer_are_blocked(self):
        with transaction.atomic():
            reviewer = self.orphan_fixture()
            approval = self.review['orphan_owner_review']
            for invalid in (self.faculty.pk, self.judges[-1].pk):
                approval['verified_by'] = invalid
                self.assertFalse(self.plan()['can_apply'])
            approval['verified_by'] = reviewer.pk
            approval['orphan_score_ids'] = list(range(175, 190))
            self.assertFalse(self.plan()['can_apply'])
            approval['orphan_score_ids'] = list(range(175, 191))
            plan = self.plan()
            reviewer.is_active = False
            reviewer.save()
            with self.assertRaises(ResetError):
                self.apply(plan)
            transaction.set_rollback(True)

    def test_orphan_owner_reappearance_invalidates_approved_fingerprint(self):
        with transaction.atomic():
            self.orphan_fixture()
            plan = self.plan()
            get_user_model().objects.create_user(pk=24, username='TEST recreated account fixture')
            with self.assertRaises(ResetError):
                self.apply(plan)
            self.assertEqual(JudgeScore.objects.filter(candidate__event=self.duet).count(), 16)
            transaction.set_rollback(True)
