"""Bounded root-only public-file publication; install separately after approval."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import uuid

APP = Path('/srv/eventtab/Final-EventTab-System-WEB')
PUBLIC = {'static': Path('/var/www/eventtab/static'), 'assets': Path('/var/www/eventtab/assets')}
STATE = Path('/srv/eventtab-public')
CONFIG = Path('/etc/eventtab-public.json')
ALLOWLIST = Path('/usr/local/lib/eventtab-deploy/publication-allowlist.json')
MAX_BYTES = 512 * 1024 * 1024
HASH = re.compile(r'^[0-9a-f]{64}$')
IDENTIFIER = re.compile(r'^[0-9a-f]{32}$')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_key(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        return False
    path = PurePosixPath(name)
    return (not path.is_absolute() and path.as_posix() == name
            and all(part not in {'', '.', '..'} and not part.startswith('.')
                    and not any(ord(char) < 32 for char in part) for part in name.split('/')))


def trusted(info, directory=False):
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Public configuration, destinations and checkpoints must be root-controlled.')
    if not directory and info.st_nlink != 1:
        raise ValueError('Hard-linked public artifact rejected.')


@contextmanager
def directory(path, protected=False, create=False, mode=0o700):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('An absolute bounded directory is required.')
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        if protected:
            trusted(os.fstat(descriptor), directory=True)
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=mode, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            if protected:
                trusted(os.fstat(descriptor), directory=True)
        yield descriptor
    finally:
        os.close(descriptor)


def read(root, name, protected=False, missing=False):
    if not safe_key(name):
        raise ValueError('Unsafe public path.')
    path = Path(name)
    try:
        with directory(root / path.parent, protected) as parent:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(descriptor, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
                    raise ValueError('Unsafe or oversized public file.')
                if protected:
                    trusted(info)
                raw = stream.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError('Oversized public file.')
                return raw
    except FileNotFoundError:
        if missing:
            return None
        raise


def atomic(root, name, raw, mode=0o600, directory_mode=0o700):
    if not safe_key(name):
        raise ValueError('Unsafe public destination.')
    path = Path(name)
    with directory(root / path.parent, protected=True, create=True, mode=directory_mode) as parent:
        try:
            trusted(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
        except FileNotFoundError:
            pass
        temporary = path.name + '.' + uuid.uuid4().hex + '.new'
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), mode)
            os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def remove(root, name):
    path = Path(name)
    if not safe_key(name):
        raise ValueError('Unsafe removal path.')
    try:
        with directory(root / path.parent, protected=True) as parent:
            try:
                trusted(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
                os.unlink(path.name, dir_fd=parent)
                os.fsync(parent)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass


def save(name, value):
    raw = json.dumps(value, sort_keys=True).encode()
    atomic(STATE, name, raw)
    return digest(raw)


def load(name):
    raw = read(STATE, name, protected=True, missing=True)
    return json.loads(raw) if raw is not None else None


def pending():
    raw = read(STATE, 'pending.json', protected=True, missing=True)
    if raw is None:
        return None
    value = json.loads(raw)
    if value is None:
        raise ValueError('Malformed pending public journal.')
    return value


def configuration():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Public publisher requires Linux root; application sudo access is not supported.')
    module = Path(__file__).absolute()
    read(module.parent, module.name, protected=True)
    raw = read(CONFIG.parent, CONFIG.name, protected=True)
    config = json.loads(raw)
    if set(config) != {'machine_id', 'allowlist_sha256'}:
        raise ValueError('Unexpected public configuration fields.')
    if config['machine_id'] != Path('/etc/machine-id').read_text().strip():
        raise ValueError('Public publisher machine identity mismatch.')
    raw = read(ALLOWLIST.parent, ALLOWLIST.name, protected=True)
    if digest(raw) != config['allowlist_sha256']:
        raise ValueError('Public allowlist checksum mismatch.')
    values = json.loads(raw)
    if set(values) != {'format', 'static', 'assets'} or values['format'] != 1:
        raise ValueError('Invalid public allowlist format.')
    keys = []
    for kind in PUBLIC:
        names = values[kind]
        if (not isinstance(names, list) or not names or any(not safe_key(name) for name in names)
                or names != sorted(set(names))):
            raise ValueError('Public allowlists must contain sorted unique exact paths.')
        for name in names:
            if any(part.lower().startswith(('secret', 'credential')) or part.lower() in {'backup', 'backups'}
                   for part in Path(name).parts):
                raise ValueError('Private public-file name rejected.')
            suffix = Path(name).suffix.lower()
            if (suffix not in {'.html', '.css', '.js', '.svg', '.png', '.jpg', '.jpeg', '.webp',
                               '.ico', '.woff', '.woff2', '.ttf', '.eot', '.otf', '.gif', '.txt', '.map'}
                    and Path(name).name != 'README.md' and not Path(name).name.startswith('LICENSE')):
                raise ValueError('Disallowed public file type.')
            keys.append(kind + '/' + name)
        with directory(PUBLIC[kind], protected=True):
            pass
    if len(keys) > 10000:
        raise ValueError('Oversized public allowlist.')
    return sorted(keys), config['allowlist_sha256']


@contextmanager
def locked():
    import fcntl
    with directory(STATE, protected=True, create=True) as parent:
        descriptor = os.open('publication.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        trusted(os.fstat(descriptor))
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def target(key):
    kind, name = key.split('/', 1)
    return PUBLIC[kind], name


def sources(keys, missing=False):
    payload = {}
    total = 0
    for key in keys:
        kind, name = key.split('/', 1)
        root = APP / ('staticfiles' if kind == 'static' else 'assets')
        raw = read(root, name, missing=missing)
        total += len(raw) if raw is not None else 0
        if total > MAX_BYTES:
            raise ValueError('Expanded public payload exceeds limit.')
        payload[key] = raw
    return payload


def checkpoint(reference, keys, allowlist_hash):
    if (not isinstance(reference, dict) or set(reference) != {'id', 'sha256'}
            or not isinstance(reference['id'], str) or not IDENTIFIER.fullmatch(reference['id'])
            or not isinstance(reference['sha256'], str) or not HASH.fullmatch(reference['sha256'])):
        raise ValueError('Invalid public checkpoint reference.')
    name = 'checkpoints/' + reference['id'] + '/checkpoint.json'
    raw = read(STATE, name, protected=True)
    if digest(raw) != reference['sha256']:
        raise ValueError('Public checkpoint checksum mismatch.')
    value = json.loads(raw)
    if (set(value) != {'format', 'allowlist_sha256', 'before', 'after', 'previous'} or value['format'] != 1
            or value['allowlist_sha256'] != allowlist_hash
            or not isinstance(value['before'], dict) or not isinstance(value['after'], dict)
            or set(value['before']) != set(keys) or set(value['after']) != set(keys)
            or any(not isinstance(item, str) or not HASH.fullmatch(item) for item in value['after'].values())
            or any(item is not None and (not isinstance(item, str) or not HASH.fullmatch(item))
                   for item in value['before'].values())):
        raise ValueError('Invalid public checkpoint inventory.')
    if value['previous'] is not None:
        previous = value['previous']
        if (not isinstance(previous, dict) or set(previous) != {'id', 'sha256'}
                or not isinstance(previous['id'], str) or not IDENTIFIER.fullmatch(previous['id'])
                or previous['id'] == reference['id'] or not isinstance(previous['sha256'], str)
                or not HASH.fullmatch(previous['sha256'])):
            raise ValueError('Invalid previous public checkpoint.')
        prior = read(STATE, 'checkpoints/' + previous['id'] + '/checkpoint.json', protected=True)
        if digest(prior) != previous['sha256']:
            raise ValueError('Previous public checkpoint checksum mismatch.')
    return value


def restore(reference, keys, allowlist_hash):
    value = checkpoint(reference, keys, allowlist_hash)
    payload = {}
    total = 0
    for key in keys:
        root, name = target(key)
        actual = read(root, name, protected=True, missing=True)
        actual_hash = digest(actual) if actual is not None else None
        if actual_hash not in {value['before'][key], value['after'][key]}:
            raise ValueError('Public destination changed outside the checkpoint.')
        before = value['before'][key]
        raw = None if before is None else read(STATE, 'checkpoints/' + reference['id'] + '/backup/' + key, protected=True)
        if raw is not None and digest(raw) != before:
            raise ValueError('Public backup checksum mismatch.')
        total += len(raw) if raw is not None else 0
        if total > MAX_BYTES:
            raise ValueError('Expanded public backup exceeds limit.')
        payload[key] = raw
    for key, raw in payload.items():
        root, name = target(key)
        if raw is None:
            remove(root, name)
        else:
            atomic(root, name, raw, mode=0o644, directory_mode=0o755)
    save('current.json', value['previous'])
    remove(STATE, 'pending.json')
    save('checkpoints/' + reference['id'] + '/recovered.json', {'restored': True})


def synchronize(keys, allowlist_hash):
    if pending() is not None:
        raise ValueError('Interrupted public publication requires root-only recover first.')
    payload = sources(keys)
    before = {}
    after = {key: digest(raw) for key, raw in payload.items()}
    backups = {}
    total = 0
    for key in keys:
        root, name = target(key)
        raw = read(root, name, protected=True, missing=True)
        total += len(raw) if raw is not None else 0
        if total > MAX_BYTES:
            raise ValueError('Public baseline exceeds limit.')
        before[key] = digest(raw) if raw is not None else None
        backups[key] = raw
    previous = load('current.json')
    if previous is not None:
        prior = checkpoint(previous, keys, allowlist_hash)
        if before != prior['after']:
            raise ValueError('Public destinations changed since the last publication.')
    if before == after:
        return {'result': 'unchanged', 'files': len(keys)}
    identifier = uuid.uuid4().hex
    prefix = 'checkpoints/' + identifier + '/'
    for key, raw in backups.items():
        if raw is not None:
            atomic(STATE, prefix + 'backup/' + key, raw)
    value = {'format': 1, 'allowlist_sha256': allowlist_hash, 'before': before,
             'after': after, 'previous': previous}
    reference = {'id': identifier, 'sha256': save(prefix + 'checkpoint.json', value)}
    checkpoint(reference, keys, allowlist_hash)
    save('pending.json', reference)
    try:
        for key, raw in payload.items():
            root, name = target(key)
            atomic(root, name, raw, mode=0o644, directory_mode=0o755)
        save('current.json', reference)
        remove(STATE, 'pending.json')
    except Exception:
        restore(reference, keys, allowlist_hash)
        raise
    return {'result': 'published', 'files': len(keys), 'checkpoint': reference}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {'sync', 'recover', 'rollback'}:
        raise ValueError('Use sync | recover | rollback; paths and checkpoint IDs are never CLI inputs.')
    keys, allowlist_hash = configuration()
    with locked():
        if sys.argv[1] == 'sync':
            result = synchronize(keys, allowlist_hash)
        else:
            interrupted = pending()
            if sys.argv[1] == 'rollback' and interrupted is not None:
                raise ValueError('Recover the interrupted public publication first.')
            reference = interrupted if sys.argv[1] == 'recover' else load('current.json')
            if reference is None:
                raise ValueError('No public recovery checkpoint is available.')
            if sys.argv[1] == 'rollback':
                value = checkpoint(reference, keys, allowlist_hash)
                expected = {key: digest(raw) if raw is not None else None for key, raw in sources(keys, missing=True).items()}
                if expected != value['before']:
                    raise ValueError('Restore the application source version before public rollback.')
                save('pending.json', reference)
            restore(reference, keys, allowlist_hash)
            result = {'result': 'restored', 'checkpoint': reference}
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Public publication blocked: ' + str(error), file=sys.stderr)
        sys.exit(1)
