"""Server-side calculations for normalized criteria submissions."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from .models import CriteriaScoreSubmission, RegistryCandidate, Team


def normalized_criterion_score(raw_score, maximum_score, criterion_weight):
    try:
        raw, maximum, weight = (
            Decimal(str(value)) for value in (raw_score, maximum_score, criterion_weight)
        )
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError('Score, maximum, and weight must be numeric.') from exc
    if not all(value.is_finite() for value in (raw, maximum, weight)):
        raise ValueError('Score, maximum, and weight must be finite.')
    if maximum <= 0 or raw < 0 or raw > maximum or weight < 0 or weight > 100:
        raise ValueError('Score or criterion weight is outside its allowed range.')
    return raw / maximum * weight


def compute_official_criteria_rankings(event, *, round_id):
    """Calculate one round; incomplete judge sheets do not contribute.

    Legacy scores stay on the legacy calculation path. In particular, an empty
    approved set must never fall back to unapproved or previously locked scores.
    """
    round_id = str(round_id)
    categories = list(event.scoring_categories.filter(
        assigned_round_id=round_id, purpose='official',
    ).prefetch_related('criteria'))
    if not categories:
        return []
    if sum((row.overall_weight_percent for row in categories), Decimal(0)) != 100:
        raise ValueError('Official category weights must total 100% within the round.')
    expected = {}
    for category in categories:
        criteria = list(category.criteria.all())
        if not criteria or sum((row.weight_percent for row in criteria), Decimal(0)) != 100:
            raise ValueError(f'Criteria in "{category.name}" must total 100%.')
        expected[category.id] = {row.id for row in criteria}

    registry = RegistryCandidate if event.participation_type == 'individual' else Team
    participants = {
        row.id: row for row in registry.objects.filter(
            id__in=event.participant_ids, status=registry.STATUS_ACTIVE,
        )
    }
    judge_ids = set(event.assigned_judges.filter(is_active=True).values_list('id', flat=True))
    scores = CriteriaScoreSubmission.objects.filter(
        event=event, assigned_round_id=round_id, category__in=categories,
        judge_id__in=judge_ids, participant_id__in=participants,
        status=CriteriaScoreSubmission.STATUS_APPROVED, is_active=True,
    ).select_related('criterion', 'category')
    sheets = defaultdict(dict)
    for row in scores:
        if row.criterion.category_id != row.category_id:
            raise ValueError('Score criterion does not belong to the submission category.')
        if row.raw_score < row.criterion.min_score:
            raise ValueError('Score is below the criterion minimum.')
        sheets[(row.participant_id, row.judge_id)][row.criterion_id] = row

    totals = defaultdict(list)
    required = set().union(*expected.values())
    for (participant_id, judge_id), sheet in sheets.items():
        if set(sheet) != required:
            continue
        total = Decimal(0)
        for category in categories:
            subtotal = sum((
                normalized_criterion_score(
                    sheet[key].raw_score, sheet[key].criterion.max_score,
                    sheet[key].criterion.weight_percent,
                ) for key in expected[category.id]
            ), Decimal(0))
            total += subtotal * category.overall_weight_percent / 100
        totals[participant_id].append(total)

    rows = []
    for participant_id, values in totals.items():
        participant = participants[participant_id]
        rows.append({
            'participant_id': participant_id,
            'name': participant.name,
            'final_score': sum(values, Decimal(0)) / len(values),
            'approved_judges': len(values),
            'complete': bool(judge_ids) and len(values) == len(judge_ids),
        })
    rows.sort(key=lambda row: (-row['final_score'], row['participant_id']))
    prior = None
    rank = 0
    for index, row in enumerate(rows, 1):
        if row['final_score'] != prior:
            rank = index
        prior = row['final_score']
        row['rank'] = rank
    return rows
