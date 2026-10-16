"""One reviewed test-data reset. Not an API, generic reset, or workflow override."""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from .criteria_faculty_service import criteria_rounds, round_is_scoreable, round_participant_ids
from .criteria_mobile_rounds import read_mobile_round_context
from .mobile_historical_review import _row_fingerprint
from .mobile_scoring_compatibility import effective_weight
from .models import (AuditLog, Candidate, Criterion, CriterionSubcriterion, CriteriaScoreSubmission, CriteriaSubcriterionScore,
                     Event, EventScoringCriterion, JudgeScore, JudgeSubcriterionScore, JudgingEvent)
from .subcriteria import subcriteria_issue


SCORE_IDS = list(range(175, 191))
CRITERION_IDS = [1197, 1198, 1199, 1200]
CANDIDATE_IDS = [372, 373]
ROUND_ID = 's9bu97fk'
CONFIRMATION = 'DELETE ONLY VOCAL DUET TEST SCORES 175-190 AND REPAIR 64/79'


class ResetError(ValueError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _authorized(actor, event):
    roles = {name.casefold() for name in actor.groups.values_list('name', flat=True)} if actor else set()
    conflicting = roles.intersection({'judge', 'judges', 'scorer', 'tabulator', 'viewer', 'viewers'})
    return bool(actor and actor.is_active and (actor.is_superuser or (not conflicting and (
        (actor.is_staff and not roles.intersection({'faculty', 'faculty in-charge'})) or
        (actor.pk == event.faculty_account_id and roles.intersection({'faculty', 'faculty in-charge'}))))))


def _related_scores():
    return JudgeScore.objects.filter(Q(candidate_id__in=CANDIDATE_IDS) | Q(criterion_id__in=CRITERION_IDS) |
        Q(candidate__event_id=64) | Q(criterion__event_id=64) | Q(pk__in=SCORE_IDS)).distinct()


def _database_dependencies(targets):
    """Find actual inbound PostgreSQL FKs, including tables unknown to Django."""
    if connection.vendor != 'postgresql':
        return [], ['This repair requires PostgreSQL dependency inspection.']
    result, errors = [], []
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute("""SELECT ns.nspname, src.relname, c.conname, c.conkey, c.confkey,
            dst.relname, c.conrelid, c.confrelid
            FROM pg_constraint c JOIN pg_class src ON src.oid=c.conrelid
            JOIN pg_namespace ns ON ns.oid=src.relnamespace
            JOIN pg_class dst ON dst.oid=c.confrelid
            WHERE c.contype='f' AND c.confrelid=ANY(%s::regclass[]) ORDER BY 1,2,3""", [list(targets)])
        constraints = cursor.fetchall()
        for schema, table, name, columns, referenced, target, source_oid, target_oid in constraints:
            if len(columns) != 1 or len(referenced) != 1:
                errors.append(f'Composite dependency {schema}.{table}/{name} requires separate review.')
                continue
            cursor.execute('SELECT attname FROM pg_attribute WHERE attrelid=%s AND attnum=%s', [source_oid, columns[0]])
            column = cursor.fetchone()[0]
            cursor.execute('SELECT attname FROM pg_attribute WHERE attrelid=%s AND attnum=%s', [target_oid, referenced[0]])
            if cursor.fetchone()[0] != 'id':
                errors.append(f'Non-ID dependency {schema}.{table}/{name} requires separate review.')
                continue
            ids = targets[target]
            cursor.execute(f'SELECT to_jsonb(r) FROM {quote(schema)}.{quote(table)} r WHERE {quote(column)}=ANY(%s)', [ids])
            rows = [json.loads(row) if isinstance(row, str) else row for (row,) in cursor.fetchall()]
            rows.sort(key=lambda row: json.dumps(row, sort_keys=True, default=str))
            result.append({'schema': schema, 'table': table, 'constraint': name, 'column': column,
                'target_table': target, 'count': len(rows), 'ids': [row.get('id') for row in rows],
                'rows_sha256': _digest(rows)})
            allowed = (schema == 'public' and (
                (table == JudgeScore._meta.db_table and target in {Candidate._meta.db_table, Criterion._meta.db_table}
                 and {row.get('id') for row in rows}.issubset(SCORE_IDS)) or
                (table == CriterionSubcriterion._meta.db_table and target == Criterion._meta.db_table)))
            # Activity logs reference candidates, which are retained unchanged in identity.
            if schema == 'public' and table == 'events_judgeactivitylog' and target == Candidate._meta.db_table:
                allowed = True
            if rows and not allowed:
                errors.append(f'Dependent records in {schema}.{table}/{name} require separate review; none will be deleted.')
        cursor.execute("""SELECT tgname FROM pg_trigger WHERE NOT tgisinternal
            AND tgenabled <> 'D' AND tgrelid=ANY(%s::regclass[])""", [list(targets)])
        if cursor.fetchall():
            errors.append('Custom database triggers on reset tables require separate review.')
    return result, errors


def _configuration_plan(actor, review):
    event = Event.objects.select_related('judging_event').get(pk=79)
    if not _authorized(actor, event):
        raise ResetError('Only an active authorized Admin or the assigned Faculty may preview/apply this repair.')
    blocked = []
    plan = {'read_only': True, 'judging_event_id': 64, 'portal_event_id': 79, 'round_id': ROUND_ID,
        'actor_id': actor.pk, 'review': review, 'delete_score_ids': SCORE_IDS, 'criteria': [], 'pairs': [],
        'blocked_reasons': blocked}
    if event.judging_event_id != 64 or event.scoring_method != 'criteria' or event.participation_type != 'pair':
        blocked.append('Event 79 must be linked to JudgingEvent 64 and use criteria/pair judging.')
    if event.judging_event_id != 64:
        plan['can_apply'] = False
        plan['fingerprint'] = _digest(plan)
        return plan
    config = event.result_processing_config or {}
    if event.results_finalized or any(config.get(key) for key in (
        'published_at', 'publication_submitted_at', 'approved', 'approved_at', 'confirmed_stages',
        'calculated_placements', 'tabulation_confirmed_at', 'ready_for_publication', 'faculty_submitted_at')):
        blocked.append('Published, submitted-for-publication, approved or confirmed result state exists.')
    # publication_status controls event visibility; it is not criteria result publication.
    plan['publication_state'] = {'results_finalized': event.results_finalized,
        'event_visibility': event.publication_status, 'config': config}
    rounds = criteria_rounds(event)
    try:
        index = int(config.get('active_stage_index') or 0)
        eligible = round_participant_ids(event, index)
        open_round = round_is_scoreable(event, index) and rounds[index]['id'] == ROUND_ID
    except (TypeError, ValueError, IndexError):
        eligible, open_round = [], False
    if not open_round or event.status != Event.STATUS_ACTIVE or event.judging_event.status != 'active':
        blocked.append('The existing Faculty-controlled active round must be open and both events active.')
    scores = _related_scores().order_by('pk')
    cells = list(scores.values('id', 'judge_id', 'candidate_id', 'criterion_id', 'submitted_at', 'is_locked'))
    plan['test_scores'] = [{**row, 'submitted_at': row['submitted_at'].isoformat() if row['submitted_at'] else None} for row in cells]
    if [row['id'] for row in cells] != SCORE_IDS or any(
        row['candidate_id'] not in CANDIDATE_IDS or row['criterion_id'] not in CRITERION_IDS for row in cells):
        blocked.append('Exactly score IDs 175–190 must be the entire Vocal Duet score set, using only the approved criteria and candidates.')
    mobile_criteria = list(Criterion.objects.filter(event_id=64).prefetch_related('subcriteria').order_by('pk'))
    candidates = list(Candidate.objects.filter(event_id=64).order_by('pk'))
    if [row.pk for row in mobile_criteria] != CRITERION_IDS or [row.pk for row in candidates] != CANDIDATE_IDS:
        blocked.append('Mobile criterion/candidate inventory differs from the explicitly approved IDs.')
    owner_ids = {row['judge_id'] for row in cells}
    existing_owners = set(get_user_model().objects.filter(pk__in=owner_ids).values_list('pk', flat=True))
    missing_owners = sorted(owner_ids - existing_owners)
    orphan_score_ids = [row['id'] for row in cells if row['judge_id'] in missing_owners]
    plan['owner_integrity'] = {'missing_judge_ids': missing_owners, 'orphan_score_ids': orphan_score_ids,
        'orphan_rows': [{'score_id': row['id'], 'judge_id': row['judge_id']} for row in cells
                        if row['judge_id'] in missing_owners],
        'independently_verified': False}
    categories = list(event.scoring_categories.filter(purpose='official', assigned_round_id=ROUND_ID)
        .prefetch_related('criteria__subcriteria'))
    definitions = [row for category in categories for row in category.criteria.all()]
    normalized = CriteriaScoreSubmission.objects.filter(Q(event_id=79) | Q(category__event_id=79) | Q(criterion__category__event_id=79)).distinct()
    plan['normalized_submission_ids'] = list(normalized.order_by('pk').values_list('pk', flat=True))
    if plan['normalized_submission_ids']:
        blocked.append('Normalized submissions exist, including drafts/inactive sources; no cleanup is permitted.')
    plan['normalized_subscore_ids'] = list(CriteriaSubcriterionScore.objects.filter(
        Q(subcriterion__criterion__category__event_id=79) | Q(submission__event_id=79)).values_list('pk', flat=True))
    if plan['normalized_subscore_ids']:
        blocked.append('Normalized child scoring records exist; no cleanup is permitted.')
    plan['match_scoresheet_ids'] = list(event.scoresheets.values_list('pk', flat=True))
    if plan['match_scoresheet_ids']:
        blocked.append('Unexpected match scoresheets are attached to this criteria event; separate review is required.')
    if len(definitions) != 4 or sum((row.overall_weight_percent for row in categories), Decimal(0)) != 100:
        blocked.append('Exactly four official criteria and category weights totaling 100% are required.')
    for category in categories:
        if category.judge_mode != 'scoring' or not 0 <= category.overall_weight_percent <= 100 or sum(
            (row.weight_percent for row in category.criteria.all()), Decimal(0)) != 100:
            blocked.append('Official numeric scoring category configuration is invalid.')
    criteria_review = review.get('criteria') if isinstance(review, dict) else None
    pair_review = review.get('pairs') if isinstance(review, dict) else None
    verified = isinstance(review, dict) and review.get('verified_by') == actor.pk and review.get('all_16_scores_are_test_data') is True and bool(str(review.get('evidence_reference', '')).strip())
    if not verified:
        blocked.append('Explicit test-data confirmation, evidence reference and verification by the acting Admin/Faculty are required.')
    criteria_review = criteria_review if isinstance(criteria_review, list) else []
    pair_review = pair_review if isinstance(pair_review, list) else []
    criterion_ids = {row.pk for row in definitions}
    if (len(criteria_review) != 4 or {row.get('official_id') for row in criteria_review} != criterion_ids or
            {row.get('mobile_id') for row in criteria_review} != set(CRITERION_IDS)):
        blocked.append('Review must explicitly bind each of the four official criterion IDs to distinct approved mobile IDs.')
    stored = {row.pk: row for row in mobile_criteria}
    mapping = config.get('mobile_round_mapping') or {}
    for definition in definitions:
        bindings = [row for row in criteria_review if row.get('official_id') == definition.pk]
        mobile = stored.get(bindings[0].get('mobile_id')) if len(bindings) == 1 else None
        if mobile is None:
            continue
        if (definition.max_score is None or not 0 <= definition.min_score <= definition.max_score or definition.max_score <= 0
                or not 0 <= definition.weight_percent <= 100 or subcriteria_issue(definition) or not definition.subcriteria.all()):
            blocked.append(f'Official criterion {definition.pk} must have valid detailed subcriteria, bounds and weights.')
        for key, value in (mapping.get('criteria') or {}).items():
            if value == mobile.pk and key != f'{ROUND_ID}:{definition.pk}':
                blocked.append(f'Criterion {mobile.pk} is mapped to another official definition or round.')
        children = [{'name': child.name, 'max_score': str(child.max_score), 'display_order': child.display_order}
            for child in definition.subcriteria.all()]
        existing = [{'name': child.name, 'max_score': str(child.max_score), 'display_order': child.display_order}
            for child in mobile.subcriteria.all()]
        if existing and existing != children:
            blocked.append(f'Criterion {mobile.pk} has differing existing subcriteria; replacement requires separate review.')
        if len(definition.name) > 100 or definition.max_score is not None and definition.max_score > Decimal('99999.99'):
            blocked.append('Official criterion exceeds existing legacy field limits.')
        plan['criteria'].append({'official_id': definition.pk, 'mobile_id': mobile.pk,
            'before': {'name': mobile.name, 'max_score': str(mobile.max_score), 'weight_percent': str(mobile.weight_percent), 'order': mobile.order, 'subcriteria': existing},
            'after': {'name': definition.name, 'description': definition.judging_description[:200],
                'max_score': str(definition.max_score), 'weight_percent': str(effective_weight(definition).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)),
                'order': mobile.order, 'subcriteria': children}, 'create_subcriteria': not bool(existing)})
    if len({row.order for row in mobile_criteria}) != len(mobile_criteria):
        blocked.append('Existing global criterion orders are not unique; separate review is required.')
    entries = list(event.criteria_entries.select_related('department').prefetch_related('member_links__individual__department').order_by('pk'))
    if len(entries) != 2 or {row.pk for row in entries} != set(eligible) or {row.pk for row in entries} != set(event.participant_ids):
        blocked.append('Exactly two official eligible pair entries must comprise the event roster.')
    if (len(pair_review) != 2 or {row.get('entry_id') for row in pair_review} != {row.pk for row in entries}
            or {row.get('mobile_id') for row in pair_review} != set(CANDIDATE_IDS)):
        blocked.append('Review must explicitly bind both official entry IDs to distinct approved candidate IDs.')
    for entry in entries:
        bindings = [row for row in pair_review if row.get('entry_id') == entry.pk]
        binding = bindings[0] if len(bindings) == 1 else {}
        candidate = next((row for row in candidates if row.pk == binding.get('mobile_id')), None)
        members = list(entry.member_links.all())
        member_ids = [link.individual_id for link in members]
        if (entry.entry_type != 'pair' or len(members) != 2 or len(set(member_ids)) != 2 or
                [link.position for link in members] != [1, 2] or binding.get('member_ids') != member_ids or
                not str(binding.get('identity_evidence', '')).strip()):
            blocked.append(f'Pair {entry.pk} requires explicit verified ordered member IDs and identity evidence.')
        if candidate is None:
            continue
        department = entry.department.name if entry.department else ''
        if (candidate.department != department or candidate.name != entry.display_name) and not str(binding.get('snapshot_discrepancy_explanation', '')).strip():
            blocked.append(f'Pair {entry.pk} requires explicit explanation of the name/department discrepancy.')
        if any(value == candidate.pk and key != str(entry.pk) for key, value in (mapping.get('candidates') or {}).items()):
            blocked.append(f'Candidate {candidate.pk} is mapped to a different entry.')
        if any(not link.individual.status == 'active' for link in members) or len(entry.display_name) > 200 or len(department) > 150:
            blocked.append(f'Pair {entry.pk} contains inactive members or exceeds mobile field limits.')
        plan['pairs'].append({'entry_id': entry.pk, 'mobile_id': candidate.pk, 'member_ids': member_ids,
            'members': [{'id': link.individual_id, 'name': link.individual.name, 'department_id': link.individual.department_id} for link in members],
            'before': {'name': candidate.name, 'department': candidate.department, 'description': candidate.description},
            'after': {'name': entry.display_name, 'department': department, 'description': ' + '.join(link.individual.name for link in members)}})
    targets = {JudgeScore._meta.db_table: SCORE_IDS, Criterion._meta.db_table: CRITERION_IDS,
        Candidate._meta.db_table: CANDIDATE_IDS,
        CriterionSubcriterion._meta.db_table: list(CriterionSubcriterion.objects.filter(criterion_id__in=CRITERION_IDS).values_list('pk', flat=True))}
    dependencies, errors = _database_dependencies(targets)
    plan['dependencies'] = dependencies
    blocked.extend(errors)
    # Defense when a production FK is missing: query known child tables directly.
    child_scores = JudgeSubcriterionScore.objects.filter(Q(judge_score_id__in=SCORE_IDS) | Q(subcriterion__criterion_id__in=CRITERION_IDS))
    plan['dependent_subscore_ids'] = list(child_scores.values_list('pk', flat=True))
    if plan['dependent_subscore_ids']:
        blocked.append('Dependent legacy subcriterion scores exist; none are approved for deletion.')
    plan['preserved_activity_log_policy'] = 'Keep existing event/candidate activity logs; add one separate reset audit.'
    plan['state_sha256'] = {
        'scores': _row_fingerprint(scores), 'criteria': _row_fingerprint(Criterion.objects.filter(event_id=64)),
        'candidates': _row_fingerprint(Candidate.objects.filter(event_id=64)),
        'children': _row_fingerprint(CriterionSubcriterion.objects.filter(criterion_id__in=CRITERION_IDS)),
        'event': _row_fingerprint(Event.objects.filter(pk=79)),
        'official_categories': _row_fingerprint(event.scoring_categories.all()),
        'official_criteria': _row_fingerprint(EventScoringCriterion.objects.filter(category__event_id=79)),
        'entries': _row_fingerprint(event.criteria_entries.all()),
    }
    # Include all current round/config and official member/child details in the reviewed plan.
    plan['rounds'] = event.rounds_config
    plan['official_judge_ids'] = list(event.assigned_judges.order_by('pk').values_list('pk', flat=True))
    plan['can_apply'] = not blocked
    plan['fingerprint'] = _digest(plan)
    return plan


def reset_plan(actor, review):
    """Production policy: independent approval remains mandatory for orphans."""
    plan = _configuration_plan(actor, review)
    if 'owner_integrity' not in plan:
        return plan
    event = Event.objects.get(pk=79)
    missing_owners = plan['owner_integrity']['missing_judge_ids']
    orphan_score_ids = plan['owner_integrity']['orphan_score_ids']
    blocked = plan['blocked_reasons']
    if missing_owners:
        approval = review.get('orphan_owner_review') if isinstance(review, dict) else None
        approval = approval if isinstance(approval, dict) else {}
        reviewer_id = approval.get('verified_by')
        reviewer = get_user_model().objects.filter(pk=reviewer_id).first() if type(reviewer_id) is int else None
        independent = bool(reviewer and reviewer.pk != actor.pk and _authorized(reviewer, event))
        plan['owner_integrity']['independent_reviewer_id'] = reviewer_id
        # Missing historical owners are not participant/event identity evidence.
        # This exception approves only explicitly listed test rows, never an ID range
        # expansion, cross-event row, dependent submission or incomplete pair review.
        approved = (independent and approval.get('confirmed_test_data') is True and
            approval.get('missing_judge_ids') == missing_owners and
            approval.get('orphan_score_ids') == orphan_score_ids and
            isinstance(approval.get('evidence_reference'), str) and bool(approval['evidence_reference'].strip()))
        plan['owner_integrity']['independently_verified'] = bool(approved)
        if not approved:
            blocked.append('Orphan-owner test rows require exact missing-judge and score-ID lists, test-data confirmation and independent verification by a different authorized Admin/assigned Faculty with evidence.')
    plan['can_apply'] = not blocked
    plan.pop('fingerprint', None)
    plan['fingerprint'] = _digest(plan)
    return plan


def verify_backup(path, expected_hash, receipt_path, fingerprint, actor_id, scoped_rows_hash):
    """Enforce archive/checksum identity and the operator's restore attestation.

    Restoration is deliberately a separate DBA step, never run by this command.
    A receipt is not a substitute for actually restoring and checking the dump.
    """
    backup = Path(path)
    with backup.open('rb') as source:
        if source.read(5) != b'PGDMP':
            raise ResetError('A full custom-format pg_dump archive is required.')
        source.seek(0)
        checksum = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            checksum.update(chunk)
        digest = checksum.hexdigest()
    receipt = json.loads(Path(receipt_path).read_text())
    if (digest != expected_hash or receipt.get('backup_sha256') != digest or
            receipt.get('plan_fingerprint') != fingerprint or receipt.get('verified_by') != actor_id or
            receipt.get('restore_succeeded') is not True or not receipt.get('verified_at') or
            receipt.get('source_database') != connection.settings_dict['NAME'] or
            receipt.get('scoped_rows_sha256') != scoped_rows_hash or
            not receipt.get('isolated_restore_database') or receipt['isolated_restore_database'] == connection.settings_dict['NAME']):
        raise ResetError('Backup checksum or explicit isolated restore-verification receipt is invalid.')
    return {'backup_sha256': digest, 'restore_receipt_sha256': _digest(receipt)}


def _preimage(plan):
    """Private rollback evidence includes extra production columns absent from ORM."""
    result = {'plan': plan, 'created_at': timezone.now().isoformat(), 'tables': {}}
    for model, ids in ((JudgeScore, SCORE_IDS), (Criterion, CRITERION_IDS), (Candidate, CANDIDATE_IDS),
                       (Event, [79]), (CriterionSubcriterion, list(CriterionSubcriterion.objects.filter(criterion_id__in=CRITERION_IDS).values_list('pk', flat=True)))):
        with connection.cursor() as cursor:
            table = connection.ops.quote_name(model._meta.db_table)
            cursor.execute(f'SELECT to_jsonb(r) FROM {table} r WHERE id=ANY(%s) ORDER BY id', [ids])
            result['tables'][model._meta.db_table] = [json.loads(row) if isinstance(row, str) else row for (row,) in cursor.fetchall()]
    return result


def _lock_reset(actor_id):
    # Same lock order as mobile submit. Local timeouts avoid hanging a live server.
    if connection.vendor != 'postgresql':
        raise ResetError('PostgreSQL is required.')
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")
    JudgingEvent.objects.select_for_update().get(pk=64)
    Event.objects.select_for_update().get(pk=79)
    actor = get_user_model().objects.select_for_update().get(pk=actor_id)
    # Conservative table locks prevent phantom rows from alternate legacy writers,
    # official configuration changes and new FK dependencies during this one-off repair.
    # This briefly blocks writes to shared tables; use a maintenance window.
    tables = ['events_judgescore', 'events_criterion', 'events_candidate', 'events_criterionsubcriterion',
        'events_judgesubcriterionscore', 'events_criteriascoresubmission', 'events_criteriasubcriterionscore',
        'events_eventscoringcategory', 'events_eventscoringcriterion', 'events_eventscoringsubcriterion',
        'events_criteriaevententry', 'events_criteriaevententrymember', 'events_registrycandidate', 'events_department']
    with connection.cursor() as cursor:
        for table in sorted(tables):
            cursor.execute(f'LOCK TABLE {connection.ops.quote_name(table)} IN SHARE ROW EXCLUSIVE MODE')
        # Keep owner existence and independent-verifier authorization stable until
        # commit; do not create accounts or reassign orphaned scores.
        user_model = get_user_model()
        for table in sorted({user_model._meta.db_table, user_model.groups.through._meta.db_table,
                             user_model._meta.get_field('groups').remote_field.model._meta.db_table}):
            cursor.execute(f'LOCK TABLE {connection.ops.quote_name(table)} IN SHARE ROW EXCLUSIVE MODE')
        cursor.execute("""SELECT DISTINCT ns.nspname, src.relname FROM pg_constraint c
            JOIN pg_class src ON src.oid=c.conrelid JOIN pg_namespace ns ON ns.oid=src.relnamespace
            WHERE c.contype='f' AND c.confrelid=ANY(%s::regclass[]) ORDER BY 1,2""",
            [[JudgeScore._meta.db_table, Criterion._meta.db_table, Candidate._meta.db_table, CriterionSubcriterion._meta.db_table]])
        for schema, table in cursor.fetchall():
            cursor.execute(f'LOCK TABLE {connection.ops.quote_name(schema)}.{connection.ops.quote_name(table)} IN SHARE ROW EXCLUSIVE MODE')
    return actor


def _execute_reset(plan, actor, fingerprint, backup_path, backup_hash, receipt_path, snapshot_path):
    """Shared atomic mutation after the entry point validates its own policy."""
    if not connection.in_atomic_block or not plan['can_apply'] or plan['fingerprint'] != fingerprint:
        raise ResetError('An atomic approved and unchanged plan is required.')
    snapshot = _preimage(plan)
    backup = verify_backup(backup_path, backup_hash, receipt_path, fingerprint, actor.pk, _digest(snapshot['tables']))
    # Never overwrite a preimage. It remains useful even if the DB transaction rolls back.
    descriptor = os.open(snapshot_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
        json.dump(snapshot, output, indent=2, default=str)
        output.flush()
        os.fsync(output.fileno())
    deleted, details = JudgeScore.objects.filter(pk__in=SCORE_IDS).delete()
    if deleted != 16 or details != {JudgeScore._meta.label: 16}:
        raise ResetError('Unexpected deletion dependency; the whole transaction was rolled back.')
    for item in plan['criteria']:
        values = {key: value for key, value in item['after'].items() if key != 'subcriteria'}
        Criterion.objects.filter(pk=item['mobile_id'], event_id=64).update(**values)
        if item['create_subcriteria']:
            for child in item['after']['subcriteria']:
                CriterionSubcriterion.objects.create(criterion_id=item['mobile_id'], **child)
    for item in plan['pairs']:
        Candidate.objects.filter(pk=item['mobile_id'], event_id=64).update(**item['after'])
    event = Event.objects.get(pk=79)
    config = deepcopy(event.result_processing_config or {})
    mapping = config.setdefault('mobile_round_mapping', {})
    mapping.setdefault('criteria', {}).update({f"{ROUND_ID}:{item['official_id']}": item['mobile_id'] for item in plan['criteria']})
    mapping.setdefault('candidates', {}).update({str(item['entry_id']): item['mobile_id'] for item in plan['pairs']})
    event.result_processing_config = config
    event.save(update_fields=['result_processing_config'])
    context = read_mobile_round_context(JudgingEvent.objects.get(pk=64))
    if (not context or not context['compatible'] or context['round_id'] != ROUND_ID or
            len(context['criteria']) != 4 or len(context['candidates']) != 2 or _related_scores().exists() or
            CriteriaScoreSubmission.objects.filter(event_id=79).exists()):
        raise ResetError('Post-repair read-only readiness validation failed; everything was rolled back.')
    audit = AuditLog.objects.create(user=actor, user_name=actor.username, user_role='Authorized test-reset operator',
        module='Judge Mobile', action='Reset Vocal Duet test scores', event_name=event.name,
        description='Explicitly approved test IDs 175–190 removed; reviewed existing criteria/candidates repaired.',
        metadata={'plan_fingerprint': fingerprint, 'score_ids': SCORE_IDS, 'criterion_ids': CRITERION_IDS,
            'candidate_ids': CANDIDATE_IDS, 'owner_integrity': plan['owner_integrity'],
            'snapshot_sha256': _digest(snapshot),
            'development_authorization': plan.get('development_authorization'), **backup})
    return {'applied': True, 'audit_id': audit.pk, 'deleted_test_score_ids': SCORE_IDS,
        'judging_event_id': 64, 'portal_event_id': 79, 'round_id': ROUND_ID,
        'configuration_compatible': True, 'note': 'Each Judge still requires their own valid assignment and round permission.'}


@transaction.atomic
def apply_reset(actor_id, review, fingerprint, confirmation, backup_path, backup_hash, receipt_path, snapshot_path):
    if confirmation != CONFIRMATION:
        raise ResetError('The exact event-scoped test-data confirmation is required.')
    actor = _lock_reset(actor_id)
    plan = reset_plan(actor, review)
    if plan['fingerprint'] != fingerprint or not plan['can_apply']:
        raise ResetError('The reviewed plan is stale or blocked; nothing was changed. Preview again.')
    return _execute_reset(plan, actor, fingerprint, backup_path, backup_hash, receipt_path, snapshot_path)
