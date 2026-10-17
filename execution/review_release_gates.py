"""Read-only production metadata review and local unstaged-release proposal."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

BASELINE = '79f45446fde9db78d922c0085fef812b6aff5934'
TARGET = 'eventtab@103.125.217.125'
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15']

REMOTE = r'''
from pathlib import Path
import datetime, hashlib, importlib.metadata, json, os, stat, subprocess
root=Path('/srv/eventtab/Final-EventTab-System-WEB/staticfiles')
def metadata(path):
    value=path.lstat()
    return {'path':str(path),'uid':value.st_uid,'gid':value.st_gid,
        'mode':oct(stat.S_IMODE(value.st_mode)),'device':value.st_dev,'inode':value.st_ino,'ctime_ns':value.st_ctime_ns,
        'type':'directory' if stat.S_ISDIR(value.st_mode) else 'symlink' if stat.S_ISLNK(value.st_mode) else 'file' if stat.S_ISREG(value.st_mode) else 'other'}
def acl(path):
    result=subprocess.run(['getfacl','-cpn','--',str(path)],capture_output=True,text=True,timeout=10)
    if result.returncode: raise RuntimeError('ACL could not be verified: '+str(path))
    names=os.listxattr(path,follow_symlinks=False)
    return {'getfacl':result.stdout,'posix_acl_xattrs':{name:os.getxattr(path,name,follow_symlinks=False).hex()
        for name in names if name in ('system.posix_acl_access','system.posix_acl_default')}}
ancestors=[]
for path in reversed([root,*root.parents]):
    value=metadata(path)
    if value['type']!='directory': raise RuntimeError('Unsafe static ancestor')
    ancestors.append({**value,**acl(path)})
directories=[]
files=0
for current,nested,names in os.walk(root,followlinks=False):
    path=Path(current)
    before=metadata(path)
    if before['type']!='directory' or before['uid']!=1000 or before['gid']!=1000 or before['mode']!='0o775':
        raise RuntimeError('Static directory differs from reviewed permission baseline: '+str(path))
    access=acl(path)
    if access['posix_acl_xattrs'] or access['getfacl'].strip()!='user::rwx\ngroup::rwx\nother::r-x':
        raise RuntimeError('Extended or unexpected static ACL needs separate review: '+str(path))
    for name in nested:
        if metadata(path/name)['type']!='directory': raise RuntimeError('Static symlink/non-directory rejected')
    for name in names:
        value=metadata(path/name)
        if value['type']!='file' or value['mode']!='0o644': raise RuntimeError('Unexpected static file type/mode')
        files+=1
    if metadata(path)!=before: raise RuntimeError('Directory metadata changed during inspection')
    directories.append({'observed':{**before,**access},'proposed':{'uid':1000,'gid':1000,'mode':'0o755',
        'type':'directory','getfacl':'user::rwx\ngroup::r-x\nother::r-x\n'}})
directories.sort(key=lambda item:item['observed']['path'])
if len(directories)!=35: raise RuntimeError('Static directory inventory is no longer exactly 35')
backups=[]
for name in ['/home/eventtab/eventtab-baseline-backup/eventtab_db.dump',
             '/home/eventtab/eventtab-baseline-backup/eventtab-source.tar.gz']:
    path=Path(name)
    value=metadata(path)
    value['readable']=os.access(path,os.R_OK)
    value['size']=path.stat().st_size
    if value['type']!='file': raise RuntimeError('Backup is not a regular file')
    if value['readable']:
        value['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        if name.endswith('.dump'):
            result=subprocess.run(['/usr/lib/postgresql/18/bin/pg_restore','--file=-',name],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,timeout=30)
            value['full_archive_read_exit']=result.returncode
            if result.returncode: value['error']=result.stderr[:300]
    backups.append(value)
receipt_candidates=[]
receipt_roots=['/home/eventtab/eventtab-baseline-backup','/home/eventtab/eventtab-backups','/srv/eventtab-deploy/logs']
for name in receipt_roots:
    path=Path(name)
    try:
        for item in path.rglob('*'):
            if item.is_file() and not item.is_symlink() and any(word in item.name.lower() for word in ['receipt','checksum','restore','retention','offhost','off-host']):
                receipt_candidates.append(metadata(item))
    except PermissionError: pass
application=Path('/srv/eventtab/Final-EventTab-System-WEB')
config_path=Path('/etc/eventtab-deploy.json')
config=json.loads(config_path.read_text())
try: openpyxl_version=importlib.metadata.version('openpyxl')
except importlib.metadata.PackageNotFoundError: openpyxl_version=None
protected_config={'metadata':metadata(config_path),'machine_id_matches':config.get('machine_id')==Path('/etc/machine-id').read_text().strip(),
    'approved_requirements_sha256':config.get('approved_requirements_sha256'),
    'production_requirements_sha256':hashlib.sha256((application/'requirements.txt').read_bytes()).hexdigest(),
    'production_openpyxl_version':openpyxl_version,
    'production_helper_hashes':{name:hashlib.sha256((Path('/usr/local/lib/eventtab-deploy')/name).read_bytes()).hexdigest()
        for name in ['deploy_server.py','deploy_release.py']}}
print(json.dumps({'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'status':'READ-ONLY MANIFEST; NO PERMISSION CHANGES AUTHORIZED OR PERFORMED',
    'target':str(root),'directory_count':len(directories),'unchanged_regular_0644_files':files,
    'ancestors':ancestors,'directories':directories,'backups':backups,'protected_configuration':protected_config,
    'receipt_search_roots':receipt_roots,'receipt_candidates':receipt_candidates,
    'receipt_search_limit':'No off-host provider credentials or root access; absence here is not proof that no receipt exists elsewhere.'},indent=2))
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git(root, *args, raw=None):
    return subprocess.check_output(['git', '-C', str(root), *args], input=raw)


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def collect(output):
    result = subprocess.run([*SSH, TARGET, '/srv/eventtab/Final-EventTab-System-WEB/venv/bin/python -B -'], input=REMOTE,
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    data = json.loads(result.stdout)
    canonical = json.dumps(data['directories'], sort_keys=True, separators=(',', ':')).encode()
    data['exact_permission_inventory_sha256'] = sha(canonical)
    write(output / 'production-read-only-manifest.json', data)
    return data


def proposal(root, output):
    import deploy_release
    if git(root, 'rev-parse', 'HEAD').decode().strip() != BASELINE:
        raise ValueError('Baseline commit changed; renew review.')
    if git(root, 'diff', '--cached', '--name-only').strip():
        raise ValueError('Unexpected staged content. This tool never changes the index.')
    tracked = git(root, 'diff', '--name-only', '-z').decode().split('\0')
    untracked = git(root, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0')
    entries = []
    for name in sorted(set(filter(None, tracked + untracked))):
        path = root / name
        if not path.is_file() or any(parent.is_symlink() for parent in [path, *path.parents]):
            raise ValueError('Unexpected missing/symlink proposal path: ' + name)
        raw = path.read_bytes()
        normalized = raw.replace(b'\r\n', b'\n')
        oid = git(root, 'hash-object', '--path=' + name, '--stdin', raw=raw).decode().strip()
        entries.append({'path': name, 'working_sha256': sha(raw), 'proposed_lf_sha256': sha(normalized),
                        'filtered_git_blob_oid': oid, 'runtime_member': deploy_release.allowed(name)})
    files = [name for name in git(root, 'ls-files', '-z').decode().split('\0') if name and deploy_release.allowed(name)]
    runtime = []
    for name in sorted(files):
        raw = (root / name).read_bytes()
        if Path(name).suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.ico', '.woff', '.woff2', '.ttf'}:
            raw = raw.replace(b'\r\n', b'\n')
        runtime.append({'path': name, 'sha256': sha(raw), 'size': len(raw)})
    baseline = json.loads((root / '.tmp/reconciliation/local-release-candidate/prospective-release-manifest.json').read_text())
    previous = {entry['path']: entry['proposed_sha256'] for entry in baseline['entries']}
    if len(runtime) != 261 or set(previous) != set(files):
        raise ValueError('Application archive membership changed; independent review required.')
    changed = [entry['path'] for entry in runtime if previous[entry['path']] != entry['sha256']]
    if changed != ['requirements.txt']:
        raise ValueError('Unexpected runtime changes: ' + repr(changed))
    originals = ['events/migrations/0019_align_team_schema.py', 'events/migrations/0020_team_delete_set_null_on_legacy_match.py']
    if any(sha((root / name).read_bytes().replace(b'\r\n', b'\n')) != sha(git(root, 'show', BASELINE + ':' + name)) for name in originals):
        raise ValueError('Historical migration changed.')
    value = {'status': 'UNSTAGED REVIEW PROPOSAL; NOT A COMMITTED RELEASE OR DEPLOYABLE ARCHIVE',
             'baseline_commit': BASELINE, 'entries': entries, 'path_count': len(entries),
             'application_members': 261, 'changed_runtime_paths': changed,
             'original_migrations_0019_0020_unchanged': True,
             'real_packaging_and_genuine_helper_rehearsal': 'BLOCKED until separately approved clean commit'}
    write(output / 'commit-proposal.json', value)
    write(output / 'prospective-release-manifest.json', {'status': value['status'], 'files': runtime})
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--collect-production-read-only', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('.tmp/reconciliation/final-readiness-candidate'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    args.output.mkdir(parents=True, exist_ok=True)
    if args.collect_production_read_only:
        result = collect(args.output)
        print(json.dumps({'permission_directories': result['directory_count'],
                          'inventory_sha256': result['exact_permission_inventory_sha256'],
                          'receipt_candidates': len(result['receipt_candidates'])}))
    result = proposal(root, args.output)
    print(json.dumps({'proposal_paths': result['path_count'], 'runtime_members': 261,
                      'changed_runtime_paths': result['changed_runtime_paths'], 'staged': False}))


if __name__ == '__main__':
    main()
