"""Development authorization is separate from the unchanged production policy."""
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test import TestCase
from django.utils import timezone

from . import test_vocal_duet_reset as fixtures
from . import vocal_duet_development as development
from .models import AuditLog, JudgeScore
from .vocal_duet_reset import ResetError, _digest, _preimage, reset_plan
from .test_mobile_api import read_only_queries


class DevelopmentVocalDuetTests(TestCase):
    def setUp(self):
        fixtures.VocalDuetResetTests.setUp(self)
        self.target = {'host_uuid': 'verified-test-host', 'database': connection.settings_dict['NAME'],
            'database_oid': 1, 'cluster_system_identifier': 'TEST-CLUSTER'}
        self.authorization = {'mode': 'verified-development-only', 'permit_sha256': 'test-permit', 'target': self.target}
        self.review['development_test_data_review'] = {'verified_by': 69, 'confirmed_test_data': True,
            'missing_judge_ids': [], 'orphan_score_ids': [], 'evidence_reference': 'TEST original test-run receipts'}
        self.review['roster_evidence_reference'] = 'TEST signed original roster'
        for entry, binding in zip(self.entries, self.review['pairs']):
            binding.update(roster_verified_by=69, verified_department_id=entry.department_id,
                verified_department_name=entry.department.name,
                verified_member_department_ids=list(entry.member_links.values_list('individual__department_id', flat=True)))
        for name, value in (('target_identity', self.target), ('load_authorization', self.authorization)):
            mock = patch.object(development, name, return_value=value)
            self.addCleanup(mock.stop)
            mock.start()

    def plan(self):
        return development.development_plan(self.faculty, self.review)

    def prepare_orphans(self):
        JudgeScore.objects.filter(pk__range=(175, 190), judge_id=16).update(judge_id=24)
        JudgeScore.objects.filter(pk__range=(175, 190), judge_id=21).update(judge_id=25)
        self.review['development_test_data_review'].update(missing_judge_ids=[24, 25], orphan_score_ids=list(range(175, 191)))

    def apply(self, plan=None):
        plan = plan or self.plan()
        self.receipt.write_text(json.dumps({'backup_sha256': self.backup_hash,
            'plan_fingerprint': plan['fingerprint'], 'verified_by': 69, 'restore_succeeded': True,
            'verified_at': timezone.now().isoformat(), 'source_database': connection.settings_dict['NAME'],
            'isolated_restore_database': 'isolated_test_restore', 'scoped_rows_sha256': _digest(_preimage(plan)['tables'])}))
        with patch.object(development, 'verify_isolated_restore', return_value='test-verified-scope'):
            return development.apply_development_reset(69, self.review, plan['fingerprint'], development.CONFIRMATION,
                str(self.backup), self.backup_hash, str(self.receipt), str(self.snapshot), 'isolated_test_restore')

    def test_read_only_preview_preserves_all_records(self):
        before = list(JudgeScore.objects.order_by('pk').values())
        with read_only_queries():
            plan = self.plan()
        self.assertTrue(plan['can_apply'], plan['blocked_reasons'])
        self.assertEqual(list(JudgeScore.objects.order_by('pk').values()), before)

    def test_one_real_operator_can_reset_verified_development_orphans_but_production_stays_blocked(self):
        with transaction.atomic():
            self.prepare_orphans()
            self.assertFalse(reset_plan(self.faculty, self.review)['can_apply'])
            self.assertTrue(self.plan()['can_apply'])
            protected = development.protected_state()
            result = self.apply()
            self.assertTrue(result['applied'])
            self.assertEqual(protected, development.protected_state())
            self.assertEqual(JudgeScore.objects.filter(candidate__event_id=63).count(), 60)
            self.assertFalse(JudgeScore.objects.filter(candidate__event_id=64).exists())
            self.assertEqual(AuditLog.objects.get().metadata['development_authorization'], self.authorization)
            transaction.set_rollback(True)

    def test_incomplete_pair_or_orphan_review_still_blocks(self):
        self.review['pairs'][0]['identity_evidence'] = ''
        self.assertFalse(self.plan()['can_apply'])

    def test_unverified_or_conflicting_roster_departments_remain_blocked(self):
        self.review['pairs'][0]['verified_department_name'] = 'Wrong department'
        self.assertFalse(self.plan()['can_apply'])
        self.review['pairs'][0]['verified_department_name'] = self.entries[0].department.name
        del self.review['pairs'][0]['verified_member_department_ids']
        self.assertFalse(self.plan()['can_apply'])
        self.review['pairs'][0]['identity_evidence'] = 'TEST verified roster'
        self.review['development_test_data_review']['confirmed_test_data'] = False
        self.assertFalse(self.plan()['can_apply'])

    def test_configuration_or_protected_event_change_invalidates_preview(self):
        plan = self.plan()
        self.portal.name = 'Changed Pageants'
        self.portal.save()
        with self.assertRaises(ResetError):
            self.apply(plan)
        self.assertEqual(JudgeScore.objects.filter(candidate__event=self.duet).count(), 16)

    def test_protected_state_change_during_apply_rolls_back_all_mutations(self):
        with transaction.atomic():
            self.prepare_orphans()
            before = list(JudgeScore.objects.order_by('pk').values())
            original = development.protected_state
            count = 0
            def state():
                nonlocal count
                count += 1
                result = original()
                if count == 3:
                    result['unexpected'] = 'mutation'
                return result
            with patch.object(development, 'protected_state', side_effect=state):
                with self.assertRaises(ResetError):
                    self.apply()
            self.assertEqual(list(JudgeScore.objects.order_by('pk').values()), before)
            self.assertEqual(AuditLog.objects.count(), 0)
            transaction.set_rollback(True)

    def test_restore_database_cannot_be_target_itself(self):
        with self.assertRaises(ResetError):
            development.verify_isolated_restore(self.plan(), self.target['database'])

    def test_restore_comparison_failure_prevents_cleanup(self):
        plan = self.plan()
        with patch.object(development, 'verify_isolated_restore', side_effect=ResetError('Restore mismatch')):
            with self.assertRaises(ResetError):
                development.apply_development_reset(69, self.review, plan['fingerprint'], development.CONFIRMATION,
                    str(self.backup), self.backup_hash, str(self.receipt), str(self.snapshot), 'other_db')
        self.assertEqual(JudgeScore.objects.filter(candidate__event=self.duet).count(), 16)

    def test_cli_default_is_read_only_and_production_cli_has_no_development_option(self):
        review = self.directory / 'review.json'
        review.write_text(json.dumps(self.review))
        output = StringIO()
        with read_only_queries():
            call_command('reset_development_vocal_duet', actor_id=69, review_file=str(review), stdout=output)
        self.assertTrue(json.loads(output.getvalue())['read_only'])
        with self.assertRaises(TypeError):
            call_command('reset_vocal_duet_test_scores', actor_id=69, review_file=str(review), development=True)


class InfrastructurePermitTests(TestCase):
    def setUp(self):
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        self.root_check = development._root_controlled
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.actor = SimpleNamespace(pk=69)
        self.review = {'verified': 'TEST review'}
        self.target = {'host_uuid': 'TEST-VM', 'database': 'TEST-DB', 'database_oid': 999, 'cluster_system_identifier': 'TEST-CLUSTER'}
        (self.root / 'deployment-inventory.json').write_text(json.dumps({'host_uuid': 'TEST-VM', 'target': self.target,
            'infrastructure_record_reference': 'TEST infrastructure export'}))
        (self.root / 'development-use-evidence.json').write_text(json.dumps({'deployment_classification': 'development-only',
            'actual_school_use': False, 'shared_with_live_events': False, 'deployment_owner': 'TEST owner',
            'usage_verification_reference': 'TEST owner-approved inventory and usage records'}))
        self.permit = {'purpose': 'eventtab-development-vocal-duet-test-reset-v1', 'actor_id': 69,
            'issued_at': (timezone.now() - timedelta(minutes=1)).isoformat(),
            'expires_at': (timezone.now() + timedelta(hours=1)).isoformat(), 'target': self.target,
            'review_sha256': _digest(self.review), 'score_ids': list(range(175, 191)),
            'inventory_sha256': development._file_hash(self.root / 'deployment-inventory.json'),
            'development_use_sha256': development._file_hash(self.root / 'development-use-evidence.json')}
        for name, value in (('DIRECTORY', self.root), ('_root_controlled', None)):
            mock = patch.object(development, name, **({'return_value': value} if name == '_root_controlled' else {'new': value}))
            mock.start(); self.addCleanup(mock.stop)

    def load(self):
        (self.root / 'permit.json').write_text(json.dumps(self.permit))
        with patch.object(development.subprocess, 'run'):
            return development.load_authorization(self.actor, self.review, self.target)

    def test_exact_signed_evidence_is_bound_to_target_review_and_expiration(self):
        self.assertEqual(self.load()['mode'], 'verified-development-only')
        self.permit['target'] = dict(self.target, database_oid=1000)
        with self.assertRaises(ResetError): self.load()
        self.permit['target'] = self.target
        self.permit['review_sha256'] = 'different'
        with self.assertRaises(ResetError): self.load()

    def test_expired_permit_or_environment_flags_cannot_authorize(self):
        self.permit['expires_at'] = (timezone.now() - timedelta(seconds=1)).isoformat()
        with self.settings(DEBUG=True):
            with self.assertRaises(ResetError): self.load()

    def test_external_evidence_tamper_and_replay_are_rejected(self):
        authorization = self.load()
        AuditLog.objects.create(action='TEST used permit', metadata={'development_authorization': authorization})
        with self.assertRaises(ResetError): self.load()
        self.permit['issued_at'] = (timezone.now() - timedelta(minutes=2)).isoformat()
        (self.root / 'development-use-evidence.json').write_text('{}')
        with self.assertRaises(ResetError): self.load()

    def test_untrusted_files_and_bad_signature_fail_closed(self):
        (self.root / 'permit.json').write_text(json.dumps(self.permit))
        with patch.object(development, '_root_controlled', side_effect=ResetError('Untrusted file')):
            with self.assertRaises(ResetError): development.load_authorization(self.actor, self.review, self.target)
        with patch.object(development.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'openssl')):
            with self.assertRaises(ResetError): development.load_authorization(self.actor, self.review, self.target)

    def test_real_openssl_signature_and_tamper(self):
        executable = shutil.which('openssl') or 'C:/Program Files/Git/usr/bin/openssl.exe'
        if not Path(executable).exists(): self.skipTest('OpenSSL not installed')
        private = self.root / 'private.pem'
        public = self.root / 'authority-public.pem'
        subprocess.run([executable, 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', str(private)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([executable, 'pkey', '-in', str(private), '-pubout', '-out', str(public)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        payload = self.root / 'permit.json'
        payload.write_text(json.dumps(self.permit))
        subprocess.run([executable, 'dgst', '-sha256', '-sign', str(private), '-out', str(self.root / 'permit.sig'), str(payload)], check=True)
        run = subprocess.run
        def local_openssl(args, **kwargs):
            # Windows test OpenSSL has a different runtime environment; the Linux
            # server keeps its fixed executable and sanitized environment.
            import os
            if os.name == 'nt': kwargs.pop('env', None)
            return run([executable, *args[1:]], **kwargs)
        with patch.object(development.subprocess, 'run', side_effect=local_openssl):
            self.assertEqual(development.load_authorization(self.actor, self.review, self.target)['mode'], 'verified-development-only')
            payload.write_text(json.dumps(dict(self.permit, actor_id=70)))
            with self.assertRaises(ResetError): development.load_authorization(self.actor, self.review, self.target)

    def test_root_ownership_writability_and_symlink_checks(self):
        from types import SimpleNamespace
        path = self.root / 'permit.json'
        for uid, mode in ((1000, 0o100644), (0, 0o100664), (0, 0o120777)):
            with patch.object(development.os, 'name', 'posix'), patch.object(Path, 'lstat', autospec=True,
                    return_value=SimpleNamespace(st_uid=uid, st_mode=mode)):
                with self.assertRaises(ResetError): self.root_check(path)
        def trusted(item):
            return SimpleNamespace(st_uid=0, st_mode=0o100644 if item == path else 0o040755)
        with patch.object(development.os, 'name', 'posix'), patch.object(Path, 'lstat', autospec=True, side_effect=trusted):
            self.root_check(path)
