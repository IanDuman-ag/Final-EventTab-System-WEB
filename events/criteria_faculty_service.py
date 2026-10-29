"""Faculty review and explicitly unlocked rounds using existing event metadata."""
import hashlib
import json
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .criteria_scoring import compute_official_criteria_rankings, normalized_criterion_score
from .faculty_service import FacultyServiceError
from .models import CriteriaEventEntry, CriteriaScoreSubmission, Event, JudgeScore, RegistryCandidate, Team


FINALIZED = ('SUBMITTED', 'VERIFIED', 'APPROVED')


def criteria_rounds(event):
    rows = [dict(row) for row in event.rounds_config or [] if isinstance(row, dict)]
    if not rows:
        known_ids = list(event.scoring_categories.filter(purpose='official').values_list('assigned_round_id', flat=True).distinct())
        default_id = known_ids[0] if len(known_ids) == 1 else 'main'
        rows = [{'id': default_id, 'name': 'Main Round', 'status': 'open', 'is_final': True}]
    for index, row in enumerate(rows):
        row['id'] = str(row['id'] if 'id' in row else row.get('stage_number') or index + 1)
        row['is_final'] = index == len(rows) - 1
    return rows


def round_participant_ids(event, round_index):
    rows = criteria_rounds(event)
    if round_index < 0 or round_index >= len(rows):
        raise FacultyServiceError('Round index is invalid.')
    cfg = event.result_processing_config or {}
    ids = event.participant_ids if round_index == 0 else cfg.get('qualified_participant_ids', {}).get(str(round_index), (cfg.get('confirmed_stages') or {}).get(str(round_index - 1), {}).get('qualifier_ids', []))
    try:
        cleaned = [int(value) for value in ids]
    except (TypeError, ValueError) as exc:
        raise FacultyServiceError('Round participants are invalid.') from exc
    if len(cleaned) != len(set(cleaned)) or not set(cleaned).issubset(set(event.participant_ids)):
        raise FacultyServiceError('Round participants contain duplicates or unknown entries.')
    from .participant_removal_service import excluded_participant_ids
    excluded = excluded_participant_ids(event, round_index)
    return [participant_id for participant_id in cleaned if participant_id not in excluded]


def round_is_scoreable(event, round_index):
    rounds = criteria_rounds(event)
    cfg = event.result_processing_config or {}
    approvals = cfg.get('confirmed_stages') or {}
    return (0 <= round_index < len(rounds) and not event.results_finalized
            and round_index == int(cfg.get('active_stage_index') or 0)
            and str(round_index) not in approvals
            and rounds[round_index].get('status', 'open' if round_index == 0 else 'locked') in {'open', 'active'}
            and (round_index == 0 or bool(approvals.get(str(round_index - 1), {}).get('approved_at'))))


def _can_mutate(event, user):
    return bool(user and user.is_authenticated and user.is_active and event.faculty_account_id == user.pk
                and (user.is_staff or user.groups.filter(Q(name__iexact='Faculty') | Q(name__iexact='Faculty In-Charge')).exists()))


def _authorize(event, user):
    if event.scoring_method != 'criteria' or not _can_mutate(event, user):
        raise FacultyServiceError('Only the assigned active Faculty-in-Charge can perform this action.')
    if event.results_finalized:
        raise FacultyServiceError('Published results are read-only.')


def _name(user):
    return user.get_full_name() or user.username if user else 'Unassigned'


def _schedule_label(event):
    if not event.event_date:
        return ''
    label = event.event_date.strftime('%b %d, %Y')
    if event.end_date and event.end_date != event.event_date:
        label += ' - ' + event.end_date.strftime('%b %d, %Y')
    if event.event_time:
        label += ' | ' + event.event_time.strftime('%I:%M %p').lstrip('0')
    return label


def _participants(event):
    entries = list(CriteriaEventEntry.objects.filter(event=event, pk__in=event.participant_ids).select_related('department'))
    if entries:
        return [{'id': row.pk, 'name': row.display_name, 'department': str(row.department or ''), 'entry_type': row.entry_type} for row in entries]
    model = RegistryCandidate if event.participation_type == 'individual' else Team
    return [{'id': row.pk, 'name': row.name, 'department': str(row.department or ''), 'entry_type': event.participation_type} for row in model.objects.filter(pk__in=event.participant_ids)]


def _qualifiers(round_cfg, results, selection=None, event=None):
    from .divisions import division_config, participant_division
    if event is not None and division_config(event) and not round_cfg['is_final']:
        selected_ids, errors = [], []
        try:
            groups = {}
            for row in results:
                name = participant_division(event, row['participant_id'])
                groups.setdefault(name, []).append(row)
            requested = [int(value) for value in selection] if selection is not None else None
            if requested is not None and (len(requested) != len(set(requested)) or not set(requested).issubset({row['participant_id'] for row in results})):
                raise FacultyServiceError('Select distinct eligible qualifiers.')
            for name, rows in groups.items():
                cfg = dict(round_cfg)
                if cfg.get('qualification_method') == 'top_n_per_division':
                    cfg['qualification_method'] = 'top_ranking'
                if cfg.get('qualification_method') not in {'advance_all', 'no_elimination', 'no_advancement', 'minimum_score'} and int(cfg.get('qualifiers') or 0) > len(rows):
                    errors.append(f'{name}: qualifier count exceeds the {len(rows)} eligible participants.')
                    continue
                chosen = None if requested is None else [value for value in requested if value in {row['participant_id'] for row in rows}]
                ids, issues = _qualifiers(cfg, rows, chosen)
                selected_ids.extend(ids)
                errors.extend(f'{name}: {issue}' for issue in issues)
        except (TypeError, ValueError) as exc:
            raise FacultyServiceError(str(exc)) from exc
        return selected_ids, errors

    if round_cfg['is_final']:
        return [], []
    method = round_cfg.get('qualification_method') or 'top_ranking'
    ids = [row['participant_id'] for row in results]
    if method in {'advance_all', 'no_elimination', 'no_advancement'}:
        return ids, []
    if method == 'minimum_score':
        minimum = Decimal(str(round_cfg.get('minimum_score') or 0))
        eligible = [row['participant_id'] for row in results if Decimal(str(row['final_score'])) >= minimum]
        return eligible, [] if eligible else ['No participants meet the qualification threshold.']
    count = int(round_cfg.get('qualifiers') or 0)
    if count <= 0:
        return [], ['Configure the number of qualifiers.']
    if method == 'manual_selection':
        if selection is None:
            return [], []
        try:
            chosen = [int(value) for value in selection]
        except (ValueError, TypeError) as exc:
            raise FacultyServiceError('Qualifier selection is invalid.') from exc
        if len(chosen) != len(set(chosen)) or not set(chosen).issubset(ids) or len(chosen) != min(count, len(ids)):
            raise FacultyServiceError('Select the configured number of distinct eligible qualifiers.')
        return chosen, []
    if method not in {'top_ranking', 'top_n_overall'}:
        return [], ['This qualification rule is not supported.']
    if count < len(results) and results[count - 1].get('rank') == results[count].get('rank'):
        return [], ['A tie at the qualification boundary must be resolved before approval.']
    return ids[:count], []


def _round_data(event, index, participants, judges, *, require_verification=True):
    round_cfg = criteria_rounds(event)[index]
    cfg = event.result_processing_config or {}
    approval = (cfg.get('confirmed_stages') or {}).get(str(index), {})
    eligible = round_participant_ids(event, index)
    from .participant_removal_service import excluded_participant_ids
    from .divisions import group_participants, participant_division
    rank_groups = group_participants(event, event.participant_ids if excluded_participant_ids(event, index) else eligible)
    errors, results, monitor = [], [], []
    categories = list(event.scoring_categories.filter(assigned_round_id=round_cfg['id'], purpose='official').prefetch_related('criteria'))
    required = {criterion.pk: criterion for category in categories for criterion in category.criteria.all()}
    accepted = FINALIZED
    verification = cfg.get('verification_mode') in {'tabulator_verification', 'tabulator_admin_approval'}
    scores = list(CriteriaScoreSubmission.objects.filter(event=event, assigned_round_id=round_cfg['id'], category__purpose='official', is_active=True, judge_id__in=[j.pk for j in judges], participant_id__in=eligible).select_related('criterion', 'category'))
    if required:
        seen, invalid_cells = set(), set()
        for score in scores:
            key = (score.judge_id, score.participant_id, score.criterion_id)
            if key in seen:
                errors.append('Duplicate active score submissions were detected.')
            seen.add(key)
            if score.criterion_id not in required or score.category_id != score.criterion.category_id:
                errors.append('A score does not match this round’s configured criteria.')
                continue
            try:
                if score.category.judge_mode == 'ranking':
                    rank_limit = len(rank_groups[participant_division(event, score.participant_id)])
                    if score.raw_score != score.raw_score.to_integral_value() or not 1 <= score.raw_score <= rank_limit:
                        raise ValueError('Ranking scores must be valid ordinal ranks.')
                else:
                    normalized_criterion_score(score.raw_score, score.criterion.max_score, score.criterion.weight_percent)
                if score.category.judge_mode != 'ranking' and score.raw_score < score.criterion.min_score:
                    raise ValueError('Score is below the minimum.')
            except ValueError as exc:
                errors.append(str(exc))
                invalid_cells.add(key)
        for judge in judges:
            sheet = [score for score in scores if score.judge_id == judge.pk and score.criterion_id in required and score.status in accepted and (score.judge_id, score.participant_id, score.criterion_id) not in invalid_cells]
            completed = len({(row.participant_id, row.criterion_id) for row in sheet})
            expected = len(eligible) * len(required)
            complete = expected > 0 and completed == expected
            timestamps = [row.submitted_at for row in sheet if row.submitted_at]
            sources = sorted({row.source for row in sheet})
            monitor.append({'id': judge.pk, 'name': _name(judge), 'status': 'Submitted' if complete else 'Pending', 'progress_percent': round(100 * completed / expected) if expected else 0, 'completed_scores': completed, 'required_scores': expected, 'submitted_at': max(timestamps).isoformat() if timestamps else None, 'submission_method': ' / '.join('OCR / Paper-Based' if source == 'OCR' else 'Mobile' if source == 'MOBILE' else 'Manual' for source in sources) or '—'})
        if len({row.judge_mode for row in categories}) > 1:
            errors.append('Mixed ordinal ranking and numerical scoring categories need an explicit conversion rule.')
        if event.criteria_score_method == 'ranking_based' and any(row.judge_mode != 'ranking' for row in categories):
            errors.append('Ranking-based events require ranking-mode categories and ordinal rank submissions.')
        if categories and all(row.judge_mode == 'ranking' for row in categories) and cfg.get('ranking_aggregation') not in {'mean_rank', 'weighted_mean_rank'}:
            errors.append('Configure the authorized ranking aggregation rule before calculating ordinal ranks.')
        if verification and require_verification and any(row.status not in {'VERIFIED', 'APPROVED'} for row in scores):
            errors.append('The Tabulation Officer must verify all round scores first.')
        if eligible and judges and all(row['status'] == 'Submitted' for row in monitor) and not errors:
            try:
                results = compute_official_criteria_rankings(event, round_id=round_cfg['id'], accepted_statuses=accepted, participant_ids=eligible, configured=True)
            except ValueError as exc:
                errors.append(str(exc))
    elif len(criteria_rounds(event)) == 1 and event.judging_event_id:
        # Old single-round events have no normalized submissions. Validate every
        # legacy cell before reusing the original ranking engine.
        from .faculty_service import compute_criteria_rankings
        judging = event.judging_event
        legacy_criteria = list(judging.criteria.all())
        candidates = list(judging.candidates.all())
        legacy = list(JudgeScore.objects.filter(candidate__event=judging, judge_id__in=[row.pk for row in judges]).select_related('criterion'))
        mapping = (cfg.get('mobile_round_mapping') or {}).get('candidates', {})
        candidate_to_entry = {int(value): int(key) for key, value in mapping.items()} if isinstance(mapping, dict) else {}
        if not candidate_to_entry and len(candidates) == len(participants):
            # Legacy rows were created in participant order. Require matching
            # names as well; ambiguous/mismatched identities are never guessed.
            by_name = {}
            for participant in participants:
                by_name.setdefault(participant['name'], []).append(participant['id'])
            for candidate in candidates:
                matches = by_name.get(candidate.name, [])
                if len(matches) == 1:
                    candidate_to_entry[candidate.pk] = matches[0]
        candidate_to_entry = {key: value for key, value in candidate_to_entry.items() if value in eligible}
        candidates = [row for row in candidates if row.pk in candidate_to_entry]
        legacy = [row for row in legacy if row.candidate_id in candidate_to_entry]
        if set(candidate_to_entry.values()) != set(eligible) or len(candidate_to_entry) != len(candidates):
            errors.append('Legacy participants need an explicit validated mobile mapping.')
        expected_keys = {(candidate.pk, criterion.pk) for candidate in candidates for criterion in legacy_criteria}
        for judge in judges:
            sheet = [row for row in legacy if row.judge_id == judge.pk and row.is_locked]
            keys = {(row.candidate_id, row.criterion_id) for row in sheet}
            timestamps = [row.submitted_at for row in sheet if row.submitted_at]
            for row in sheet:
                try:
                    normalized_criterion_score(row.score, row.criterion.max_score, row.criterion.weight_percent)
                except ValueError as exc:
                    errors.append(str(exc))
            complete = bool(expected_keys) and keys == expected_keys
            monitor.append({'id': judge.pk, 'name': _name(judge), 'status': 'Submitted' if complete else 'Pending', 'progress_percent': round(100 * len(keys & expected_keys) / len(expected_keys)) if expected_keys else 0, 'completed_scores': len(keys & expected_keys), 'required_scores': len(expected_keys), 'submitted_at': max(timestamps).isoformat() if timestamps else None, 'submission_method': 'Mobile' if sheet else '?'})
        if verification and require_verification and not cfg.get('tabulation_confirmed_at'):
            errors.append('The Tabulation Officer must confirm the legacy tabulation first.')
        if event.criteria_score_method == 'ranking_based' or (event.judge_settings or {}).get('remove_high_low') or event.deductions_enabled:
            errors.append('This legacy scoring configuration needs normalized round scores before faculty approval.')
        if judges and all(row['status'] == 'Submitted' for row in monitor) and not errors:
            for row in compute_criteria_rankings(event):
                if row['candidate_id'] in candidate_to_entry:
                    results.append({'participant_id': candidate_to_entry[row['candidate_id']], 'name': row['name'], 'final_score': row['final_score'], 'rank': len(results) + 1, 'complete': True, 'judge_scores': [], 'criteria_breakdown': []})
    else:
        errors.append('No official scoring criteria are configured for this round.')
    if not eligible:
        errors.append('Participants are pending qualification.' if index else 'No participants are configured.')
    if not judges:
        errors.append('No active judges are assigned.')
    if len(results) != len(eligible) or not all(row.get('complete') for row in results):
        if eligible:
            errors.append('All assigned judges must finalize every required participant score.')
        results = []
    criterion_names = {key: value for key, value in required.items()}
    judge_names = {row.pk: _name(row) for row in judges}
    from .divisions import division_config, participant_division
    for row in results:
        for detail in row['judge_scores']:
            detail['name'] = judge_names.get(detail['judge_id'], '')
            detail['total'] = float(detail['total'])
        for detail in row['criteria_breakdown']:
            criterion = criterion_names[detail['criterion_id']]
            detail.update(name=criterion.name, weight=float(criterion.weight_percent), score=float(detail['score']))
            if event.criteria_score_method not in {'raw_score', 'average_score', 'ranking_based'}:
                detail['contribution'] = float(normalized_criterion_score(detail['score'], criterion.max_score, criterion.weight_percent) * criterion.category.overall_weight_percent / 100)
        row['final_score'] = float(row['final_score'])
        if 'deduction' in row:
            row['deduction'] = float(row['deduction'])
    explicit_basis = cfg.get('final_result_basis') in {'final_round_only', 'weighted_rounds', 'cumulative_rounds'}
    if results and round_cfg.get('carry_previous_scores') and index and not explicit_basis:
        previous = (cfg.get('confirmed_stages') or {}).get(str(index - 1), {}).get('results', [])
        by_id = {row['participant_id']: row for row in previous}
        if not all(row['participant_id'] in by_id for row in results):
            errors.append('The previous round has no approved score snapshot for carryover.')
        else:
            previous_weight = Decimal(str((cfg.get('confirmed_stages') or {}).get(str(index - 1), {}).get('accumulated_weight') or criteria_rounds(event)[index - 1].get('weight') or 0))
            current_weight = Decimal(str(round_cfg.get('weight') or 0))
            if previous_weight + current_weight <= 0:
                errors.append('Round carryover weights must be configured.')
            else:
                for row in results:
                    row['round_score'] = row['final_score']
                    row['final_score'] = float((Decimal(str(by_id[row['participant_id']]['final_score'])) * previous_weight + Decimal(str(row['final_score'])) * current_weight) / (previous_weight + current_weight))
                def cumulative_order(row):
                    keys = [-row['final_score']]
                    for rule in event.tie_break_rules or []:
                        method = rule.get('method')
                        if method == 'highest_final_round':
                            keys.append(-row['round_score'])
                        elif method == 'highest_selected_criterion':
                            selected = str(rule.get('criterion_id') or '')
                            selected_name = next((item.get('name') for item in event.judging_criteria_config or [] if str(item.get('id')) == selected), None)
                            match = next((item for item in row['criteria_breakdown'] if str(item['criterion_id']) == selected or (selected_name and item['name'] == selected_name)), None)
                            if match:
                                keys.append(-match['score'])
                        elif method == 'highest_chief_judge':
                            match = next((item for item in row['judge_scores'] if item['judge_id'] == event.chief_judge_id), None)
                            keys.append(-match['total'] if match else 0)
                    return tuple(keys)
                results.sort(key=lambda row: (*cumulative_order(row), row['participant_id']))
                prior, rank = None, 0
                for position, row in enumerate(results, 1):
                    comparison = cumulative_order(row)
                    if comparison != prior:
                        rank = position
                    prior = comparison
                    row['rank'] = rank
    from .divisions import rank_divisions
    # Normalized round rankings already have division ranks. Legacy totals and
    # cumulative scores need their per-division ranks rebuilt here.
    if results and (not required or (round_cfg.get('carry_previous_scores') and index)):
        results = rank_divisions(event, results)
    if results and explicit_basis:
        from .criteria_scoring import combine_round_results
        try:
            results = combine_round_results(event, results, index)
        except ValueError as exc:
            errors.append(str(exc))
    qualifiers, qualification_errors = _qualifiers(round_cfg, results, event=event) if results else ([], [])
    errors.extend(qualification_errors)
    if approval.get('approved_at'):
        results = approval.get('results', results)
        qualifiers = approval.get('qualifier_ids', qualifiers)
        errors = []
    for row in results:
        participant = next((item for item in participants if item['id'] == row['participant_id']), {})
        row['division'] = participant_division(event, row['participant_id']) if division_config(event) else ''
        row['department'] = participant.get('department', '')
        judge_totals = {detail['judge_id']: detail for detail in row.get('judge_scores', [])}
        row['judge_score_cells'] = [
            {'id': judge.pk, 'name': _name(judge),
             'total': float(judge_totals[judge.pk]['total']) if judge.pk in judge_totals else None}
            for judge in judges
        ]
        judge_average = Decimal(str(row.get('round_score', row['final_score'])))
        included_judges = len(judges)
        if (event.judge_settings or {}).get('remove_high_low') and included_judges >= 3:
            included_judges -= 2
        row['judge_average'] = float(judge_average)
        row['weighted_score'] = float(judge_average * included_judges)
        row['qualified'] = row['participant_id'] in qualifiers
        row['qualification_status'] = 'Finalist' if round_cfg['is_final'] else ('Pending Manual Selection' if round_cfg.get('qualification_method') == 'manual_selection' and not approval else ('Qualified' if row['qualified'] else 'Not Qualified'))
    submitted = sum(row['status'] == 'Submitted' for row in monitor)
    locked = index > int(cfg.get('active_stage_index') or 0) or round_cfg.get('status') == 'locked'
    status = 'completed' if approval.get('approved_at') else ('locked' if locked else ('ready' if results and not errors else ('awaiting' if scores else 'active')))
    previous = (cfg.get('confirmed_stages') or {}).get(str(index - 1), {})
    status_labels = {
        'completed': 'Completed', 'locked': 'Locked', 'ready': 'Ready for Review',
        'awaiting': 'Awaiting Submissions', 'active': 'Active',
    }
    from .divisions import division_config
    divisions = division_config(event)
    division_counts = [
        {'name': name, 'count': sum(participant_division(event, value) == name for value in eligible)}
        for name in divisions.get('names', [])
    ]
    method = round_cfg.get('qualification_method') or 'top_ranking'
    target = int(round_cfg.get('qualifiers') or 0)
    qualification_label = ''
    if not round_cfg['is_final']:
        if method in {'top_ranking', 'top_n_overall', 'top_n_per_division'} and target:
            qualification_label = f'Top {target}' + (' per division' if divisions else ' overall')
        elif method == 'manual_selection':
            qualification_label = 'Manual selection'
        elif method == 'minimum_score':
            qualification_label = f"Minimum score: {round_cfg.get('minimum_score') or 0}"
        elif method in {'advance_all', 'no_elimination', 'no_advancement'}:
            qualification_label = 'All participants advance'
    qualifier_count = len(qualifiers) if results and (
        round_cfg.get('qualification_method') != 'manual_selection' or approval
    ) else None
    return {
        'index': index, 'number': index + 1, 'id': round_cfg['id'],
        'name': round_cfg.get('name', 'Round'),
        'status_key': status, 'status': status_labels[status],
        'participant_count': len(eligible), 'submitted_judges': submitted,
        'judge_count': len(judges),
        'progress_percent': round(100 * submitted / len(judges)) if judges else 0,
        'calculation_status': 'Completed' if results else 'Pending',
        'qualifier_count': qualifier_count, 'is_final': round_cfg['is_final'],
        'can_approve': status == 'ready' and round_is_scoreable(event, index),
        'can_unlock': (
            locked and index == int(cfg.get('active_stage_index') or 0) + 1
            and bool(previous.get('approved_at')) and bool(previous.get('qualifier_ids'))
        ),
        'approved_by': approval.get('approved_by'),
        'approved_by_name': approval.get('approved_by_name'),
        'approved_at': approval.get('approved_at'),
        'blockers': list(dict.fromkeys(errors)),
        'qualification_rule': round_cfg.get('qualification_method') or '',
        'qualification_label': qualification_label, 'division_counts': division_counts,
        'divisions': division_config(event).get('names', []),
        'criteria_categories': [
            {'name': category.name, 'weight': float(category.overall_weight_percent)}
            for category in categories
        ],
        'criteria_weight_total': float(sum(
            (category.overall_weight_percent for category in categories), Decimal(0)
        )),
        'judges': monitor, 'results': results,
    }



def criteria_faculty_portal(event, user, selected_round_index=0):
    from .participant_removal_service import excluded_participant_ids, participant_change_round
    participants = _participants(event)
    judges = list(event.assigned_judges.filter(is_active=True))
    rounds = [_round_data(event, index, participants, judges) for index in range(len(criteria_rounds(event)))]
    authorized = _can_mutate(event, user) and not event.results_finalized
    for row in rounds:
        row['can_approve'] &= authorized
        row['can_unlock'] &= authorized
    selected = rounds[max(0, min(int(selected_round_index), len(rounds) - 1))]
    cfg = event.result_processing_config or {}
    excluded = excluded_participant_ids(event)
    active_participants = [row for row in participants if row['id'] not in excluded]
    try:
        change_index = participant_change_round(event)
        can_manage_participants = authorized
    except FacultyServiceError:
        change_index = None
        can_manage_participants = False
    current = rounds[max(0, min(int(cfg.get('active_stage_index') or 0), len(rounds) - 1))]
    publication_errors = []
    if not (cfg.get('confirmed_stages') or {}).get(str(len(rounds) - 1), {}).get('approved_at'):
        publication_errors.append('Approve the final round before submitting for publication.')
    if cfg.get('verification_mode') in {'tabulator_verification', 'tabulator_admin_approval'}:
        verified = cfg.get('verified_stages') or {}
        if not cfg.get('tabulation_confirmed_at') or (verified and str(len(rounds) - 1) not in verified):
            publication_errors.append('The Tabulation Officer must confirm final tabulation first.')
    publish_errors = list(publication_errors)
    if cfg.get('verification_mode') == 'tabulator_admin_approval' and not cfg.get('approved'):
        publish_errors.append('Admin approval is required before publishing results.')
    from .publication_service import can_publish_event
    tabulator = event.assigned_tabulators.first()
    event_status = event.get_status_display()
    publication_status = (
        'Published' if event.results_finalized else
        'Submitted for Publication' if cfg.get('faculty_submitted_at') else 'Pending Review'
    )
    portal = {
        'header': {
            'name': event.name, 'category': event.category.title() if event.category.isupper() else event.category, 'venue': event.venue,
            'schedule': _schedule_label(event),
            'participant_count': len(active_participants),
            'faculty_name': _name(event.faculty_account), 'tabulator_name': _name(tabulator),
            'status': event_status, 'status_key': event.status,
        },
        'summary': {
            'participant_count': len(active_participants), 'judge_count': len(judges),
            'round_count': len(rounds), 'current_round_name': current['name'],
            'submitted_judges': current['submitted_judges'], 'total_judges': len(judges),
            'progress_percent': current['progress_percent'], 'event_status': event_status,
        },
        'rounds': rounds, 'current_round': current, 'selected_round': selected,
        'judges': selected['judges'], 'results': selected['results'], 'participants': active_participants,
        'can_manage_participants': can_manage_participants,
        'participant_change_round_index': change_index,
        'participant_change_round_name': criteria_rounds(event)[change_index].get('name', 'Round') if change_index is not None else '',
        'removal_history': list(reversed(cfg.get('participant_removals') or [])),
        'publication': {
            'status': publication_status,
            'can_submit': authorized and not publication_errors and not cfg.get('faculty_submitted_at'),
            'submitted_at': cfg.get('faculty_submitted_at'),
            'submitted_by': cfg.get('faculty_submitted_by'),
            'submitted_by_name': cfg.get('faculty_submitted_by_name'),
            'errors': publication_errors, 'publish_errors': publish_errors,
            'published': event.results_finalized,
            'can_publish': authorized and can_publish_event(user, event) and not publish_errors,
            'can_unpublish': _can_mutate(event, user) and can_publish_event(user, event)
            and event.results_finalized and bool(cfg.get('published_at')),
        },
        'errors': selected['blockers'],
    }
    # A compact results preview uses the same authoritative qualification flags
    # as the full results table. Do not imply pending participants are qualified.
    from .divisions import division_config, participant_division
    names = division_config(event).get('names', [])
    by_id = {row['id']: row for row in participants}
    preview = []
    for row in selected['results']:
        if row.get('qualified') or selected['is_final']:
            participant = by_id.get(row['participant_id'], {})
            preview.append({**row, 'department': participant.get('department', ''),
                'division': participant_division(event, row['participant_id']) if names else ''})
    portal['qualified_groups'] = [
        {'name': name, 'rows': [row for row in preview if row['division'] == name]}
        for name in names
    ] if names else [{'name': '', 'rows': preview}]
    portal['revision'] = hashlib.sha256(json.dumps(portal, sort_keys=True, default=str).encode()).hexdigest()
    return portal


def _lock(event, user):
    Event.objects.select_for_update().get(pk=event.pk)
    event.refresh_from_db()
    _authorize(event, user)


def _audit(event, user, action, metadata):
    from .audit_service import record_audit
    record_audit(user=user, action=action, module='Results', event_name=event.name, description=f'{action}: {event.name}', metadata=metadata)


@transaction.atomic
def approve_criteria_round(event, user, round_index, qualifier_ids=None):
    _lock(event, user)
    round_index = int(round_index)
    round_participant_ids(event, round_index)
    portal = criteria_faculty_portal(event, user, round_index)
    selected = portal['selected_round']
    if not selected['can_approve'] or not round_is_scoreable(event, round_index):
        raise FacultyServiceError(' '.join(selected['blockers']) or 'This round is not available for approval.')
    rounds = criteria_rounds(event)
    qualifiers, errors = _qualifiers(rounds[round_index], selected['results'], qualifier_ids, event=event)
    if errors or (not rounds[round_index]['is_final'] and not qualifiers):
        raise FacultyServiceError(' '.join(errors) or 'Select eligible qualifiers before approval.')
    cfg = dict(event.result_processing_config or {})
    confirmed = dict(cfg.get('confirmed_stages') or {})
    now = timezone.now().isoformat()
    accumulated = Decimal(str(rounds[round_index].get('weight') or 0))
    if round_index and rounds[round_index].get('carry_previous_scores'):
        accumulated += Decimal(str(confirmed.get(str(round_index - 1), {}).get('accumulated_weight') or rounds[round_index - 1].get('weight') or 0))
    confirmed[str(round_index)] = {'approved_by': user.pk, 'approved_by_name': _name(user), 'approved_at': now, 'confirmed_by': user.pk, 'confirmed_at': now, 'qualifier_ids': qualifiers, 'results': selected['results'], 'accumulated_weight': float(accumulated)}
    cfg['confirmed_stages'] = confirmed
    if rounds[round_index]['is_final']:
        cfg['faculty_reviewed_at'] = now
        cfg['faculty_reviewed_by'] = user.pk
    rounds[round_index]['status'] = 'completed'
    event.rounds_config, event.result_processing_config = rounds, cfg
    event.save(update_fields=['rounds_config', 'result_processing_config', 'updated_at'])
    CriteriaScoreSubmission.objects.filter(event=event, assigned_round_id=rounds[round_index]['id'], participant_id__in=round_participant_ids(event, round_index), judge_id__in=event.assigned_judges.filter(is_active=True).values_list('pk', flat=True), category__purpose='official', is_active=True, status__in=FINALIZED).update(status=CriteriaScoreSubmission.STATUS_APPROVED, approved_at=timezone.now())
    _audit(event, user, 'Approved Criteria Round', {'round_index': round_index, 'qualifier_ids': qualifiers})


@transaction.atomic
def unlock_criteria_round(event, user, round_index):
    _lock(event, user)
    round_index = int(round_index)
    round_participant_ids(event, round_index)
    portal = criteria_faculty_portal(event, user, round_index)
    if not portal['selected_round']['can_unlock']:
        raise FacultyServiceError('Approve the immediately preceding round before unlocking this locked round.')
    cfg = dict(event.result_processing_config or {})
    previous = cfg['confirmed_stages'][str(round_index - 1)]
    qualifiers = previous['qualifier_ids']
    if not qualifiers or not set(qualifiers).issubset(set(event.participant_ids)):
        raise FacultyServiceError('Approved qualifications are no longer valid.')
    qualified = dict(cfg.get('qualified_participant_ids') or {})
    qualified[str(round_index)] = qualifiers
    cfg.update(qualified_participant_ids=qualified, active_stage_index=round_index)
    unlocks = dict(cfg.get('unlocked_stages') or {})
    unlocks[str(round_index)] = {'unlocked_by': user.pk, 'unlocked_at': timezone.now().isoformat()}
    cfg['unlocked_stages'] = unlocks
    rounds = criteria_rounds(event)
    rounds[round_index]['status'] = 'open'
    event.rounds_config, event.result_processing_config = rounds, cfg
    event.save(update_fields=['rounds_config', 'result_processing_config', 'updated_at'])
    _audit(event, user, 'Unlocked Criteria Round', {'round_index': round_index, 'qualifier_ids': qualifiers})


@transaction.atomic
def submit_criteria_publication(event, user):
    _lock(event, user)
    publication = criteria_faculty_portal(event, user)['publication']
    if not publication['can_submit']:
        raise FacultyServiceError(' '.join(publication['errors']) or 'Results have already been submitted for publication.')
    cfg = dict(event.result_processing_config or {})
    cfg.update(ready_for_publication=True, faculty_submitted_by=user.pk, faculty_submitted_by_name=_name(user), faculty_submitted_at=timezone.now().isoformat())
    if cfg.get('verification_mode') != 'tabulator_admin_approval':
        cfg['approved'] = True
    event.result_processing_config = cfg
    event.save(update_fields=['result_processing_config', 'updated_at'])
    _audit(event, user, 'Submitted Criteria Results for Publication', {})


@transaction.atomic
def verify_criteria_round(event, user):
    """Use the existing Tabulation Officer confirmation for the active round."""
    Event.objects.select_for_update().get(pk=event.pk)
    event.refresh_from_db()
    if not user.is_active or not event.assigned_tabulators.filter(pk=user.pk).exists() or not user.groups.filter(name__iexact='Tabulator').exists():
        raise FacultyServiceError('Only the assigned Tabulation Officer can verify this round.')
    cfg = dict(event.result_processing_config or {})
    if cfg.get('verification_mode') not in {'tabulator_verification', 'tabulator_admin_approval'}:
        raise FacultyServiceError('This event does not require Tabulation Officer verification.')
    index = int(cfg.get('active_stage_index') or 0)
    if not round_is_scoreable(event, index):
        raise FacultyServiceError('Only an active round can be verified.')
    judges = list(event.assigned_judges.filter(is_active=True))
    reviewed = _round_data(event, index, _participants(event), judges, require_verification=False)
    if reviewed['blockers'] or not reviewed['results']:
        raise FacultyServiceError(' '.join(reviewed['blockers']) or 'Complete validated round scores are required.')
    now = timezone.now()
    CriteriaScoreSubmission.objects.filter(
        event=event, assigned_round_id=reviewed['id'],
        participant_id__in=round_participant_ids(event, index),
        judge_id__in=[judge.pk for judge in judges], category__purpose='official',
        is_active=True, status=CriteriaScoreSubmission.STATUS_SUBMITTED,
    ).update(status=CriteriaScoreSubmission.STATUS_VERIFIED, updated_at=now)
    verified = dict(cfg.get('verified_stages') or {})
    verified[str(index)] = {'verified_by': user.pk, 'verified_at': now.isoformat()}
    cfg.update(verified_stages=verified, tabulation_status='confirmed',
               tabulation_confirmed_by=user.pk, tabulation_confirmed_at=now.isoformat(),
               tabulation_confirmed_round_id=reviewed['id'], ready_for_publication=False)
    event.result_processing_config = cfg
    event.save(update_fields=['result_processing_config', 'updated_at'])
    _audit(event, user, 'Verified Criteria Round', {'round_index': index})
    return cfg


def approved_final_rankings(event):
    """Expose only the explicitly reviewed final snapshot to publication readers."""
    cfg = event.result_processing_config or {}
    if not cfg.get('faculty_submitted_at'):
        return None
    last_index = len(criteria_rounds(event)) - 1
    approval = (cfg.get('confirmed_stages') or {}).get(str(last_index), {})
    if not approval.get('approved_at'):
        raise FacultyServiceError('The final round has not been approved.')
    results = approval.get('results') or []
    eligible = round_participant_ids(event, last_index)
    if (not results or len(results) != len(eligible)
            or {row.get('participant_id') for row in results} != set(eligible)
            or not all(row.get('complete') and row.get('rank', 0) > 0 for row in results)):
        raise FacultyServiceError('The approved final rankings do not match the qualified participants.')
    from .divisions import division_config, participant_division
    if division_config(event) and any(row.get('division') != participant_division(event, row['participant_id']) for row in results):
        raise FacultyServiceError('Approved rankings do not match the event divisions.')
    return results
