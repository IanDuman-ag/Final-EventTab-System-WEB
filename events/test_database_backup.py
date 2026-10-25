"""Private database exports: authorization, failure handling and actual archive format."""
from io import BytesIO
from unittest.mock import patch
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import Client, TransactionTestCase
from django.urls import reverse

from .database_backup import DatabaseExportError, export_database
from .models import AuditLog


class DatabaseExportTests(TransactionTestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('export-owner', 'owner@example.test', 'Owner!98752')
        self.url = reverse('superadmin_database_export')

    @patch('events.database_backup.export_database')
    def test_only_superadmin_can_export_using_csrf_protected_post(self, export):
        self.assertEqual(self.client.post(self.url).status_code, 302)
        staff = get_user_model().objects.create_user('export-staff', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(self.url).status_code, 403)
        export.assert_not_called()

    def test_download_is_private_and_audited_without_database_credentials(self):
        self.client.force_login(self.owner)
        archive = BytesIO(b'PGDMPtest-archive')
        with patch('events.database_backup.export_database', return_value=archive):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('.dump', response['Content-Disposition'])
        self.assertEqual(b''.join(response.streaming_content), b'PGDMPtest-archive')
        response.close()
        self.assertTrue(archive.closed)
        log = AuditLog.objects.get(action='Exported Database')
        self.assertEqual(log.user_id, self.owner.pk)
        self.assertEqual(log.module, 'System Settings')

    def test_failure_is_graceful_and_never_reports_a_successful_export(self):
        self.client.force_login(self.owner)
        with patch('events.database_backup.export_database', side_effect=DatabaseExportError('Backup tool unavailable.')):
            response = self.client.post(self.url, follow=True)
        self.assertContains(response, 'Backup tool unavailable.')
        self.assertFalse(AuditLog.objects.filter(action='Exported Database').exists())
        with patch('events.database_backup.export_database', side_effect=DatabaseExportError('Backup tool unavailable.')):
            response = self.client.post(self.url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['message'], 'Backup tool unavailable.')

    def test_export_tool_errors_are_redacted_and_do_not_expose_password(self):
        with patch('events.database_backup.shutil.which', return_value='pg_dump'), patch('events.database_backup.subprocess.run') as run:
            run.return_value.returncode = 1
            run.return_value.stderr = b'PRIVATE_DATABASE_PASSWORD'
            with self.assertRaises(DatabaseExportError) as error:
                export_database()
            self.assertNotIn('PRIVATE_DATABASE_PASSWORD', str(error.exception))
            self.assertNotIn('--password', run.call_args.args[0])
            self.assertTrue(run.call_args.kwargs['stdout'].closed)


class RealDatabaseArchiveTests(TransactionTestCase):
    def test_postgres_export_produces_a_readable_custom_archive(self):
        with export_database() as archive:
            self.assertEqual(archive.read(5), b'PGDMP')
            archive.seek(0)
            with tempfile.TemporaryDirectory() as directory:
                backup = Path(directory) / 'test.dump'
                backup.write_bytes(archive.read())
                binary = shutil.which('pg_restore') or r'C:\Program Files\PostgreSQL\18\bin\pg_restore.exe'
                result = subprocess.run([binary, '--list', str(backup)], capture_output=True, text=True, check=True)
                self.assertIn('events_auditlog', result.stdout)
                self.assertIn('auth_user', result.stdout)
