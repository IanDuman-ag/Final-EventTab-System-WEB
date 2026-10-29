"""Offline public publication tests; root-only filesystem cases run in WSL."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import deploy_public as public


class PublicPathTests(unittest.TestCase):
    def test_exact_keys(self):
        for name in ['../secret.css', '/a.css', 'a//b.css', 'a/../b.css', 'a\\b.css',
                     '.env', 'a/.hidden.css', 'a.css\x00', 'C:/a.css', 'a/./b.css']:
            with self.subTest(name=name):
                self.assertFalse(public.safe_key(name))
        self.assertTrue(public.safe_key('admin/css/base.css'))
        self.assertTrue(public.safe_key('USTP BG.png'))

    def test_installed_allowlist_shape(self):
        values = json.loads(Path(__file__).with_name('publication-allowlist.json').read_text())
        self.assertEqual(len(values['static']), 327)
        self.assertEqual(len(values['assets']), 8)
        for kind in ['static', 'assets']:
            self.assertEqual(values[kind], sorted(set(values[kind])))
            self.assertTrue(all(public.safe_key(name) for name in values[kind]))

    def test_non_root_is_rejected(self):
        with patch.object(public.os, 'geteuid', return_value=1000, create=True):
            with self.assertRaises(ValueError):
                public.configuration()


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Linux root filesystem safety cases')
class PublicFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='eventtab-public-tests-', dir='/run')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = self.root / 'app'
        self.destinations = {'static': self.root / 'public/static', 'assets': self.root / 'public/assets'}
        self.state = self.root / 'state'
        self.config = self.root / 'config.json'
        self.allowlist = self.root / 'allowlist.json'
        for directory in [self.app / 'staticfiles/nested', self.app / 'assets', *self.destinations.values()]:
            directory.mkdir(parents=True)
        self.values = {'format': 1, 'static': ['nested/app.css'], 'assets': ['logo.png']}
        self.allowlist.write_text(json.dumps(self.values))
        self.allowlist_hash = public.digest(self.allowlist.read_bytes())
        self.config.write_text(json.dumps({'machine_id': Path('/etc/machine-id').read_text().strip(),
                                          'allowlist_sha256': self.allowlist_hash}))
        for name, value in [('APP', self.app), ('PUBLIC', self.destinations), ('STATE', self.state),
                            ('CONFIG', self.config), ('ALLOWLIST', self.allowlist)]:
            override = patch.object(public, name, value)
            override.start()
            self.addCleanup(override.stop)
        self.set_sources(b'first')
        self.keys, self.allowlist_hash = public.configuration()

    def set_sources(self, value):
        (self.app / 'staticfiles/nested/app.css').write_bytes(value + b'-css')
        (self.app / 'assets/logo.png').write_bytes(value + b'-image')

    def publish(self):
        with public.locked():
            return public.synchronize(self.keys, self.allowlist_hash)

    def inventory(self):
        return {kind + '/' + path.relative_to(root).as_posix(): path.read_bytes()
                for kind, root in self.destinations.items() for path in root.rglob('*') if path.is_file()}

    def test_publish_noop_and_root_modes(self):
        result = self.publish()
        self.assertEqual(result['result'], 'published')
        self.assertEqual(self.publish()['result'], 'unchanged')
        self.assertIsNone(public.load('pending.json'))
        for root in self.destinations.values():
            for path in root.rglob('*'):
                self.assertEqual(path.stat().st_uid, 0)
                self.assertEqual(path.stat().st_mode & 0o777, 0o755 if path.is_dir() else 0o644)
        for path in self.state.rglob('*'):
            self.assertEqual(path.stat().st_uid, 0)
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)

    def test_unlisted_files_and_uploads_unchanged(self):
        extra = self.destinations['assets'] / 'user-upload.png'
        extra.write_bytes(b'private unmanaged fixture')
        (self.app / 'assets/not-approved.png').write_bytes(b'not published')
        self.publish()
        self.assertEqual(extra.read_bytes(), b'private unmanaged fixture')
        self.assertFalse((self.destinations['assets'] / 'not-approved.png').exists())

    def test_leaf_and_ancestor_symlinks_rejected(self):
        target = self.app / 'assets/logo.png'
        target.unlink()
        target.symlink_to(self.app / 'staticfiles/nested/app.css')
        with self.assertRaises(OSError):
            self.publish()
        target.unlink()
        target.write_bytes(b'restored')
        nested = self.app / 'staticfiles/nested'
        nested.rename(nested.with_name('real-nested'))
        nested.symlink_to(nested.with_name('real-nested'), target_is_directory=True)
        with self.assertRaises(OSError):
            self.publish()
        self.assertEqual(self.inventory(), {})

    def test_source_hardlink_rejected(self):
        os.link(self.app / 'assets/logo.png', self.app / 'assets/another.png')
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.inventory(), {})

    def test_destination_symlink_rejected(self):
        destination = self.destinations['assets'] / 'logo.png'
        destination.symlink_to(self.app / 'assets/logo.png')
        with self.assertRaises(OSError):
            self.publish()

    def test_group_writable_public_directory_rejected(self):
        self.destinations['assets'].chmod(0o775)
        with self.assertRaises(ValueError):
            self.publish()

    def test_config_machine_hash_and_modes_rejected(self):
        config = json.loads(self.config.read_text())
        self.config.write_text(json.dumps({**config, 'machine_id': 'wrong'}))
        with self.assertRaises(ValueError):
            public.configuration()
        self.config.write_text(json.dumps({**config, 'allowlist_sha256': '0' * 64}))
        with self.assertRaises(ValueError):
            public.configuration()
        self.config.write_text(json.dumps(config))
        self.config.chmod(0o664)
        with self.assertRaises(ValueError):
            public.configuration()

    def test_partial_failure_restores_public_and_removes_new_files(self):
        actual = public.atomic
        failed = False

        def injected(root, name, raw, **options):
            nonlocal failed
            if root == self.destinations['static'] and not failed:
                failed = True
                raise OSError('deliberate publication failure')
            return actual(root, name, raw, **options)

        with patch.object(public, 'atomic', side_effect=injected):
            with self.assertRaisesRegex(OSError, 'deliberate'):
                self.publish()
        self.assertEqual(self.inventory(), {})
        self.assertIsNone(public.load('pending.json'))
        self.assertEqual(len(list((self.state / 'checkpoints').glob('*/recovered.json'))), 1)

    def test_partial_failure_restores_previous_versions(self):
        self.publish()
        before = self.inventory()
        previous = public.load('current.json')
        self.set_sources(b'second')
        actual = public.atomic
        failed = False

        def injected(root, name, raw, **options):
            nonlocal failed
            if root == self.destinations['static'] and not failed:
                failed = True
                raise OSError('deliberate publication failure')
            return actual(root, name, raw, **options)

        with patch.object(public, 'atomic', side_effect=injected):
            with self.assertRaises(OSError):
                self.publish()
        self.assertEqual(self.inventory(), before)
        self.assertEqual(public.load('current.json'), previous)

    def test_corrupt_checkpoint_and_backup_block_before_restore(self):
        self.publish()
        self.set_sources(b'second')
        self.publish()
        reference = public.load('current.json')
        before = self.inventory()
        path = self.state / 'checkpoints' / reference['id'] / 'backup/assets/logo.png'
        path.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'backup checksum'):
            public.restore(reference, self.keys, self.allowlist_hash)
        self.assertEqual(self.inventory(), before)
        with self.assertRaisesRegex(ValueError, 'checkpoint checksum'):
            public.checkpoint({**reference, 'sha256': '0' * 64}, self.keys, self.allowlist_hash)

    def test_pending_recovery_and_external_drift(self):
        self.publish()
        reference = public.load('current.json')
        public.save('pending.json', reference)
        with self.assertRaisesRegex(ValueError, 'recover first'):
            self.publish()
        (self.destinations['assets'] / 'logo.png').write_bytes(b'outside drift')
        before = self.inventory()
        with self.assertRaisesRegex(ValueError, 'outside the checkpoint'):
            public.restore(reference, self.keys, self.allowlist_hash)
        self.assertEqual(self.inventory(), before)
        self.assertEqual(public.load('pending.json'), reference)

    def test_real_lock_contention(self):
        with public.locked():
            with self.assertRaises(BlockingIOError):
                with public.locked():
                    self.fail('second publication lock acquired')

    def test_interrupted_publication_retains_journal_and_recovers(self):
        self.publish()
        before = self.inventory()
        previous = public.load('current.json')
        self.set_sources(b'second')
        actual = public.atomic

        def interrupted(root, name, raw, **options):
            if root == self.destinations['static']:
                raise SystemExit('simulated process interruption')
            return actual(root, name, raw, **options)

        with patch.object(public, 'atomic', side_effect=interrupted):
            with self.assertRaises(SystemExit):
                self.publish()
        self.assertIsNotNone(public.pending())
        self.assertNotEqual(self.inventory(), before)
        with patch.object(sys, 'argv', ['deploy_public.py', 'recover']):
            public.main()
        self.assertEqual(self.inventory(), before)
        self.assertEqual(public.load('current.json'), previous)
        self.assertIsNone(public.pending())

    def test_null_pending_journal_is_not_ignored(self):
        self.publish()
        public.save('pending.json', None)
        with self.assertRaisesRegex(ValueError, 'Malformed pending'):
            self.publish()

    def test_public_drift_blocks_new_publication(self):
        self.publish()
        (self.destinations['assets'] / 'logo.png').write_bytes(b'outside drift')
        self.set_sources(b'second')
        before = self.inventory()
        with self.assertRaisesRegex(ValueError, 'changed since the last publication'):
            self.publish()
        self.assertEqual(self.inventory(), before)
        self.assertIsNone(public.pending())

    def test_no_arbitrary_checkpoint_ids(self):
        with self.assertRaises(ValueError):
            public.checkpoint({'id': '../outside', 'sha256': '0' * 64}, self.keys, self.allowlist_hash)

    def test_rollback_requires_application_to_match_previous_public_version(self):
        self.publish()
        self.set_sources(b'second')
        self.publish()
        with patch.object(sys, 'argv', ['deploy_public.py', 'rollback']):
            with self.assertRaisesRegex(ValueError, 'Restore the application'):
                public.main()
        self.set_sources(b'first')
        with patch.object(sys, 'argv', ['deploy_public.py', 'rollback']):
            public.main()
        self.assertEqual((self.destinations['assets'] / 'logo.png').read_bytes(), b'first-image')
        self.assertIsNone(public.load('pending.json'))


if __name__ == '__main__':
    unittest.main()
