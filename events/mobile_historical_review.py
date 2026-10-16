"""Read-only evidence for historical identity/rubric review; never authorizes setup."""
from collections import defaultdict
from decimal import Decimal
import hashlib
import json

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import Q

from .criteria_faculty_service import criteria_rounds, round_is_scoreable
from .mobile_scoring_compatibility import criterion_compatibility_issue, effective_weight, round_mapping_issues
from .models import Candidate, Criterion, CriterionSubcriterion, Department, JudgeScore, JudgeSubcriterionScore


def _stamp(value):
    return value.isoformat() if value else None


def _coefficient(maximum, weight):
    if maximum is None or not maximum.is_finite() or maximum <= 0 or not weight.is_finite():
        return None
    return weight / maximum


def _text(value):
    return str(value) if value is not None else None


def _children(criterion):
    return [{'id': child.pk, 'name': child.name, 'max_score': str(child.max_score),
             'display_order': child.display_order} for child in criterion.subcriteria.all()]


def _names(definition):
    return {definition.name[:100], f'{definition.category.name}: {definition.name}'[:100]}


def _criterion_comparison(definition, mobile):
    official_coefficient = _coefficient(definition.max_score, effective_weight(definition))
    legacy_coefficient = _coefficient(mobile.max_score, mobile.weight_percent)
    return {
        'mobile_id': mobile.pk, 'name_exact_or_category_qualified': mobile.name in _names(definition),
        'maximum_equal': definition.max_score == mobile.max_score,
        'weight_matches_category_local': abs(definition.weight_percent - mobile.weight_percent) <= Decimal('0.05'),
        'weight_matches_effective': abs(effective_weight(definition) - mobile.weight_percent) <= Decimal('0.05'),
        'official_category_local_order': definition.display_order, 'mobile_global_order': mobile.order,
        'order_used_as_identity_proof': False,
        'legacy_weighted_coefficient': _text(legacy_coefficient),
        'official_weighted_coefficient': _text(official_coefficient),
        'same_raw_score_preserves_weighted_contribution': official_coefficient is not None and official_coefficient == legacy_coefficient,
        'runtime_compatibility_issue': criterion_compatibility_issue(mobile, definition),
    }


def _row_fingerprint(queryset):
    """Hash full PostgreSQL rows, including columns unknown to the Django model.

    Raw values stay internal unless the separate report option requests scores.
    """
    ids = list(queryset.order_by('pk').values_list('pk', flat=True))
    if connection.vendor == 'postgresql' and ids:
        table = connection.ops.quote_name(queryset.model._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT to_jsonb(r) FROM {table} r WHERE id = ANY(%s) ORDER BY id', [ids])
            rows = [json.loads(row) if isinstance(row, str) else row for (row,) in cursor.fetchall()]
    else:
        rows = list(queryset.order_by('pk').values())
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def historical_identity_report(event, round_id, *, include_score_values=False):
    mobile = event.judging_event
    rounds = criteria_rounds(event)
    selected = next((row for row in rounds if row['id'] == round_id), None)
    if selected is None:
        raise ValueError('Select an existing round ID for this event.')
    try:
        scoreable = round_is_scoreable(event, rounds.index(selected))
    except (TypeError, ValueError):
        scoreable = False
    config = event.result_processing_config or {}
    mapping = config.get('mobile_round_mapping') or {}
    definitions = [definition for category in event.scoring_categories.filter(
        purpose='official', assigned_round_id=round_id).prefetch_related('criteria__subcriteria')
        for definition in category.criteria.all()]
    criteria = list(mobile.criteria.prefetch_related('subcriteria').order_by('order', 'pk'))
    candidates = list(mobile.candidates.order_by('number', 'pk'))
    # No inner joins to auth_user: a missing judge FK must not silently hide rows.
    scores = list(JudgeScore.objects.filter(Q(candidate_id__in=[row.pk for row in candidates]) |
        Q(criterion_id__in=[row.pk for row in criteria])).order_by('pk'))
    owners = {user.pk: user for user in get_user_model().objects.filter(pk__in={row.judge_id for row in scores})}
    score_criteria = {row.pk: row for row in Criterion.objects.filter(pk__in={row.criterion_id for row in scores})}
    score_candidates = {row.pk: row for row in Candidate.objects.filter(pk__in={row.candidate_id for row in scores})}
    web_assignees = set(event.assigned_judges.values_list('pk', flat=True))
    mobile_assignees = set(mobile.assigned_judges.values_list('pk', flat=True))
    entries = list(event.criteria_entries.select_related('department', 'source_team', 'source_individual')
                   .prefetch_related('member_links__individual__department').order_by('display_order', 'pk'))
    departments = defaultdict(list)
    for department in Department.objects.filter(name__in={row.department for row in candidates}):
        departments[department.name].append({'id': department.pk, 'name': department.name, 'code': department.code})
    criterion_reports = []
    criterion_map = mapping.get('criteria') or {}
    unique_matches = {}
    for definition in definitions:
        linked = criterion_map.get(f'{round_id}:{definition.pk}')
        matches = [row for row in criteria if row.name in _names(definition) or row.pk == linked]
        comparisons = [_criterion_comparison(definition, row) for row in matches]
        if len(comparisons) == 1 and comparisons[0]['runtime_compatibility_issue'] is None:
            unique_matches[definition.pk] = comparisons[0]
        criterion_reports.append({
            'official_id': definition.pk, 'name': definition.name, 'category_id': definition.category_id,
            'category_name': definition.category.name, 'assigned_round_id': definition.category.assigned_round_id,
            'category_weight_percent': str(definition.category.overall_weight_percent),
            'category_local_weight_percent': str(definition.weight_percent), 'effective_weight_percent': str(effective_weight(definition)),
            'min_score': str(definition.min_score), 'max_score': _text(definition.max_score),
            'category_local_order': definition.display_order, 'children': _children(definition),
            'existing_explicit_mobile_id': linked, 'comparisons': comparisons,
            'identity_verified': False,
        })
    participant_reports = []
    candidate_map = mapping.get('candidates') or {}
    for entry in entries:
        department_name = entry.department.name if entry.department else ''
        linked = candidate_map.get(str(entry.pk))
        matches = [row for row in candidates if row.name == entry.display_name or row.pk == linked]
        members = [{'individual_id': link.individual_id, 'position': link.position,
                    'name': link.individual.name, 'registry_number': link.individual.number,
                    'department_id': link.individual.department_id,
                    'department_name': link.individual.department.name if link.individual.department else None}
                   for link in entry.member_links.all()]
        participant_reports.append({
            'official_entry_id': entry.pk, 'entry_type': entry.entry_type, 'name': entry.display_name,
            'on_current_event_roster': entry.pk in event.participant_ids, 'display_order': entry.display_order,
            'department': {'id': entry.department_id, 'name': department_name,
                           'code': entry.department.code if entry.department else None},
            'source_individual_id': entry.source_individual_id, 'source_team_id': entry.source_team_id,
            'members': members, 'existing_explicit_mobile_id': linked,
            'comparisons': [{'mobile_candidate_id': row.pk, 'exact_name': row.name == entry.display_name,
                'department_exact': row.department == department_name, 'stored_department': row.department,
                'current_department_records_matching_stored_text': departments[row.department],
                'mobile_description': row.description,
                'member_description_exact': bool(members) and row.description == ' + '.join(member['name'] for member in members),
                'legacy_score_ids': [score.pk for score in scores if score.candidate_id == row.pk],
                'judge_ids': sorted({score.judge_id for score in scores if score.candidate_id == row.pk}),
                'identity_verified': False} for row in matches],
            'identity_warning': 'Names and department text are snapshots, not stable pair-member identity proof. No inferred mapping is approved.',
        })
    score_reports, sheets = [], defaultdict(list)
    children = defaultdict(list)
    child_queryset = JudgeSubcriterionScore.objects.filter(judge_score_id__in=[row.pk for row in scores])
    for child in child_queryset.order_by('judge_score_id', 'pk'):
        item = {'id': child.pk, 'subcriterion_id': child.subcriterion_id}
        if include_score_values:
            item['raw_score'] = str(child.score)
        children[child.judge_score_id].append(item)
    for score in scores:
        criterion = score_criteria.get(score.criterion_id)
        candidate = score_candidates.get(score.candidate_id)
        owner = owners.get(score.judge_id)
        item = {'id': score.pk, 'judge_id': score.judge_id, 'owner_exists': owner is not None,
            'owner_currently_active': owner.is_active if owner else None,
            'judge_currently_assigned_web': score.judge_id in web_assignees,
            'judge_currently_assigned_mobile': score.judge_id in mobile_assignees,
            'candidate_id': score.candidate_id, 'candidate_event_id': candidate.event_id if candidate else None,
            'criterion_id': score.criterion_id, 'criterion_event_id': criterion.event_id if criterion else None,
            'submitted_at': _stamp(score.submitted_at), 'is_locked': score.is_locked,
            'has_verification_id': bool(score.verification_id), 'children': children[score.pk],
            'round_id_recorded_on_legacy_row': None}
        if include_score_values:
            coefficient = _coefficient(criterion.max_score, criterion.weight_percent) if criterion else None
            item['raw_score'] = str(score.score)
            item['contribution_using_current_legacy_definition'] = _text(score.score * coefficient) if coefficient is not None and score.score.is_finite() else None
        score_reports.append(item)
        sheets[(score.judge_id, score.candidate_id)].append(score)
    normalized = event.criteria_score_submissions.all().order_by('pk')
    ids = [row.pk for row in scores]
    snapshots = {
        'judge_scores_full_rows_sha256': _row_fingerprint(JudgeScore.objects.filter(pk__in=ids)),
        'judge_subscores_full_rows_sha256': _row_fingerprint(child_queryset),
        'mobile_criteria_full_rows_sha256': _row_fingerprint(mobile.criteria.all()),
        'mobile_subcriteria_full_rows_sha256': _row_fingerprint(CriterionSubcriterion.objects.filter(criterion_id__in=[row.pk for row in criteria])),
        'mobile_candidates_full_rows_sha256': _row_fingerprint(mobile.candidates.all()),
        'normalized_submissions_full_rows_sha256': _row_fingerprint(normalized),
    }
    mapped_ids = [row['mobile_id'] for row in unique_matches.values()]
    selected_orders = [row.order for row in criteria if row.pk in mapped_ids]
    structurally_compatible = bool(definitions) and len(unique_matches) == len(definitions) and len(set(mapped_ids)) == len(mapped_ids) and len(set(selected_orders)) == len(mapped_ids)
    proposed_mapping = {'criteria': dict(criterion_map), 'candidates': dict(candidate_map)}
    proposed_mapping['criteria'].update({f'{round_id}:{key}': row['mobile_id'] for key, row in unique_matches.items()})
    configuration_issues = round_mapping_issues(event, mobile, round_id, mapping=proposed_mapping)
    structurally_compatible = structurally_compatible and not configuration_issues
    reasons = []
    if scores or normalized.exists():
        reasons.append('Saved scores exist: prepare_judge_mobile remains blocked; this inspector cannot approve or apply mappings.')
    if not structurally_compatible:
        reasons.append('One or more official criteria have no unique compatible legacy definition; mappings alone cannot fix a maximum, weight, child-definition or order mismatch.')
    if any(not row['comparisons'] or len(row['comparisons']) != 1 or
           not row['comparisons'][0]['exact_name'] or not row['comparisons'][0]['department_exact'] for row in participant_reports):
        reasons.append('Participant identity is unresolved or has conflicting department snapshots. Signed roster/member evidence is required; matching names are insufficient.')
    if any(not row['owner_exists'] or row['candidate_event_id'] != mobile.pk or row['criterion_event_id'] != mobile.pk for row in score_reports):
        reasons.append('Orphan owners or cross-event score relationships require separate integrity review.')
    reasons.append('JudgeScore stores no round ID or immutable rubric snapshot. Current configuration cannot by itself prove the round, scale or participant identity at submission time.')
    return {
        'read_only': True, 'judging_event_id': mobile.pk, 'portal_event_id': event.pk, 'title': event.name,
        'faculty_account_id': event.faculty_account_id, 'round_id': round_id, 'round': selected,
        'faculty_round_is_scoreable': scoreable, 'event_status': event.status,
        'results_finalized': event.results_finalized, 'current_scoring_method': event.criteria_score_method,
        'current_rounds': rounds, 'current_explicit_mapping': mapping,
        'score_values_included': include_score_values,
        'counts': {'legacy_scores': len(scores), 'normalized_submissions': normalized.count(),
                   'official_round_criteria': len(definitions), 'mobile_criteria': len(criteria), 'mobile_candidates': len(candidates)},
        'mobile_criteria': [{'id': row.pk, 'name': row.name, 'global_order': row.order,
            'max_score': str(row.max_score), 'weight_percent': str(row.weight_percent), 'children': _children(row)} for row in criteria],
        'mobile_candidates': [{'id': row.pk, 'name': row.name, 'department': row.department,
            'number': row.number, 'description': row.description} for row in candidates],
        'official_criterion_comparisons': criterion_reports, 'official_pair_comparisons': participant_reports,
        'legacy_scores': score_reports,
        'legacy_sheets': [{'judge_id': judge, 'candidate_id': candidate, 'score_ids': [row.pk for row in sheet],
            'criterion_ids': sorted(row.criterion_id for row in sheet),
            'submitted_count': sum(row.submitted_at is not None for row in sheet),
            'locked_count': sum(row.is_locked for row in sheet)} for (judge, candidate), sheet in sorted(sheets.items())],
        'normalized_sources': list(normalized.values('pk', 'judge_id', 'participant_id', 'assigned_round_id', 'criterion_id', 'category_id', 'source', 'status', 'is_active', 'submitted_at')),
        'snapshots': snapshots,
        'assessment': {'can_apply': False, 'mapping_only_verified_safe': False,
            'criterion_mapping_structurally_compatible': structurally_compatible,
            'configuration_issues_with_proposed_criterion_ids': configuration_issues,
            'same_raw_scores_preserve_legacy_weighted_contributions': structurally_compatible and all(row['same_raw_score_preserves_weighted_contribution'] for row in unique_matches.values()),
            'reasons': reasons,
            'historical_maximum_and_weight_snapshot_available_on_JudgeScore': False,
            'note': 'All numerical comparisons use current stored definitions, not a certified historical rubric. No numeric transformation or identity override is authorized.'},
    }
