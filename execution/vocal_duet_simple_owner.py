#!/usr/bin/python3 -I
"""Root-only fixed host observation/owner approval. No Django, database, or keys.

Install from a reviewed root-private release as the fixed path below. The owner
attests actual development use; this is not a second Faculty identity/signature.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import uuid

INSTALL = Path('/usr/local/libexec/eventtab-vocal-duet-owner.py')
WORK = Path('/home/eventtab/vocal-duet-development.mufqRL')
DIRECTORY = Path('/etc/eventtab/development-reset')
SEAL = Path('/etc/eventtab/official-production.json')
CONFIRM = 'RESET ONLY VOCAL DUET TEST SCORES 175-190'
EVIDENCE_RULES = Path('/usr/local/libexec/eventtab-vocal-duet-evidence.py')


def evidence_rules():
    # Isolated Python does not import application-writable modules. Execute only
    # this fixed root-controlled copy of the standard-library-only shared rules.
    namespace = {}
    exec(compile(secure_read(EVIDENCE_RULES, True), str(EVIDENCE_RULES), 'exec'), namespace)
    return namespace


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def secure_read(path, trusted=False):
    """Walk with openat/O_NOFOLLOW; never dereference operator-owned symlinks."""
    if not path.is_absolute():
        raise ValueError('Absolute fixed paths are required.')
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            final = index == len(path.parts) - 2
            flags = os.O_RDONLY | os.O_NOFOLLOW | (0 if final else os.O_DIRECTORY)
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            if trusted and (info.st_uid != 0 or info.st_mode & 0o022):
                raise ValueError('Host/helper paths must be root-owned and not group/other writable.')
            if final and (not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024):
                raise ValueError('Evidence must be a small regular file.')
        with os.fdopen(os.dup(descriptor), 'rb') as source:
            return source.read(2 * 1024 * 1024 + 1)
    finally:
        os.close(descriptor)


def host():
    # Direct sysfs path, not /sys/class/dmi/id (a symlink).
    return {'host_uuid': str(uuid.UUID(secure_read(Path('/sys/devices/virtual/dmi/id/product_uuid'), True).decode().strip())),
        'host_machine_id': secure_read(Path('/etc/machine-id'), True).decode().strip().lower(),
        'host_boot_id': str(uuid.UUID(secure_read(Path('/proc/sys/kernel/random/boot_id'), True).decode().strip()))}


def publish(path, record):
    secure_read(INSTALL, True)
    if path.parent != DIRECTORY and path != SEAL:
        raise ValueError('Invalid publication path.')
    # Refuse redirected or weak parent directories; do not repair permissions.
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Install root-owned /etc/eventtab[/development-reset] with mode 0755 first.')
    descriptor, temporary = tempfile.mkstemp(prefix='.owner-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump(record, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), 0o644)
        os.replace(temporary, path)  # Replaces a final symlink without following it.
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def utc_timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != timedelta(0):
        raise ValueError('UTC timestamp required.')
    return parsed


def current_approval():
    try:
        raw = secure_read(DIRECTORY / 'simple-approval.json', True)
    except FileNotFoundError:
        return None
    approval = json.loads(raw)
    if not isinstance(approval, dict) or approval.get('purpose') != 'eventtab-simple-owner-vocal-duet-test-approval-v1':
        raise ValueError('Existing approval has an unexpected purpose; stop for review.')
    sha = hashlib.sha256(raw).hexdigest()
    marker = DIRECTORY / ('owner-rejected-' + sha + '.json')
    rejection = None
    try:
        rejection_raw = secure_read(marker, True)
    except FileNotFoundError:
        pass
    else:
        rejection = json.loads(rejection_raw)
        if (rejection.get('purpose') != 'eventtab-owner-approval-rejection-v1' or
                rejection.get('approval_sha256') != sha or
                base64.b64decode(rejection.get('previous_approval_base64', ''), validate=True) != raw):
            raise ValueError('Rejection audit does not match the exact previous approval; stop for review.')
    return {'raw': raw, 'approval': approval, 'sha256': sha, 'rejection': rejection,
        'rejection_sha256': hashlib.sha256(rejection_raw).hexdigest() if rejection else None}


def approval_status(previous):
    if previous is None:
        return {'state': 'absent', 'requires_rejection': False}
    error = evidence_rules()['approval_evidence_error'](previous['approval'])
    return {'state': 'rejected' if previous['rejection'] else ('invalid-evidence' if error else 'recorded'),
        'sha256': previous['sha256'], 'evidence_error': error,
        'checkpoint_sha256': previous['approval'].get('checkpoint_sha256'),
        'owner_evidence_reference': previous['approval'].get('owner_evidence_reference'),
        'evidence_version': previous['approval'].get('evidence_version'),
        'development_use_confirmed': previous['approval'].get('development_use_confirmed'),
        'supersedes_approval_sha256': previous['approval'].get('supersedes_approval_sha256'),
        'rejection_record_sha256': previous['approval'].get('rejection_record_sha256'),
        'requires_rejection': previous['rejection'] is None}


def reject_approval(now):
    previous = current_approval()
    if previous is None:
        raise ValueError('There is no owner approval to reject.')
    if previous['rejection']:
        print('Already rejected: ' + previous['sha256'] + '. Original approval and audit retained.')
        return
    phrase = 'REJECT VOCAL DUET OWNER APPROVAL ' + previous['sha256']
    print('Rejecting approval SHA256 ' + previous['sha256'] + '; checkpoint ' + str(previous['approval'].get('checkpoint_sha256')))
    reason = input('Actual reason for rejection (for example, confirmation entered as evidence): ').strip()
    if len(reason) < 10 or input('Type ' + phrase + ': ').strip() != phrase:
        raise ValueError('Rejection declined; approval file unchanged.')
    # Never replace an audit file. Root-owned directory plus exclusive creation
    # and the process lock prevent silent replacement/concurrent helper writes.
    record = {'purpose': 'eventtab-owner-approval-rejection-v1', 'approval_sha256': previous['sha256'],
        'checkpoint_sha256': previous['approval'].get('checkpoint_sha256'),
        'previous_approval_base64': base64.b64encode(previous['raw']).decode('ascii'),
        'reason': reason, 'rejected_at': datetime.now(timezone.utc).isoformat(), 'operator_uid': os.geteuid()}
    if current_approval()['sha256'] != previous['sha256']:
        raise ValueError('Approval changed during rejection; inspect again.')
    path = DIRECTORY / ('owner-rejected-' + previous['sha256'] + '.json')
    # Validate parent trust using an existing installed file before opening.
    secure_read(INSTALL, True)
    for parent in (DIRECTORY, *DIRECTORY.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Rejection audit directory must remain root-controlled.')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, 'w') as output:
        json.dump(record, output, indent=2)
        output.flush(); os.fsync(output.fileno())
    directory_fd = os.open(DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    print('Rejected and preserved: ' + previous['sha256'] + '. No checkpoint or database rows changed.')


@contextmanager
def workflow_lock():
    import fcntl
    secure_read(INSTALL, True)
    for parent in (DIRECTORY, *DIRECTORY.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Owner workflow directory must remain root-controlled.')
    descriptor = os.open(DIRECTORY / 'owner-workflow.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ValueError('Owner workflow lock is not a private root-owned regular file.')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def checkpoint_errors(checkpoint, name, facts, now):
    """Report each failing predicate without changing any bound evidence."""
    if not isinstance(checkpoint, dict):
        return ['checkpoint: expected an object']
    target = checkpoint.get('target')
    backup = checkpoint.get('backup')
    if not isinstance(target, dict) or not isinstance(backup, dict):
        return ['checkpoint.target/backup: expected objects']
    errors = []
    expected = {'purpose': 'eventtab-simple-vocal-duet-checkpoint-v1', 'session': name, 'actor_id': 69,
        'score_ids': list(range(175, 191)), 'confirmed': CONFIRM, 'archive_restore_confirmed': True,
        'criteria': {'93': 1197, '96': 1198, '97': 1199, '98': 1200},
        'pairs': {'14': [372, [1, 9], 'BFPT'], '15': [373, [4, 10], 'BTLEd']}}
    for field, value in expected.items():
        if checkpoint.get(field) != value or (field == 'archive_restore_confirmed' and checkpoint.get(field) is not True):
            errors.append('checkpoint.' + field + ': does not match the approved scope/confirmation')
    for field, value in facts.items():
        if target.get(field) != value:
            errors.append('target.' + field + ': differs from actual root-read host identity')
    for field, value in {'database': 'eventtab_db', 'database_role': 'eventtab_dbuser', 'server_port': 5433}.items():
        if target.get(field) != value:
            errors.append('target.' + field + ': wrong fixed development database target')
    # PostgreSQL inet::text includes the /32 host mask. Accept these two exact
    # representations only; keep the original target bytes/digest unchanged.
    if target.get('server_address') not in ('127.0.0.1', '127.0.0.1/32'):
        errors.append('target.server_address: expected 127.0.0.1 or PostgreSQL 127.0.0.1/32')
    if target.get('in_recovery') is not False:
        errors.append('target.in_recovery: writable primary required')
    if type(target.get('database_oid')) is not int or target['database_oid'] <= 0:
        errors.append('target.database_oid: positive integer required')
    if not re.fullmatch(r'[0-9]+', str(target.get('cluster_system_identifier', ''))):
        errors.append('target.cluster_system_identifier: invalid cluster identity')
    try:
        if not 180000 <= int(target.get('server_version_num', 0)) < 190000:
            raise ValueError
    except (ValueError, TypeError):
        errors.append('target.server_version_num: PostgreSQL 18 required')
    if backup.get('isolated_restore_database') != 'eventtab_vd_dev_restore':
        errors.append('backup.isolated_restore_database: wrong isolated restore database')
    for field in ('backup_sha256', 'relevant_rows_sha256', 'scoped_rows_sha256'):
        if not isinstance(backup.get(field), str) or not re.fullmatch('[0-9a-f]{64}', backup[field]):
            errors.append('backup.' + field + ': valid SHA256 required')
    try:
        if not now - timedelta(hours=2) <= utc_timestamp(checkpoint.get('verified_at')) <= now:
            raise ValueError
    except (ValueError, TypeError):
        errors.append('checkpoint.verified_at: must be UTC, not in the future, and at most two hours old; rerun preview/verify')
    review = checkpoint.get('review')
    if not isinstance(review, dict) or not isinstance(review.get('roster_evidence_reference'), str) or not review['roster_evidence_reference'].strip():
        errors.append('review.roster_evidence_reference: actual roster evidence is required')
    if 'root_observation_sha256' in target:
        try:
            raw = secure_read(DIRECTORY / 'simple-host.json', True)
            observation = json.loads(raw)
            if hashlib.sha256(raw).hexdigest() != target['root_observation_sha256']:
                errors.append('target.root_observation_sha256: observe changed since verification; rerun preview/verify, never edit the checkpoint')
            if (observation.get('purpose') != 'eventtab-simple-root-host-v1' or
                    any(observation.get(key) != value for key, value in facts.items())):
                errors.append('root_observation.identity: purpose/actual host identity mismatch')
            issued, expires = utc_timestamp(observation['issued_at']), utc_timestamp(observation['expires_at'])
            if not (issued <= now < expires and timedelta(0) < expires - issued <= timedelta(hours=2)):
                errors.append('root_observation.freshness: expired/future/invalid lifetime; observe then rerun preview/verify')
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            errors.append('root_observation.trust: current root-owned observation is missing, unreadable or malformed')
    return errors


def inspect_checkpoint(facts, now):
    pointer = json.loads(secure_read(WORK / 'simple-current.json'))
    name = pointer.get('session', '') if isinstance(pointer, dict) else ''
    if not isinstance(name, str) or not re.fullmatch(r'simple-[0-9a-f]{16}', name):
        raise ValueError('Invalid session pointer; rerun preview as eventtab.')
    checkpoint = json.loads(secure_read(WORK / name / 'approval-request.json'))
    errors = checkpoint_errors(checkpoint, name, facts, now)
    return checkpoint, {'read_only': True, 'session': name, 'checkpoint_sha256': digest(checkpoint),
        'verified_at': checkpoint.get('verified_at') if isinstance(checkpoint, dict) else None,
        'target': checkpoint.get('target') if isinstance(checkpoint, dict) else None,
        'backup_sha256': (checkpoint.get('backup') or {}).get('backup_sha256') if isinstance(checkpoint, dict) and isinstance(checkpoint.get('backup'), dict) else None,
        'can_approve': not errors, 'failed_checks': errors}


def approve(facts, now):
    checkpoint, report = inspect_checkpoint(facts, now)
    if report['failed_checks']:
        raise ValueError('No approval issued for ' + report['session'] + ':\n- ' + '\n- '.join(report['failed_checks']))
    previous = current_approval()
    if previous and not previous['rejection']:
        raise ValueError('An existing approval cannot be silently overwritten. Run the root helper reject action first; it preserves an immutable audit.')
    target, backup = checkpoint['target'], checkpoint['backup']
    print(json.dumps({'target': target, 'backup': backup, 'score_ids': checkpoint['score_ids'],
        'criteria': checkpoint['criteria'], 'pairs': checkpoint['pairs'],
        'roster_reference': checkpoint['review'].get('roster_evidence_reference')}, indent=2))
    print('As the actual infrastructure owner, independently verify this host/cluster is exclusively development/testing,')
    print('with no official/shared school use. Verify the roster and that this exact archive produced the isolated restore.')
    print('Use actual ownership/development-use evidence; this is NOT a second Faculty approval.')
    reference = input('Actual ownership + exclusive-development-use verification reference: ').strip()
    rules = evidence_rules()
    error = rules['evidence_error'](reference)
    if error:
        raise ValueError(error + ' No approval issued.')
    if input('After verifying ALL current scores and published results are tests, type ' + rules['DEVELOPMENT_CONFIRMATION'] + ': ').strip() != rules['DEVELOPMENT_CONFIRMATION']:
        raise ValueError('Current development-only use was not confirmed; no approval issued.')
    if input('Type ' + CONFIRM + ': ').strip() != CONFIRM:
        raise ValueError('Owner approval declined; all database rows remain unchanged.')
    # Confirmation may take time. Do not approve expired/replaced evidence or a
    # session pointer changed while the owner reviewed the displayed checkpoint.
    now = datetime.now(timezone.utc)
    _, current_report = inspect_checkpoint(facts, now)
    if current_report['checkpoint_sha256'] != report['checkpoint_sha256']:
        raise ValueError('Checkpoint/session changed during owner confirmation; inspect and verify the intended session again.')
    if current_report['failed_checks']:
        raise ValueError('No approval issued after confirmation:\n- ' + '\n- '.join(current_report['failed_checks']))
    latest_previous = current_approval()
    if (latest_previous or {}).get('sha256') != (previous or {}).get('sha256'):
        raise ValueError('Previous approval changed during confirmation; inspect again.')
    publish(DIRECTORY / 'simple-approval.json', {'purpose': 'eventtab-simple-owner-vocal-duet-test-approval-v1',
        'checkpoint_sha256': digest(checkpoint), 'target': target, 'backup_sha256': backup['backup_sha256'],
        'confirmed': CONFIRM, 'owner_evidence_reference': reference, 'evidence_version': 2, 'development_use_confirmed': True,
        'supersedes_approval_sha256': previous['sha256'] if previous else None,
        'rejection_record_sha256': previous['rejection_sha256'] if previous else None, 'issued_at': now.isoformat(),
        'expires_at': (now + timedelta(hours=2)).isoformat()})


def main():
    if os.geteuid() != 0 or Path(__file__) != INSTALL or len(sys.argv) != 2:
        raise ValueError('Use the installed root-owned helper through sudo: observe | inspect | reject | approve | retire. Never run Django as root.')
    secure_read(INSTALL, True)
    action = sys.argv[1]
    if action not in ('observe', 'inspect', 'reject', 'approve', 'retire'):
        raise ValueError('Only observe, inspect, reject, approve or retire is accepted; no target/scope overrides exist.')
    if os.path.lexists(SEAL):
        raise ValueError('Official production seal exists. Development maintenance is retired.')
    if action in ('reject', 'approve', 'retire') and not sys.stdin.isatty():
        raise ValueError('Owner approval/retirement requires an interactive terminal, not piped confirmation.')
    if action == 'inspect':
        dispatch(action)
    else:
        with workflow_lock():
            dispatch(action)


def dispatch(action):
    now = datetime.now(timezone.utc)
    if action == 'reject':
        reject_approval(now)
    elif action == 'retire':
        if input('Type RETIRE DEVELOPMENT MAINTENANCE FOR OFFICIAL SCHOOL USE: ') != 'RETIRE DEVELOPMENT MAINTENANCE FOR OFFICIAL SCHOOL USE':
            raise ValueError('Retirement declined.')
        publish(SEAL, {'purpose': 'official-school-production', 'retired_at': now.isoformat()})
    else:
        facts = host()
        if action == 'observe':
            publish(DIRECTORY / 'simple-host.json', {**facts, 'purpose': 'eventtab-simple-root-host-v1',
                'issued_at': now.isoformat(), 'expires_at': (now + timedelta(hours=2)).isoformat()})
        elif action == 'inspect':
            _, report = inspect_checkpoint(facts, now)
            report['current_approval'] = approval_status(current_approval())
            print(json.dumps(report, indent=2))
            if not report['can_approve']:
                raise ValueError('Inspection found failed checks; no approval issued.')
        else:
            approve(facts, now)
    print('Completed ' + action + '. No PostgreSQL connection or Django command was run by this helper.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, EOFError) as error:
        print('Refused: ' + str(error), file=sys.stderr)
        sys.exit(1)
