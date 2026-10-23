"""Release format and local packaging. Standard library only; never reads .env."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile

FORMAT = 1
MAX_BYTES = 512 * 1024 * 1024
RELEASE_ID = re.compile(r'^\d{8}T\d{6}Z-[0-9a-f]{12}$')
STATIC_EXTENSIONS = {'.html', '.css', '.js', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.ico', '.woff', '.woff2', '.ttf'}
DESIGN_ASSETS = {'assets/USTP BG.png', 'assets/loginlogo/adminlogo.png',
    'assets/loginlogo/ustpentrance.png', 'assets/departmentlogo/OSA.png',
    'assets/departmentlogo/USG.png', 'assets/departmentlogo/USTP.png',
    'assets/landingpagelogo/landingpage.jpg', 'assets/landingpagelogo/logoeventtab (1).png'}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def safe_name(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or any(ord(c) < 32 for c in name):
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and name == path.as_posix() and all(
        part not in {'', '.', '..'} and not part.startswith('.') for part in name.split('/'))


def allowed(name):
    if not safe_name(name):
        return False
    path = PurePosixPath(name)
    if name in {'manage.py', 'requirements.txt'} or name in DESIGN_ASSETS:
        return True
    if name in {'frontend/admindash/vendor/LICENSE.bootstrap', 'frontend/tabulator/vendor/LICENSE.bootstrap-icons'}:
        return True
    if any(part.lower() in {'__pycache__', 'backup', 'backups', 'evidence', 'uploads', 'logs', 'node_modules'}
           or part.lower().startswith(('test_', 'test.', 'credentials', 'secret', 'vocal_duet'))
           for part in path.parts):
        return False
    if path.parts[0] in {'core', 'accounts', 'events'}:
        if 'management' in path.parts:
            return path.name == '__init__.py' or name == 'events/management/commands/prepare_event_covers.py'
        return path.suffix == '.py' and path.name != 'tests.py'
    return path.parts[0] == 'frontend' and path.suffix.lower() in STATIC_EXTENSIONS


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True, encoding='utf-8').strip()


def inspect(root):
    if Path(git(root, 'rev-parse', '--show-toplevel')).resolve() != root.resolve():
        raise ValueError('Run from the Django Git repository root.')
    branch = git(root, 'symbolic-ref', '--short', 'HEAD')
    commit = git(root, 'rev-parse', 'HEAD')
    dirty = git(root, 'status', '--porcelain', '--untracked-files=normal')
    files = [p for p in git(root, 'ls-files', '-z').split('\0') if p and allowed(p)]
    return branch, commit, dirty, sorted(files)


def committed_files(root, commit, files):
    """Read exact committed bytes, avoiding Windows autocrlf/smudge differences."""
    process = subprocess.Popen(['git', '-C', str(root), 'cat-file', '--batch'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    total = 0
    try:
        for name in files:
            process.stdin.write((commit + ':' + name + '\n').encode('utf-8'))
            process.stdin.flush()
            header = process.stdout.readline(512).split()
            if len(header) != 3 or header[1] != b'blob':
                raise ValueError('Unable to read committed file: ' + name)
            size = int(header[2]); total += size
            if size < 0 or total > MAX_BYTES:
                raise ValueError('Committed source exceeds release size limit.')
            raw = process.stdout.read(size)
            if len(raw) != size or process.stdout.read(1) != b'\n':
                raise ValueError('Incomplete Git blob: ' + name)
            yield name, raw
        process.stdin.close()
        if process.wait(timeout=10):
            raise ValueError('Git blob export failed.')
    finally:
        if process.poll() is None:
            process.kill(); process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def build(root, output, dry_run=False):
    branch, commit, dirty, files = inspect(root)
    if not dry_run and dirty:
        raise ValueError('Commit reviewed changes first. The working tree has uncommitted or untracked files.')
    if not files or not {'manage.py', 'requirements.txt', 'core/settings.py', 'core/urls.py'}.issubset(files):
        raise ValueError('Required application files are missing from Git.')
    now = datetime.now(timezone.utc)
    release = now.strftime('%Y%m%dT%H%M%SZ') + '-' + commit[:12]
    entries = {}
    modes = {entry.split('\t', 1)[1]: entry.split()[0]
             for entry in git(root, 'ls-files', '-s', '-z').split('\0') if entry}
    for name in files:
        path = root / name
        if any(parent.is_symlink() for parent in [path, *path.parents]) or not path.is_file():
            raise ValueError('Non-regular or symlink source: ' + name)
        # Git index mode catches symlinks even on Windows checkouts with core.symlinks=false.
        mode = modes[name]
        if mode != '100644' and mode != '100755':
            raise ValueError('Unsupported Git file mode: ' + name)
        entries[name] = {'sha256': sha256(path), 'size': path.stat().st_size}
    payload = {}
    if not dry_run:
        for name, raw in committed_files(root, commit, files):
            entries[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
            payload[name] = raw
        if git(root, 'rev-parse', 'HEAD') != commit or git(root, 'status', '--porcelain', '--untracked-files=normal'):
            raise ValueError('Git changed during packaging. Review and retry.')
    manifest = {'format': FORMAT, 'release': release, 'timestamp': now.isoformat(),
                'commit': commit, 'branch': branch, 'files': entries}
    print(json.dumps({'release': release, 'branch': branch, 'commit': commit, 'dirty': bool(dirty),
        'file_count': len(entries), 'files': files,
        'schema_files': [p for p in files if '/migrations/' in p or p.endswith('/models.py')],
        'dry_run': dry_run}, indent=2))
    if dry_run:
        return manifest
    output.mkdir(parents=True, exist_ok=True)
    archive = output / (release + '.tar.gz')
    if archive.exists():
        raise ValueError('Release already exists; wait one second and retry.')
    with tarfile.open(archive, 'w:gz') as tar:
        raw = json.dumps(manifest, sort_keys=True).encode()
        info = tarfile.TarInfo('release-manifest.json'); info.size = len(raw); info.mode = 0o600
        tar.addfile(info, io.BytesIO(raw))
        for name in files:
            raw = payload[name]
            info = tarfile.TarInfo(name); info.size = len(raw); info.mode = 0o644
            tar.addfile(info, io.BytesIO(raw))
    validate_archive(archive, sha256(archive))
    (output / 'release.json').write_text(json.dumps({
        'release': release, 'archive': str(archive.resolve()), 'sha256': sha256(archive)}, indent=2), encoding='utf-8')
    return manifest


def validate_archive(archive, expected_hash):
    if not re.fullmatch('[0-9a-f]{64}', expected_hash) or sha256(archive) != expected_hash:
        raise ValueError('Archive SHA256 verification failed.')
    if Path(archive).stat().st_size > MAX_BYTES:
        raise ValueError('Archive is too large.')
    with tarfile.open(archive, 'r:gz') as tar:
        members = []; names = set(); total = 0
        for member in tar:
            if not member.isfile() or not safe_name(member.name) or member.name in names:
                raise ValueError('Unsafe, duplicate or non-regular archive member.')
            if member.name != 'release-manifest.json' and not allowed(member.name):
                raise ValueError('Excluded archive member: ' + member.name)
            total += member.size
            if total > MAX_BYTES or len(members) >= 10000:
                raise ValueError('Archive exceeds expanded size/file limit.')
            names.add(member.name); members.append(member)
        manifest_member = next((m for m in members if m.name == 'release-manifest.json'), None)
        if not manifest_member or manifest_member.size > 4 * 1024 * 1024:
            raise ValueError('Missing or oversized release manifest.')
        manifest = json.load(tar.extractfile(manifest_member))
        if manifest.get('format') != FORMAT or not RELEASE_ID.fullmatch(manifest.get('release', '')):
            raise ValueError('Invalid release format/identifier.')
        if not re.fullmatch('[0-9a-f]{40,64}', manifest.get('commit', '')):
            raise ValueError('Invalid Git commit.')
        if not manifest['release'].endswith('-' + manifest['commit'][:12]):
            raise ValueError('Release identifier does not match commit.')
        if set(manifest['files']) != names - {'release-manifest.json'}:
            raise ValueError('Manifest file set mismatch.')
        if not {'manage.py', 'requirements.txt', 'core/settings.py', 'core/urls.py'}.issubset(manifest['files']):
            raise ValueError('Incomplete application release.')
        for member in members:
            if member.name == 'release-manifest.json':
                continue
            item = manifest['files'][member.name]
            digest = hashlib.file_digest(tar.extractfile(member), 'sha256').hexdigest()
            if item != {'sha256': digest, 'size': member.size}:
                raise ValueError('File hash/size mismatch: ' + member.name)
    return manifest


def extract(archive, destination):
    """Call only after validation, into a new private staging directory."""
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            if not member.isfile() or not safe_name(member.name):
                raise ValueError('Unsafe archive member.')
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                shutil.copyfileobj(tar.extractfile(member), stream)
            target.chmod(0o644)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        build(args.root.resolve(), args.output.resolve(), args.dry_run)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, 'Deployment blocked: ' + str(exc) + '\n')
