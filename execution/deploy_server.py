"""Install under /usr/local/lib/eventtab-deploy; run as eventtab, NEVER root.

Fixed paths and root-owned configuration. No migrate, pip, nginx, reset, shell,
or arbitrary sudo commands. Only systemctl stop/restart eventtab use sudo.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

from deploy_release import RELEASE_ID, allowed, extract, safe_name, sha256, validate_archive

APP = Path('/srv/eventtab/Final-EventTab-System-WEB')
STATE = Path('/srv/eventtab-deploy')
CONFIG = Path('/etc/eventtab-deploy.json')
SERVICE = 'eventtab'
HEALTH = 'http://127.0.0.1/api/health/'
HEALTH_HOST = '103.125.217.125'

# This probe executes with the EXISTING production venv as the unprivileged
# application account. It only reads database identity and migration history.
PROBE = """
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ['DJANGO_SETTINGS_MODULE'] = 'core.settings'
import django
django.setup()
from django.conf import settings
from pathlib import Path
if Path(settings.STATIC_ROOT).resolve() != Path.cwd() / 'staticfiles' or Path(settings.MEDIA_ROOT).resolve() != Path.cwd() / 'assets':
    raise SystemExit('Unexpected static/media paths; review deployment layout first.')
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
c = connection.settings_dict
if c['ENGINE'] != 'django.db.backends.postgresql' or c['HOST'] not in ('127.0.0.1', 'localhost') or str(c['PORT']) != '5433' or c['NAME'] != 'eventtab_db':
    raise SystemExit('Unexpected production database identity; deployment blocked.')
with connection.cursor() as cursor:
    cursor.execute('SELECT current_database(), inet_server_port()')
    if cursor.fetchone() != ('eventtab_db', 5433):
        raise SystemExit('Database connection does not match expected production identity.')
executor = MigrationExecutor(connection)
executor.loader.check_consistent_history(connection)
if executor.migration_plan(executor.loader.graph.leaf_nodes()):
    raise SystemExit('Deployment blocked: migration review required. No migrations were run.')
print(json.dumps(sorted([list(key) for key in MigrationRecorder(connection).applied_migrations()])))
"""

# Independent of the newly installed application and Django's app registry.
# A broken new app must not prevent us checking whether rollback is safe.
HISTORY_PROBE = """
import json, os, sys
sys.path.insert(0, os.getcwd())
from settings import DATABASES
import psycopg2
c = DATABASES['default']
if c['ENGINE'] != 'django.db.backends.postgresql' or c['HOST'] not in ('127.0.0.1', 'localhost') or str(c['PORT']) != '5433' or c['NAME'] != 'eventtab_db':
    raise SystemExit('Unexpected database identity.')
with psycopg2.connect(dbname=c['NAME'], user=c['USER'], password=c['PASSWORD'], host=c['HOST'], port=c['PORT'], connect_timeout=10,
        options='-c default_transaction_read_only=on -c statement_timeout=10000') as connection:
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), inet_server_port()')
        if cursor.fetchone() != ('eventtab_db', 5433):
            raise SystemExit('Unexpected database identity.')
        cursor.execute('SELECT app, name FROM django_migrations ORDER BY app, name')
        print(json.dumps(cursor.fetchall()))
"""

ENVIRONMENT_PROBE = """
import importlib.metadata, json
print(json.dumps(sorted((d.metadata['Name'].lower().replace('_', '-'), d.version)
                        for d in importlib.metadata.distributions())))
"""


def atomic_json(path, data):
    temporary = path.with_suffix('.new')
    regular(temporary)
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def sync_directory(path):
    if sys.platform == 'linux':
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def clear_pending():
    (STATE / 'pending.json').unlink()
    sync_directory(STATE)


def read_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def regular(path):
    """Reject symlinks in every existing path component, including the leaf."""
    for component in [path, *path.parents]:
        if component.is_symlink():
            raise ValueError('Symlink path is not allowed: ' + str(component))
    if path.exists() and not path.is_file():
        raise ValueError('Expected a regular file: ' + str(path))
    return path


def configuration():
    import pwd
    if sys.platform != 'linux' or os.geteuid() == 0 or pwd.getpwuid(os.geteuid()).pw_name != 'eventtab':
        raise ValueError('Run this installed helper as the eventtab Linux user, never root.')
    for path in (CONFIG, Path(__file__).resolve(), Path(__file__).resolve().parent / 'deploy_release.py'):
        regular(path)
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Deployment helper and configuration must be root-owned and not group/world writable.')
    for path in (Path(__file__).resolve().parent, Path('/usr/local/lib')):
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Helper directory must be controlled by root.')
    cfg = read_json(CONFIG)
    if cfg.get('machine_id') != Path('/etc/machine-id').read_text().strip():
        raise ValueError('Target host identity does not match the installed configuration.')
    for directory in (APP, STATE, STATE / 'incoming', STATE / 'releases', STATE / 'logs'):
        if directory.resolve() != directory or not directory.is_dir():
            raise ValueError('Missing or symlink deployment directory: ' + str(directory))
    for path in (APP / 'manage.py', APP / '.env', APP / 'requirements.txt'):
        if not regular(path).is_file():
            raise ValueError('Existing deployment file is missing: ' + str(path))
    if not (APP / 'venv/bin/python').is_file():
        raise ValueError('Existing production venv is missing.')
    if not isinstance(cfg.get('approved_requirements_sha256'), str):
        raise ValueError('Missing approved requirements SHA256 in root-owned configuration.')
    unit_user = subprocess.check_output(['/usr/bin/systemctl', 'show', SERVICE, '--property=User', '--value'], text=True).strip()
    unit_directory = subprocess.check_output(['/usr/bin/systemctl', 'show', SERVICE, '--property=WorkingDirectory', '--value'], text=True).strip()
    if unit_user != 'eventtab' or unit_directory != str(APP):
        raise ValueError('Existing service must run as eventtab with the expected project WorkingDirectory. Review setup; no service configuration was changed.')
    return cfg


@contextlib.contextmanager
def deployment_lock():
    import fcntl
    lock_path = STATE / 'deploy.lock'
    regular(lock_path)
    with lock_path.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another deployment or rollback is running.') from None
        yield


class Audit:
    def __init__(self, operation, release):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        self.path = STATE / 'logs' / (stamp + '-' + operation + '.jsonl')
        self.operation = operation
        self.release = release

    def record(self, step, **fields):
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'timestamp': datetime.now(timezone.utc).isoformat(),
                'operation': self.operation, 'release': self.release, 'step': step, **fields}) + '\n')
            stream.flush(); os.fsync(stream.fileno())

    def run(self, args, cwd=APP):
        result = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True,
                                text=True, timeout=300, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        # Private audit logs may contain Django diagnostics. Do not print them or
        # environment values to the public terminal. No credentials in command args.
        self.record('command', command=[str(a) for a in args], returncode=result.returncode,
                    stdout=result.stdout, stderr=result.stderr)
        if result.returncode:
            if 'makemigrations' in args or 'migration review required' in result.stdout + result.stderr:
                raise ValueError('Deployment blocked: migration review required. No migrations were run.')
            label = 'Django ' + str(args[2]) if len(args) > 2 and str(args[1]) == 'manage.py' else str(args[0])
            raise ValueError('Check failed: ' + label + '. Inspect private audit log: ' + str(self.path))
        return result.stdout.strip()


def service(audit, action):
    if action not in {'stop', 'restart'}:
        raise ValueError('Unsupported service operation.')
    audit.run(['/usr/bin/sudo', '-n', '/usr/bin/systemctl', action, SERVICE])
    audit.record('service', action=action, result='success')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        return None


def http_health():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(HEALTH, headers={'Host': HEALTH_HOST})
    with opener.open(request, timeout=5) as response:
        payload = json.loads(response.read(4096))
        if response.status != 200 or payload.get('status') != 'ok' or payload.get('backend') != 'django':
            raise ValueError('Application HTTP health check failed.')


def healthy(audit, attempts=12):
    for attempt in range(attempts):
        try:
            audit.run(['/usr/bin/systemctl', 'is-active', '--quiet', SERVICE])
            http_health()
            audit.record('health', result='success')
            return
        except (ValueError, OSError) as exc:
            audit.record('health', attempt=attempt + 1, result='failed', error=str(exc))
            if attempt + 1 < attempts:
                time.sleep(2)
    raise ValueError('Application failed systemd/HTTP health checks.')


def schema(audit, source):
    output = audit.run([APP / 'venv/bin/python', '-c', PROBE], cwd=source)
    return json.loads(output.splitlines()[-1])


def history(audit, release):
    if not RELEASE_ID.fullmatch(release):
        raise ValueError('Invalid history checkpoint.')
    source = STATE / 'releases' / release / 'baseline'
    return json.loads(audit.run([APP / 'venv/bin/python', '-c', HISTORY_PROBE], cwd=source).splitlines()[-1])


def environment_snapshot(audit):
    return json.loads(audit.run([APP / 'venv/bin/python', '-c', ENVIRONMENT_PROBE]).splitlines()[-1])


def changed_files(manifest):
    result = []
    for name, item in manifest['files'].items():
        target = regular(APP / name)
        if not target.exists() or sha256(target) != item['sha256']:
            result.append(name)
    return sorted(result)


def writable_target(path):
    regular(path)
    parent = path.parent
    while not parent.exists():
        parent = parent.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        raise ValueError('Deployment account cannot replace files under ' + str(parent))


def requirements_gate(cfg, source):
    if sha256(source / 'requirements.txt') != cfg['approved_requirements_sha256']:
        raise ValueError('Dependency review required. Requirements changed; use the documented separate backup/install/approval procedure.')


def copy_atomic(source, target):
    regular(target)
    previous_mtime = int(target.stat().st_mtime) if target.exists() else None
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.eventtab-deploy-new')
    regular(temporary)
    if temporary.exists():
        raise ValueError('Unexpected temporary file; manual review required: ' + str(temporary))
    shutil.copyfile(source, temporary)
    temporary.chmod(0o644)
    if int(temporary.stat().st_mtime) == previous_mtime:
        # Invalidate timestamp-based bytecode even for a same-size update within
        # one second; leave shared venv and unrelated bytecode files alone.
        moment = time.time() + 1
        os.utime(temporary, (moment, moment))
    with temporary.open('r+b') as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    sync_directory(target.parent)


def snapshot_static(destination):
    source = APP / 'staticfiles'
    if source.exists():
        if source.is_symlink() or not source.is_dir():
            raise ValueError('staticfiles must be a real directory; review this deployment layout first.')
        for path in source.rglob('*'):
            if path.is_symlink() or (not path.is_file() and not path.is_dir()):
                raise ValueError('Unsafe staticfiles member.')
        shutil.copytree(source, destination)
        return {p.relative_to(destination).as_posix(): sha256(p) for p in destination.rglob('*') if p.is_file()}
    return {}


def validate_restore_files(checkpoint):
    """Read-only validation, shared by preflight and the final restore check."""
    backup = STATE / 'releases' / checkpoint['release'] / 'backup'
    if checkpoint['static_changed']:
        target = APP / 'staticfiles'
        if target.is_symlink() or target.resolve() != target or (target.exists() and not target.is_dir()):
            raise ValueError('Unsafe staticfiles rollback path.')
        static_backup = backup.parent / 'staticfiles'
        actual_static = {}
        if checkpoint['static_existed']:
            if not static_backup.is_dir() or static_backup.is_symlink():
                raise ValueError('Missing or unsafe staticfiles backup.')
            trusted_artifact(static_backup, directory=True)
            for path in static_backup.rglob('*'):
                if path.is_symlink() or (not path.is_file() and not path.is_dir()):
                    raise ValueError('Unsafe staticfiles backup member.')
                if path.is_file():
                    trusted_artifact(path)
                    actual_static[path.relative_to(static_backup).as_posix()] = sha256(path)
        if actual_static != checkpoint['static_before']:
            raise ValueError('Staticfiles backup verification failed.')
        if not checkpoint['static_existed'] and (static_backup.exists() or static_backup.is_symlink()):
            raise ValueError('Unexpected staticfiles backup.')
    # Validate EVERYTHING before touching any file; refuse unexpected edits.
    for name, before in checkpoint['before'].items():
        if not allowed(name):
            raise ValueError('Unsafe checkpoint member.')
        target = regular(APP / name)
        actual = sha256(target) if target.exists() else None
        expected_after = checkpoint['after'][name]['sha256']
        if actual not in {before, expected_after}:
            raise ValueError('Rollback blocked: unexpected server edit to ' + name)
        if before is not None and sha256(regular(backup / name)) != before:
            raise ValueError('Rollback backup verification failed: ' + name)
        writable_target(target)
        temporary = regular(target.with_name(target.name + '.eventtab-deploy-new'))
        if temporary.exists():
            raise ValueError('Unexpected temporary file; manual review required: ' + str(temporary))
    if checkpoint['static_changed']:
        writable_target(APP / 'staticfiles' / 'permission-probe')
        for path in (APP / 'staticfiles').rglob('*'):
            if path.is_symlink() or (not path.is_file() and not path.is_dir()):
                raise ValueError('Unsafe current staticfiles member.')
            if path.is_dir() and not os.access(path, os.W_OK | os.X_OK):
                raise ValueError('Cannot restore staticfiles directory.')


def restore(checkpoint, audit):
    """Restore only journaled source paths, never media, secrets or SQL state."""
    validate_restore_files(checkpoint)  # Recheck after stop, before any writes.
    backup = STATE / 'releases' / checkpoint['release'] / 'backup'
    for name, before in checkpoint['before'].items():
        target = APP / name
        if before is None:
            target.unlink(missing_ok=True)
        else:
            copy_atomic(backup / name, target)
    if checkpoint['static_changed']:
        target = APP / 'staticfiles'
        if target.is_symlink() or target.resolve() != target:
            raise ValueError('Unsafe staticfiles rollback path.')
        if target.exists():
            shutil.rmtree(target)
        if checkpoint['static_existed']:
            shutil.copytree(backup.parent / 'staticfiles', target)
    audit.record('restore', files=list(checkpoint['before']), result='success')


def checkpoint_reference(checkpoint):
    path = STATE / 'releases' / checkpoint['release'] / 'checkpoint.json'
    return {'release': checkpoint['release'], 'checkpoint_sha256': sha256(regular(path))}


def trusted_artifact(path, directory=False):
    if directory:
        regular(path / 'rollback-path-probe')  # Checks every ancestor without writing.
        if not path.is_dir():
            raise ValueError('Missing rollback directory: ' + str(path))
    else:
        regular(path)
    # Trust the existing deployment-account boundary, never a shared writable
    # artifact. Hashes detect corruption; they are not signatures against a
    # compromised deployment account, which already controls application code.
    if sys.platform == 'linux':
        boundary = APP if path.is_relative_to(APP) else STATE
        for component in [path, *path.parents]:
            info = component.stat()
            if info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022:
                raise ValueError('Untrusted rollback artifact: ' + str(component))
            if component == boundary:
                break
    return path


def validate_checkpoint(reference, current):
    try:
        return _validate_checkpoint(reference, current)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('Malformed rollback checkpoint; operator review required.') from exc


def _validate_checkpoint(reference, current):
    """Validate metadata, provenance, complete inventory and restore inputs."""
    if (not isinstance(reference, dict) or set(reference) != {'release', 'checkpoint_sha256'}
            or not isinstance(reference['release'], str) or not RELEASE_ID.fullmatch(reference['release'])
            or not isinstance(reference['checkpoint_sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', reference['checkpoint_sha256'])):
        raise ValueError('Invalid or legacy rollback checkpoint reference; operator review required.')
    directory = STATE / 'releases' / reference['release']
    path = trusted_artifact(directory / 'checkpoint.json')
    if sha256(path) != reference['checkpoint_sha256']:
        raise ValueError('Rollback checkpoint integrity verification failed.')
    checkpoint = read_json(path)
    keys = {'format', 'release', 'previous', 'schema', 'environment', 'requirements_sha256',
            'before', 'after', 'static_changed', 'static_existed', 'static_before',
            'manifest', 'archive_sha256', 'baseline_files', 'baseline_settings_sha256'}
    if not isinstance(checkpoint, dict) or set(checkpoint) != keys or checkpoint['format'] != 2:
        raise ValueError('Invalid rollback checkpoint structure.')
    if checkpoint['release'] != reference['release']:
        raise ValueError('Rollback checkpoint release identity mismatch.')
    manifest = validate_archive(trusted_artifact(STATE / 'incoming' / (reference['release'] + '.tar.gz')),
                                checkpoint['archive_sha256'])
    if manifest != checkpoint['manifest'] or manifest['release'] != reference['release']:
        raise ValueError('Rollback release manifest mismatch.')
    files = manifest['files']
    baseline = checkpoint['baseline_files']
    valid_hash = lambda value: isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)
    if (not isinstance(baseline, dict) or set(baseline) != set(files)
            or any(value is not None and not valid_hash(value) for value in baseline.values())):
        raise ValueError('Invalid rollback baseline inventory.')
    expected = {name for name in files if baseline[name] != files[name]['sha256']}
    if (not isinstance(checkpoint['before'], dict) or set(checkpoint['before']) != expected
            or checkpoint['before'] != {name: baseline[name] for name in expected}
            or checkpoint['after'] != {name: files[name] for name in expected}):
        raise ValueError('Rollback checkpoint file inventory mismatch.')
    if (type(checkpoint['static_changed']) is not bool or type(checkpoint['static_existed']) is not bool
            or checkpoint['static_changed'] != any(name.startswith(('frontend/', 'assets/'))
                                                  or name == 'core/settings.py' for name in expected)
            or not isinstance(checkpoint['static_before'], dict)
            or any(not safe_name(name) or not valid_hash(value)
                   for name, value in checkpoint['static_before'].items())
            or (not checkpoint['static_changed'] or not checkpoint['static_existed']) and checkpoint['static_before']):
        raise ValueError('Invalid rollback static inventory.')
    if (not valid_hash(checkpoint['requirements_sha256'])
            or checkpoint['requirements_sha256'] != baseline['requirements.txt']
            or not isinstance(checkpoint['schema'], list) or not checkpoint['schema']
            or not isinstance(checkpoint['environment'], list) or not checkpoint['environment']):
        raise ValueError('Invalid rollback compatibility metadata.')
    installed = {**manifest, 'archive_sha256': checkpoint['archive_sha256'],
                 'checkpoint_sha256': reference['checkpoint_sha256']}
    previous = checkpoint['previous']
    if not isinstance(previous, dict) or (previous and (not isinstance(previous.get('release'), str)
            or not RELEASE_ID.fullmatch(previous['release']) or previous['release'] == reference['release'])):
        raise ValueError('Invalid previous deployment identity.')
    if previous:
        previous_manifest = validate_archive(
            trusted_artifact(STATE / 'incoming' / (previous['release'] + '.tar.gz')),
            previous.get('archive_sha256'))
        if (previous != {**previous_manifest, 'archive_sha256': previous.get('archive_sha256'),
                         'checkpoint_sha256': previous.get('checkpoint_sha256')}
                or not valid_hash(previous.get('checkpoint_sha256'))
                or sha256(trusted_artifact(STATE / 'releases' / previous['release'] / 'checkpoint.json'))
                   != previous['checkpoint_sha256']):
            raise ValueError('Previous deployment integrity verification failed.')
    if current != previous and current != installed:
        raise ValueError('Rollback checkpoint does not match deployment state.')
    # Check unchanged managed files too, not just files to be restored.
    for name in set(files) - expected:
        if sha256(trusted_artifact(APP / name)) != files[name]['sha256']:
            raise ValueError('Rollback blocked: unexpected server edit to ' + name)
    backup = directory / 'backup'
    trusted_artifact(backup, directory=True)
    actual = set()
    for member in backup.rglob('*'):
        if member.is_symlink() or (not member.is_file() and not member.is_dir()):
            raise ValueError('Unsafe rollback backup member.')
        if member.is_file():
            trusted_artifact(member)
            actual.add(member.relative_to(backup).as_posix())
    if not backup.is_dir() or backup.is_symlink() or actual != {name for name in expected if baseline[name] is not None}:
        raise ValueError('Rollback backup inventory mismatch.')
    settings = trusted_artifact(directory / 'baseline' / 'settings.py')
    if sha256(settings) != checkpoint['baseline_settings_sha256']:
        raise ValueError('Rollback baseline settings integrity verification failed.')
    environment = settings.parent / '.env'
    if not environment.is_symlink() or environment.resolve() != (APP / '.env').resolve():
        raise ValueError('Unexpected rollback environment reference.')
    validate_restore_files(checkpoint)
    return checkpoint


def deploy(release, digest, cfg, audit):
    if not RELEASE_ID.fullmatch(release):
        raise ValueError('Invalid release identifier.')
    if (STATE / 'pending.json').exists():
        raise ValueError('An interrupted deployment needs explicit rollback before deploying again.')
    archive = regular(STATE / 'incoming' / (release + '.tar.gz'))
    manifest = validate_archive(archive, digest)
    if manifest['release'] != release:
        raise ValueError('Archive release does not match requested identifier.')
    audit.record('archive_verified', archive_sha256=digest, commit=manifest['commit'], files=manifest['files'])
    current = read_json(STATE / 'current.json', {})
    if current.get('release') == release:
        if current.get('archive_sha256') != digest or changed_files(manifest):
            raise ValueError('Release identifier conflict or server source drift.')
        healthy(audit, attempts=1)
        print('Release already deployed and healthy.'); return
    missing = set(current.get('files', {})) - set(manifest['files'])
    if missing:
        raise ValueError('Source removal requires separate review; no files were deleted: ' + ', '.join(sorted(missing)))
    for name, item in current.get('files', {}).items():
        if not allowed(name):
            raise ValueError('Unsafe current release manifest.')
        if name == 'requirements.txt' or '/migrations/' in name:
            continue  # Separately reviewed maintenance has its own gates below.
        target = regular(APP / name)
        if not target.exists() or sha256(target) != item['sha256']:
            raise ValueError('Server source changed outside deployment; review and preserve it first: ' + name)
    release_dir = STATE / 'releases' / release
    if release_dir.exists():
        raise ValueError('Release ID was already staged. Inspect audit logs; package a new release to retry.')
    static_bytes = sum(p.stat().st_size for p in (APP / 'staticfiles').rglob('*') if p.is_file())
    old_bytes = sum(regular(APP / name).stat().st_size for name in manifest['files'] if (APP / name).exists())
    needed = 4 * (sum(item['size'] for item in manifest['files'].values()) + old_bytes + static_bytes) + 256 * 1024 * 1024
    if shutil.disk_usage(STATE).free < needed or shutil.disk_usage(APP).free < needed:
        raise ValueError('Insufficient disk space for staging, backup and static files.')
    release_dir.mkdir(mode=0o700)
    stage = release_dir / 'source'; stage.mkdir(mode=0o700)
    extract(archive, stage)
    requirements_gate(cfg, stage)
    for name, item in manifest['files'].items():
        if '/migrations/' in name and (not regular(APP / name).exists() or sha256(APP / name) != item['sha256']):
            raise ValueError('Deployment blocked: migration review required. Install reviewed migration files and apply them during separate backed-up maintenance first.')
    # Preserve production settings, but only use the existing venv; never recreate it.
    (stage / '.env').symlink_to(APP / '.env')
    audit.run([APP / 'venv/bin/python', '-m', 'pip', 'check'])
    installed_packages = environment_snapshot(audit)
    audit.run([APP / 'venv/bin/python', 'manage.py', 'check'], cwd=stage)
    audit.run([APP / 'venv/bin/python', 'manage.py', 'makemigrations', '--check', '--dry-run', '--noinput'], cwd=stage)
    migration_state = schema(audit, stage)
    if migration_state != schema(audit, APP):
        raise ValueError('Migration history changed during preflight; review required.')
    healthy(audit, attempts=1)
    baseline = release_dir / 'baseline'; baseline.mkdir(mode=0o700)
    shutil.copyfile(APP / 'core/settings.py', baseline / 'settings.py')
    (baseline / '.env').symlink_to(APP / '.env')
    if history(audit, release) != migration_state:
        raise ValueError('Baseline migration history mismatch.')
    changes = changed_files(manifest)
    for name in changes:
        writable_target(APP / name)
    for action in ('stop', 'restart'):
        audit.run(['/usr/bin/sudo', '-n', '-l', '/usr/bin/systemctl', action, SERVICE])
    audit.record('release_validated', commit=manifest['commit'], archive_sha256=digest, changed_files=changes)
    static_changed = any(name.startswith(('frontend/', 'assets/')) or name == 'core/settings.py' for name in changes)
    if static_changed:
        writable_target(APP / 'staticfiles' / 'permission-probe')
        for directory in (APP / 'staticfiles').rglob('*'):
            if directory.is_dir() and not os.access(directory, os.W_OK | os.X_OK):
                raise ValueError('Deployment account cannot update/restore staticfiles directories.')
    checkpoint = {'format': 2, 'release': release, 'previous': current, 'schema': migration_state,
        'manifest': manifest, 'archive_sha256': digest,
        'baseline_files': {name: sha256(APP / name) if (APP / name).exists() else None for name in manifest['files']},
        'baseline_settings_sha256': sha256(baseline / 'settings.py'),
        'environment': installed_packages,
        'requirements_sha256': sha256(APP / 'requirements.txt'),
        'before': {}, 'after': {name: manifest['files'][name] for name in changes},
        'static_changed': static_changed, 'static_existed': (APP / 'staticfiles').exists()}
    backup = release_dir / 'backup'; backup.mkdir(mode=0o700)
    for name in changes:
        source = regular(APP / name)
        checkpoint['before'][name] = sha256(source) if source.exists() else None
        if source.exists():
            target = backup / name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    checkpoint['static_before'] = snapshot_static(release_dir / 'staticfiles') if static_changed else {}
    for path in release_dir.rglob('*'):
        if path.is_file() and not path.is_symlink():
            with path.open('r+b') as stream:
                os.fsync(stream.fileno())
    for directory in sorted((p for p in release_dir.rglob('*') if p.is_dir()), reverse=True):
        sync_directory(directory)
    sync_directory(release_dir)
    sync_directory(release_dir.parent)
    atomic_json(release_dir / 'checkpoint.json', checkpoint)
    reference = checkpoint_reference(checkpoint)
    validate_checkpoint(reference, current)
    atomic_json(STATE / 'pending.json', reference)
    try:
        # Stop requests/workers during the in-place source switch. No mixed code.
        service(audit, 'stop')
        for name in changes:
            copy_atomic(stage / name, APP / name)
        if changed_files(manifest):
            raise ValueError('Installed source hash verification failed.')
        if static_changed:
            audit.run([APP / 'venv/bin/python', 'manage.py', 'collectstatic', '--noinput'])
        if history(audit, release) != migration_state:
            raise ValueError('Database migration history changed; automatic rollback is unsafe.')
        if environment_snapshot(audit) != installed_packages:
            raise ValueError('Installed dependencies changed during deployment; review required.')
        service(audit, 'restart')
        healthy(audit)
        atomic_json(STATE / 'current.json', {**manifest, 'archive_sha256': digest,
                                           'checkpoint_sha256': reference['checkpoint_sha256']})
        clear_pending()
        audit.record('deployment', result='success', rollback_checkpoint=str(release_dir / 'checkpoint.json'))
        print('Deployment successful. Release ' + release)
    except Exception:
        audit.record('deployment', result='failed')
        try:
            validate_checkpoint(reference, read_json(STATE / 'current.json', {}))
            if sha256(APP / 'requirements.txt') != checkpoint['requirements_sha256']:
                raise ValueError('Requirements changed; automatic rollback needs operator review.')
            if history(audit, release) != migration_state:
                raise ValueError('Schema changed; automatic code rollback blocked.')
            if environment_snapshot(audit) != installed_packages:
                raise ValueError('Dependencies changed; automatic code rollback blocked.')
            service(audit, 'stop')
            restore(checkpoint, audit)
            service(audit, 'restart')
            healthy(audit)
            atomic_json(STATE / 'current.json', current)
            clear_pending()
            audit.record('rollback', result='success')
            print('Deployment failed; previous application files restored and health checks passed.')
        except Exception as rollback_error:
            audit.record('rollback', result='failed', error=str(rollback_error))
            print('Automatic rollback blocked/failed. Keep the checkpoint and inspect ' + str(audit.path), file=sys.stderr)
        raise


def rollback(cfg, audit):
    for name in ('current.json', 'pending.json'):
        if (STATE / name).exists() or (STATE / name).is_symlink():
            trusted_artifact(STATE / name)
    current = read_json(STATE / 'current.json', {})
    pending = read_json(STATE / 'pending.json')
    if not isinstance(current, dict):
        raise ValueError('Invalid current deployment state.')
    if (STATE / 'pending.json').exists():
        reference = pending
    elif current.get('release'):
        reference = {'release': current['release'], 'checkpoint_sha256': current.get('checkpoint_sha256')}
    else:
        raise ValueError('No rollback checkpoint is available.')
    checkpoint = validate_checkpoint(reference, current)
    if pending is None and changed_files(checkpoint['manifest']):
        raise ValueError('Rollback blocked: current deployment source inventory changed.')
    if not sys.stdin.isatty() or input('Type the release being undone (' + checkpoint['release'] + '): ') != checkpoint['release']:
        raise ValueError('Explicit interactive rollback confirmation is required.')
    requirements_gate(cfg, APP)
    if sha256(APP / 'requirements.txt') != checkpoint['requirements_sha256']:
        raise ValueError('Rollback across a requirements change needs separate dependency review.')
    if history(audit, checkpoint['release']) != checkpoint['schema']:
        raise ValueError('Rollback blocked: migration history changed. Never reverse migrations automatically.')
    if environment_snapshot(audit) != checkpoint['environment']:
        raise ValueError('Rollback blocked: installed dependencies changed. Separate dependency review is required.')
    audit.record('rollback_confirmed', checkpoint=checkpoint['release'])
    audit.record('rollback_preflight', result='success', checkpoint=reference)
    # Journal rollback too; an interrupted rollback must block future deployment.
    atomic_json(STATE / 'pending.json', reference)
    service(audit, 'stop')
    restore(checkpoint, audit)
    service(audit, 'restart')
    healthy(audit)
    atomic_json(STATE / 'current.json', checkpoint['previous'])
    clear_pending()
    audit.record('rollback', result='success')
    print('Rollback successful. No database migrations were reversed.')


def main():
    os.umask(0o077)
    if len(sys.argv) < 2 or sys.argv[1] not in {'status', 'deploy', 'rollback'}:
        raise ValueError('Use status | deploy RELEASE SHA256 | rollback.')
    action = sys.argv[1]
    if len(sys.argv) != (4 if action == 'deploy' else 2):
        raise ValueError('Unexpected arguments.')
    cfg = configuration()
    with deployment_lock():
        audit = Audit(action, sys.argv[2] if action == 'deploy' else 'current')
        audit.record('begin')
        try:
            if action == 'status':
                status_health = 'ok'
                try:
                    healthy(audit, attempts=1)
                except (ValueError, OSError):
                    status_health = 'unhealthy'
                print(json.dumps({'host': HEALTH_HOST, 'app': str(APP), 'service': SERVICE,
                    'current': read_json(STATE / 'current.json', {}).get('release', 'baseline (not managed yet)'),
                    'pending_recovery': (STATE / 'pending.json').exists(),
                    'disk_free_bytes': shutil.disk_usage(STATE).free,
                    'health': status_health}, indent=2))
                if status_health != 'ok':
                    raise ValueError('Server is unhealthy. Check audit logs or use explicit rollback recovery.')
            elif action == 'deploy':
                deploy(sys.argv[2], sys.argv[3], cfg, audit)
            else:
                rollback(cfg, audit)
        except Exception as exc:
            audit.record('failure', error=str(exc))
            raise


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print('Deployment blocked: ' + str(exc), file=sys.stderr)
        sys.exit(1)
