"""Read-only compatibility checks; legacy definitions and scores stay untouched."""
from decimal import Decimal, InvalidOperation

from .models import CriteriaScoreSubmission
from .subcriteria import subcriteria_issue


def read_mobile_mapping(portal, judging_event):
    """Resolve existing Admin-synced rows only; never provision or repair records.

    Explicit mappings always win, including stale mappings (which fail validation).
    Missing identities require a unique round-scoped Admin snapshot and exact
    stored name/order/max/weight. Ambiguous identities remain configuration errors.
    """
    saved = (portal.result_processing_config or {}).get('mobile_round_mapping') or {}
    criteria = dict(saved.get('criteria') or {})
    candidates = dict(saved.get('candidates') or {})
    definitions = list(portal.scoring_categories.filter(purpose='official').prefetch_related('criteria__subcriteria'))
    round_ids = {category.assigned_round_id for category in definitions}
    snapshots = portal.judging_criteria_config or []
    stored = list(judging_event.criteria.prefetch_related('subcriteria'))
    resolved = bool(criteria)
    for category in definitions:
        for definition in category.criteria.all():
            key = f'{category.assigned_round_id}:{definition.pk}'
            if key in criteria:
                continue
            sources = [row for row in snapshots if isinstance(row, dict)
                and (str(row.get('round_id')) == category.assigned_round_id
                     or (not row.get('round_id') and len(round_ids) == 1))
                and (str(row.get('id')) == f'esc_{definition.pk}'
                     or row.get('name') in (definition.name, f'{category.name}: {definition.name}'))]
            if len(sources) != 1:
                continue
            source = sources[0]
            try:
                maximum = Decimal(str(source.get('max_score')))
                weight = Decimal(str(source.get('weight', source.get('weight_percent'))))
                order = int(source['order'])
                if (not maximum.is_finite() or not weight.is_finite()
                        or Decimal(str(source['order'])) != order):
                    continue
            except (InvalidOperation, ValueError, TypeError, KeyError):
                continue
            matches = [row for row in stored if row.name == str(source.get('name') or '')[:100]
                and row.order == order and row.max_score == maximum
                and abs(row.weight_percent - weight) <= Decimal('0.05')
                and criterion_is_compatible(row, definition)]
            # Two definitions cannot independently claim the same stored row.
            peers = [item for cat in definitions for item in cat.criteria.all()
                     if item.name == definition.name and cat.assigned_round_id == category.assigned_round_id]
            if len(matches) == 1 and (str(source.get('id')) == f'esc_{definition.pk}' or len(peers) == 1):
                criteria[key] = matches[0].pk
                resolved = True
    if resolved:
        entries = list(portal.criteria_entries.select_related('department'))
        mobile = list(judging_event.candidates.all())
        for entry in entries:
            if str(entry.pk) in candidates:
                continue
            department = entry.department.name if entry.department else ''
            peers = [row for row in entries if row.display_name == entry.display_name
                     and row.department_id == entry.department_id]
            matches = [row for row in mobile if row.name == entry.display_name and row.department == department]
            if len(peers) == len(matches) == 1 and matches[0].pk not in candidates.values():
                candidates[str(entry.pk)] = matches[0].pk
    return {'criteria': criteria, 'candidates': candidates}


def effective_weight(definition):
    return definition.category.overall_weight_percent * definition.weight_percent / Decimal('100')


def criterion_compatibility_issue(mobile, definition):
    if mobile is None:
        return 'No unique existing mobile criterion is mapped.'
    if not definition.max_score or definition.max_score <= 0:
        return 'The official criterion maximum must be positive.'
    if mobile.max_score != definition.max_score:
        return f'Mobile maximum {mobile.max_score} differs from official maximum {definition.max_score}.'
    if definition.category.judge_mode != 'scoring' or not 0 <= definition.min_score <= definition.max_score:
        return 'The official scoring mode or minimum score is invalid for this mobile form.'
    child_issue = subcriteria_issue(definition)
    if child_issue:
        return child_issue
    # Older Web sync stored category-local percentages; the newer mobile bridge
    # stored effective percentages. Recognize these two forms, never rescale data.
    if not any(abs(mobile.weight_percent - weight) <= Decimal('0.05')
               for weight in (definition.weight_percent, effective_weight(definition))):
        return f'Mobile weight {mobile.weight_percent} matches neither category-local weight {definition.weight_percent} nor effective weight {effective_weight(definition)}.'
    names = {definition.name[:100], f'{definition.category.name}: {definition.name}'[:100]}
    for row in definition.category.event.judging_criteria_config or []:
        if ((str(row.get('id')) == f'esc_{definition.pk}'
             or row.get('name') in (definition.name, f'{definition.category.name}: {definition.name}'))
                and (str(row.get('round_id')) == definition.category.assigned_round_id
                     or not row.get('round_id'))):
            names.add(str(row.get('name') or '')[:100])
    if mobile.name not in names:
        return 'The mobile criterion name does not match the official definition or Admin snapshot.'
    legacy = [(row.display_order, row.name, row.max_score) for row in mobile.subcriteria.all()]
    official = [(row.display_order, row.name, row.max_score) for row in definition.subcriteria.all()]
    # Parent order is global in legacy and local to a category in normalized
    # definitions; it is deliberately not used as an identity check.
    return None if legacy == official else 'Mobile subcriterion names, order or maximum scores differ from the official definition.'


def criterion_is_compatible(mobile, definition):
    return criterion_compatibility_issue(mobile, definition) is None


def round_mapping_issues(portal, judging_event, round_id, mapping=None):
    categories = list(portal.scoring_categories.filter(assigned_round_id=round_id, purpose='official')
                      .prefetch_related('criteria__subcriteria'))
    issues = []
    if not categories:
        return ['No official scoring categories are configured for this round.']
    if sum((row.overall_weight_percent for row in categories), Decimal(0)) != 100:
        issues.append('Official category weights for this round do not total 100%.')
    mapping = mapping if mapping is not None else read_mobile_mapping(portal, judging_event)
    ids = mapping.get('criteria') or {}
    mobile = {row.pk: row for row in judging_event.criteria.prefetch_related('subcriteria')}
    seen_orders = set()
    for category in categories:
        definitions = list(category.criteria.all())
        if (not definitions or not 0 <= category.overall_weight_percent <= 100
                or sum((row.weight_percent for row in definitions), Decimal(0)) != 100):
            issues.append(f'Category "{category.name}" has missing criteria or invalid weights; criterion weights must total 100%.')
        if category.judge_mode != 'scoring':
            issues.append(f'Category "{category.name}" uses ranking mode, which this mobile scoresheet does not support.')
        for definition in definitions:
            mobile_id = ids.get(f'{round_id}:{definition.pk}')
            stored = mobile.get(mobile_id)
            issue = criterion_compatibility_issue(stored, definition)
            if not 0 <= definition.weight_percent <= 100:
                issue = 'The official criterion weight is outside 0–100%.'
            elif stored and list(ids.values()).count(mobile_id) != 1:
                issue = 'The mobile criterion ID is shared by multiple official definitions or rounds.'
            elif stored and stored.order in seen_orders:
                issue = 'The global mobile criterion order is duplicated within this round.'
            if issue:
                issues.append(f'Criterion "{category.name}: {definition.name}" has an incompatible mobile mapping: {issue}')
            if stored:
                seen_orders.add(stored.order)
    return issues


def round_mapping_is_compatible(portal, judging_event, round_id):
    return not round_mapping_issues(portal, judging_event, round_id)


def legacy_score_is_submitted(score):
    """Timestamped legacy cells are protected unless explicitly returned by Faculty.

    Unlocking alone is insufficient evidence. A normalized REOPENED source must
    match the exact mapped round, participant, criterion and original cell.
    """
    if score.is_locked:
        return True
    if score.submitted_at is None:
        return False
    portal = getattr(score.candidate.event, 'portal_event', None)
    if portal is None or portal.scoring_method != 'criteria' or not portal.scoring_categories.exists():
        # Unlinked/legacy-only workflows retain their existing lock semantics.
        # The additional timestamp protection belongs to normalized event bridging.
        return False
    mapping = read_mobile_mapping(portal, score.candidate.event)
    entries = [key for key, value in (mapping.get('candidates') or {}).items() if value == score.candidate_id]
    definitions = [key for key, value in (mapping.get('criteria') or {}).items() if value == score.criterion_id]
    if len(entries) != 1 or len(definitions) != 1:
        return True
    try:
        rid, definition_id = definitions[0].rsplit(':', 1)
        participant_id, definition_id = int(entries[0]), int(definition_id)
    except (ValueError, TypeError):
        return True
    return not CriteriaScoreSubmission.objects.filter(
        event=portal, assigned_round_id=rid, participant_id=participant_id,
        criterion_id=definition_id, judge_id=score.judge_id, source='MOBILE',
        status='REOPENED', is_active=False, raw_score=score.score, submitted_at=score.submitted_at,
    ).exists()
