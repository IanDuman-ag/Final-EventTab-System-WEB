"""Optional detailed criteria: validation and score aggregation.

Parent score rows remain the compatibility cache for existing weighted scoring.
Child scores are the source of truth whenever a criterion has subcriteria.
"""
import json
from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import (CriteriaSubcriterionScore, EventScoringSubcriterion,
                     JudgeSubcriterionScore)


def subcriteria_data(criterion):
    return [{
        'id': child.id, 'name': child.name, 'max_score': float(child.max_score),
        'display_order': child.display_order,
    } for child in criterion.subcriteria.all()]


def subcriteria_issue(criterion):
    children = list(criterion.subcriteria.all())
    if not children:
        return None
    total = sum((child.max_score for child in children), Decimal('0'))
    maximum = criterion.max_score
    if maximum is None or any(not child.name.strip() or child.max_score <= 0 for child in children) or total != maximum:
        return (f'"{criterion.name}" subcriteria scores must total {maximum:g} points '
                f'(currently {total:g}).') if maximum is not None else f'"{criterion.name}" needs a maximum score.'
    return None


def parse_subcriteria(raw):
    try:
        rows = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError('Subcriteria must be a valid list.') from exc
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError('Provide no more than 100 subcriteria.')
    parsed = []
    seen_ids = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError('Each subcriterion must have a name and maximum score.')
        try:
            maximum = Decimal(str(row.get('max_score') or '0'))
            child_id = int(row['id']) if row.get('id') else None
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError('Subcriterion maximum scores must be numeric.') from exc
        if not maximum.is_finite() or maximum < 0 or maximum > Decimal('99999.99') or maximum.as_tuple().exponent < -2:
            raise ValueError('Subcriterion maximum scores must be nonnegative values with up to two decimal places.')
        if child_id and child_id in seen_ids:
            raise ValueError('A subcriterion appears more than once.')
        seen_ids.add(child_id)
        parsed.append({'id': child_id, 'name': str(row.get('name') or '').strip()[:120],
                       'max_score': maximum, 'display_order': index})
    return parsed


@transaction.atomic
def save_subcriteria(criterion, rows):
    """Save child definitions while retaining IDs and protecting scored rows."""
    existing = {row.id: row for row in criterion.subcriteria.select_for_update()}
    supplied_ids = {row['id'] for row in rows if row['id']}
    if supplied_ids - existing.keys():
        raise ValueError('A subcriterion does not belong to this criterion.')
    for old in existing.values():
        if old.id not in supplied_ids and old.scores.exists():
            raise ValueError(f'"{old.name}" has saved scores and cannot be removed.')
    for row in rows:
        old = existing.get(row['id'])
        if old and old.scores.exists() and (old.max_score != row['max_score'] or old.name != row['name']):
            raise ValueError(f'"{old.name}" has saved scores and cannot be changed.')
    # Unique parent/order constraints require temporary positions during a reorder.
    for old in existing.values():
        old.display_order += 1000
        old.save(update_fields=['display_order'])
    for old in existing.values():
        if old.id not in supplied_ids:
            old.delete()
    for row in rows:
        old = existing.get(row['id'])
        if old:
            old.name, old.max_score, old.display_order = row['name'], row['max_score'], row['display_order']
            old.save(update_fields=['name', 'max_score', 'display_order', 'updated_at'])
        else:
            EventScoringSubcriterion.objects.create(criterion=criterion, name=row['name'],
                                                    max_score=row['max_score'], display_order=row['display_order'])


def validated_child_scores(criterion, items):
    """Return child/score pairs and raw parent subtotal from a complete payload."""
    children = list(criterion.subcriteria.all())
    if not children:
        raise ValueError('This criterion has no subcriteria.')
    if subcriteria_issue(criterion):
        raise ValueError(subcriteria_issue(criterion))
    if not isinstance(items, list):
        raise ValueError('Provide a score for every subcriterion.')
    by_id = {child.id: child for child in children}
    result = []
    seen = set()
    for item in items:
        try:
            child_id = int(item['subcriterion_id'])
            score = Decimal(str(item['score']))
        except (TypeError, KeyError, ValueError, InvalidOperation) as exc:
            raise ValueError('Each subcriterion needs a valid numeric score.') from exc
        child = by_id.get(child_id)
        if not child or child_id in seen or not score.is_finite() or score < 0 or score > child.max_score:
            raise ValueError('Subcriterion score is invalid or exceeds its maximum.')
        seen.add(child_id)
        result.append((child, score))
    if seen != set(by_id):
        raise ValueError('Provide a score for every subcriterion.')
    return result, sum((score for _, score in result), Decimal('0'))


@transaction.atomic
def save_submission_subscores(submission, items):
    pairs, subtotal = validated_child_scores(submission.criterion, items)
    for child, score in pairs:
        CriteriaSubcriterionScore.objects.update_or_create(
            submission=submission, subcriterion=child, defaults={'raw_score': score})
    submission.raw_score = subtotal
    from .criteria_scoring import normalized_criterion_score
    submission.normalized_score = normalized_criterion_score(
        subtotal, submission.criterion.max_score, submission.criterion.weight_percent)
    submission.save(update_fields=['raw_score', 'normalized_score', 'updated_at'])
    return subtotal


@transaction.atomic
def save_judge_subscores(judge_score, items):
    pairs, subtotal = validated_child_scores(judge_score.criterion, items)
    for child, score in pairs:
        JudgeSubcriterionScore.objects.update_or_create(
            judge_score=judge_score, subcriterion=child, defaults={'score': score})
    judge_score.score = subtotal
    judge_score.save(update_fields=['score'])
    return subtotal
