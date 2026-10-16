"""Run as root ONLY in a disposable Linux test container, never on Kamatera.

Standard-library tests use real Linux ownership/openat/flock and a fixed empty
isolated PostgreSQL database. No Django, scoring fixtures or apply/reset calls.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('owner_under_test', HERE / 'vocal_duet_simple_owner.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def database_snapshot():
    # Explicit target only, never Django settings or a deployment environment.
    result = subprocess.run(['psql', '-X', '-qAt', '-h', '127.0.0.1', '-p', '55449', '-U', 'postgres',
        '-d', 'eventtab_owner_evidence_test', '-v', 'ON_ERROR_STOP=1', '-c',
        "BEGIN READ ONLY; SELECT current_database(),inet_server_port(),COALESCE((SELECT json_agg(t ORDER BY tablename) FROM pg_tables t WHERE schemaname='public'),'[]'); ROLLBACK;"],
        check=True, capture_output=True, text=True)
    if result.stdout.strip() != 'eventtab_owner_evidence_test|55449|[]':
        raise RuntimeError('A dedicated empty test database on 127.0.0.1:55449 is required; no school/scoring database is allowed.')
    return result.stdout


class LinuxOwnerEvidenceTests(unittest.TestCase):
    def setUp(self):
        if os.geteuid() != 0 or not hasattr(os, 'O_NOFOLLOW'):
            self.fail('Use a disposable root Linux container, not the deployment host.')
        self.tmp = tempfile.TemporaryDirectory(prefix='vocal-owner-test-', dir='/root')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.directory = self.root / 'evidence'; self.directory.mkdir(mode=0o755)
        self.work = self.root / 'work'; self.work.mkdir(mode=0o700)
        rules_source = HERE.parent / 'events' / 'vocal_duet_owner_evidence.py'
        self.rules = self.root / 'rules.py'; shutil.copyfile(rules_source, self.rules)
        self.install = self.root / 'owner.py'; shutil.copyfile(HERE / 'vocal_duet_simple_owner.py', self.install)
        for name, value in (('DIRECTORY', self.directory), ('WORK', self.work), ('INSTALL', self.install), ('EVIDENCE_RULES', self.rules)):
            context = patch.object(helper, name, value); context.start(); self.addCleanup(context.stop)
        self.now = datetime.now(timezone.utc)
        self.name = 'simple-e12d94844961a3c3'
        self.facts = {'host_uuid': '11111111-2222-4333-8444-555555555555', 'host_machine_id': 'a' * 32,
            'host_boot_id': '66666666-7777-4888-8999-aaaaaaaaaaaa'}
        observation = {**self.facts, 'purpose': 'eventtab-simple-root-host-v1',
            'issued_at': self.now.isoformat(), 'expires_at': (self.now + timedelta(hours=2)).isoformat()}
        self.host_path = self.directory / 'simple-host.json'; self.host_path.write_text(json.dumps(observation))
        self.checkpoint = {'purpose': 'eventtab-simple-vocal-duet-checkpoint-v1', 'session': self.name, 'actor_id': 69,
            'score_ids': list(range(175, 191)), 'confirmed': helper.CONFIRM, 'archive_restore_confirmed': True,
            'criteria': {'93': 1197, '96': 1198, '97': 1199, '98': 1200},
            'pairs': {'14': [372, [1, 9], 'BFPT'], '15': [373, [4, 10], 'BTLEd']},
            'target': {**self.facts, 'root_observation_sha256': hashlib.sha256(self.host_path.read_bytes()).hexdigest(),
                'database': 'eventtab_db', 'database_oid': 123, 'database_role': 'eventtab_dbuser',
                'server_address': '127.0.0.1/32', 'server_port': 5433, 'in_recovery': False,
                'cluster_system_identifier': '123456789', 'server_version_num': '180006'},
            'backup': {'backup_sha256': 'b' * 64, 'relevant_rows_sha256': 'c' * 64,
                'scoped_rows_sha256': 'd' * 64, 'isolated_restore_database': 'eventtab_vd_dev_restore'},
            'review': {'roster_evidence_reference': 'TEST actual pair roster'}, 'verified_at': self.now.isoformat()}
        session = self.work / self.name; session.mkdir(mode=0o700)
        self.checkpoint_path = session / 'approval-request.json'; self.checkpoint_path.write_text(json.dumps(self.checkpoint))
        (self.work / 'simple-current.json').write_text(json.dumps({'session': self.name}))
        self.approval = self.directory / 'simple-approval.json'
        self.approval.write_text(json.dumps({'purpose': 'eventtab-simple-owner-vocal-duet-test-approval-v1',
            'checkpoint_sha256': helper.digest(self.checkpoint), 'owner_evidence_reference': helper.CONFIRM}))
        self.original = self.approval.read_bytes(); self.sha = hashlib.sha256(self.original).hexdigest()

    def reject(self):
        with helper.workflow_lock(), patch('builtins.input', side_effect=[
                'TEST operator accidentally entered confirmation as evidence',
                'REJECT VOCAL DUET OWNER APPROVAL ' + self.sha]), patch('builtins.print'):
            helper.reject_approval(self.now)

    def test_rejection_preserves_exact_bytes_and_is_idempotent(self):
        self.reject()
        self.assertEqual(self.approval.read_bytes(), self.original)
        record = helper.current_approval()
        self.assertEqual(base64.b64decode(record['rejection']['previous_approval_base64']), self.original)
        self.assertEqual(record['rejection']['approval_sha256'], self.sha)
        marker = self.directory / ('owner-rejected-' + self.sha + '.json')
        before = marker.read_bytes()
        with helper.workflow_lock(), patch('builtins.print'), patch('builtins.input') as prompt:
            helper.reject_approval(self.now)
        prompt.assert_not_called(); self.assertEqual(marker.read_bytes(), before)

    def test_legitimate_reapproval_preserves_checkpoint_and_links_rejection(self):
        self.reject(); before = self.checkpoint_path.read_bytes()
        rules = helper.evidence_rules()
        with helper.workflow_lock(), patch('builtins.print'), patch('builtins.input', side_effect=[
                'TEST Kamatera console ownership record for isolated test fixture', rules['DEVELOPMENT_CONFIRMATION'], helper.CONFIRM]):
            helper.approve(self.facts, datetime.now(timezone.utc))
        approval = helper.current_approval()['approval']
        self.assertIsNone(rules['approval_evidence_error'](approval))
        self.assertEqual(approval['supersedes_approval_sha256'], self.sha)
        self.assertEqual(approval['checkpoint_sha256'], helper.digest(self.checkpoint))
        self.assertEqual(self.checkpoint_path.read_bytes(), before)

    def test_confirmation_cannot_be_reused_as_evidence(self):
        self.reject()
        with helper.workflow_lock(), patch('builtins.print'), patch('builtins.input', return_value=helper.CONFIRM):
            with self.assertRaisesRegex(ValueError, 'not an evidence reference'):
                helper.approve(self.facts, self.now)
        self.assertEqual(self.approval.read_bytes(), self.original)

    def test_stale_checkpoint_reapproval_is_blocked(self):
        self.reject(); changed = {**self.checkpoint, 'verified_at': (self.now - timedelta(hours=3)).isoformat()}
        self.checkpoint_path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, 'checkpoint.verified_at'):
            helper.approve(self.facts, self.now)
        self.assertEqual(self.approval.read_bytes(), self.original)

    def test_symlink_and_group_writable_rules_are_rejected(self):
        target = self.root / 'actual.py'; self.rules.rename(target); self.rules.symlink_to(target)
        with self.assertRaises(OSError): helper.evidence_rules()
        self.rules.unlink(); target.rename(self.rules); self.rules.chmod(0o664)
        with self.assertRaises(ValueError): helper.evidence_rules()

    def test_unprivileged_process_cannot_modify_approval_or_audit(self):
        self.reject()
        paths = [self.approval, self.directory / ('owner-rejected-' + self.sha + '.json'), self.rules]
        pid = os.fork()
        if pid == 0:
            os.setgroups([]); os.setgid(65534); os.setuid(65534)
            for path in paths:
                try:
                    with path.open('wb') as output: output.write(b'forged')
                except PermissionError:
                    continue
                os._exit(1)
            os._exit(0)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        self.assertEqual(self.approval.read_bytes(), self.original)

    def test_lock_rejects_concurrent_helper_and_symlink_lock(self):
        with helper.workflow_lock():
            with self.assertRaises(BlockingIOError):
                with helper.workflow_lock(): pass
        lock = self.directory / 'owner-workflow.lock'; lock.unlink(); lock.symlink_to(self.approval)
        with self.assertRaises(OSError):
            with helper.workflow_lock(): pass


if __name__ == '__main__':
    before = database_snapshot()
    outcome = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LinuxOwnerEvidenceTests))
    if database_snapshot() != before:
        raise RuntimeError('Isolated database changed unexpectedly.')
    print('Isolated PostgreSQL read-only snapshots unchanged; no scoring tables or score records were created.')
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
