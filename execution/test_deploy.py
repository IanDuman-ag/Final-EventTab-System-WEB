"""Offline deployment safety tests. No SSH, service commands or real databases."""
import hashlib
import io
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import deploy_release as release
import deploy_server as server
from deploy_checks import configure_isolation
from local_test_database import cluster_ready

RID = '20261016T120000Z-' + 'a' * 12
BASE = {'manage.py': b'# manage', 'requirements.txt': b'Django==6.0.3\n',
        'core/settings.py': b'# settings', 'core/urls.py': b'# urls',
        'frontend/page.html': b'<h1>new</h1>'}


def archive(path, files=None, extra=None, manifest_change=None):
    files = BASE if files is None else files
    manifest = {'format': 1, 'release': RID, 'commit': 'a' * 40,
        'timestamp': '2026-10-16T12:00:00+00:00', 'branch': 'main',
        'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
                  for name, data in files.items()}}
    if manifest_change:
        manifest_change(manifest)
    with tarfile.open(path, 'w:gz') as tar:
        for name, data in [('release-manifest.json', json.dumps(manifest).encode()), *files.items()]:
            info = tarfile.TarInfo(name); info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if extra:
            tar.addfile(extra, io.BytesIO(b'x') if extra.isfile() else None)
    return manifest


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.archive = self.root / 'release.tar.gz'

    def test_allowlist_excludes_secrets_uploads_evidence_and_reset_tools(self):
        for name in ['.env', '.env.example', '.git/config', '.ssh/id_rsa', 'venv/a.py',
            'credentials.json', 'key.pem', 'backup.sql', 'db.sqlite3', 'debug.log',
            'assets/admin_reports/report-1.pdf', 'assets/teams/a.png', 'assets/candidates/a.png',
            'media/a.png', '.tmp/evidence.json', 'execution/vocal_duet_simple_owner.py',
            'events/vocal_duet_reset.py', 'events/test_vocal_duet_reset.py',
            'events/management/commands/reset_development_vocal_duet.py',
            'events/management/commands/vocal_duet_simple.py', 'events/tests.py',
            'frontend/secret.json', 'frontend/uploads/photo.png', 'frontend/.env',
            'core/__pycache__/settings.py', 'core/secrets.py']:
            with self.subTest(name=name):
                self.assertFalse(release.allowed(name))
        for name in ['core/settings.py', 'events/models.py', 'events/migrations/0001_initial.py',
            'frontend/admindash/vendor/bootstrap-5.3.8.min.css',
            'frontend/admindash/vendor/LICENSE.bootstrap', 'assets/USTP BG.png']:
            self.assertTrue(release.allowed(name), name)

    def test_unsafe_paths(self):
        for name in ['../core/settings.py', '/core/settings.py', 'core/../settings.py',
                     'C:/core/a.py', 'core\\a.py', 'core//a.py', './core/a.py', 'core/a.py\x00']:
            self.assertFalse(release.safe_name(name), name)

    def test_local_checks_remove_all_production_database_aliases(self):
        settings = SimpleNamespace(DATABASES={
            'default': {'HOST': '103.125.217.125', 'NAME': 'eventtab_db', 'PORT': '5433'},
            'production': {'HOST': '103.125.217.125'}}, USE_CLOUDINARY=True)
        configure_isolation(settings, self.root)
        self.assertEqual(set(settings.DATABASES), {'default'})
        connection = settings.DATABASES['default']
        self.assertEqual(connection, {'ENGINE': 'django.db.backends.dummy'})
        self.assertEqual(settings.MEDIA_ROOT, self.root / 'media')
        self.assertFalse(settings.USE_CLOUDINARY)

    def test_valid_archive_hashes_and_extraction(self):
        expected = archive(self.archive)
        self.assertEqual(release.validate_archive(self.archive, release.sha256(self.archive)), expected)
        destination = self.root / 'stage'; destination.mkdir()
        release.extract(self.archive, destination)
        self.assertEqual((destination / 'core/settings.py').read_bytes(), BASE['core/settings.py'])

    def test_bad_sha256_blocks(self):
        archive(self.archive)
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            release.validate_archive(self.archive, '0' * 64)

    def test_payload_hash_mismatch_blocks(self):
        archive(self.archive, manifest_change=lambda m: m['files']['manage.py'].update(sha256='0' * 64))
        with self.assertRaisesRegex(ValueError, 'hash/size'):
            release.validate_archive(self.archive, release.sha256(self.archive))

    def test_symlinks_hardlinks_duplicate_and_traversal_block(self):
        for name, kind in [('core/link.py', tarfile.SYMTYPE), ('core/link.py', tarfile.LNKTYPE),
                           ('manage.py', tarfile.REGTYPE), ('../escape.py', tarfile.REGTYPE),
                           ('/core/escape.py', tarfile.REGTYPE)]:
            with self.subTest(name=name, kind=kind):
                member = tarfile.TarInfo(name); member.type = kind; member.linkname = '/etc/passwd'
                member.size = 1 if kind == tarfile.REGTYPE else 0
                archive(self.archive, extra=member)
                with self.assertRaises(ValueError):
                    release.validate_archive(self.archive, release.sha256(self.archive))

    def test_oversize_blocks(self):
        archive(self.archive)
        with patch.object(release, 'MAX_BYTES', 10), self.assertRaisesRegex(ValueError, 'large'):
            release.validate_archive(self.archive, release.sha256(self.archive))

    def test_manifest_missing_extra_or_incomplete_blocks(self):
        for change in [lambda m: m['files'].pop('manage.py'),
                       lambda m: m['files'].update({'core/extra.py': {'sha256': '0' * 64, 'size': 0}}),
                       lambda m: m.update(release='invalid')]:
            archive(self.archive, manifest_change=change)
            with self.assertRaises(ValueError):
                release.validate_archive(self.archive, release.sha256(self.archive))

    def test_real_git_dry_run_writes_nothing_and_dirty_deployment_blocks(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        for name, data in BASE.items():
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        (self.root / '.env').write_text('DO_NOT_SHIP=secret')
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '-qm', 'fixture'], check=True, capture_output=True)
        output = self.root / 'outside-package'
        with patch('builtins.print'):
            result = release.build(self.root, output, dry_run=True)
        self.assertNotIn('.env', result['files']); self.assertFalse(output.exists())
        (self.root / 'core/settings.py').write_text('# edited')
        with self.assertRaisesRegex(ValueError, 'Commit reviewed'):
            release.build(self.root, output)

    def test_real_git_clean_build_matches_manifest(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        for name, data in BASE.items():
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '-qm', 'fixture'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), 'config', 'core.autocrlf', 'true'], check=True)
        (self.root / 'requirements.txt').write_bytes(BASE['requirements.txt'].replace(b'\n', b'\r\n'))
        subprocess.run(['git', '-C', str(self.root), 'add', 'requirements.txt'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), 'diff', '--cached', '--quiet'], check=True)
        with patch('builtins.print'):
            manifest = release.build(self.root, self.root / 'output')
        metadata = json.loads((self.root / 'output/release.json').read_text())
        self.assertEqual(release.validate_archive(metadata['archive'], metadata['sha256']), manifest)
        with tarfile.open(metadata['archive']) as tar:
            committed = subprocess.check_output(['git', '-C', str(self.root), 'show', 'HEAD:requirements.txt'])
            self.assertEqual(tar.extractfile('requirements.txt').read(), committed)


class LocalPostgresSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.process = Mock(pid=12345); self.process.poll.return_value = None

    def pid_file(self, pid=12345, port=55440, directory=None, status='ready'):
        (self.data / 'postmaster.pid').write_text(
            f'{pid}\n{directory or self.data}\n0\n{port}\n\n127.0.0.1\n0\n{status}\n')

    def test_only_owned_ready_cluster_can_be_used(self):
        self.assertFalse(cluster_ready(self.process, self.data, 55440))
        self.pid_file(status='starting')
        self.assertFalse(cluster_ready(self.process, self.data, 55440))
        self.pid_file()
        self.assertTrue(cluster_ready(self.process, self.data, 55440))

    def test_other_process_path_or_port_is_refused_before_connecting(self):
        for args in [{'pid': 999}, {'directory': self.data / 'other'}, {'port': 5433}]:
            with self.subTest(args=args):
                self.pid_file(**args)
                with self.assertRaisesRegex(RuntimeError, 'no connection was attempted'):
                    cluster_ready(self.process, self.data, 55440)

    def test_exited_cluster_is_not_replaced_by_existing_server(self):
        self.pid_file()
        self.process.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, 'exited'):
            cluster_ready(self.process, self.data, 55440)


class ServerFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / 'app'; self.app.mkdir()
        self.state = self.root / 'state'; self.state.mkdir()
        for name in ['incoming', 'releases', 'logs']:
            (self.state / name).mkdir()
        for name, data in BASE.items():
            path = self.app / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        (self.app / 'frontend/page.html').write_text('<h1>old</h1>')
        (self.app / '.env').write_text('PRIVATE=untouched')
        (self.app / 'assets/teams').mkdir(parents=True)
        (self.app / 'assets/teams/upload.png').write_bytes(b'upload')
        (self.app / 'staticfiles').mkdir(); (self.app / 'staticfiles/old.css').write_text('old')
        self.archive = self.state / 'incoming' / (RID + '.tar.gz')
        self.manifest = archive(self.archive)
        self.cfg = {'approved_requirements_sha256': release.sha256(self.app / 'requirements.txt')}
        self.audit = Mock()
        self.audit.run.return_value = ''
        for attr, value in [('APP', self.app), ('STATE', self.state)]:
            patcher = patch.object(server, attr, value); patcher.start(); self.addCleanup(patcher.stop)
        self.actual_service = server.service
        self.services = patch.object(server, 'service').start(); self.addCleanup(patch.stopall)
        self.health = patch.object(server, 'healthy').start()
        self.schema = patch.object(server, 'schema', return_value=[['events', '0001']]).start()
        self.history = patch.object(server, 'history', return_value=[['events', '0001']]).start()
        self.environment = patch.object(server, 'environment_snapshot', return_value=[['django', '6.0.3']]).start()

    def deploy(self):
        with patch('builtins.print'):
            server.deploy(RID, release.sha256(self.archive), self.cfg, self.audit)

    def test_success_preserves_secrets_uploads_and_records_checkpoint(self):
        self.deploy()
        self.assertEqual((self.app / 'frontend/page.html').read_bytes(), BASE['frontend/page.html'])
        self.assertEqual((self.app / '.env').read_text(), 'PRIVATE=untouched')
        self.assertEqual((self.app / 'assets/teams/upload.png').read_bytes(), b'upload')
        self.assertEqual(server.read_json(self.state / 'current.json')['release'], RID)
        self.assertFalse((self.state / 'pending.json').exists())
        self.assertEqual([call.args[1] for call in self.services.call_args_list], ['stop', 'restart'])

    def test_repeat_success_is_idempotent(self):
        self.deploy(); self.services.reset_mock()
        self.deploy()
        self.services.assert_not_called()

    def test_health_failure_restores_source_and_static(self):
        self.health.side_effect = [None, ValueError('new release unhealthy'), None]
        with self.assertRaisesRegex(ValueError, 'unhealthy'):
            self.deploy()
        self.assertEqual((self.app / 'frontend/page.html').read_text(), '<h1>old</h1>')
        self.assertEqual((self.app / 'staticfiles/old.css').read_text(), 'old')
        self.assertFalse((self.state / 'pending.json').exists())
        self.assertEqual(server.read_json(self.state / 'current.json'), {})

    def test_partial_copy_failure_rolls_back(self):
        real_copy = server.copy_atomic
        first = True
        def copy(source, target):
            nonlocal first
            if first:
                first = False
                real_copy(source, target)
                raise OSError('disk failure')
            return real_copy(source, target)
        with patch.object(server, 'copy_atomic', side_effect=copy), self.assertRaises(OSError):
            self.deploy()
        self.assertEqual((self.app / 'frontend/page.html').read_text(), '<h1>old</h1>')

    def test_rollback_removes_only_newly_introduced_managed_files(self):
        archive(self.archive, files={**BASE, 'core/added.py': b'# new source'})
        self.health.side_effect = [None, ValueError('unhealthy'), None]
        with self.assertRaisesRegex(ValueError, 'unhealthy'):
            self.deploy()
        self.assertFalse((self.app / 'core/added.py').exists())
        self.assertEqual((self.app / 'assets/teams/upload.png').read_bytes(), b'upload')

    def test_permission_failure_blocks_before_service_stop(self):
        with patch.object(server.os, 'access', return_value=False), self.assertRaisesRegex(ValueError, 'cannot replace'):
            self.deploy()
        self.services.assert_not_called()

    def test_dependency_change_blocks_before_service_or_file_changes(self):
        self.cfg['approved_requirements_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Dependency review'):
            self.deploy()
        self.services.assert_not_called()
        self.assertEqual((self.app / 'frontend/page.html').read_text(), '<h1>old</h1>')

    def test_migration_changes_block_before_service(self):
        archive(self.archive, files={**BASE, 'events/migrations/0002_new.py': b'# schema change'})
        with self.assertRaisesRegex(ValueError, 'migration review required'):
            self.deploy()
        self.services.assert_not_called()

    def test_pending_migrations_block_before_service(self):
        self.schema.side_effect = ValueError('Deployment blocked: migration review required')
        with self.assertRaisesRegex(ValueError, 'migration review'):
            self.deploy()
        self.services.assert_not_called()

    def test_pending_recovery_blocks_next_deployment(self):
        server.atomic_json(self.state / 'pending.json', {'release': RID})
        with self.assertRaisesRegex(ValueError, 'interrupted deployment'):
            self.deploy()
        self.services.assert_not_called()

    def test_low_disk_blocks(self):
        with patch.object(server.shutil, 'disk_usage', return_value=Mock(free=1)), self.assertRaisesRegex(ValueError, 'disk space'):
            self.deploy()
        self.services.assert_not_called()

    def test_rollback_requires_confirmation(self):
        self.deploy(); self.services.reset_mock()
        with patch.object(sys.stdin, 'isatty', return_value=False), self.assertRaisesRegex(ValueError, 'confirmation'):
            server.rollback(self.cfg, self.audit)
        self.services.assert_not_called()

    def test_explicit_rollback_restores_previous_release(self):
        self.deploy()
        with patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', return_value=RID), patch('builtins.print'):
            server.rollback(self.cfg, self.audit)
        self.assertEqual((self.app / 'frontend/page.html').read_text(), '<h1>old</h1>')
        self.assertEqual(server.read_json(self.state / 'current.json'), {})

    def assert_rollback_rejected_without_changes(self, message=None):
        """The running application, static files and journals must be untouched."""
        def snapshot(root):
            return {p.relative_to(root).as_posix(): p.read_bytes()
                    for p in root.rglob('*') if p.is_file() and not p.is_symlink()}
        application = snapshot(self.app)
        journals = {name: (self.state / name).read_bytes() if (self.state / name).exists() else None
                    for name in ('current.json', 'pending.json')}
        self.services.reset_mock()
        with patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', return_value=RID):
            with self.assertRaises((ValueError, OSError)) as raised:
                server.rollback(self.cfg, self.audit)
        if message:
            self.assertIn(message, str(raised.exception))
        self.services.assert_not_called()
        self.assertEqual(snapshot(self.app), application)
        self.assertEqual({name: (self.state / name).read_bytes() if (self.state / name).exists() else None
                          for name in journals}, journals)

    def checkpoint_path(self):
        return self.state / 'releases' / RID / 'checkpoint.json'

    def rewrite_checkpoint(self, change):
        # Simulate internally inconsistent metadata even with a matching receipt.
        checkpoint = server.read_json(self.checkpoint_path())
        change(checkpoint)
        server.atomic_json(self.checkpoint_path(), checkpoint)
        current = server.read_json(self.state / 'current.json')
        current['checkpoint_sha256'] = release.sha256(self.checkpoint_path())
        server.atomic_json(self.state / 'current.json', current)

    def test_corrupt_backup_aborts_manual_rollback_before_service_stop(self):
        self.deploy()
        (self.checkpoint_path().parent / 'backup/frontend/page.html').write_text('corrupt')
        self.assert_rollback_rejected_without_changes('backup verification')

    def test_corrupt_static_backup_aborts_before_service_stop(self):
        self.deploy()
        (self.checkpoint_path().parent / 'staticfiles/old.css').write_text('corrupt')
        self.assert_rollback_rejected_without_changes('Staticfiles backup verification')

    def test_missing_checkpoint_aborts_before_service_stop(self):
        self.deploy(); self.checkpoint_path().unlink()
        self.assert_rollback_rejected_without_changes()

    def test_corrupt_checkpoint_receipt_aborts_before_service_stop(self):
        self.deploy(); self.checkpoint_path().write_text('{}')
        self.assert_rollback_rejected_without_changes('integrity')

    def test_malformed_checkpoint_aborts_before_service_stop(self):
        self.deploy(); self.checkpoint_path().write_text('{')
        current = server.read_json(self.state / 'current.json')
        current['checkpoint_sha256'] = release.sha256(self.checkpoint_path())
        server.atomic_json(self.state / 'current.json', current)
        self.assert_rollback_rejected_without_changes()

    def test_omitted_checkpoint_member_aborts_before_service_stop(self):
        self.deploy()
        self.rewrite_checkpoint(lambda checkpoint: checkpoint['before'].clear())
        self.assert_rollback_rejected_without_changes('file inventory')

    def test_release_identity_mismatch_aborts_before_service_stop(self):
        self.deploy()
        self.rewrite_checkpoint(lambda checkpoint: checkpoint.update(release='20261015T120000Z-' + 'b' * 12))
        self.assert_rollback_rejected_without_changes('identity mismatch')

    def test_missing_backup_member_aborts_before_service_stop(self):
        self.deploy(); (self.checkpoint_path().parent / 'backup/frontend/page.html').unlink()
        self.assert_rollback_rejected_without_changes('backup inventory')

    def test_extra_backup_member_aborts_before_service_stop(self):
        self.deploy(); (self.checkpoint_path().parent / 'backup/extra.py').write_text('extra')
        self.assert_rollback_rejected_without_changes('backup inventory')

    def test_corrupt_archive_aborts_before_service_stop(self):
        self.deploy(); self.archive.write_bytes(b'corrupt')
        self.assert_rollback_rejected_without_changes('SHA256')

    def test_corrupt_baseline_settings_aborts_before_service_stop(self):
        self.deploy(); (self.checkpoint_path().parent / 'baseline/settings.py').write_text('corrupt')
        self.assert_rollback_rejected_without_changes('settings integrity')

    def test_changed_unchanged_source_aborts_before_service_stop(self):
        self.deploy(); (self.app / 'core/urls.py').write_text('manual edit')
        self.assert_rollback_rejected_without_changes('unexpected server edit')

    def test_changed_requirements_aborts_before_service_stop(self):
        self.deploy(); (self.app / 'requirements.txt').write_text('unapproved')
        self.assert_rollback_rejected_without_changes()

    def test_invalid_pending_reference_aborts_before_service_stop(self):
        self.deploy(); server.atomic_json(self.state / 'pending.json', {})
        self.assert_rollback_rejected_without_changes('checkpoint reference')

    def test_legacy_checkpoint_aborts_before_service_stop(self):
        self.deploy()
        current = server.read_json(self.state / 'current.json'); current.pop('checkpoint_sha256')
        server.atomic_json(self.state / 'current.json', current)
        self.assert_rollback_rejected_without_changes('legacy')

    def test_inconsistent_deployment_state_aborts_before_service_stop(self):
        self.deploy()
        current = server.read_json(self.state / 'current.json'); current['commit'] = 'b' * 40
        server.atomic_json(self.state / 'current.json', current)
        self.assert_rollback_rejected_without_changes('deployment state')

    def test_managed_release_rollback_preserves_previous_checkpoint(self):
        self.deploy()
        previous = server.read_json(self.state / 'current.json')
        second_id = '20261016T120001Z-' + 'a' * 12
        second_archive = self.state / 'incoming' / (second_id + '.tar.gz')
        archive(second_archive, files={**BASE, 'frontend/page.html': b'<h1>second</h1>'},
                manifest_change=lambda manifest: manifest.update(release=second_id))
        with patch('builtins.print'):
            server.deploy(second_id, release.sha256(second_archive), self.cfg, self.audit)
        with patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', return_value=second_id), patch('builtins.print'):
            server.rollback(self.cfg, self.audit)
        self.assertEqual(server.read_json(self.state / 'current.json'), previous)
        self.assertEqual((self.app / 'frontend/page.html').read_bytes(), BASE['frontend/page.html'])
        with patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', return_value=RID), patch('builtins.print'):
            server.rollback(self.cfg, self.audit)
        self.assertEqual((self.app / 'frontend/page.html').read_text(), '<h1>old</h1>')

    def test_symlink_backup_aborts_before_service_stop(self):
        self.deploy()
        backup = self.checkpoint_path().parent / 'backup/frontend/page.html'
        backup.unlink(); backup.symlink_to(self.app / 'frontend/page.html')
        self.assert_rollback_rejected_without_changes('Unsafe rollback backup')

    def test_permission_failure_aborts_rollback_before_service_stop(self):
        self.deploy()
        with patch.object(server.os, 'access', return_value=False):
            self.assert_rollback_rejected_without_changes('cannot replace')

    def test_source_revalidated_after_service_stop(self):
        self.deploy()
        def stop_then_edit(audit, action):
            if action == 'stop':
                (self.app / 'frontend/page.html').write_text('concurrent edit')
        self.services.side_effect = stop_then_edit
        with patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', return_value=RID):
            with self.assertRaisesRegex(ValueError, 'unexpected server edit'):
                server.rollback(self.cfg, self.audit)
        self.assertEqual((self.app / 'frontend/page.html').read_text(), 'concurrent edit')
        self.assertTrue((self.state / 'pending.json').exists())

    def test_pending_recovery_after_interrupted_restore(self):
        self.deploy()
        actual_copy = server.copy_atomic
        with patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', return_value=RID):
            with patch.object(server, 'copy_atomic', side_effect=OSError('simulated interrupted restore')):
                with self.assertRaisesRegex(OSError, 'interrupted restore'):
                    server.rollback(self.cfg, self.audit)
            self.assertTrue((self.state / 'pending.json').exists())
            with patch.object(server, 'copy_atomic', side_effect=actual_copy), patch('builtins.print'):
                server.rollback(self.cfg, self.audit)
        self.assertEqual((self.app / 'frontend/page.html').read_text(), '<h1>old</h1>')
        self.assertFalse((self.state / 'pending.json').exists())

    @unittest.skipUnless(sys.platform == 'linux', 'Linux artifact permissions')
    def test_untrusted_checkpoint_aborts_before_service_stop(self):
        self.deploy(); self.checkpoint_path().chmod(0o666)
        self.assert_rollback_rejected_without_changes('Untrusted rollback artifact')

    @unittest.skipUnless(sys.platform == 'linux', 'Isolated Linux HTTP process integration')
    def test_linux_http_process_rollback_and_failure_recovery(self):
        # Real Linux process/filesystem/HTTP; fixed production service and DB
        # adapters remain mocked. Loopback only, no Kamatera or systemd calls.
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        handler = '''
from http.server import BaseHTTPRequestHandler, HTTPServer
import sys
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
        self.wfile.write(b'{"status":"ok","backend":"django"}')
    def log_message(self, *args): pass
HTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()
'''
        processes = []
        def stop():
            if processes and processes[-1].poll() is None:
                processes[-1].terminate(); processes[-1].wait(timeout=10)
        self.addCleanup(stop)
        def start():
            processes.append(subprocess.Popen([sys.executable, '-B', '-c', handler, str(port)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            deadline = time.monotonic() + 10
            while True:
                try:
                    server.http_health(); return
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
        def service_process(audit, action):
            stop()
            if action == 'restart': start()
        with patch.object(server, 'HEALTH', f'http://127.0.0.1:{port}/api/health/'):
            start()
            self.services.side_effect = service_process
            self.health.side_effect = lambda *args, **kwargs: server.http_health()
            self.deploy()
            running = processes[-1]
            backup = self.checkpoint_path().parent / 'backup/frontend/page.html'
            original = backup.read_bytes(); backup.write_bytes(b'corrupt')
            self.assert_rollback_rejected_without_changes('backup verification')
            self.assertIsNone(running.poll()); server.http_health()
            backup.write_bytes(original)
            with patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', return_value=RID):
                with patch('builtins.print'):
                    server.rollback(self.cfg, self.audit)
            server.http_health()
            self.assertEqual((self.app / 'frontend/page.html').read_text(), '<h1>old</h1>')
            # A fresh release ID is needed after rollback; exercise a failed
            # health check during a second isolated deploy and its real restart.
            second_id = '20261016T120001Z-' + 'a' * 12
            second_archive = self.state / 'incoming' / (second_id + '.tar.gz')
            archive(second_archive, manifest_change=lambda manifest: manifest.update(release=second_id))
            failures = [False, True, False]
            def health(*args, **kwargs):
                if failures.pop(0): raise ValueError('simulated failed release health')
                server.http_health()
            self.health.side_effect = health
            with patch('builtins.print'), self.assertRaisesRegex(ValueError, 'simulated failed release health'):
                server.deploy(second_id, release.sha256(second_archive), self.cfg, self.audit)
            server.http_health()
            self.assertEqual((self.app / 'frontend/page.html').read_text(), '<h1>old</h1>')
            self.assertFalse((self.state / 'pending.json').exists())

    def test_rollback_blocks_changed_schema_without_stopping_service(self):
        self.deploy(); self.services.reset_mock(); self.history.return_value = [['events', '0002']]
        with patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', return_value=RID), self.assertRaisesRegex(ValueError, 'migration history'):
            server.rollback(self.cfg, self.audit)
        self.services.assert_not_called()

    def test_rollback_blocks_changed_installed_packages(self):
        self.deploy(); self.services.reset_mock(); self.environment.return_value = [['django', 'different']]
        with patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', return_value=RID), self.assertRaisesRegex(ValueError, 'installed dependencies changed'):
            server.rollback(self.cfg, self.audit)
        self.services.assert_not_called()

    def test_remote_manual_source_changes_are_preserved(self):
        server.atomic_json(self.state / 'current.json', {'release': '20261015T120000Z-' + 'b' * 12,
            'files': {'frontend/page.html': {'sha256': '0' * 64, 'size': 0}}})
        with self.assertRaisesRegex(ValueError, 'Server source changed outside deployment'):
            self.deploy()
        self.assertEqual((self.app / 'frontend/page.html').read_text(), '<h1>old</h1>')
        self.services.assert_not_called()

    def test_restore_refuses_unexpected_server_edit(self):
        self.deploy()
        checkpoint = server.read_json(self.state / 'releases' / RID / 'checkpoint.json')
        (self.app / 'frontend/page.html').write_text('manual edit')
        with self.assertRaisesRegex(ValueError, 'unexpected server edit'):
            server.restore(checkpoint, self.audit)
        self.assertEqual((self.app / 'frontend/page.html').read_text(), 'manual edit')

    def test_backup_corruption_blocks_restore(self):
        self.deploy()
        checkpoint = server.read_json(self.state / 'releases' / RID / 'checkpoint.json')
        backup = self.state / 'releases' / RID / 'backup/frontend/page.html'
        backup.write_text('corruption')
        with self.assertRaisesRegex(ValueError, 'backup verification'):
            server.restore(checkpoint, self.audit)

    def test_static_backup_corruption_blocks_before_restoring_source(self):
        self.deploy()
        checkpoint = server.read_json(self.state / 'releases' / RID / 'checkpoint.json')
        (self.state / 'releases' / RID / 'staticfiles/old.css').write_text('corrupt')
        with self.assertRaisesRegex(ValueError, 'Staticfiles backup verification'):
            server.restore(checkpoint, self.audit)
        self.assertEqual((self.app / 'frontend/page.html').read_bytes(), BASE['frontend/page.html'])

    def test_changed_source_removed_from_next_release_requires_review(self):
        server.atomic_json(self.state / 'current.json', {'release': '20261015T120000Z-' + 'b' * 12,
            'files': {'events/old.py': {'sha256': '0' * 64, 'size': 1}}})
        with self.assertRaisesRegex(ValueError, 'Source removal requires separate review'):
            self.deploy()
        self.services.assert_not_called()

    def test_symlink_target_is_rejected(self):
        target = self.app / 'frontend/page.html'; target.unlink()
        outside = self.root / 'outside'; outside.write_text('keep')
        target.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.deploy()
        self.services.assert_not_called()
        self.assertEqual(outside.read_text(), 'keep')

    def test_subprocess_failure_is_reported_and_audited(self):
        audit = server.Audit('test', RID)
        with patch.object(server.subprocess, 'run', return_value=Mock(returncode=1, stdout='', stderr='failure')):
            with self.assertRaisesRegex(ValueError, 'Check failed'):
                audit.run(['safe-command'])
        records = [json.loads(line) for line in audit.path.read_text().splitlines()]
        self.assertEqual(records[0]['returncode'], 1)

    def test_only_fixed_service_commands_are_accepted(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            self.actual_service(self.audit, 'arbitrary')

    def test_rollback_failure_leaves_recovery_journal(self):
        self.health.side_effect = [None, ValueError('unhealthy'), ValueError('rollback unhealthy')]
        with patch('builtins.print'), self.assertRaises(ValueError):
            self.deploy()
        self.assertTrue((self.state / 'pending.json').exists())

    @unittest.skipUnless(sys.platform == 'linux', 'Linux flock integration')
    def test_linux_lock_prevents_simultaneous_deployments(self):
        with server.deployment_lock():
            with self.assertRaisesRegex(ValueError, 'Another deployment'):
                with server.deployment_lock():
                    self.fail('Second deployment acquired the lock.')


if __name__ == '__main__':
    unittest.main()
