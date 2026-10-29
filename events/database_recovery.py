"""Private recovery jobs, executed by a worker outside the web service."""
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import urllib.request

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

from .audit_service import record_audit
from .database_backup import database_environment, export_database


class DatabaseRecoveryError(Exception):
    pass


def root():
    directory = Path(settings.DATABASE_RECOVERY_ROOT)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream)
    temporary.replace(path)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def worker_ready():
    try:
        heartbeat = read_json(root() / 'worker.json') or {}
        age = time.time() - heartbeat.get('time', 0)
        return bool(getattr(settings, 'SUPERADMIN_ALLOW_RESTORE', False) and 0 <= age < 15)
    except (OSError, TypeError):
        return False


def restore_binary():
    binary = shutil.which('pg_restore')
    if not binary and os.name == 'nt':
        candidate = Path(r'C:\Program Files\PostgreSQL\18\bin\pg_restore.exe')
        if candidate.is_file():
            binary = str(candidate)
    if not binary:
        raise DatabaseRecoveryError('The PostgreSQL restore tool is unavailable.')
    return binary


def validate_archive(path):
    with path.open('rb') as stream:
        if stream.read(5) != b'PGDMP':
            raise DatabaseRecoveryError('Choose a PostgreSQL .dump backup exported from EventTab.')
    try:
        result = subprocess.run([restore_binary(), '--list', str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DatabaseRecoveryError('The backup could not be inspected. Please try again.') from exc
    if result.returncode or any(name not in result.stdout for name in
                                (b'TABLE public auth_user ', b'TABLE public django_migrations ', b'TABLE public events_event ')):
        raise DatabaseRecoveryError('The file is not a readable EventTab database backup.')
    return set(re.findall(rb'^\d+;\s+\d+\s+\d+\s+TABLE public (\w+) ', result.stdout, re.MULTILINE))


def queue_recovery(upload, actor, request=None):
    if not worker_ready():
        raise DatabaseRecoveryError('Database recovery is not ready on this server. Contact your system administrator.')
    directory = root()
    try:
        descriptor = os.open(directory / 'pending.lock', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise DatabaseRecoveryError('Another database recovery is already queued or running.') from exc
    os.close(descriptor)
    job_id = uuid.uuid4().hex
    archive = directory / (job_id + '.dump')
    try:
        size = 0
        digest = hashlib.sha256()
        with archive.open('xb') as stream:
            os.chmod(archive, 0o600)
            for chunk in upload.chunks():
                size += len(chunk)
                if size > settings.DATABASE_RECOVERY_MAX_BYTES:
                    raise DatabaseRecoveryError('The backup exceeds the allowed upload size.')
                stream.write(chunk)
                digest.update(chunk)
        validate_archive(archive)
        job = {'id': job_id, 'state': 'queued', 'actor_id': actor.pk, 'actor_name': actor.get_username(),
               'sha256': digest.hexdigest(), 'created_at': timezone.now().isoformat()}
        # The private job journal survives a database replacement.
        record_audit(user=actor, action='Requested Database Restore', module='System Settings',
                     description='Queued a confirmed database recovery.', request=request, metadata={'recovery_id': job_id})
        write_json(directory / (job_id + '.json'), job)
        return job
    except Exception:
        archive.unlink(missing_ok=True)
        (directory / 'pending.lock').unlink(missing_ok=True)
        raise


def set_state(job, state, message):
    job.update(state=state, message=message, updated_at=timezone.now().isoformat())
    write_json(root() / (job['id'] + '.json'), job)


def restore_archive(path):
    try:
        result = subprocess.run([restore_binary(), '--clean', '--if-exists', '--no-owner', '--no-acl',
            '--single-transaction', '--exit-on-error', '--no-password', '--dbname', connections['default'].settings_dict['NAME'], str(path)],
            env=database_environment(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=600, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DatabaseRecoveryError('The restore process did not complete.') from exc
    if result.returncode:
        raise DatabaseRecoveryError('PostgreSQL could not restore this backup.')


def web_service(action):
    subprocess.run(['/usr/bin/sudo', '-n', '/usr/bin/systemctl', action, 'eventtab'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60, check=True)
    if action == 'restart':
        for attempt in range(5):
            try:
                request = urllib.request.Request('http://127.0.0.1/api/health/', headers={'Host':'103.125.217.125'})
                with urllib.request.urlopen(request, timeout=3) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            time.sleep(1)
        raise DatabaseRecoveryError('The application did not pass its health check.')


def run_recovery(job, control=web_service):
    """Web service is stopped before snapshots/writes; failed recovery retains its safety backup."""
    directory = root()
    archive = directory / (job['id'] + '.dump')
    safety = directory / (job['id'] + '-before.dump')
    stopped = False
    restore_started = False
    resume = True
    try:
        if not get_user_model().objects.filter(pk=job['actor_id'], username=job['actor_name'], is_superuser=True, is_active=True).exists():
            raise DatabaseRecoveryError('The requesting Super Admin is no longer authorized.')
        tables = validate_archive(archive)
        current_tables = {name.encode() for name in connections['default'].introspection.table_names()}
        if tables != current_tables:
            raise DatabaseRecoveryError('The backup table inventory does not match this EventTab version.')
        with archive.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != job['sha256']:
            raise DatabaseRecoveryError('The uploaded backup changed after validation.')
        migrations = set(MigrationRecorder(connections['default']).applied_migrations())
        set_state(job, 'running', 'Preparing database recovery.')
        stopped = True
        control('stop')
        connections.close_all()
        with export_database() as snapshot, safety.open('xb') as target:
            os.chmod(safety, 0o600)
            shutil.copyfileobj(snapshot, target)
        validate_archive(safety)
        restore_started = True
        restore_archive(archive)
        connections.close_all()
        if set(MigrationRecorder(connections['default']).applied_migrations()) != migrations:
            raise DatabaseRecoveryError('The backup schema does not match this EventTab version.')
        if not get_user_model().objects.filter(is_superuser=True, is_active=True).exists():
            raise DatabaseRecoveryError('The backup does not contain an active Super Admin account.')
        from django.contrib.sessions.models import Session
        Session.objects.all().delete()  # Do not revive sessions captured in an old backup.
        actor = get_user_model().objects.filter(pk=job['actor_id'], username=job['actor_name']).first()
        record_audit(user=actor, action='Restored Database', module='System Settings',
            description='Restored the database from a confirmed backup.', metadata={'recovery_id': job['id'], 'requested_by': job['actor_name']})
        set_state(job, 'completed', 'Database restored. Sign in using an account from the backup.')
    except Exception:
        if restore_started:
            try:
                connections.close_all()
                restore_archive(safety)
                connections.close_all()
                set_state(job, 'failed', 'Recovery failed. The pre-restore database was recovered.')
            except Exception:
                resume = False
                set_state(job, 'needs_attention', 'Recovery needs administrator attention. The application remains paused and the safety backup is retained.')
        else:
            set_state(job, 'failed', 'Recovery could not start. The database was not changed.')
    finally:
        connections.close_all()
        if job.get('state') == 'failed':
            try:
                actor = get_user_model().objects.filter(pk=job['actor_id'], username=job['actor_name']).first()
                record_audit(user=actor, action='Failed Database Restore', module='System Settings',
                    description=job['message'], status='failed',
                    metadata={'recovery_id': job['id'], 'requested_by': job['actor_name']})
            except Exception:
                pass  # The private recovery journal remains authoritative if the database is unavailable.
        if stopped and resume:
            try:
                control('restart')
            except Exception:
                set_state(job, 'needs_attention', 'The database operation finished, but the application could not restart. Contact your system administrator.')
        archive.unlink(missing_ok=True)
        if job['state'] != 'needs_attention':
            (directory / 'pending.lock').unlink(missing_ok=True)
