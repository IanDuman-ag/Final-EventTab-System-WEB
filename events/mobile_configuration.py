"""Explicit, reviewed setup of an unscored mobile event. Never called by GET."""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json

from django.db import transaction
from django.db.models import Q

from .criteria_faculty_service import criteria_rounds, round_is_scoreable, round_participant_ids
from .faculty_service import FacultyServiceError
from .mobile_scoring_compatibility import criterion_is_compatible, effective_weight
from .models import Candidate, Criterion, CriterionSubcriterion, CriteriaScoreSubmission, Event, JudgeScore, JudgingEvent
from .subcriteria import subcriteria_issue


class MobileConfigurationError(ValueError):
    pass


def configuration_plan(event):
    """Describe exact reuse/new records without changing data or consuming IDs."""
    mobile = event.judging_event
    plan = {'portal_event_id': event.pk, 'judging_event_id': mobile.pk, 'title': event.name,
            'faculty_account_id': event.faculty_account_id,
            'round_id': None, 'criteria': [], 'participants': [], 'blocked_reasons': []}
    blocked = plan['blocked_reasons']
    legacy_count = JudgeScore.objects.filter(candidate__event=mobile).count()
    normalized_count = CriteriaScoreSubmission.objects.filter(event=event).count()
    plan['existing_score_rows'] = {'legacy': legacy_count, 'normalized': normalized_count}
    if legacy_count or normalized_count:
        blocked.append('This event has saved scores. Setup changes require a separate historical identity review.')
    rounds = criteria_rounds(event)
    try:
        index = int((event.result_processing_config or {}).get('active_stage_index') or 0)
        if not round_is_scoreable(event, index):
            blocked.append('The Faculty-controlled active round is not open and scoreable.')
        selected = rounds[index] if 0 <= index < len(rounds) else None
        eligible = round_participant_ids(event, index) if selected else []
    except (TypeError, ValueError, FacultyServiceError):
        selected, eligible = None, []
        blocked.append('Faculty round state or participant eligibility is invalid.')
    if event.status != Event.STATUS_ACTIVE or event.results_finalized:
        blocked.append('The event must be active and its results must not be finalized.')
    if selected is None:
        blocked.append('There is no valid active round.')
    rid = selected['id'] if selected else None
    plan['round_id'] = rid
    config = deepcopy(event.result_processing_config or {})
    mapping = config.get('mobile_round_mapping') or {}
    criterion_map = mapping.get('criteria') or {}
    candidate_map = mapping.get('candidates') or {}
    stored = list(mobile.criteria.prefetch_related('subcriteria'))
    candidates = list(mobile.candidates.all())
    plan['existing_mobile_criteria'] = [{'id': row.pk, 'name': row.name, 'order': row.order,
        'max_score': str(row.max_score), 'weight_percent': str(row.weight_percent),
        'subcriteria': [{'id': child.pk, 'name': child.name, 'display_order': child.display_order,
                        'max_score': str(child.max_score)} for child in row.subcriteria.all()]} for row in stored]
    plan['existing_mobile_candidates'] = [{'id': row.pk, 'name': row.name, 'number': row.number,
                                          'department': row.department} for row in candidates]
    categories = list(event.scoring_categories.filter(purpose='official', assigned_round_id=rid)
                      .prefetch_related('criteria__subcriteria'))
    if not categories or sum((row.overall_weight_percent for row in categories), Decimal(0)) != 100:
        blocked.append('Official category weights for the active round must total 100%.')
    all_definitions = [definition for category in event.scoring_categories.filter(purpose='official')
                       .prefetch_related('criteria__subcriteria') for definition in category.criteria.all()]
    used_ids, used_orders = set(), set()
    next_order = max((row.order for row in stored), default=0) + 1
    for category in categories:
        definitions = list(category.criteria.all())
        if (category.judge_mode != 'scoring' or not 0 <= category.overall_weight_percent <= 100
                or not definitions or sum((row.weight_percent for row in definitions), Decimal(0)) != 100):
            blocked.append(f'Category "{category.name}" must contain numeric scoring criteria with valid weights totaling 100%.')
        for definition in definitions:
            key = f'{rid}:{definition.pk}'
            label = f'{category.name}: {definition.name}'
            if (not definition.max_score or not 0 <= definition.min_score <= definition.max_score
                    or definition.max_score <= 0 or not 0 <= definition.weight_percent <= 100
                    or subcriteria_issue(definition)):
                blocked.append(f'Criterion "{label}" has an invalid maximum, minimum, weight or subcriterion definition.')
                continue
            matches = [row for row in stored if criterion_is_compatible(row, definition)]
            if key in criterion_map:
                matches = [row for row in stored if row.pk == criterion_map[key] and criterion_is_compatible(row, definition)]
                if len(matches) != 1:
                    blocked.append(f'Criterion "{label}" has a stale or incompatible explicit mapping; it will not be replaced.')
                    continue
            elif len(matches) == 1:
                claims = [item for item in all_definitions if criterion_is_compatible(matches[0], item)]
                if len(claims) != 1 or matches[0].pk in criterion_map.values():
                    blocked.append(f'Criterion "{label}" matches more than one source identity or round; explicit review is required.')
                    continue
            elif len(matches) > 1:
                blocked.append(f'Criterion "{label}" matches multiple existing mobile rows; no duplicate will be created.')
                continue
            else:
                names = {definition.name[:100], label[:100]}
                if any(row.name in names for row in stored):
                    blocked.append(f'Criterion "{label}" has an incompatible existing mobile row; no replacement will be created.')
                    continue
                if any(row.max_score == definition.max_score and any(abs(row.weight_percent - weight) <= Decimal('0.05')
                       for weight in (definition.weight_percent, effective_weight(definition))) for row in stored):
                    blocked.append(f'Criterion "{label}" has a possible existing identity with a different name; review explicit mapping before creating anything.')
                    continue
            row = matches[0] if matches else None
            if row and (row.pk in used_ids or row.order in used_orders
                        or list(criterion_map.values()).count(row.pk) > 1):
                blocked.append(f'Criterion "{label}" would share a mobile ID or global order.')
                continue
            if row and key not in criterion_map and row.pk in criterion_map.values():
                blocked.append(f'Criterion "{label}" is already mapped to another source.')
                continue
            if not row and len(label) > Criterion._meta.get_field('name').max_length:
                blocked.append(f'Criterion "{label}" exceeds the mobile name limit; no truncated identity will be created.')
                continue
            plan['criteria'].append({'source_id': definition.pk, 'mapping_key': key,
                'category_id': category.pk, 'category_name': category.name,
                'official_weight_percent': str(definition.weight_percent),
                'category_weight_percent': str(category.overall_weight_percent), 'min_score': str(definition.min_score),
                'mobile_id': row.pk if row else None, 'action': 'reuse' if row else 'create',
                'name': row.name if row else label, 'order': row.order if row else next_order,
                'max_score': str(definition.max_score),
                'weight_percent': str(row.weight_percent if row else effective_weight(definition).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)),
                'description': definition.judging_description[:200],
                'subcriteria': [{'source_id': child.pk, 'name': child.name, 'max_score': str(child.max_score), 'display_order': child.display_order}
                                for child in definition.subcriteria.all()]})
            if row:
                used_ids.add(row.pk)
                used_orders.add(row.order)
            else:
                used_orders.add(next_order)
                next_order += 1
    entries = list(event.criteria_entries.filter(pk__in=eligible).select_related('department'))
    names = {(row.display_name, row.department.name if row.department else '') for row in entries}
    unmatched_candidates = [row for row in candidates if (row.name, row.department) not in names
                            and row.pk not in candidate_map.values()]
    if not eligible or {row.pk for row in entries} != set(eligible):
        blocked.append('Eligible participants must reference existing canonical entries in this event.')
    used_candidates = set()
    next_number = max((row.number for row in candidates), default=0) + 1
    for entry in entries:
        key = str(entry.pk)
        department = entry.department.name if entry.department else ''
        matches = [row for row in candidates if row.name == entry.display_name and row.department == department]
        if key in candidate_map:
            matches = [row for row in candidates if row.pk == candidate_map[key]]
            if len(matches) != 1:
                blocked.append(f'Participant entry {entry.pk} has a stale explicit mapping; it will not be replaced.')
                continue
        elif (len(matches) > 1 or len([row for row in entries if row.display_name == entry.display_name
                                      and row.department_id == entry.department_id]) != 1):
            blocked.append(f'Participant entry {entry.pk} has an ambiguous existing identity; no duplicate will be created.')
            continue
        elif not matches and any(row.name == entry.display_name for row in candidates):
            blocked.append(f'Participant entry {entry.pk} has an existing name with a different department; review identity.')
            continue
        row = matches[0] if matches else None
        if row is None and unmatched_candidates:
            blocked.append(f'Participant entry {entry.pk} has unmatched existing mobile candidates; review identity before creating anything.')
            continue
        if row and (row.pk in used_candidates or (row.pk in candidate_map.values() and key not in candidate_map)
                    or list(candidate_map.values()).count(row.pk) > 1):
            blocked.append(f'Participant entry {entry.pk} would share an existing candidate mapping.')
            continue
        if not row and (len(entry.display_name) > 200 or len(department) > 150):
            blocked.append(f'Participant entry {entry.pk} exceeds mobile field limits; no truncated identity will be created.')
            continue
        plan['participants'].append({'entry_id': entry.pk, 'mobile_id': row.pk if row else None,
            'entry_type': entry.entry_type,
            'action': 'reuse' if row else 'create', 'name': row.name if row else entry.display_name,
            'department': row.department if row else department, 'number': row.number if row else next_number})
        if row:
            used_candidates.add(row.pk)
        else:
            next_number += 1
    plan['can_apply'] = not blocked
    # Fingerprint binds the reviewed plan to round state, mapping and eligibility.
    state = {'plan': plan, 'rounds': event.rounds_config, 'config': config, 'roster': event.participant_ids,
             'faculty_id': event.faculty_account_id, 'participation_type': event.participation_type,
             'scoring_method': event.criteria_score_method}
    plan['fingerprint'] = hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()
    return plan


@transaction.atomic
def apply_configuration(judging_event_id, actor, fingerprint):
    mobile = JudgingEvent.objects.select_for_update().get(pk=judging_event_id)
    event = Event.objects.select_for_update().get(judging_event=mobile, scoring_method='criteria')
    faculty = event.faculty_account_id == actor.pk and (actor.is_staff or actor.groups.filter(
        Q(name__iexact='Faculty') | Q(name__iexact='Faculty In-Charge')).exists())
    if not actor.is_active or not (actor.is_superuser or faculty):
        raise MobileConfigurationError('Only the active assigned Faculty or an active superuser may apply setup.')
    plan = configuration_plan(event)
    if plan['fingerprint'] != fingerprint:
        raise MobileConfigurationError('The setup changed since preview. Generate and review a fresh plan.')
    if not plan['can_apply']:
        raise MobileConfigurationError('The plan is blocked; no records were changed.')
    config = deepcopy(event.result_processing_config or {})
    mapping = config['mobile_round_mapping'] = deepcopy(config.get('mobile_round_mapping') or {})
    criteria_map = mapping['criteria'] = dict(mapping.get('criteria') or {})
    candidates_map = mapping['candidates'] = dict(mapping.get('candidates') or {})
    created = {'criteria': [], 'candidates': []}
    for item in plan['criteria']:
        mobile_id = item['mobile_id']
        if item['action'] == 'create':
            criterion = Criterion.objects.create(event=mobile, name=item['name'], description=item['description'],
                order=item['order'], max_score=item['max_score'], weight_percent=item['weight_percent'])
            for child in item['subcriteria']:
                CriterionSubcriterion.objects.create(criterion=criterion,
                    **{key: child[key] for key in ('name', 'max_score', 'display_order')})
            mobile_id = criterion.pk
            created['criteria'].append(mobile_id)
        criteria_map[item['mapping_key']] = mobile_id
    for item in plan['participants']:
        mobile_id = item['mobile_id']
        if item['action'] == 'create':
            candidate = Candidate.objects.create(event=mobile, name=item['name'], department=item['department'], number=item['number'])
            mobile_id = candidate.pk
            created['candidates'].append(mobile_id)
        candidates_map[str(item['entry_id'])] = mobile_id
    event.result_processing_config = config
    event.save(update_fields=['result_processing_config'])
    from .criteria_mobile_rounds import read_mobile_round_context
    context = read_mobile_round_context(mobile)
    if not context or not context['compatible'] or context['round_id'] != plan['round_id']:
        raise MobileConfigurationError('Post-setup validation failed; the complete transaction was rolled back.')
    return {'applied': True, 'actor_id': actor.pk, 'judging_event_id': mobile.pk,
            'portal_event_id': event.pk, 'round_id': plan['round_id'], 'created': created}
