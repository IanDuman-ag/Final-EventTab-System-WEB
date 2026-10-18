import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

CP = Path('/srv/eventtab-activation-backups/20261018T100033Z-checkpoint')
APP = Path('/srv/eventtab/Final-EventTab-System-WEB')
SCRIPT = CP / 'capture.py'
BIN = Path('/usr/lib/postgresql/18/bin')
TOOLS = {
    'tar': '/usr/bin/tar',
    'getfacl': '/usr/bin/getfacl',
    'systemctl': '/usr/bin/systemctl',
    'runuser': '/usr/sbin/runuser',
    'env': '/usr/bin/env',
    'pg_dump': str(BIN / 'pg_dump'),
    'pg_restore': str(BIN / 'pg_restore'),
}

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def save(name, data):
    with (CP / name).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')

def capacity():
    require(shutil.disk_usage(CP).free >= 2 * 1024**3,
            'Less than 2 GiB disk reserve')
    memory = dict(line.split(':', 1)
                  for line in Path('/proc/meminfo').read_text().splitlines())
    require(int(memory['MemAvailable'].split()[0]) >= 512 * 1024,
            'Less than 512 MiB available memory')

def validate_root(path):
    require(path.is_absolute(), 'Non-absolute source')
    for component in (path, *path.parents):
        require(not component.is_symlink(),
                'Symlink source/ancestor requires review: ' + str(component))
    require(path.exists(), 'Missing required source: ' + str(path))
    require(path.is_file() or path.is_dir(), 'Unexpected source type')

def service_properties():
    return subprocess.check_output([
        TOOLS['systemctl'], 'show', 'eventtab',
        '--property=FragmentPath,DropInPaths,EnvironmentFiles'
    ], text=True, timeout=15)

def inventory(roots, excluded=()):
    result = {}

    def visit(path):
        if path in excluded:
            return
        before = path.lstat()
        mode = before.st_mode
        record = {
            'uid': before.st_uid,
            'gid': before.st_gid,
            'mode': oct(stat.S_IMODE(mode)),
            'device': before.st_dev,
            'inode': before.st_ino,
            'links': before.st_nlink,
            'size': before.st_size,
            'mtime_ns': before.st_mtime_ns,
            'ctime_ns': before.st_ctime_ns,
        }
        if stat.S_ISLNK(mode):
            record['type'] = 'symlink'
            record['target'] = os.readlink(path)
            # Preserve venv interpreter/library links without following them.
            if not path.is_relative_to(APP / 'venv'):
                target = path.resolve(strict=True)
                require(any(target.is_relative_to(root) for root in roots),
                        'External source symlink requires review: ' + str(path))
        elif stat.S_ISDIR(mode):
            record['type'] = 'directory'
            require(not os.path.ismount(path),
                    'Mounted source requires review: ' + str(path))
            for child in sorted(path.iterdir()):
                visit(child)
        elif stat.S_ISREG(mode):
            record['type'] = 'file'
            record['sha256'] = digest(path)
        else:
            raise RuntimeError('Unexpected special file: ' + str(path))

        record['xattrs'] = {
            name: os.getxattr(path, name, follow_symlinks=False).hex()
            for name in sorted(os.listxattr(path, follow_symlinks=False))
        }
        after = path.lstat()
        require(
            (before.st_dev, before.st_ino, before.st_size,
             before.st_mtime_ns, before.st_ctime_ns) ==
            (after.st_dev, after.st_ino, after.st_size,
             after.st_mtime_ns, after.st_ctime_ns),
            'Source changed during inspection: ' + str(path))
        result[str(path)] = record

    for root in roots:
        visit(root)
    return result

def run(arguments, log, output=None):
    with (CP / log).open('xb') as errors:
        process = subprocess.run(
            arguments, check=False,
            stdout=errors if output is None else output,
            stderr=errors, stdin=subprocess.DEVNULL)
    require(process.returncode == 0,
            'Command failed; inspect private log ' + log)

def main():
    require(os.geteuid() == 0, 'Root console required')
    os.umask(0o077)

    for path in (CP.parent, CP):
        require(not path.is_symlink(), 'Unsafe checkpoint path')
        value = path.stat()
        require(stat.S_ISDIR(value.st_mode) and
                value.st_uid == 0 and value.st_gid == 0 and
                stat.S_IMODE(value.st_mode) == 0o700,
                'Checkpoint directory must be root:root 0700')
    require(set(CP.iterdir()) == {SCRIPT},
            'Checkpoint contains existing artifacts; do not overwrite or delete')
    value = SCRIPT.lstat()
    require(stat.S_ISREG(value.st_mode) and value.st_uid == 0 and
            value.st_gid == 0 and stat.S_IMODE(value.st_mode) == 0o600,
            'Capture script must be root:root 0600')
    for executable in TOOLS.values():
        require(Path(executable).is_file() and os.access(executable, os.X_OK),
                'Missing executable: ' + executable)
    subprocess.run([TOOLS['systemctl'], 'is-active', '--quiet', 'eventtab'],
                   check=True, timeout=15)
    capacity()

    properties = service_properties()
    fields = dict(line.partition('=')[::2]
                  for line in properties.splitlines())
    require(fields.get('FragmentPath') ==
            '/etc/systemd/system/eventtab.service',
            'Unexpected EventTab unit path')
    # The last reviewed production evidence reported no drop-ins.
    require(not fields.get('DropInPaths'), 'New drop-ins require scope review')

    service_paths = [Path(fields['FragmentPath'])]
    absent = []
    tokens = shlex.split(fields.get('EnvironmentFiles', ''))
    require(len(tokens) % 2 == 0, 'Unrecognized EnvironmentFiles format')
    for index in range(0, len(tokens), 2):
        name, flag = tokens[index:index + 2]
        require(flag in ('(ignore_errors=yes)', '(ignore_errors=no)'),
                'Unrecognized EnvironmentFiles optional flag')
        require(name.startswith('/') and
                not re.search(r'[\s%*?\[\]\\]', name),
                'Environment-file expansion/escaping requires review')
        path = Path(name)
        require(str(path) == os.path.normpath(str(path)),
                'Non-canonical environment-file path')
        require(path == APP / '.env' or
                path.is_relative_to('/etc/eventtab-rehearsal') is False and
                (path.is_relative_to('/etc/eventtab') or
                 path == Path('/etc/default/eventtab')),
                'Unreviewed environment-file path')
        if not path.exists() and not path.is_symlink():
            require(flag == '(ignore_errors=yes)',
                    'Required environment file missing')
            absent.append(str(path))
        else:
            require(path.is_file(), 'Environment file is not regular')
            service_paths.append(path)

    config = [
        Path('/etc/eventtab-deploy.json'),
        Path('/usr/local/lib/eventtab-deploy'),
        Path('/srv/eventtab-deploy'),
    ]
    for path in (Path('/etc/eventtab-public.json'),
                 Path('/srv/eventtab-public')):
        if path.exists() or path.is_symlink():
            config.append(path)
        else:
            absent.append(str(path))

    groups = {
        'application': ([APP], [APP / 'venv']),
        'venv': ([APP / 'venv'], []),
        'deployment-config': (config, []),
        'service-config': (sorted(set(service_paths)), []),
        'public': ([Path('/var/www/eventtab/static'),
                    Path('/var/www/eventtab/assets')], []),
    }
    for roots, excluded in groups.values():
        for root in roots:
            validate_root(root)

    save('scope.json', {
        'checkpoint': str(CP),
        'started_utc': datetime.now(timezone.utc).isoformat(),
        'groups': {
            label: {'roots': list(map(str, roots)),
                    'excluded': list(map(str, excluded))}
            for label, (roots, excluded) in groups.items()
        },
        'absent_optional_paths': absent,
        'service_properties': properties,
    })
    before = {
        label: inventory(roots, excluded)
        for label, (roots, excluded) in groups.items()
    }
    save('before-manifest.json', before)

    for label, (roots, excluded) in groups.items():
        capacity()
        archive = CP / (label + '.tar')
        require(not archive.exists(), 'Archive already exists')
        names = [str(root).lstrip('/') for root in roots]
        exclusions = [
            '--exclude=' + str(path).lstrip('/') for path in excluded
        ]
        # Exact anchored venv exclusion, including all its descendants.
        run([
            TOOLS['tar'], '--create', '--file=' + str(archive),
            '--acls', '--xattrs', '--xattrs-include=*',
            '--numeric-owner', '--sparse', '--one-file-system',
            '--anchored', '--wildcards-match-slash',
            *exclusions, '-C', '/', '--', *names
        ], label + '-capture.log')
        with (CP / (label + '-members.txt')).open('xb') as output:
            run([TOOLS['tar'], '--list', '--file=' + str(archive)],
                label + '-list.log', output)
        run([
            TOOLS['tar'], '--compare', '--file=' + str(archive),
            '--acls', '--xattrs', '--xattrs-include=*',
            '--numeric-owner', '-C', '/'
        ], label + '-compare.log')
        with (CP / (label + '-acl.txt')).open('xb') as output:
            run([TOOLS['getfacl'], '-R', '-P', '-p', '--',
                 *map(str, roots)], label + '-acl.log', output)

    capacity()
    with (CP / 'database.dump').open('xb') as output:
        run([
            TOOLS['runuser'], '-u', 'postgres', '--', TOOLS['env'],
            'PGOPTIONS=-c default_transaction_read_only=on',
            TOOLS['pg_dump'], '-h', '/var/run/postgresql',
            '-p', '5433', '-d', 'eventtab_db',
            '--format=custom', '--lock-wait-timeout=5s'
        ], 'database-dump.log', output)
    run([TOOLS['pg_restore'], '--file=/dev/null',
         str(CP / 'database.dump')], 'database-parse.log')

    after = {
        label: inventory(roots, excluded)
        for label, (roots, excluded) in groups.items()
    }
    save('after-manifest.json', after)
    require(before == after,
            'Source drift: retain incomplete checkpoint; request a quiet window')
    require(service_properties() == properties,
            'Service configuration changed during capture')
    for name in absent:
        path = Path(name)
        require(not path.exists() and not path.is_symlink(),
                'Previously absent source appeared')
    subprocess.run([TOOLS['systemctl'], 'is-active', '--quiet', 'eventtab'],
                   check=True, timeout=15)
    capacity()

    files = {}
    for path in sorted(CP.iterdir()):
        value = path.lstat()
        require(stat.S_ISREG(value.st_mode) and
                value.st_uid == 0 and value.st_gid == 0 and
                stat.S_IMODE(value.st_mode) == 0o600,
                'Unsafe checkpoint file permissions/type')
        files[path.name] = {
            'bytes': value.st_size, 'sha256': digest(path),
            'uid': 0, 'gid': 0, 'mode': '0600',
        }
    save('verification-receipt.json', {
        'status': 'PASS',
        'checkpoint': str(CP),
        'finished_utc': datetime.now(timezone.utc).isoformat(),
        'files': files,
        'source_drift': False,
        'tar_payload_and_metadata_comparisons': 'PASS',
        'database_full_parse': 'PASS',
        'restoration_tested': False,
        'synchronized_files_database_snapshot': False,
        'service_operations': False,
    })
    checksums = {}
    for path in sorted(CP.iterdir()):
        checksums[path.name] = digest(path)
    with (CP / 'SHA256SUMS').open('x') as stream:
        for name, value in checksums.items():
            stream.write(value + '  ' + name + '\n')
    for name, expected in checksums.items():
        require(digest(CP / name) == expected,
                'Final checksum verification failed: ' + name)

    print('PASS: checkpoint capture, archive comparisons, database parsing,')
    print('source-drift checks and checkpoint SHA256 verification.')
    print('Checkpoint:', CP)
    for name in ('application.tar', 'venv.tar', 'deployment-config.tar',
                 'service-config.tar', 'public.tar', 'database.dump'):
        path = CP / name
        print(name, path.stat().st_size, checksums[name])
    print('Restoration and synchronized cross-resource recovery remain unverified.')

try:
    main()
except Exception as error:
    print('FAIL: ' + str(error), file=sys.stderr)
    print('Retain all artifacts. Do not retry in this checkpoint directory.',
          file=sys.stderr)
    sys.exit(1)
