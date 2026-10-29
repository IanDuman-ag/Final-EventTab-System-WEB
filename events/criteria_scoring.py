"""Server-side calculations for normalized criteria submissions."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from .models import CriteriaEventEntry, CriteriaScoreSubmission, RegistryCandidate, Team


def combine_round_results(event, results, round_index):
    """Combine finalist scores on a 0–100 scale; never include award segments.

    Numerical rounds must use percentage scoring. Ordinal rounds are converted
    to 100 * (group size - mean rank) / (group size - 1), or 100 for one entry.
    Cumulative results are the arithmetic mean of these normalized round scores.
    Only finalists receive final placements; eliminated entries retain scores.
    """
    from .criteria_faculty_service import criteria_rounds, round_participant_ids
    from .divisions import group_participants, participant_division, rank_divisions
    method = (event.result_processing_config or {}).get('final_result_basis')
    rounds = criteria_rounds(event)
    if method not in {'weighted_rounds', 'cumulative_rounds'} or round_index != len(rounds) - 1:
        return results
    snapshots = (event.result_processing_config or {}).get('confirmed_stages') or {}
    values = defaultdict(list)
    for index, stage in enumerate(rounds):
        rows = results if index == round_index else snapshots.get(str(index), {}).get('results', [])
        by_id = {row['participant_id']: row for row in rows}
        categories = list(event.scoring_categories.filter(assigned_round_id=stage['id'], purpose='official'))
        ranking = bool(categories) and all(category.judge_mode == 'ranking' for category in categories)
        groups = group_participants(event, round_participant_ids(event, index))
        weight = Decimal(str(stage.get('weight', 0))) if method == 'weighted_rounds' else Decimal(1)
        if not weight.is_finite() or weight < 0:
            raise ValueError('Round weights must be finite and nonnegative.')
        for finalist in results:
            participant_id = finalist['participant_id']
            if participant_id not in by_id:
                raise ValueError('Every finalist requires an approved score from each previous round.')
            row = by_id[participant_id]
            value = Decimal(str(row.get('round_score', row['final_score'])))
            if ranking:
                size = len(groups[participant_division(event, participant_id)])
                value = Decimal(100) if size == 1 else 100 * (size - value) / (size - 1)
            elif event.criteria_score_method != 'weighted_percentage':
                raise ValueError('Combining rounds requires normalized weighted-percentage scoring.')
            if not value.is_finite():
                raise ValueError('Round scores must be finite.')
            values[participant_id].append((value, weight))
    total_weight = sum((weight for _, weight in next(iter(values.values()), [])), Decimal(0))
    if total_weight <= 0 or (method == 'weighted_rounds' and total_weight != 100):
        raise ValueError('Round weights must total 100%.')
    combined = []
    for original in results:
        row = dict(original)
        row['round_score'] = original['final_score']
        row['final_score'] = float(sum((value * weight for value, weight in values[row['participant_id']]), Decimal(0)) / total_weight)
        combined.append(row)
    combined.sort(key=lambda row: -row['final_score'])
    prior, rank = None, 0
    for position, row in enumerate(combined, 1):
        if row['final_score'] != prior:
            rank = position
        prior = row['final_score']
        row['rank'] = rank
    return rank_divisions(event, combined)


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


def compute_official_criteria_rankings(event, *, round_id, accepted_statuses=None, participant_ids=None, configured=False):
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
    if configured and all(row.judge_mode == 'ranking' for row in categories) and (event.result_processing_config or {}).get('ranking_aggregation') not in {'mean_rank', 'weighted_mean_rank'}:
        raise ValueError('The event needs an explicit authorized ranking aggregation rule.')
    if sum((row.overall_weight_percent for row in categories), Decimal(0)) != 100:
        raise ValueError('Official category weights must total 100% within the round.')
    expected = {}
    for category in categories:
        criteria = list(category.criteria.all())
        if not criteria or sum((row.weight_percent for row in criteria), Decimal(0)) != 100:
            raise ValueError(f'Criteria in "{category.name}" must total 100%.')
        expected[category.id] = {row.id for row in criteria}

    roster = event.participant_ids if participant_ids is None else participant_ids
    from .criteria_faculty_service import criteria_rounds
    from .participant_removal_service import excluded_participant_ids
    index = next((index for index, row in enumerate(criteria_rounds(event)) if row['id'] == round_id), None)
    excluded = excluded_participant_ids(event, index) if index is not None else set()
    if excluded:
        roster = [participant_id for participant_id in roster if participant_id not in excluded]
    participants = {row.id: row for row in CriteriaEventEntry.objects.filter(event=event, id__in=roster)}
    if not participants:  # legacy fallback before migration
        registry = RegistryCandidate if event.participation_type == 'individual' else Team
        participants = {row.id: row for row in registry.objects.filter(id__in=roster, status=registry.STATUS_ACTIVE)}
    judge_ids = set(event.assigned_judges.filter(is_active=True).values_list('id', flat=True))
    scores = CriteriaScoreSubmission.objects.filter(
        event=event, assigned_round_id=round_id, category__in=categories,
        judge_id__in=judge_ids, participant_id__in=participants,
        status__in=accepted_statuses or [CriteriaScoreSubmission.STATUS_APPROVED], is_active=True,
    ).select_related('criterion', 'category')
    from .divisions import group_participants, participant_division
    division_groups = group_participants(event, participants)
    rank_groups = group_participants(event, event.participant_ids if excluded else participants)
    sheets = defaultdict(dict)
    for row in scores:
        if row.criterion.category_id != row.category_id:
            raise ValueError('Score criterion does not belong to the submission category.')
        ranking = configured and row.category.judge_mode == 'ranking'
        rank_limit = len(rank_groups[participant_division(event, row.participant_id)])
        if ranking and (row.raw_score != row.raw_score.to_integral_value() or not 1 <= row.raw_score <= rank_limit):
            raise ValueError('Ranking submissions must contain valid ordinal participant ranks.')
        if not ranking and row.raw_score < row.criterion.min_score:
            raise ValueError('Score is below the criterion minimum.')
        sheets[(row.participant_id, row.judge_id)][row.criterion_id] = row

    totals = defaultdict(list)
    details = defaultdict(list)
    breakdowns = defaultdict(lambda: defaultdict(list))
    required = set().union(*expected.values())
    for (participant_id, judge_id), sheet in sheets.items():
        if set(sheet) != required:
            continue
        total = Decimal(0)
        for category in categories:
            if configured and category.judge_mode == 'ranking':
                if (event.result_processing_config or {}).get('ranking_aggregation') == 'mean_rank':
                    subtotal = sum((sheet[key].raw_score for key in expected[category.id]), Decimal(0)) / len(expected[category.id])
                else:
                    subtotal = sum((sheet[key].raw_score * sheet[key].criterion.weight_percent / 100 for key in expected[category.id]), Decimal(0))
            else:
                subtotal = sum((
                normalized_criterion_score(
                    sheet[key].raw_score, sheet[key].criterion.max_score,
                    sheet[key].criterion.weight_percent,
                ) for key in expected[category.id]
                ), Decimal(0))
            total += subtotal * category.overall_weight_percent / 100
        if configured and event.criteria_score_method in {'raw_score', 'average_score'}:
            total = sum((row.raw_score for row in sheet.values()), Decimal(0))
            if event.criteria_score_method == 'average_score':
                total /= len(sheet)
        details[participant_id].append({'judge_id': judge_id, 'total': total})
        for key, score in sheet.items():
            breakdowns[participant_id][key].append(score.raw_score)
        totals[participant_id].append(total)

    rows = []
    for participant_id, values in totals.items():
        if configured and (event.judge_settings or {}).get('remove_high_low'):
            if len(values) < 3:
                raise ValueError('At least three complete judges are needed to remove highest and lowest scores.')
            values = sorted(values)[1:-1]
        participant = participants[participant_id]
        rows.append({
            'participant_id': participant_id,
            'name': getattr(participant, 'display_name', None) or participant.name,
            'final_score': sum(values, Decimal(0)) / len(values),
            'approved_judges': len(details[participant_id]),
            'complete': bool(judge_ids) and len(values) == len(judge_ids),
            'judge_scores': details[participant_id],
            'criteria_breakdown': [
                {'criterion_id': key, 'score': sum(vals, Decimal(0)) / len(vals)}
                for key, vals in breakdowns[participant_id].items()
            ],
        })
        rows[-1]['complete'] = bool(judge_ids) and len(details[participant_id]) == len(judge_ids)
        if configured and event.deductions_enabled:
            deduction = Decimal(0)
            for rule in event.deductions_config or []:
                value = Decimal(str(rule.get('value') or 0))
                if not value.is_finite() or value < 0:
                    raise ValueError('Deductions must be finite nonnegative values.')
                deduction += rows[-1]['final_score'] * value / 100 if rule.get('deduction_type') == 'percentage' else value
            rows[-1]['final_score'] = max(Decimal(0), rows[-1]['final_score'] - deduction)
            rows[-1]['deduction'] = deduction
    ranking_mode = configured and all(row.judge_mode == 'ranking' for row in categories)
    if ranking_mode:
        for judge_id in judge_ids:
            for criterion_id in required:
                for members in division_groups.values():
                    ranks = [sheet[criterion_id].raw_score for (participant_id, sheet_judge), sheet in sheets.items()
                             if participant_id in members and sheet_judge == judge_id and criterion_id in sheet]
                    # Exclusions retain original ordinal scores; only gaps are allowed.
                    if len(ranks) == len(members) and (len(set(ranks)) != len(ranks) or
                            (not excluded and set(ranks) != {Decimal(value) for value in range(1, len(members) + 1)})):
                        raise ValueError('Each ranking criterion must assign distinct ordinal ranks within each division.')
    def ordering(row):
        keys = [row['final_score'] if ranking_mode else -row['final_score']]
        if configured:
            for rule in event.tie_break_rules or []:
                method = rule.get('method')
                if method == 'highest_selected_criterion':
                    selected = str(rule.get('criterion_id') or '')
                    configured_name = next((item.get('name') for item in event.judging_criteria_config or [] if str(item.get('id')) == selected), None)
                    criterion_by_id = {criterion.pk: criterion for category in categories for criterion in category.criteria.all()}
                    match = next((item for item in row['criteria_breakdown'] if str(item['criterion_id']) == selected or (configured_name and criterion_by_id[item['criterion_id']].name == configured_name)), None)
                    if match:
                        keys.append(match['score'] if ranking_mode else -match['score'])
                elif method == 'highest_chief_judge':
                    match = next((item for item in row['judge_scores'] if item['judge_id'] == event.chief_judge_id), None)
                    keys.append((match['total'] if ranking_mode else -match['total']) if match else Decimal(0))
        return tuple(keys)
    rows.sort(key=lambda row: (*ordering(row), row['participant_id']))
    prior = None
    rank = 0
    for index, row in enumerate(rows, 1):
        comparison = ordering(row)
        if comparison != prior:
            rank = index
        prior = comparison
        row['rank'] = rank
    from .divisions import rank_divisions
    return rank_divisions(event, rows)
