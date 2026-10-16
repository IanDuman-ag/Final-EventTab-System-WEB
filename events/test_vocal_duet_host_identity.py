"""Linux identity trust tests; filesystem/kernel fixtures are explicit, never live."""
from datetime import timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from . import vocal_duet_development as development
from . import vocal_duet_host_identity as identity
from .vocal_duet_reset import ResetError


class SignedHostIdentityTests(SimpleTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        now = timezone.now()
        self.observation = {'purpose': 'eventtab-root-host-identity-v1', 'collector': 'root-fixed-path-host-export-v1',
            'host_uuid': '11111111-2222-4333-8444-555555555555', 'machine_id': 'a' * 32,
            'boot_id': '66666666-7777-4888-8999-aaaaaaaaaaaa', 'challenge': 'b' * 64,
            'issued_at': (now - timedelta(minutes=1)).isoformat(),
            'expires_at': (now + timedelta(hours=1)).isoformat()}
        self.challenge = {'purpose': 'eventtab-host-identity-challenge-v1', 'nonce': 'b' * 64,
            'created_at': (now - timedelta(minutes=2)).isoformat()}
        (self.root / 'machine-id').write_text('a' * 32)
        (self.root / 'boot-id').write_text(self.observation['boot_id'])
        self.save()
        self.original_verify = development._verify_signature
        for name, value in (('DIRECTORY', self.root), ('_root_controlled', None), ('_verify_signature', None)):
            mock = patch.object(development, name, **({'return_value': None} if name.startswith('_') else {'new': value}))
            mock.start(); self.addCleanup(mock.stop)
        for name, value in (('MACHINE_ID', self.root / 'machine-id'), ('BOOT_ID', self.root / 'boot-id')):
            mock = patch.object(identity, name, value)
            mock.start(); self.addCleanup(mock.stop)

    def save(self):
        (self.root / 'host-attestation.json').write_text(json.dumps(self.observation))
        (self.root / 'host-challenge.json').write_text(json.dumps(self.challenge))

    def test_valid_unprivileged_observation_does_not_read_dmi_or_database(self):
        original = Path.read_text
        def guarded(path, *args, **kwargs):
            if str(path).endswith('product_uuid'):
                raise PermissionError('DMI is root-only')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', autospec=True, side_effect=guarded):
            result = identity.verified_host_identity()
        self.assertEqual(result['host_uuid'], self.observation['host_uuid'])
        self.assertEqual(result['host_boot_id'], self.observation['boot_id'])
        self.assertEqual(result['host_attestation_sha256'], hashlib.sha256((self.root / 'host-attestation.json').read_bytes()).hexdigest())

    def test_missing_attestation_is_actionable_without_root_django_fallback(self):
        (self.root / 'host-attestation.json').unlink()
        with self.assertRaisesRegex(ResetError, 'do not run Django as root or chmod DMI'):
            identity.verified_host_identity()

    def test_rotated_root_challenge_prevents_replay(self):
        self.challenge['nonce'] = 'c' * 64
        self.save()
        with self.assertRaisesRegex(ResetError, 'root challenge'):
            identity.verified_host_identity()

    def test_different_machine_or_new_boot_rejects_previous_certificate(self):
        (self.root / 'machine-id').write_text('d' * 32)
        with self.assertRaisesRegex(ResetError, 'different machine or boot'):
            identity.verified_host_identity()
        (self.root / 'machine-id').write_text('a' * 32)
        (self.root / 'boot-id').write_text('bbbbbbbb-cccc-4ddd-8eee-ffffffffffff')
        with self.assertRaisesRegex(ResetError, 'different machine or boot'):
            identity.verified_host_identity()

    def test_expired_future_wrong_purpose_or_unbounded_attestation_is_rejected(self):
        original = dict(self.observation)
        for change in ({'expires_at': (timezone.now() - timedelta(seconds=1)).isoformat()},
                       {'issued_at': (timezone.now() + timedelta(minutes=1)).isoformat()},
                       {'expires_at': (timezone.now() + timedelta(days=2)).isoformat()},
                       {'purpose': 'eventtab-development-vocal-duet-test-reset-v1'}):
            self.observation = {**original, **change}; self.save()
            with self.assertRaises(ResetError): identity.verified_host_identity()

    def test_bad_uuid_and_untrusted_files_fail_closed(self):
        self.observation['host_uuid'] = '0' * 32; self.save()
        with self.assertRaises(ResetError): identity.verified_host_identity()
        with patch.object(development, '_root_controlled', side_effect=ResetError('Symlink/non-root/writable path')):
            with self.assertRaisesRegex(ResetError, 'Symlink'):
                identity.verified_host_identity()

    def test_actual_off_server_signature_and_payload_tamper(self):
        executable = shutil.which('openssl') or 'C:/Program Files/Git/usr/bin/openssl.exe'
        if not Path(executable).exists(): self.skipTest('OpenSSL unavailable')
        private = self.root / 'test-only-private.pem'
        subprocess.run([executable, 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', str(private)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([executable, 'pkey', '-in', str(private), '-pubout', '-out', str(self.root / 'authority-public.pem')], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([executable, 'dgst', '-sha256', '-sign', str(private), '-out', str(self.root / 'host-attestation.sig'), str(self.root / 'host-attestation.json')], check=True)
        run = subprocess.run
        def native(args, **kwargs):
            import os
            if os.name == 'nt': kwargs.pop('env', None)
            return run([executable, *args[1:]], **kwargs)
        with patch.object(development, '_verify_signature', self.original_verify), patch.object(development.subprocess, 'run', side_effect=native):
            self.assertEqual(identity.verified_host_identity()['host_uuid'], self.observation['host_uuid'])
            self.observation['host_uuid'] = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'; self.save()
            with self.assertRaisesRegex(ResetError, 'signature'):
                identity.verified_host_identity()


class RootHostExporterTests(SimpleTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        script = Path(__file__).resolve().parents[1] / 'execution' / 'export_vocal_duet_host_identity.py'
        spec = importlib.util.spec_from_file_location('test_root_host_export', script)
        self.helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.helper)
        self.checker = self.helper.trusted
        now = timezone.now()
        files = {'CHALLENGE': json.dumps({'purpose': 'eventtab-host-identity-challenge-v1', 'nonce': 'b' * 64,
                    'created_at': (now - timedelta(minutes=1)).isoformat()}),
                 'DMI': '11111111-2222-4333-8444-555555555555', 'MACHINE': 'a' * 32,
                 'BOOT': '66666666-7777-4888-8999-aaaaaaaaaaaa', 'INSTALLED': 'test helper'}
        for name, value in files.items():
            path = self.root / name; path.write_text(value)
            mock = patch.object(self.helper, name, path); mock.start(); self.addCleanup(mock.stop)
        for mock in (patch.object(self.helper, '__file__', str(self.root / 'INSTALLED')),
                     patch.object(self.helper, 'trusted', return_value=None),
                     patch.object(self.helper.os, 'geteuid', return_value=0, create=True),
                     patch.object(self.helper.sys, 'platform', 'linux'),
                     patch.object(self.helper.sys, 'argv', [str(self.root / 'INSTALLED')])):
            mock.start(); self.addCleanup(mock.stop)

    def test_export_uses_fixed_files_and_changes_none(self):
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        result = self.helper.export_identity()
        self.assertEqual(result['host_uuid'], before['DMI'].decode())
        self.assertEqual(result['challenge'], 'b' * 64)
        self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir()}, before)

    def test_non_root_arbitrary_arguments_or_untrusted_paths_are_rejected(self):
        with patch.object(self.helper.os, 'geteuid', return_value=1000):
            with self.assertRaises(ValueError): self.helper.export_identity()
        with patch.object(self.helper.sys, 'argv', ['helper', '/attacker/uuid']):
            with self.assertRaises(ValueError): self.helper.export_identity()
        with patch.object(self.helper, 'trusted', side_effect=ValueError('Symlink')):
            with self.assertRaises(ValueError): self.helper.export_identity()

    def test_linux_symlink_uid_and_write_mode_rejections(self):
        from types import SimpleNamespace
        path = self.root / 'DMI'
        for uid, mode in ((1000, 0o100400), (0, 0o100420), (0, 0o120777)):
            with patch.object(Path, 'lstat', autospec=True, return_value=SimpleNamespace(st_uid=uid, st_mode=mode)):
                with self.assertRaises(ValueError): self.checker(path)


class HostBindingDatabaseTests(TestCase):
    def test_database_identity_remains_live_and_exact(self):
        host = {'host_uuid': 'test-root-export', 'host_machine_id': 'a' * 32,
            'host_boot_id': 'test-boot', 'host_attestation_sha256': 'test-host-hash'}
        with patch.object(identity, 'verified_host_identity', return_value=host):
            result = development.target_identity()
        self.assertEqual(result['database'], connection.settings_dict['NAME'])
        self.assertEqual(result['database_role'], connection.settings_dict['USER'])
        self.assertEqual(result['server_port'], int(connection.settings_dict['PORT']))
        self.assertTrue(result['cluster_system_identifier'])
        self.assertTrue(result['database_oid'])
        self.assertEqual(len(result['table_identities']), 5)
        self.assertEqual(result['host_attestation_sha256'], 'test-host-hash')

    def test_consumed_host_attestation_cannot_approve_another_permit(self):
        from .test_vocal_duet_development import InfrastructurePermitTests
        from .models import AuditLog
        InfrastructurePermitTests.setUp(self)
        self.target['host_attestation_sha256'] = 'consumed-host-attestation'
        inventory_path = self.root / 'deployment-inventory.json'
        inventory = json.loads(inventory_path.read_text())
        inventory['target'] = self.target
        inventory_path.write_text(json.dumps(inventory))
        self.permit['inventory_sha256'] = development._file_hash(inventory_path)
        InfrastructurePermitTests.load(self)
        AuditLog.objects.create(action='TEST completed reset', metadata={'development_authorization': {'target': self.target}})
        with self.assertRaisesRegex(ResetError, 'host attestation has already'):
            InfrastructurePermitTests.load(self)
