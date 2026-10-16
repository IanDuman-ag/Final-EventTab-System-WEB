"""Separately provisioned infrastructure authorization; no production CLI switch."""
from datetime import datetime, timedelta
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from django.db import connection, transaction
from django.db.utils import load_backend
from django.utils import timezone

from . import models
from .vocal_duet_reset import (ResetError, _configuration_plan, _digest, _preimage,
                               _execute_reset, _lock_reset)
from .mobile_historical_review import _row_fingerprint


DIRECTORY = Path('/etc/eventtab/development-reset')
OPENSSL_BINARY = '/usr/bin/openssl'
CONFIRMATION = 'DEVELOPMENT ONLY: RESET VOCAL DUET TEST SCORES 175-190 ON VERIFIED TARGET'


def _file_hash(path):
    checksum = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def _root_controlled(path):
    if os.name != 'posix':
        raise ResetError('Development permits require a POSIX root-controlled installation.')
    for item in (path, *path.parents):
        info = item.lstat()
        if (stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or
                (item == path and not stat.S_ISREG(info.st_mode)) or
                (item != path and not stat.S_ISDIR(info.st_mode))):
            raise ResetError('Permit, public key and evidence must be root-owned, regular, non-writable by group/others and have trusted parents.')


def target_identity():
    """Read actual server/cluster identity, not configured DB name or DEBUG."""
    if connection.vendor != 'postgresql':
        raise ResetError('PostgreSQL is required.')
    from .vocal_duet_host_identity import verified_host_identity
    host = verified_host_identity()
    return {**host, **database_identity()}


def database_identity():
    """Live PostgreSQL identity; caller supplies its separately verified host proof."""
    with connection.cursor() as cursor:
        cursor.execute("""SELECT current_database(), (SELECT oid FROM pg_database WHERE datname=current_database()),
            inet_server_addr()::text, inet_server_port(), current_user,
            (SELECT system_identifier::text FROM pg_control_system()), current_setting('server_version_num'),
            current_setting('search_path'), pg_is_in_recovery()""")
        values = cursor.fetchone()
        cursor.execute("""SELECT n.nspname, c.relname, c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.oid=ANY(%s::regclass[]) ORDER BY n.nspname,c.relname""",
            [['events_event', 'events_judgingevent', 'events_judgescore', 'events_criterion', 'events_candidate']])
        tables = [list(row) for row in cursor.fetchall()]
    result = dict(zip(('database', 'database_oid', 'server_address', 'server_port', 'database_role',
        'cluster_system_identifier', 'server_version_num', 'search_path', 'in_recovery'), values))
    if not result['server_address'] or not result['server_port'] or result['in_recovery']:
        raise ResetError('A verified TCP connection to the writable primary is required.')
    return {**result, 'table_identities': tables}


def _verify_signature(payload, signature, public_key, label):
    _root_controlled(Path(OPENSSL_BINARY))
    try:
        subprocess.run([OPENSSL_BINARY, 'dgst', '-sha256', '-verify', str(public_key),
            '-signature', str(signature)], input=payload, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=True, timeout=10,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'OPENSSL_CONF': '/dev/null'})
    except (subprocess.SubprocessError, OSError):
        raise ResetError(f'{label} signature could not be verified.') from None


def ensure_development_enabled():
    seal = Path('/etc/eventtab/official-production.json')
    if os.path.lexists(seal):
        raise ResetError('Development maintenance is retired for official school production. No development reset is permitted.')


def load_authorization(actor, review, target):
    ensure_development_enabled()
    names = ('permit.json', 'permit.sig', 'authority-public.pem', 'deployment-inventory.json', 'development-use-evidence.json')
    paths = {name: DIRECTORY / name for name in names}
    for path in paths.values():
        _root_controlled(path)
    payload = paths['permit.json'].read_bytes()
    _verify_signature(payload, paths['permit.sig'], paths['authority-public.pem'], 'Infrastructure permit')
    permit = json.loads(payload)
    if not isinstance(permit, dict):
        raise ResetError('The signed infrastructure permit must be an object.')
    try:
        issued = datetime.fromisoformat(permit['issued_at'])
        expires = datetime.fromisoformat(permit['expires_at'])
        now = timezone.now()
        valid_time = (issued.utcoffset() == timedelta(0) and expires.utcoffset() == timedelta(0) and
            issued <= now < expires and timedelta(0) < expires - issued <= timedelta(hours=24))
    except (KeyError, TypeError, ValueError):
        valid_time = False
    if not valid_time:
        raise ResetError('The infrastructure permit must be current, UTC and valid for no more than 24 hours.')
    inventory = json.loads(paths['deployment-inventory.json'].read_text())
    usage = json.loads(paths['development-use-evidence.json'].read_text())
    if not isinstance(inventory, dict) or not isinstance(usage, dict):
        raise ResetError('Infrastructure evidence must contain structured records.')
    if (permit.get('purpose') != 'eventtab-development-vocal-duet-test-reset-v1' or
            permit.get('actor_id') != actor.pk or actor.pk != 69 or
            permit.get('target') != target or permit.get('review_sha256') != _digest(review) or
            permit.get('score_ids') != list(range(175, 191)) or
            permit.get('inventory_sha256') != _file_hash(paths['deployment-inventory.json']) or
            permit.get('development_use_sha256') != _file_hash(paths['development-use-evidence.json'])):
        raise ResetError('The signed permit does not match this actor, exact database target, review, evidence or score scope.')
    if (inventory.get('host_uuid') != target['host_uuid'] or inventory.get('target') != target or
            not inventory.get('infrastructure_record_reference') or
            usage.get('deployment_classification') != 'development-only' or
            usage.get('actual_school_use') is not False or usage.get('shared_with_live_events') is not False or
            not usage.get('deployment_owner') or not usage.get('usage_verification_reference')):
        raise ResetError('Infrastructure inventory and external usage verification must independently establish exclusive development use.')
    digest = hashlib.sha256(payload).hexdigest()
    if models.AuditLog.objects.filter(metadata__development_authorization__permit_sha256=digest).exists():
        raise ResetError('This infrastructure permit has already been used.')
    host_digest = target.get('host_attestation_sha256')
    if host_digest and models.AuditLog.objects.filter(
            metadata__development_authorization__target__host_attestation_sha256=host_digest).exists():
        raise ResetError('This host attestation has already authorized a reset. Obtain a fresh root challenge and signed attestation.')
    return {'mode': 'verified-development-only', 'permit_sha256': digest, 'target': target,
        'expires_at': permit['expires_at'], 'inventory_sha256': permit['inventory_sha256'],
        'development_use_sha256': permit['development_use_sha256']}


def protected_state():
    """Detect any accidental change outside the exact approved mobile records."""
    queries = {
        'web_events': models.Event.objects.exclude(pk=79),
        'mobile_events': models.JudgingEvent.objects.all(),
        'criteria': models.Criterion.objects.exclude(event_id=64),
        'candidates': models.Candidate.objects.exclude(event_id=64),
        'scores': models.JudgeScore.objects.exclude(pk__in=range(175, 191)),
        'mobile_children': models.CriterionSubcriterion.objects.exclude(criterion_id__in=range(1197, 1201)),
    }
    for model in (models.CriteriaScoreSubmission, models.CriteriaSubcriterionScore, models.JudgeSubcriterionScore,
                  models.EventScoringCategory, models.EventScoringCriterion, models.EventScoringSubcriterion,
                  models.CriteriaEventEntry, models.CriteriaEventEntryMember, models.RegistryCandidate, models.Department, models.Team):
        queries[model._meta.label] = model.objects.all()
    for model in (models.Event, models.JudgingEvent):
        for field in model._meta.many_to_many:
            queries[field.remote_field.through._meta.db_table] = field.remote_field.through.objects.all()
    from django.contrib.auth import get_user_model
    user_model = get_user_model()
    queries['users'] = user_model.objects.all()
    queries['user_groups'] = user_model.groups.through.objects.all()
    queries['groups'] = user_model._meta.get_field('groups').remote_field.model.objects.all()
    state = {key: _row_fingerprint(queryset) for key, queryset in queries.items()}
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_jsonb(r) - 'result_processing_config' FROM events_event r WHERE id=79")
        row = cursor.fetchone()[0]
    state['own_event_except_mapping_config'] = _digest(json.loads(row) if isinstance(row, str) else row)
    config = dict(models.Event.objects.get(pk=79).result_processing_config or {})
    config.pop('mobile_round_mapping', None)
    state['own_config_except_mobile_mapping'] = _digest(config)
    return state


def development_plan(actor, review):
    plan = _configuration_plan(actor, review)
    plan['workflow'] = 'verified-development-only'
    target = target_identity()
    plan['database_target'] = target
    plan['development_authorization'] = load_authorization(actor, review, target)
    _review_issues(plan, actor, review)
    plan['protected_state_sha256'] = protected_state()
    plan['can_apply'] = not plan['blocked_reasons']
    plan.pop('fingerprint', None)
    plan['fingerprint'] = _digest(plan)
    return plan


def _review_issues(plan, actor, review):
    ownership = plan.get('owner_integrity', {})
    approved = review.get('development_test_data_review') or {}
    approved = approved if isinstance(approved, dict) else {}
    if (approved.get('verified_by') != actor.pk or approved.get('confirmed_test_data') is not True or
            approved.get('missing_judge_ids') != ownership.get('missing_judge_ids') or
            approved.get('orphan_score_ids') != ownership.get('orphan_score_ids') or
            not isinstance(approved.get('evidence_reference'), str) or not approved['evidence_reference'].strip()):
        plan['blocked_reasons'].append('A complete actual-operator development test-data review with exact orphan IDs and evidence is required.')
    if not isinstance(review.get('roster_evidence_reference'), str) or not review['roster_evidence_reference'].strip():
        plan['blocked_reasons'].append('The actual verified roster evidence reference is required.')
    for entry in models.CriteriaEventEntry.objects.filter(event_id=79).select_related('department').prefetch_related('member_links__individual'):
        bindings = [row for row in review.get('pairs', []) if row.get('entry_id') == entry.pk]
        binding = bindings[0] if len(bindings) == 1 else {}
        if (not entry.department_id or binding.get('roster_verified_by') != actor.pk or
                binding.get('verified_department_id') != entry.department_id or
                binding.get('verified_department_name') != entry.department.name or
                binding.get('verified_member_department_ids') != [link.individual.department_id for link in entry.member_links.all()]):
            plan['blocked_reasons'].append(f'Pair {entry.pk} requires explicit roster-based verification of its department and member departments.')


def verify_isolated_restore(plan, restored_database):
    if not restored_database or restored_database == plan['database_target']['database']:
        raise ResetError('An existing separate restored database is required; the target database cannot verify itself.')
    settings = deepcopy(connection.settings_dict)
    settings['NAME'] = restored_database
    restored = load_backend(settings['ENGINE']).DatabaseWrapper(settings, alias='vocal_duet_restore_verification')
    try:
        if restored.get_connection_params().get('dbname') != restored_database:
            raise ResetError('Connection options override the isolated restore database.')
        expected = _preimage(plan)['tables']
        with restored.cursor() as cursor:
            cursor.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cursor.execute("SELECT set_config('TimeZone', %s, true)", [connection.timezone_name])
            cursor.execute('SELECT current_database(), (SELECT oid FROM pg_database WHERE datname=current_database())')
            name, oid = cursor.fetchone()
            if name != restored_database or oid == plan['database_target']['database_oid']:
                raise ResetError('The restored database must be distinct from the reset target.')
            for table, rows in expected.items():
                quoted = restored.ops.quote_name(table)
                cursor.execute(f'SELECT to_jsonb(r) FROM {quoted} r WHERE id=ANY(%s) ORDER BY id', [[row['id'] for row in rows]])
                actual = [json.loads(row) if isinstance(row, str) else row for (row,) in cursor.fetchall()]
                if _digest(actual) != _digest(rows):
                    raise ResetError('Restored scoped rows differ from the target; nothing was changed.')
            cursor.execute('ROLLBACK')
        return _digest(expected)
    finally:
        restored.close()


@transaction.atomic
def apply_development_reset(actor_id, review, fingerprint, confirmation, backup_path, backup_hash, receipt_path, snapshot_path, restored_database):
    if confirmation != CONFIRMATION:
        raise ResetError('The exact development-only confirmation is required.')
    actor = _lock_reset(actor_id)
    plan = development_plan(actor, review)
    if not plan['can_apply'] or plan['fingerprint'] != fingerprint:
        raise ResetError('The development plan is blocked or stale. Nothing was changed.')
    verify_isolated_restore(plan, restored_database)
    receipt = json.loads(Path(receipt_path).read_text())
    if receipt.get('isolated_restore_database') != restored_database:
        raise ResetError('The restore receipt must identify the independently queried restored database.')
    result = _execute_reset(plan, actor, fingerprint, backup_path, backup_hash, receipt_path, snapshot_path)
    if protected_state() != plan['protected_state_sha256']:
        raise ResetError('Protected data changed; the entire development reset was rolled back.')
    result['workflow'] = plan['workflow']
    return result
