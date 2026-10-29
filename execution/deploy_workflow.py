"""Local-only activation and ordinary-update classification; never contacts a server."""
import argparse
import json
from pathlib import Path
import re
import subprocess

from deploy_release import allowed


def exceptional(name):
    path = Path(name)
    return (name in {'requirements.txt', 'core/settings.py', 'deploy.ps1'}
            or name.startswith('execution/') or '/migrations/' in name
            or path.name == 'models.py' or path.name.startswith('.env'))


def classify(before, after):
    reasons = []
    changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    protected = [name for name in changed if exceptional(name)]
    if protected:
        reasons.append('Separate review required for protected paths: ' + ', '.join(protected))
    old_members = {name for name in before if allowed(name)}
    new_members = {name for name in after if allowed(name)}
    if old_members != new_members:
        reasons.append('Application archive membership changed; review additions/removals and public allowlist.')
    return reasons


def git(root, *arguments):
    return subprocess.check_output(['git', '-C', str(root), *arguments]).decode('utf-8').strip()


def tree(root, commit):
    result = {}
    for entry in git(root, 'ls-tree', '-r', '-z', commit).split('\0'):
        if entry:
            metadata, name = entry.split('\t', 1)
            result[name] = metadata
    return result


def check(root, recovery=False):
    receipt = root / '.tmp/deploy/activation.json'
    if not receipt.is_file() or receipt.is_symlink():
        return {'ready': False, 'reasons': ['First-time activation is pending. See EVENTTAB_DEPLOYMENT_GUIDE.md.']}
    value = json.loads(receipt.read_text(encoding='utf-8'))
    required = {'format', 'activated_commit', 'production_write_approval_reference',
                'backup_verification_reference', 'publication_integration_reference'}
    if not isinstance(value, dict) or set(value) != required or value['format'] != 1:
        raise ValueError('Invalid local activation receipt.')
    baseline = value['activated_commit']
    if not isinstance(baseline, str) or not re.fullmatch('[0-9a-f]{40}', baseline):
        raise ValueError('Invalid activated commit.')
    for name in required - {'format', 'activated_commit'}:
        if not isinstance(value[name], str) or not value[name].strip() or value[name].strip().upper() in {'PENDING', 'TODO'}:
            raise ValueError('Activation evidence reference is missing: ' + name)
    if recovery:
        return {'ready': True, 'activated_commit': baseline, 'recovery': True, 'reasons': []}
    subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', baseline, 'HEAD'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if git(root, 'status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('Commit reviewed changes first; working tree must be clean.')
    reasons = classify(tree(root, baseline), tree(root, 'HEAD'))
    return {'ready': not reasons, 'activated_commit': baseline, 'commit': git(root, 'rev-parse', 'HEAD'), 'reasons': reasons}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--recovery', action='store_true')
    args = parser.parse_args()
    try:
        result = check(args.root.resolve(), args.recovery)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, 'Local deployment gate blocked: ' + str(error) + '\n')
