"""Fixed Vocal Duet development maintenance; no generic reset or force policy."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.utils import load_backend
from django.utils import timezone

from . import vocal_duet_development as dev
from . import models
from .vocal_duet_owner_evidence import approval_evidence_error
from .vocal_duet_reset import (ResetError, _authorized, _configuration_plan, _digest, _execute_reset,
                               _lock_reset, _preimage)


WORK = Path('/home/eventtab/vocal-duet-development.mufqRL')
OWNER_APPROVAL = Path('/etc/eventtab/development-reset/simple-approval.json')
HOST_OBSERVATION = Path('/etc/eventtab/development-reset/simple-host.json')
CRITERIA = {93: 1197, 96: 1198, 97: 1199, 98: 1200}
PAIRS = {14: (372, [1, 9], 'BFPT'), 15: (373, [4, 10], 'BTLEd')}
CONFIRM = 'RESET ONLY VOCAL DUET TEST SCORES 175-190'


def read_json(path):
    if path.is_symlink():
        raise ResetError(f'Refusing symlink: {path.name}. Keep maintenance evidence in the private workspace.')
    return json.loads(path.read_text())


def write_new(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'w') as output:
        json.dump(value, output, indent=2, default=str)
        output.flush(); os.fsync(output.fileno())


def actor():
    user = get_user_model().objects.filter(pk=69).first()
    if not _authorized(user, models.Event.objects.get(pk=79)):
        raise ResetError('Active assigned Faculty/Admin account 69 is required.')
    return user


def session_path():
    pointer = read_json(WORK / 'simple-current.json')
    name = pointer.get('session', '')
    if not name.startswith('simple-') or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name):
        raise ResetError('Invalid session pointer; rerun preview.')
    path = WORK / name
    validate_private_directory(path)
    return path


def validate_private_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or (hasattr(os, 'geteuid') and info.st_uid != os.geteuid()):
        raise ResetError('The session must be a private directory owned by the operator running Django.')


def host_and_database():
    dev.ensure_development_enabled()
    try:
        return dev.target_identity()
    except ResetError:
        try:
            dev._root_controlled(HOST_OBSERVATION)
            raw = HOST_OBSERVATION.read_bytes()
            record = json.loads(raw)
            from .vocal_duet_host_identity import MACHINE_ID, BOOT_ID, _utc, _uuid
            for path in (MACHINE_ID, BOOT_ID): dev._root_controlled(path)
            now = timezone.now()
            issued, expires = _utc(record['issued_at']), _utc(record['expires_at'])
            if (record.get('purpose') != 'eventtab-simple-root-host-v1' or
                    not issued <= now < expires or expires - issued > timedelta(hours=2) or
                    record['host_machine_id'] != MACHINE_ID.read_text().strip().lower() or
                    record['host_boot_id'] != _uuid(BOOT_ID.read_text().strip().lower())):
                raise ValueError
            host = {key: record[key] for key in ('host_uuid', 'host_machine_id', 'host_boot_id')}
            _uuid(host['host_uuid'])
            return {**host, 'root_observation_sha256': hashlib.sha256(raw).hexdigest(), **dev.database_identity()}
        except (OSError, ValueError, KeyError, TypeError, ResetError):
            raise ResetError('Host proof is missing/expired. In stage 1, the server owner must run: sudo /usr/local/libexec/eventtab-vocal-duet-owner.py observe ; then rerun preview. Do not run Django as root.') from None


def proposed_review(reference=None):
    source = WORK / 'review.json'
    if not source.exists(): source = WORK / 'review\\.json'
    original = read_json(source)
    if not isinstance(original, dict):
        raise ResetError('The existing review must contain a JSON object; it was not changed.')
    review = deepcopy(original)
    evidence = reference or review.get('roster_evidence_reference') or review.get('evidence_reference')
    review.update(verified_by=69, all_16_scores_are_test_data=True,
        evidence_reference=review.get('evidence_reference') or evidence, roster_evidence_reference=evidence)
    review['criteria'] = [{'official_id': key, 'mobile_id': value} for key, value in CRITERIA.items()]
    review['pairs'] = []
    for entry_id, (candidate, members, code) in PAIRS.items():
        entry = models.CriteriaEventEntry.objects.select_related('department').get(pk=entry_id, event_id=79)
        actual_members = list(entry.member_links.order_by('position').values_list('individual_id', flat=True))
        if not entry.department or entry.department.code != code or actual_members != members:
            raise ResetError(f'Canonical pair {entry_id} differs from the confirmed members/department ({members}, {code}). Faculty must resolve the roster separately; no identities were inferred.')
        if any(value != entry.department_id for value in entry.member_links.values_list('individual__department_id', flat=True)):
            raise ResetError(f'Pair {entry_id} member departments differ from its confirmed {code} roster. Faculty must verify/correct the official roster separately; no departments were changed.')
        review['pairs'].append({'entry_id': entry_id, 'mobile_id': candidate, 'member_ids': members,
            'identity_evidence': evidence, 'snapshot_discrepancy_explanation': evidence,
            'roster_verified_by': 69, 'verified_department_id': entry.department_id,
            'verified_department_name': entry.department.name,
            'verified_member_department_ids': list(entry.member_links.order_by('position').values_list('individual__department_id', flat=True))})
    missing = sorted(set(models.JudgeScore.objects.filter(pk__range=(175, 190)).values_list('judge_id', flat=True)) -
        set(get_user_model().objects.values_list('pk', flat=True)))
    review['development_test_data_review'] = {'verified_by': 69, 'confirmed_test_data': True,
        'missing_judge_ids': missing, 'orphan_score_ids': list(models.JudgeScore.objects.filter(pk__range=(175, 190), judge_id__in=missing).order_by('pk').values_list('pk', flat=True)),
        'evidence_reference': evidence}
    # Preserve a complete existing review byte-for-byte semantically so that a
    # still-valid signed permit bound to its digest can be reused.
    if not reference:
        previous = _configuration_plan(actor(), original)
        dev._review_issues(previous, actor(), original)
        if (not previous['blocked_reasons'] and
                {row['official_id']: row['mobile_id'] for row in previous['criteria']} == CRITERIA and
                {row['entry_id']: (row['mobile_id'], row['member_ids']) for row in previous['pairs']} ==
                {key: (value[0], value[1]) for key, value in PAIRS.items()}):
            review = original
    return review, source, original


def plan_for(review, target):
    plan = _configuration_plan(actor(), review)
    dev._review_issues(plan, actor(), review)
    if {row['official_id']: row['mobile_id'] for row in plan['criteria']} != CRITERIA:
        plan['blocked_reasons'].append('The four official/mobile criteria do not match the approved fixed bindings.')
    if {row['entry_id']: (row['mobile_id'], row['member_ids']) for row in plan['pairs']} != {key: (value[0], value[1]) for key, value in PAIRS.items()}:
        plan['blocked_reasons'].append('Pair bindings differ from the explicitly confirmed identities.')
    plan['database_target'] = target
    plan['protected_state_sha256'] = dev.protected_state()
    plan['can_apply'] = not plan['blocked_reasons']
    return plan


# Full rows include columns absent from Django, detailed official/mobile children,
# canonical entries/members/departments and event/assignment state. No counts-only check.
SCOPE_SQL = {
    'events_event': 'id=79', 'events_judgingevent': 'id=64',
    'events_criterion': 'event_id=64', 'events_candidate': 'event_id=64',
    'events_criterionsubcriterion': 'criterion_id IN (SELECT id FROM events_criterion WHERE event_id=64)',
    'events_judgescore': '(id BETWEEN 175 AND 190) OR candidate_id IN (SELECT id FROM events_candidate WHERE event_id=64) OR criterion_id IN (SELECT id FROM events_criterion WHERE event_id=64)',
    'events_judgesubcriterionscore': 'judge_score_id BETWEEN 175 AND 190 OR subcriterion_id IN (SELECT id FROM events_criterionsubcriterion WHERE criterion_id IN (SELECT id FROM events_criterion WHERE event_id=64))',
    'events_eventscoringcategory': 'event_id=79',
    'events_eventscoringcriterion': 'category_id IN (SELECT id FROM events_eventscoringcategory WHERE event_id=79)',
    'events_eventscoringsubcriterion': 'criterion_id IN (SELECT id FROM events_eventscoringcriterion WHERE category_id IN (SELECT id FROM events_eventscoringcategory WHERE event_id=79))',
    'events_criteriascoresubmission': 'event_id=79 OR category_id IN (SELECT id FROM events_eventscoringcategory WHERE event_id=79) OR criterion_id IN (SELECT id FROM events_eventscoringcriterion WHERE category_id IN (SELECT id FROM events_eventscoringcategory WHERE event_id=79))',
    'events_criteriasubcriterionscore': 'submission_id IN (SELECT id FROM events_criteriascoresubmission WHERE event_id=79) OR subcriterion_id IN (SELECT id FROM events_eventscoringsubcriterion WHERE criterion_id IN (SELECT id FROM events_eventscoringcriterion WHERE category_id IN (SELECT id FROM events_eventscoringcategory WHERE event_id=79)))',
    'events_criteriaevententry': 'event_id=79',
    'events_criteriaevententrymember': 'entry_id IN (SELECT id FROM events_criteriaevententry WHERE event_id=79)',
    'events_registrycandidate': 'id IN (SELECT individual_id FROM events_criteriaevententrymember WHERE entry_id IN (SELECT id FROM events_criteriaevententry WHERE event_id=79))',
    'events_department': 'id IN (SELECT department_id FROM events_criteriaevententry WHERE event_id=79) OR id IN (SELECT department_id FROM events_registrycandidate WHERE id IN (SELECT individual_id FROM events_criteriaevententrymember WHERE entry_id IN (SELECT id FROM events_criteriaevententry WHERE event_id=79)))',
    'events_event_assigned_judges': 'event_id=79', 'events_judgingevent_assigned_judges': 'judgingevent_id=64',
}


def relevant_rows(cursor):
    result = {}
    for table, where in SCOPE_SQL.items():
        cursor.execute(f'SELECT to_jsonb(r) FROM "{table}" r WHERE {where} ORDER BY id')
        result[table] = [json.loads(row) if isinstance(row, str) else row for (row,) in cursor.fetchall()]
    return result


def verify_existing_backup(plan):
    backup = WORK / 'database.dump'
    with backup.open('rb') as source:
        if source.read(5) != b'PGDMP': raise ResetError('Existing database.dump is not a PostgreSQL custom backup; nothing was overwritten.')
    digest = dev._file_hash(backup)
    with connection.cursor() as cursor: expected = relevant_rows(cursor)
    settings = deepcopy(connection.settings_dict); settings['NAME'] = 'eventtab_vd_dev_restore'
    if settings['NAME'] == plan['database_target']['database']: raise ResetError('Restore database must differ from live target.')
    restored = load_backend(settings['ENGINE']).DatabaseWrapper(settings, alias='simple_vocal_duet_restore')
    try:
        if restored.get_connection_params().get('dbname') != settings['NAME']: raise ResetError('Connection options override the restore database.')
        with restored.cursor() as cursor:
            cursor.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cursor.execute("SELECT set_config('TimeZone', %s, true)", [connection.timezone_name])
            cursor.execute('''SELECT current_database(), (SELECT oid FROM pg_database WHERE datname=current_database()),
                (SELECT system_identifier::text FROM pg_control_system()), inet_server_addr()::text, inet_server_port(), current_user''')
            name, oid, cluster, address, port, role = cursor.fetchone()
            target = plan['database_target']
            if (name != settings['NAME'] or oid == target['database_oid'] or
                    (cluster, address, port, role) != (target['cluster_system_identifier'], target['server_address'], target['server_port'], target['database_role'])):
                raise ResetError('Invalid isolated restore database/cluster/role identity.')
            actual = relevant_rows(cursor)
            cursor.execute('ROLLBACK')
        differing = [table for table in expected if _digest(expected[table]) != _digest(actual[table])]
        if differing: raise ResetError('Existing backup/restore differs in: ' + ', '.join(differing) + '. Preserve both; obtain a separately reviewed fresh backup/restore. No source rows changed.')
    finally: restored.close()
    return {'backup_sha256': digest, 'relevant_rows_sha256': _digest(expected),
        'scoped_rows_sha256': _digest(_preimage(plan)['tables']), 'isolated_restore_database': settings['NAME']}


def preview():
    dev.ensure_development_enabled()
    review, source, original = proposed_review()
    target = host_and_database()
    plan = plan_for(review, target)
    name = 'simple-' + secrets.token_hex(8)
    session = WORK / name; session.mkdir(mode=0o700)
    record = {'stage': 'preview', 'session': name, 'target': target, 'plan': plan,
        'source_review_sha256': _digest(original), 'source_review_path': str(source), 'review': review,
        'human_confirmation_required': True}
    write_new(session / 'preview.json', record)
    pointer = WORK / 'simple-current.json'
    temporary = WORK / ('.simple-current-' + secrets.token_hex(8))
    write_new(temporary, {'session': name}); os.replace(temporary, pointer)
    return record


def verify(reference, confirmation):
    session = session_path(); initial = read_json(session / 'preview.json')
    review, source, original = proposed_review(reference)
    if _digest(original) != initial['source_review_sha256']: raise ResetError('Original review changed since preview; rerun stage 1. It was not overwritten.')
    target = host_and_database()
    plan = plan_for(review, target)
    if not plan['can_apply']: raise ResetError('Review/setup blockers: ' + ' '.join(plan['blocked_reasons']))
    if target != initial['target']:
        changed = sorted(key for key in set(target) | set(initial['target']) if target.get(key) != initial['target'].get(key))
        raise ResetError('Target evidence changed since preview (' + ', '.join(changed) + '). Refreshing observe invalidates earlier evidence; rerun preview/verify. Do not edit the checkpoint.')
    if plan['state_sha256'] != initial['plan']['state_sha256'] or plan['protected_state_sha256'] != initial['plan']['protected_state_sha256']:
        raise ResetError('Database/host state changed since preview; rerun stage 1.')
    backup = verify_existing_backup(plan)
    if callable(confirmation):
        confirmation = confirmation(backup, review)
    if confirmation != CONFIRM: raise ResetError('Backup verified, but explicit confirmation was not supplied. No scores changed.')
    checkpoint = {'purpose': 'eventtab-simple-vocal-duet-checkpoint-v1', 'session': initial['session'],
        'actor_id': 69, 'target': target, 'review': review, 'state_sha256': plan['state_sha256'],
        'protected_state_sha256': plan['protected_state_sha256'], 'backup': backup,
        'confirmed': confirmation, 'score_ids': list(range(175, 191)), 'criteria': {str(key): value for key, value in CRITERIA.items()},
        'pairs': {str(key): [value[0], value[1], value[2]] for key, value in PAIRS.items()},
        'archive_restore_confirmed': True,
        'verified_at': timezone.now().isoformat()}
    write_new(session / 'approval-request.json', checkpoint)
    return checkpoint


def authorization(checkpoint):
    # Reuse valid existing signed authorization before requesting a new owner approval.
    try:
        return dev.load_authorization(actor(), checkpoint['review'], checkpoint['target'])
    except (ResetError, OSError, ValueError):
        pass
    try:
        dev._root_controlled(OWNER_APPROVAL)
        raw = OWNER_APPROVAL.read_bytes(); approval = json.loads(raw)
        owner_digest = hashlib.sha256(raw).hexdigest()
        if approval_evidence_error(approval):
            raise ValueError('Owner evidence is invalid; reject and request legitimate reapproval.')
        # A rejection is a root-controlled denial marker, never an edited
        # approval. Fail closed even if its audit content is unreadable/corrupt.
        if os.path.lexists(OWNER_APPROVAL.parent / ('owner-rejected-' + owner_digest + '.json')):
            raise ValueError('This exact owner approval was explicitly rejected.')
        from .vocal_duet_host_identity import _utc
        now = timezone.now(); issued = _utc(approval['issued_at']); expires = _utc(approval['expires_at'])
        if (approval.get('purpose') != 'eventtab-simple-owner-vocal-duet-test-approval-v1' or
                approval.get('checkpoint_sha256') != _digest(checkpoint) or approval.get('target') != checkpoint['target'] or
                approval.get('backup_sha256') != checkpoint['backup']['backup_sha256'] or
                approval.get('confirmed') != CONFIRM or not approval.get('owner_evidence_reference') or
                not issued <= now < expires or not timedelta(0) < expires - issued <= timedelta(hours=2)):
            raise ValueError
        digest = hashlib.sha256(raw).hexdigest()
        if models.AuditLog.objects.filter(metadata__development_authorization__owner_approval_sha256=digest).exists():
            raise ValueError
        return {'mode': 'server-owner-scoped-development-maintenance', 'owner_approval_sha256': digest, 'target': checkpoint['target']}
    except (OSError, ValueError, KeyError, TypeError, ResetError):
        raise ResetError('Owner authorization is missing, invalid, rejected or expired. Run the root helper inspect. If an approval already exists, use its audited reject action, then legitimate approve; stale evidence requires new preview/verify. Never edit approval files.') from None


def commit():
    dev.ensure_development_enabled()
    session = session_path(); checkpoint = read_json(session / 'approval-request.json')
    if (checkpoint.get('purpose') != 'eventtab-simple-vocal-duet-checkpoint-v1' or
            checkpoint.get('actor_id') != 69 or checkpoint.get('confirmed') != CONFIRM or
            checkpoint.get('archive_restore_confirmed') is not True or
            checkpoint.get('score_ids') != list(range(175, 191)) or
            checkpoint.get('criteria') != {str(key): value for key, value in CRITERIA.items()} or
            checkpoint.get('pairs') != {str(key): [value[0], value[1], value[2]] for key, value in PAIRS.items()}):
        raise ResetError('A complete explicitly confirmed fixed-scope checkpoint is required; rerun stages 1 and 2.')
    intent = session / 'commit-intent.json'
    if intent.exists():
        prior = read_json(intent)
        audit = models.AuditLog.objects.filter(metadata__plan_fingerprint=prior['fingerprint']).first()
        if audit: return {'already_applied': True, 'audit_id': audit.pk, 'note': 'No new or existing scores were changed on retry.'}
    with transaction.atomic():
        user = _lock_reset(69)
        target = host_and_database()
        plan = plan_for(checkpoint['review'], target)
        if (not plan['can_apply'] or target != checkpoint['target'] or
                plan['state_sha256'] != checkpoint['state_sha256'] or plan['protected_state_sha256'] != checkpoint['protected_state_sha256']):
            raise ResetError('Checkpoint is stale or blocked; no rows changed. Rerun stage 1.')
        backup = verify_existing_backup(plan)
        if backup != checkpoint['backup']: raise ResetError('Backup/restore evidence changed; no rows changed.')
        plan['development_authorization'] = authorization(checkpoint)
        plan['workflow'] = 'simple-vocal-duet-development-maintenance'
        plan.pop('fingerprint', None); plan['fingerprint'] = _digest(plan)
        if intent.exists():
            if read_json(intent)['fingerprint'] != plan['fingerprint']: raise ResetError('Prior commit evidence differs; start a fresh reviewed session.')
        else: write_new(intent, {'fingerprint': plan['fingerprint']})
        receipt = session / 'restore-receipt.json'
        if not receipt.exists():
            write_new(receipt, {**backup, 'plan_fingerprint': plan['fingerprint'], 'verified_by': 69,
                'verified_at': checkpoint['verified_at'], 'restore_succeeded': True,
                'source_database': connection.settings_dict['NAME']})
        result = _execute_reset(plan, user, plan['fingerprint'], str(WORK / 'database.dump'),
            backup['backup_sha256'], str(receipt), str(session / ('preimage-' + secrets.token_hex(8) + '.json')))
        if dev.protected_state() != checkpoint['protected_state_sha256']: raise ResetError('Protected data changed; the entire reset was rolled back.')
        from .criteria_mobile_rounds import read_mobile_round_context
        context = read_mobile_round_context(models.JudgingEvent.objects.get(pk=64))
        if not context or not context['compatible']: raise ResetError('Final scoring readiness failed; rollback completed.')
        result.update(workflow=plan['workflow'], protected_data_unchanged=True, mobile_configuration_ready=True)
    try: write_new(session / 'completed.json', result)
    except OSError: result['report_warning'] = 'Database committed. Outcome file could not be saved; audit_id is authoritative. Retry is idempotent.'
    return result
