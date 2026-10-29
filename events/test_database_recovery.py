"""Recovery tests only use disposable PostgreSQL and never control a live service."""
import hashlib
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from events.models import AuditLog, Event
from events.database_backup import export_database
from events.database_recovery import (DatabaseRecoveryError, queue_recovery, read_json, root,
                                      run_recovery, worker_ready, write_json)


class DatabaseRecoveryTests(TransactionTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.override = override_settings(DATABASE_RECOVERY_ROOT=Path(self.temporary.name),
                                         SUPERADMIN_ALLOW_RESTORE=True)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.temporary.cleanup)
        self.owner = get_user_model().objects.create_superuser('recovery-owner', 'owner@example.test', 'Recover!82471')
        self.client.force_login(self.owner)

    def backup_job(self):
        job_id = uuid.uuid4().hex
        path = root() / (job_id + '.dump')
        with export_database() as archive:
            path.write_bytes(archive.read())
        job = {'id': job_id, 'state': 'queued', 'actor_id': self.owner.pk, 'actor_name': self.owner.username,
               'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        write_json(root() / (job_id + '.json'), job)
        (root() / 'pending.lock').touch()
        return job

    def test_actual_restore_recovers_records_and_keeps_pre_restore_backup(self):
        from datetime import date
        event = Event.objects.create(name='Original competition', event_date=date(2026,10,25))
        job = self.backup_job()
        event.name = 'Changed competition'; event.save()
        actions = []
        run_recovery(job, control=actions.append)
        self.assertEqual(actions, ['stop','restart'])
        self.assertEqual(job['state'], 'completed', read_json(root() / (job['id']+'.json')))
        self.assertEqual(Event.objects.get(pk=event.pk).name, 'Original competition')
        self.assertTrue((root() / (job['id']+'-before.dump')).is_file())
        self.assertTrue(AuditLog.objects.filter(action='Restored Database').exists())
        from django.contrib.sessions.models import Session
        self.assertEqual(Session.objects.count(),0)
        self.assertFalse((root() / 'pending.lock').exists())
        self.assertFalse((root() / (job['id']+'.dump')).exists())

    def test_invalid_restored_account_state_recovers_preimage_automatically(self):
        self.owner.is_active = False; self.owner.save()
        job = self.backup_job()
        self.owner.is_active = True; self.owner.save()
        actions = []
        run_recovery(job, control=actions.append)
        self.assertEqual(job['state'], 'failed')
        self.assertTrue(get_user_model().objects.get(pk=self.owner.pk).is_active)
        self.assertEqual(actions, ['stop','restart'])
        self.assertTrue((root() / (job['id']+'-before.dump')).exists())

    def test_worker_readiness_upload_validation_and_one_job_at_a_time(self):
        self.assertFalse(worker_ready())
        upload = SimpleUploadedFile('backup.dump', b'not-a-backup')
        with self.assertRaises(DatabaseRecoveryError): queue_recovery(upload, self.owner)
        write_json(root() / 'worker.json', {'time':time.time()})
        self.assertTrue(worker_ready())
        with self.assertRaises(DatabaseRecoveryError): queue_recovery(upload, self.owner)
        self.assertFalse((root() / 'pending.lock').exists())
        job = self.backup_job()
        with self.assertRaises(DatabaseRecoveryError): queue_recovery(upload, self.owner)

    def test_endpoint_requires_password_confirmation_csrf_and_superadmin(self):
        url = reverse('superadmin_database_restore')
        self.assertEqual(self.client.get(url).status_code,405)
        with patch('events.database_recovery.queue_recovery') as queue:
            for payload in ({}, {'current_password':'wrong','confirmation':'RESTORE'},
                            {'current_password':'Recover!82471','confirmation':'wrong'}):
                self.assertEqual(self.client.post(url,payload).status_code,400)
            queue.assert_not_called()
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code,302)

    def test_recovery_status_is_private_and_does_not_expose_paths_or_passwords(self):
        job = self.backup_job()
        response = self.client.get(reverse('superadmin_recovery_status',args=[uuid.UUID(job['id'])]))
        self.assertEqual(response.status_code,200)
        self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(set(response.json()),{'success','state','message'})
        self.assertNotIn('Recover!82471',response.content.decode())
        self.client.logout()
        self.assertEqual(self.client.get(reverse('superadmin_recovery_status',args=[uuid.UUID(job['id'])])).status_code,302)

    def test_valid_upload_is_queued_audited_and_never_stores_confirmation_password(self):
        write_json(root() / 'worker.json', {'time':time.time()})
        with export_database() as archive:
            upload = SimpleUploadedFile('eventtab.dump', archive.read())
        response = self.client.post(reverse('superadmin_database_restore'), {
            'current_password':'Recover!82471', 'confirmation':'RESTORE', 'backup':upload})
        self.assertEqual(response.status_code,202,response.content)
        job = read_json(root() / (response.json()['job_id']+'.json'))
        self.assertEqual(job['state'],'queued')
        self.assertNotIn('Recover!82471',json.dumps(job))
        self.assertTrue(AuditLog.objects.filter(action='Requested Database Restore').exists())
        from django.test import Client
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(reverse('superadmin_database_restore')).status_code,403)

    def test_failed_rollback_keeps_application_paused_for_operator_recovery(self):
        job = self.backup_job()
        actions = []
        with patch('events.database_recovery.restore_archive',side_effect=DatabaseRecoveryError('Test failure')):
            run_recovery(job,control=actions.append)
        self.assertEqual(job['state'],'needs_attention')
        self.assertEqual(actions,['stop'])
        self.assertTrue((root()/'pending.lock').exists())
        self.assertTrue((root()/(job['id']+'-before.dump')).exists())
