"""Fixed-scope guided maintenance; all data is disposable, never production."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import os
from unittest.mock import patch

from django.db import connection, transaction
from django.test import TestCase, SimpleTestCase
from django.utils import timezone

from . import vocal_duet_simple as simple
from . import vocal_duet_development as dev
from . import vocal_duet_host_identity as identity
from . import vocal_duet_owner_evidence as evidence
from . import test_vocal_duet_development as fixtures
from .test_mobile_api import read_only_queries
from .models import AuditLog, JudgeScore
from .vocal_duet_reset import ResetError, _digest, _preimage, reset_plan


class SimpleMaintenanceTests(TestCase):
    def setUp(self):
        # PostgreSQL sequences survive TestCase rollback. Keep this added suite
        # from advancing auto candidates into older fixed 372/373 fixtures.
        with connection.cursor() as cursor:
            cursor.execute('SELECT last_value, is_called FROM events_candidate_id_seq')
            previous_sequence = cursor.fetchone()
            cursor.execute("SELECT setval('events_candidate_id_seq', 1000000, false)")
        def restore_sequence():
            with connection.cursor() as cursor:
                cursor.execute("SELECT setval('events_candidate_id_seq', %s, %s)", previous_sequence)
        self.addCleanup(restore_sequence)
        fixtures.DevelopmentVocalDuetTests.setUp(self)
        self.criteria = {row['official_id']: row['mobile_id'] for row in self.review['criteria']}
        self.pairs = {entry.pk: (372 + index, list(entry.member_links.values_list('individual_id', flat=True)), entry.department.code)
            for index, entry in enumerate(self.entries)}
        self.work = self.directory / 'work'
        self.work.mkdir(mode=0o700)
        (self.work / 'review.json').write_text(json.dumps(self.review))
        (self.work / 'database.dump').write_bytes(self.backup.read_bytes())
        self.owner_file = self.directory / 'owner.json'
        for name, value in (('WORK', self.work), ('CRITERIA', self.criteria), ('PAIRS', self.pairs), ('OWNER_APPROVAL', self.owner_file)):
            context = patch.object(simple, name, value)
            context.start(); self.addCleanup(context.stop)
        self.host_mock = patch.object(simple, 'host_and_database', return_value=self.target)
        self.host_mock.start(); self.addCleanup(self.host_mock.stop)
        self.backup_mock = patch.object(simple, 'verify_existing_backup', side_effect=self.backup_evidence)
        self.backup_mock.start(); self.addCleanup(self.backup_mock.stop)
        if os.name != 'posix':
            # Windows cannot represent a POSIX 0700 owner-only directory.
            context = patch.object(simple, 'validate_private_directory', return_value=None)
            context.start(); self.addCleanup(context.stop)

    def backup_evidence(self, plan):
        return {'backup_sha256': self.backup_hash, 'relevant_rows_sha256': 'a' * 64,
            'scoped_rows_sha256': _digest(_preimage(plan)['tables']), 'isolated_restore_database': 'eventtab_vd_dev_restore'}

    def checkpoint(self):
        simple.preview()
        return simple.verify(None, simple.CONFIRM)

    def owner_approval(self, checkpoint):
        now = timezone.now()
        record = {'purpose': 'eventtab-simple-owner-vocal-duet-test-approval-v1', 'checkpoint_sha256': _digest(checkpoint),
            'target': checkpoint['target'], 'backup_sha256': checkpoint['backup']['backup_sha256'],
            'confirmed': simple.CONFIRM, 'owner_evidence_reference': 'TEST Kamatera ownership record for isolated fixture',
            'evidence_version': 2, 'development_use_confirmed': True,
            'issued_at': now.isoformat(), 'expires_at': (now + timedelta(hours=1)).isoformat()}
        self.owner_file.write_text(json.dumps(record))
        return record

    def root_policy(self):
        self.signed = patch.object(dev, 'load_authorization', side_effect=ResetError('TEST expired signed permit'))
        self.signed.start(); self.addCleanup(self.signed.stop)
        context = patch.object(dev, '_root_controlled', return_value=None)
        context.start(); self.addCleanup(context.stop)

    def test_preview_is_read_only_and_does_not_overwrite_review_or_backup(self):
        original = (self.work / 'review.json').read_bytes()
        before = list(JudgeScore.objects.order_by('pk').values())
        with read_only_queries(): result = simple.preview()
        self.assertTrue(result['plan']['can_apply'], result['plan']['blocked_reasons'])
        self.assertTrue(result['human_confirmation_required'])
        self.assertEqual(before, list(JudgeScore.objects.order_by('pk').values()))
        self.assertEqual(original, (self.work / 'review.json').read_bytes())
        self.assertEqual(self.backup.read_bytes(), (self.work / 'database.dump').read_bytes())
        self.assertEqual(simple.proposed_review()[0], self.review)  # Existing signed review digest is reusable.

    def test_verified_backup_precedes_explicit_confirmation(self):
        simple.preview()
        order = []
        def backup(plan):
            order.append('backup')
            return self.backup_evidence(plan)
        def confirm(evidence, review):
            order.append('confirm')
            return simple.CONFIRM
        with patch.object(simple, 'verify_existing_backup', side_effect=backup):
            simple.verify(None, confirm)
        self.assertEqual(order, ['backup', 'confirm'])

    def test_declining_confirmation_preserves_scores_and_has_no_checkpoint(self):
        simple.preview()
        with self.assertRaises(ResetError): simple.verify(None, 'no')
        self.assertFalse((simple.session_path() / 'approval-request.json').exists())
        self.assertEqual(JudgeScore.objects.filter(candidate__event=self.duet).count(), 16)

    def test_does_not_infer_members_or_departments_from_names(self):
        for field in ('members', 'department'):
            pairs = deepcopy(self.pairs)
            key = next(iter(pairs))
            candidate, members, code = pairs[key]
            pairs[key] = (candidate, list(reversed(members)) if field == 'members' else members,
                'WRONG' if field == 'department' else code)
            with patch.object(simple, 'PAIRS', pairs), self.assertRaises(ResetError): simple.preview()

    def test_incomplete_evidence_cannot_approve(self):
        self.review['roster_evidence_reference'] = ''
        self.review['evidence_reference'] = ''
        (self.work / 'review.json').write_text(json.dumps(self.review))
        simple.preview()
        with self.assertRaises(ResetError): simple.verify(None, simple.CONFIRM)

    def test_member_department_discrepancy_is_not_silently_approved(self):
        entry = self.entries[0]
        link = entry.member_links.first()
        link.individual.department = self.entries[1].department
        link.individual.save(update_fields=['department'])
        with self.assertRaisesMessage(ResetError, 'member departments differ'):
            simple.preview()

    def test_changed_review_or_scores_make_checkpoint_stale(self):
        simple.preview()
        review = deepcopy(self.review); review['evidence_reference'] = 'Changed'
        (self.work / 'review.json').write_text(json.dumps(review))
        with self.assertRaises(ResetError): simple.verify(None, simple.CONFIRM)
        (self.work / 'review.json').write_text(json.dumps(self.review))
        self.checkpoint()
        JudgeScore.objects.filter(pk=175).update(score=1)
        with self.assertRaises(ResetError): simple.commit()
        self.assertEqual(JudgeScore.objects.filter(candidate__event=self.duet).count(), 16)

    def test_owner_approval_requires_exact_checkpoint_target_backup_and_current_time(self):
        checkpoint = self.checkpoint(); self.root_policy()
        for change in ({'checkpoint_sha256': 'b' * 64}, {'target': {}}, {'backup_sha256': 'c' * 64},
                {'owner_evidence_reference': ''}, {'expires_at': (timezone.now() - timedelta(seconds=1)).isoformat()}):
            record = self.owner_approval(checkpoint); record.update(change)
            self.owner_file.write_text(json.dumps(record))
            with self.assertRaises(ResetError): simple.authorization(checkpoint)

    def test_untrusted_root_file_and_replay_are_blocked(self):
        checkpoint = self.checkpoint(); self.owner_approval(checkpoint); self.root_policy()
        with patch.object(dev, '_root_controlled', side_effect=ResetError('TEST untrusted ownership')):
            with self.assertRaises(ResetError): simple.authorization(checkpoint)
        digest = hashlib.sha256(self.owner_file.read_bytes()).hexdigest()
        AuditLog.objects.create(user=self.faculty, metadata={'development_authorization': {'owner_approval_sha256': digest}})
        with self.assertRaises(ResetError): simple.authorization(checkpoint)

    def test_owner_policy_reset_is_atomic_protects_other_data_and_retry_keeps_new_scores(self):
        checkpoint = self.checkpoint(); self.owner_approval(checkpoint); self.root_policy()
        protected = dev.protected_state()
        result = simple.commit()
        self.assertTrue(result['mobile_configuration_ready'])
        self.assertEqual(dev.protected_state(), protected)
        self.assertEqual(JudgeScore.objects.filter(candidate__event=self.mobile).count(), 60)
        JudgeScore.objects.create(judge=self.judges[-1], candidate_id=372, criterion_id=1197, score=80)
        self.assertTrue(simple.commit()['already_applied'])
        self.assertEqual(JudgeScore.objects.filter(candidate__event=self.duet).count(), 1)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_failure_after_deletion_rolls_everything_back(self):
        self.checkpoint()
        before = dev.protected_state()
        with patch('events.vocal_duet_reset.AuditLog.objects.create', side_effect=ResetError('TEST audit failure')):
            with self.assertRaises(ResetError): simple.commit()
        self.assertEqual(JudgeScore.objects.filter(candidate__event=self.duet).count(), 16)
        self.assertEqual(dev.protected_state(), before)
        self.assertEqual(self.duet.criteria.filter(subcriteria__isnull=False).count(), 0)

    def test_full_row_scope_sql_includes_official_children_members_and_scores(self):
        with connection.cursor() as cursor: rows = simple.relevant_rows(cursor)
        self.assertEqual(len(rows['events_judgescore']), 16)
        self.assertEqual(len(rows['events_eventscoringsubcriterion']), 8)
        self.assertEqual(len(rows['events_criteriaevententrymember']), 4)
        self.assertIn('submitted_at', rows['events_judgescore'][0])

    def test_official_production_seal_blocks_old_and_new_development_paths(self):
        with patch.object(dev.os.path, 'lexists', return_value=True):
            with self.assertRaises(ResetError): simple.preview()
            with self.assertRaises(ResetError): simple.commit()
            with self.assertRaises(ResetError): dev.ensure_development_enabled()

    def test_orphans_still_require_production_independent_review(self):
        with transaction.atomic():
            JudgeScore.objects.filter(pk__range=(175, 190), judge_id=16).update(judge_id=24)
            self.review['development_test_data_review'].update(missing_judge_ids=[24],
                orphan_score_ids=list(JudgeScore.objects.filter(judge_id=24).order_by('pk').values_list('pk', flat=True)))
            (self.work / 'review.json').write_text(json.dumps(self.review))
            self.assertFalse(reset_plan(self.faculty, self.review)['can_apply'])
            checkpoint = self.checkpoint(); self.owner_approval(checkpoint); self.root_policy()
            self.assertTrue(simple.commit()['applied'])
            self.assertEqual(JudgeScore.objects.filter(candidate__event=self.mobile).count(), 60)
            transaction.set_rollback(True)  # Disposable fixture intentionally has an orphan.


class RootOwnerHelperTests(SimpleTestCase):
    def setUp(self):
        location = Path(__file__).resolve().parents[1] / 'execution' / 'vocal_duet_simple_owner.py'
        spec = importlib.util.spec_from_file_location('vocal_duet_owner_test', location)
        self.helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.helper)
        rules = patch.object(self.helper, 'evidence_rules', return_value=vars(evidence))
        rules.start(); self.addCleanup(rules.stop)
        self.prior_patch = patch.object(self.helper, 'current_approval', return_value=None)
        self.prior_patch.start(); self.addCleanup(self.prior_patch.stop)

    def test_helper_has_no_django_database_or_signing_key_dependency(self):
        source = Path(self.helper.__file__).read_text()
        self.assertNotIn('import django', source)
        self.assertNotIn('import psycopg', source)
        self.assertNotIn('private.pem', source)

    def test_production_seal_refuses_new_approval(self):
        with patch.object(self.helper.os, 'geteuid', return_value=0, create=True), \
                patch.object(self.helper, 'INSTALL', Path(self.helper.__file__)), \
                patch.object(self.helper, 'secure_read', return_value=b''), \
                patch.object(self.helper.os.path, 'lexists', return_value=True), \
                patch.object(self.helper.sys, 'argv', ['helper', 'approve']):
            with self.assertRaises(ValueError): self.helper.main()

    def test_owner_helper_checks_actual_host_exact_scope_and_backup_before_prompt(self):
        now = timezone.now()
        facts = {'host_uuid': '11111111-2222-4333-8444-555555555555',
            'host_machine_id': 'a' * 32, 'host_boot_id': '66666666-7777-4888-8999-aaaaaaaaaaaa'}
        checkpoint = {'purpose': 'eventtab-simple-vocal-duet-checkpoint-v1', 'session': 'simple-' + 'a' * 16,
            'actor_id': 69, 'score_ids': list(range(175, 191)), 'confirmed': simple.CONFIRM,
            'archive_restore_confirmed': True,
            'criteria': {'93': 1197, '96': 1198, '97': 1199, '98': 1200},
            'pairs': {'14': [372, [1, 9], 'BFPT'], '15': [373, [4, 10], 'BTLEd']},
            'target': {**facts, 'database': 'eventtab_db', 'database_role': 'eventtab_dbuser',
                'server_address': '127.0.0.1', 'server_port': 5433, 'in_recovery': False,
                'database_oid': 123, 'cluster_system_identifier': '123456789', 'server_version_num': 180006},
            'backup': {'isolated_restore_database': 'eventtab_vd_dev_restore', 'backup_sha256': 'b' * 64,
                'relevant_rows_sha256': 'c' * 64, 'scoped_rows_sha256': 'd' * 64},
            'review': {'roster_evidence_reference': 'TEST real roster'}, 'verified_at': now.isoformat()}
        for edit in ({'score_ids': [175]}, {'criteria': {}}, {'pairs': {}}, {'actor_id': 1}, {'archive_restore_confirmed': False},
                {'verified_at': (now - timedelta(hours=3)).isoformat()},
                {'target': {**checkpoint['target'], 'host_uuid': 'wrong'}},
                {'target': {**checkpoint['target'], 'database_oid': None}},
                {'backup': {**checkpoint['backup'], 'backup_sha256': ''}}):
            changed = {**checkpoint, **edit}
            with patch.object(self.helper, 'secure_read', side_effect=[json.dumps({'session': checkpoint['session']}).encode(), json.dumps(changed).encode()]), \
                    patch('builtins.input') as prompt, self.assertRaises(ValueError):
                self.helper.approve(facts, now)
            prompt.assert_not_called()
        with patch.object(self.helper, 'secure_read', side_effect=[json.dumps({'session': checkpoint['session']}).encode(), json.dumps(checkpoint).encode()] * 2), \
                patch('builtins.input', side_effect=['TEST Kamatera ownership record for isolated fixture', evidence.DEVELOPMENT_CONFIRMATION, simple.CONFIRM]), \
                patch('builtins.print'), patch.object(self.helper, 'publish') as publish:
            self.helper.approve(facts, now)
        self.assertEqual(publish.call_args.args[1]['checkpoint_sha256'], _digest(checkpoint))

    def test_noninteractive_owner_approval_and_scope_arguments_are_refused(self):
        with patch.object(self.helper.os, 'geteuid', return_value=0, create=True), \
                patch.object(self.helper, 'INSTALL', Path(self.helper.__file__)), \
                patch.object(self.helper, 'secure_read', return_value=b''), \
                patch.object(self.helper.os.path, 'lexists', return_value=False), \
                patch.object(self.helper.sys.stdin, 'isatty', return_value=False), \
                patch.object(self.helper.sys, 'argv', ['helper', 'approve']):
            with self.assertRaises(ValueError): self.helper.main()
        with patch.object(self.helper.os, 'geteuid', return_value=0, create=True), \
                patch.object(self.helper, 'INSTALL', Path(self.helper.__file__)), \
                patch.object(self.helper.sys, 'argv', ['helper', 'approve', '--force']):
            with self.assertRaises(ValueError): self.helper.main()

    def test_openat_walk_never_follows_symlinks(self):
        # Windows cannot execute POSIX dir_fd/O_NOFOLLOW semantics. Linux runs this.
        if not hasattr(__import__('os'), 'O_NOFOLLOW'):
            self.skipTest('Linux O_NOFOLLOW is unavailable on this host.')
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'real').write_text('facts')
            (root / 'link').symlink_to(root / 'real')
            with self.assertRaises(OSError): self.helper.secure_read(root / 'link')


class OwnerApprovalDiagnosticTests(SimpleTestCase):
    """No database access, apply/reset call, score deletion or real root writes."""
    def setUp(self):
        RootOwnerHelperTests.setUp(self)
        self.now = timezone.now()
        self.name = 'simple-e12d94844961a3c3'
        self.facts = {'host_uuid': '11111111-2222-4333-8444-555555555555',
            'host_machine_id': 'a' * 32, 'host_boot_id': '66666666-7777-4888-8999-aaaaaaaaaaaa'}
        self.observation = {**self.facts, 'purpose': 'eventtab-simple-root-host-v1',
            'issued_at': (self.now - timedelta(minutes=10)).isoformat(),
            'expires_at': (self.now + timedelta(minutes=110)).isoformat()}
        self.raw = json.dumps(self.observation).encode()
        self.checkpoint = {'purpose': 'eventtab-simple-vocal-duet-checkpoint-v1', 'session': self.name,
            'actor_id': 69, 'score_ids': list(range(175, 191)), 'confirmed': simple.CONFIRM,
            'archive_restore_confirmed': True, 'criteria': {'93': 1197, '96': 1198, '97': 1199, '98': 1200},
            'pairs': {'14': [372, [1, 9], 'BFPT'], '15': [373, [4, 10], 'BTLEd']},
            'target': {**self.facts, 'root_observation_sha256': hashlib.sha256(self.raw).hexdigest(),
                'database': 'eventtab_db', 'database_role': 'eventtab_dbuser', 'database_oid': 123,
                'server_address': '127.0.0.1/32', 'server_port': 5433, 'in_recovery': False,
                'cluster_system_identifier': '123456789', 'server_version_num': '180006'},
            'backup': {'isolated_restore_database': 'eventtab_vd_dev_restore', 'backup_sha256': 'b' * 64,
                'relevant_rows_sha256': 'c' * 64, 'scoped_rows_sha256': 'd' * 64},
            'review': {'roster_evidence_reference': 'TEST actual roster'},
            'verified_at': (self.now - timedelta(minutes=2)).isoformat()}

    def read(self, path, trusted=False):
        if path == self.helper.DIRECTORY / 'simple-host.json':
            self.assertTrue(trusted)
            return self.raw
        if path == self.helper.WORK / 'simple-current.json':
            return json.dumps({'session': self.name}).encode()
        self.assertEqual(path, self.helper.WORK / self.name / 'approval-request.json')
        return json.dumps(self.checkpoint).encode()

    def errors(self):
        with patch.object(self.helper, 'secure_read', side_effect=self.read):
            return self.helper.checkpoint_errors(self.checkpoint, self.name, self.facts, self.now)

    def test_postgresql_inet_text_address_approves_without_rewriting_target_or_checkpoint(self):
        # Real PostgreSQL: SELECT inet_server_addr()::text -> 127.0.0.1/32.
        for address in ('127.0.0.1/32', '127.0.0.1'):
            self.checkpoint['target']['server_address'] = address
            before = deepcopy(self.checkpoint)
            with patch.object(self.helper, 'secure_read', side_effect=self.read), \
                    patch('builtins.input', side_effect=['TEST Kamatera ownership record for isolated fixture', evidence.DEVELOPMENT_CONFIRMATION, simple.CONFIRM]), \
                    patch('builtins.print'), patch.object(self.helper, 'publish') as publish:
                self.helper.approve(self.facts, self.now)
            approval = publish.call_args.args[1]
            self.assertEqual(approval['target'], before['target'])
            self.assertEqual(approval['checkpoint_sha256'], _digest(before))
            self.assertEqual(approval['backup_sha256'], before['backup']['backup_sha256'])
            self.assertEqual(self.checkpoint, before)

    def test_address_compatibility_does_not_accept_networks_or_other_hosts(self):
        for address in ('127.0.0.1/24', '127.0.0.2/32', '0.0.0.0', '::1', None, 123):
            self.checkpoint['target']['server_address'] = address
            self.assertTrue(any('target.server_address:' in error for error in self.errors()))

    def test_inspect_is_read_only_and_reports_current_session_digest(self):
        with patch.object(self.helper, 'secure_read', side_effect=self.read), \
                patch.object(self.helper, 'publish') as publish, patch('builtins.input') as prompt:
            checkpoint, report = self.helper.inspect_checkpoint(self.facts, self.now)
        self.assertEqual(report['session'], self.name)
        self.assertEqual(report['checkpoint_sha256'], _digest(checkpoint))
        self.assertEqual(report['failed_checks'], [])
        self.assertTrue(report['read_only'])
        publish.assert_not_called(); prompt.assert_not_called()

    def test_pointer_session_mismatch_is_named_and_never_approved(self):
        self.checkpoint['session'] = 'simple-db96190fa71810ec'
        self.assertTrue(any('checkpoint.session:' in error for error in self.errors()))
        with patch.object(self.helper, 'secure_read', side_effect=self.read), \
                patch.object(self.helper, 'publish') as publish, patch('builtins.input') as prompt:
            with self.assertRaisesMessage(ValueError, 'checkpoint.session:'):
                self.helper.approve(self.facts, self.now)
        publish.assert_not_called(); prompt.assert_not_called()

    def test_stale_future_and_non_utc_checkpoints_remain_blocked(self):
        for timestamp in ((self.now - timedelta(hours=2, seconds=1)).isoformat(),
                (self.now + timedelta(seconds=1)).isoformat(), self.now.replace(tzinfo=None).isoformat(), 'bad', None):
            self.checkpoint['verified_at'] = timestamp
            self.assertTrue(any('checkpoint.verified_at:' in error for error in self.errors()))

    def test_refresh_observe_invalidates_checkpoint_even_with_same_host_and_database(self):
        old_target = deepcopy(self.checkpoint['target'])
        refreshed = {**self.observation, 'issued_at': (self.now - timedelta(minutes=1)).isoformat(),
            'expires_at': (self.now + timedelta(minutes=119)).isoformat()}
        self.raw = json.dumps(refreshed).encode()
        self.assertTrue(any('target.root_observation_sha256:' in error for error in self.errors()))
        self.assertEqual(self.checkpoint['target'], old_target)

    def test_observation_refresh_during_confirmation_prevents_approval(self):
        def answer(prompt):
            if prompt.startswith('Actual ownership'):
                self.raw = json.dumps({**self.observation, 'issued_at': self.now.isoformat()}).encode()
                return 'TEST Kamatera ownership record for isolated fixture'
            return evidence.DEVELOPMENT_CONFIRMATION if prompt.startswith('After verifying') else simple.CONFIRM
        with patch.object(self.helper, 'secure_read', side_effect=self.read), \
                patch('builtins.input', side_effect=answer), patch('builtins.print'), \
                patch.object(self.helper, 'publish') as publish:
            with self.assertRaisesMessage(ValueError, 'target.root_observation_sha256:'):
                self.helper.approve(self.facts, self.now)
        publish.assert_not_called()

    def test_checkpoint_change_during_confirmation_prevents_approval(self):
        def answer(prompt):
            if prompt.startswith('Actual ownership'):
                self.checkpoint['backup']['backup_sha256'] = 'e' * 64
                return 'TEST Kamatera ownership record for isolated fixture'
            return evidence.DEVELOPMENT_CONFIRMATION if prompt.startswith('After verifying') else simple.CONFIRM
        with patch.object(self.helper, 'secure_read', side_effect=self.read), \
                patch('builtins.input', side_effect=answer), patch('builtins.print'), \
                patch.object(self.helper, 'publish') as publish:
            with self.assertRaisesMessage(ValueError, 'Checkpoint/session changed'):
                self.helper.approve(self.facts, self.now)
        publish.assert_not_called()

    def test_observation_expiry_and_wrong_boot_are_not_hidden_by_matching_digest(self):
        for edit, expected in (({'expires_at': (self.now - timedelta(seconds=1)).isoformat()}, 'root_observation.freshness:'),
                ({'host_boot_id': 'different-boot'}, 'root_observation.identity:')):
            self.raw = json.dumps({**self.observation, **edit}).encode()
            self.checkpoint['target']['root_observation_sha256'] = hashlib.sha256(self.raw).hexdigest()
            self.assertTrue(any(expected in error for error in self.errors()))

    def test_untrusted_observation_and_invalid_roster_or_backup_evidence_are_named(self):
        with patch.object(self.helper, 'secure_read', side_effect=ValueError('TEST untrusted ownership')):
            errors = self.helper.checkpoint_errors(self.checkpoint, self.name, self.facts, self.now)
        self.assertTrue(any('root_observation.trust:' in error for error in errors))
        self.checkpoint['review']['roster_evidence_reference'] = ' '
        self.checkpoint['backup']['backup_sha256'] = 'bad'
        errors = self.errors()
        self.assertTrue(any('review.roster_evidence_reference:' in error for error in errors))
        self.assertTrue(any('backup.backup_sha256:' in error for error in errors))

    def test_django_verify_rejects_refreshed_observation_before_backup_or_confirmation(self):
        original = {'evidence': 'TEST unchanged review'}
        plan = {'can_apply': True, 'state_sha256': 'same', 'protected_state_sha256': 'same'}
        initial = {'source_review_sha256': _digest(original), 'target': self.checkpoint['target'], 'plan': plan}
        refreshed = {**initial['target'], 'root_observation_sha256': 'f' * 64}
        with patch.object(simple, 'session_path', return_value=Path('/TEST/session')), \
                patch.object(simple, 'read_json', return_value=initial), \
                patch.object(simple, 'proposed_review', return_value=(original, None, original)), \
                patch.object(simple, 'host_and_database', return_value=refreshed), \
                patch.object(simple, 'plan_for', return_value=plan), \
                patch.object(simple, 'verify_existing_backup') as backup:
            with self.assertRaisesMessage(ResetError, 'root_observation_sha256'):
                simple.verify(None, simple.CONFIRM)
        backup.assert_not_called()
