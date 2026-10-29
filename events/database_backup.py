"""Create private PostgreSQL backups for the Super Admin download endpoint."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from django.db import connections


class DatabaseExportError(Exception):
    pass


def database_environment(database=None):
    database = database or connections['default'].settings_dict
    environment = os.environ.copy()
    # Credentials stay in the child environment, never command arguments or responses.
    for key, value in {'PGDATABASE': database['NAME'], 'PGUSER': database.get('USER', ''),
                       'PGPASSWORD': database.get('PASSWORD', ''), 'PGHOST': database.get('HOST', ''),
                       'PGPORT': database.get('PORT', ''), 'PGCONNECT_TIMEOUT': '10'}.items():
        environment[key] = str(value or '')
    for key in ('sslmode', 'sslcert', 'sslkey', 'sslrootcert'):
        if database.get('OPTIONS', {}).get(key):
            environment['PG' + key.upper()] = str(database['OPTIONS'][key])
    return environment


def export_database():
    database = connections['default'].settings_dict
    if database['ENGINE'] != 'django.db.backends.postgresql':
        raise DatabaseExportError('Database export requires PostgreSQL.')
    binary = shutil.which('pg_dump')
    if not binary and os.name == 'nt':
        candidate = Path(r'C:\Program Files\PostgreSQL\18\bin\pg_dump.exe')
        if candidate.is_file():
            binary = str(candidate)
    if not binary:
        raise DatabaseExportError('The database backup tool is unavailable. Contact your system administrator.')
    environment = database_environment(database)
    archive = tempfile.TemporaryFile(mode='w+b')
    try:
        result = subprocess.run([binary, '--format=custom', '--no-owner', '--no-acl', '--no-password'],
            env=environment, stdout=archive, stderr=subprocess.PIPE, timeout=120, check=False)
        if result.returncode:
            raise DatabaseExportError('Database export could not be completed. Contact your system administrator.')
        archive.seek(0)
        if archive.read(5) != b'PGDMP':
            raise DatabaseExportError('The database export was incomplete. Please try again.')
        archive.seek(0)
        return archive
    except (OSError, subprocess.TimeoutExpired) as exc:
        archive.close()
        raise DatabaseExportError('Database export is unavailable or timed out. Please try again.') from exc
    except Exception:
        archive.close()
        raise
