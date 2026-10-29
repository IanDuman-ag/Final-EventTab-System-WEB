import hashlib, json, os, re, stat, subprocess, tempfile
from pathlib import Path

OLD = 'f74239e40b816d1d71b1e67d28c5b7b419f0c6d1f00b2f187a20cf7f5e5ef56f'
NEW = 'aaddfda321ff5b76b9dd92fe06aad6e3f4189f58806d826b4a949d46bc09dff7'
APP = Path('/srv/eventtab/Final-EventTab-System-WEB')
REQ = APP / 'requirements.txt'
CFG = Path('/etc/eventtab-deploy.json')
SRC = Path('/home/eventtab/eventtab-deps-d6c5ade.7ta659am/requirements.txt')
BASE = Path('/srv/eventtab-activation-backups')

def check(condition, message):
    if not condition:
        raise RuntimeError(message)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def inspect(path, owner, mode):
    for parent in (path, *path.parents):
        check(not parent.is_symlink(), 'Symlink: ' + str(parent))
    info = path.stat()
    check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
          'Unexpected file type/link count: ' + str(path))
    check((info.st_uid, info.st_gid) == owner and
          stat.S_IMODE(info.st_mode) == mode,
          'Unexpected owner/mode: ' + str(path))
    return info

def identity(path):
    value = path.stat()
    return (value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)

def attributes(path):
    return {name: os.getxattr(path, name)
            for name in os.listxattr(path)}

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        check(key not in result, 'Duplicate configuration key')
        result[key] = value
    return result

def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

check(os.geteuid() == 0, 'Root console required')
os.umask(0o077)
check(not BASE.is_symlink(), 'Unsafe checkpoint parent')
base_info = BASE.stat()
check((base_info.st_uid, base_info.st_gid,
       stat.S_IMODE(base_info.st_mode)) == (0, 0, 0o700),
      'Unexpected checkpoint-parent permissions')

inspect(SRC, (1000, 1000), 0o600)
metadata = {REQ: inspect(REQ, (1000, 1000), 0o644),
            CFG: inspect(CFG, (0, 0), 0o644)}
original = {path: path.read_bytes() for path in metadata}
identities = {path: identity(path) for path in metadata}
xattrs = {path: attributes(path) for path in metadata}
new_requirements = SRC.read_bytes()
check(sha(original[REQ]) == OLD, 'Production requirements hash differs')
check(sha(new_requirements) == NEW, 'Staged requirements hash differs')

config = json.loads(original[CFG], object_pairs_hook=unique_object)
check(config.get('approved_requirements_sha256') == OLD,
      'Protected configuration hash differs')
pattern = rb'("approved_requirements_sha256"\s*:\s*")' + OLD.encode() + rb'(")'
new_config, count = re.subn(
    pattern, lambda match: match[1] + NEW.encode() + match[2], original[CFG])
check(count == 1, 'Configuration representation requires review')
expected = dict(config)
expected['approved_requirements_sha256'] = NEW
check(json.loads(new_config, object_pairs_hook=unique_object) == expected,
      'Unexpected configuration change')
desired = {REQ: new_requirements, CFG: new_config}

journal = Path(tempfile.mkdtemp(prefix='requirements-sync-', dir=BASE))
print('PRIVATE RECOVERY JOURNAL:', journal, flush=True)
for name, data in [('requirements.before', original[REQ]),
                   ('config.before', original[CFG])]:
    with (journal / name).open('xb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
with (journal / 'manifest.json').open('x') as output:
    json.dump({str(path): {
        'before_sha256': sha(original[path]),
        'after_sha256': sha(desired[path]),
        'uid': metadata[path].st_uid, 'gid': metadata[path].st_gid,
        'mode': '0644'
    } for path in metadata}, output, indent=2)
    output.flush()
    os.fsync(output.fileno())
sync_directory(journal)

def atomic_replace(path, data):
    descriptor, name = tempfile.mkstemp(prefix='.requirements-sync-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data)
            output.flush()
            os.fchown(output.fileno(), metadata[path].st_uid, metadata[path].st_gid)
            os.fchmod(output.fileno(), stat.S_IMODE(metadata[path].st_mode))
            for key in os.listxattr(temporary):
                if key not in xattrs[path]:
                    os.removexattr(temporary, key)
            for key, value in xattrs[path].items():
                os.setxattr(temporary, key, value)
            os.fsync(output.fileno())
        inspect(temporary, (metadata[path].st_uid, metadata[path].st_gid), 0o644)
        check(attributes(temporary) == xattrs[path], 'Temporary metadata differs')
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()

installed = []
try:
    for path in (REQ, CFG):
        check(identity(path) == identities[path] and
              path.read_bytes() == original[path] and
              attributes(path) == xattrs[path], 'Concurrent change: ' + str(path))
        installed.append(path)
        atomic_replace(path, desired[path])
    for path in (REQ, CFG):
        inspect(path, (metadata[path].st_uid, metadata[path].st_gid), 0o644)
        check(path.read_bytes() == desired[path] and
              attributes(path) == xattrs[path], 'Post-update mismatch')
    check(json.loads(CFG.read_bytes(), object_pairs_hook=unique_object) == expected,
          'Protected configuration mismatch')
    subprocess.run([
        '/usr/sbin/runuser', '-u', 'eventtab', '--',
        str(APP / 'venv/bin/python'), '-B', '-m', 'pip', 'check'
    ], check=True)
except BaseException:
    for path in reversed(installed):
        inspect(path, (metadata[path].st_uid, metadata[path].st_gid), 0o644)
        current = path.read_bytes()
        check(current in (original[path], desired[path]) and
              attributes(path) == xattrs[path],
              'Recovery blocked by drift; retain journal: ' + str(journal))
        if current != original[path]:
            atomic_replace(path, original[path])
    print('STOP: original requirements/configuration restored; retain journal.')
    raise

print('PASS: requirements SHA256:', sha(REQ.read_bytes()))
print('PASS: only approved_requirements_sha256 changed; metadata preserved; pip check passed.')
print('No service operations, migrations, publication or deployment performed.')
