"""Championship rule snapshots, validation, and deterministic award reconciliation."""
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json

from django.db import transaction

from .models import BracketMatch, Event, SystemSettings

METHODS = {'placement', 'per_win', 'combined'}
TERMINAL = {BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT}


class ScoringRulesError(ValueError):
    def __init__(self, message, fields=None):
        super().__init__(message)
        self.fields = fields or {}


def default_rules():
    from .match_event_service import MAJOR_POINTS, MINOR_POINTS
    return {
        'version': 2, 'method': 'placement', 'win_points': {'major': 3, 'minor': 3},
        'placements': [{'rank': i, 'major': a['points'], 'minor': b['points']}
                       for i, (a, b) in enumerate(zip(MAJOR_POINTS, MINOR_POINTS), 1)],
        'approval_required': True, 'automatic_calculation': True,
        'bye_policy': 'no_points', 'tie_policy': 'official',
    }


def scoring_rules():
    saved = SystemSettings.objects.filter(pk=1).values_list('championship_points_config', flat=True).first() or {}
    rules = default_rules()
    if saved.get('version') == 2:
        rules.update(deepcopy(saved))
    elif 'major' in saved or 'minor' in saved:
        rules['method'] = 'per_win'
        rules['win_points'] = {key: saved.get(key, 3) for key in ('major', 'minor')}
    return rules


def points_defaults():
    return scoring_rules()['win_points']


def _whole_number(raw, label, maximum=1000000):
    raw = str(raw).strip()
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 7 or int(raw) > maximum:
        raise ScoringRulesError(f'{label} must be a whole number from 0 to {maximum:,}.')
    return int(raw)


def validate_scoring_rules(data, previous=None):
    rules = deepcopy(previous or default_rules())
    method = data.get('method', '')
    if method not in METHODS:
        raise ScoringRulesError('Choose a supported calculation method.', {'method': 'Select a method.'})
    rules.update(version=2, method=method)
    errors = {}
    if method in {'per_win', 'combined'}:
        for key in ('major', 'minor'):
            try:
                rules['win_points'][key] = _whole_number(data.get(f'{key}_win', ''), key.title() + ' win points')
            except ScoringRulesError as exc:
                errors[f'{key}_win'] = str(exc)
    if method in {'placement', 'combined'}:
        try:
            rows = json.loads(data.get('placements', '[]'))
            if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
                raise ScoringRulesError('Configure between 1 and 100 placement rows.')
            cleaned, ranks = [], set()
            for row in rows:
                if not isinstance(row, dict):
                    raise ScoringRulesError('Each placement needs a rank and both point values.')
                rank = _whole_number(row.get('rank', ''), 'Placement', 100)
                if rank < 1 or rank in ranks:
                    raise ScoringRulesError('Placement positions must be unique numbers from 1 to 100.')
                ranks.add(rank)
                cleaned.append({'rank': rank, **{key: _whole_number(row.get(key, ''), f'Rank {rank} {key} points')
                                               for key in ('major', 'minor')}})
            cleaned.sort(key=lambda row: row['rank'])
            if any(cleaned[i][key] > cleaned[i - 1][key]
                   for key in ('major', 'minor') for i in range(1, len(cleaned))):
                if data.get('confirm_nonmonotonic') != 'on':
                    raise ScoringRulesError('Confirm the exception before awarding more points to a lower placement.')
            rules['placements'] = cleaned
        except (ValueError, TypeError) as exc:
            errors['placements'] = str(exc) if isinstance(exc, ScoringRulesError) else 'Enter valid placement rows.'
    tie = data.get('tie_policy', 'official')
    if tie not in {'official', 'shared', 'manual'}:
        errors['tie_policy'] = 'Select a supported tie policy.'
    if errors:
        raise ScoringRulesError('Please correct the highlighted scoring settings.', errors)
    rules.update(approval_required=data.get('approval_required') == 'on',
                 automatic_calculation=data.get('automatic_calculation') == 'on',
                 bye_policy='no_points', tie_policy=tie)
    rules.pop('classification', None)
    return rules


def event_snapshot(event, rules=None):
    if event.event_classification not in {'major', 'minor'}:
        raise ScoringRulesError('Set the event classification to Major or Minor before applying championship rules.')
    rules = deepcopy(rules or scoring_rules())
    rules['classification'] = event.event_classification
    if event.scoring_method == 'criteria':
        rules['method'] = 'placement'
    return rules


def inherit_criteria_snapshot(event, result_processing, creating):
    """Scoring snapshots are server-owned, even when other result settings are editable."""
    saved = dict(event.result_processing_config or {})
    reserved = {key for key in set(result_processing) | set(saved)
                if key.startswith('championship_') or key in {'published_championship_awards', 'calculated_placements', 'match_win_points'}}
    for key in reserved:
        result_processing.pop(key, None)
        if not creating and key in saved:
            result_processing[key] = deepcopy(saved[key])
    if creating:
        result_processing['championship_scoring'] = event_snapshot(event)


def placement_schedule(rules, classification):
    return [{'label': f"{row['rank']} Place", 'points': row[classification]} for row in rules['placements']]


def _match_placements(event, teams, matches, rules):
    supplied = (event.result_processing_config or {}).get('championship_placements')
    if supplied:
        return {int(key): value for key, value in supplied.items()}, ''
    if event.tournament_type == 'round_robin':
        return _round_robin_placements(event, teams, matches, rules)
    from .bracket_progression import _is_finals_match
    finals = [m for m in matches if _is_finals_match(m) and m.winner_id and m.loser_id]
    if not finals:
        return {}, 'A confirmed championship final or authorized final placements are required.'
    final = max(finals, key=lambda m: m.match_number)
    if event.tournament_type == 'double_elimination':
        losses = Counter(m.loser_id for m in matches if m.loser_id)
        if losses[final.loser_id] < 2:
            return {}, 'Double elimination requires a resolved grand-final reset or authorized final placements.'
    ranks = {final.winner_id: 1, final.loser_id: 2}
    third = [m for m in matches if 'third' in m.round_name.lower() or '3rd' in m.round_name.lower()]
    if third and third[-1].winner_id and third[-1].loser_id:
        ranks.update({third[-1].winner_id: 3, third[-1].loser_id: 4})
    if event.tournament_type == 'single_elimination':
        rounds = defaultdict(list)
        for match in matches:
            if match.loser_id and match.loser_id not in ranks:
                rounds[match.round_name].append(match)
        for cohort in sorted(rounds.values(), key=lambda rows: max(m.match_number for m in rows), reverse=True):
            members = {m.loser_id for m in cohort if m.loser_id not in ranks}
            if len(members) > 1 and rules['tie_policy'] != 'shared':
                break
            rank = len(ranks) + 1
            ranks.update({member: rank for member in members})
    if len(ranks) != len(teams):
        return ranks, 'Record remaining final placements through the authorized event override.'
    return ranks, ''


def _round_robin_placements(event, teams, matches, rules):
    wins = Counter(m.winner_id for m in matches if m.winner_id)
    score_for, score_against = defaultdict(Decimal), defaultdict(Decimal)
    scores_available = True
    for match in matches:
        try:
            a, b = Decimal(match.score_a), Decimal(match.score_b)
            if not a.is_finite() or not b.is_finite():
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError):
            scores_available = False
            continue
        score_for[match.team_a_id] += a
        score_against[match.team_a_id] += b
        score_for[match.team_b_id] += b
        score_against[match.team_b_id] += a
    groups = defaultdict(list)
    for team in teams:
        groups[wins[team.id]].append(team.id)
    ranks, position = {}, 1
    for count in sorted(groups, reverse=True):
        members = groups[count]
        keys = {member: [] for member in members}
        if rules['tie_policy'] == 'official':
            for tie_break in event.tie_break_rules or []:
                if tie_break == 'head_to_head':
                    head = Counter(m.winner_id for m in matches if m.team_a_id in members and m.team_b_id in members and m.winner_id)
                    for member in members:
                        keys[member].append(head[member])
                elif scores_available and tie_break in {'score_difference', 'points_scored'}:
                    for member in members:
                        keys[member].append(score_for[member] - score_against[member]
                                            if tie_break == 'score_difference' else score_for[member])
                else:
                    # Unsupported penalties/playoffs/manual decisions must not
                    # be silently skipped in favor of lower-priority metrics.
                    break
        previous, rank = None, position
        for offset, member in enumerate(sorted(members, key=lambda member: tuple(keys[member]), reverse=True)):
            key = tuple(keys[member])
            if offset == 0 or key != previous:
                rank = position + offset
            elif rules['tie_policy'] != 'shared':
                return {}, 'Tournament ties require authorized final-placement resolution.'
            ranks[member], previous = rank, key
        position += len(members)
    return ranks, ''


def criteria_placements(event, rules):
    from .public_service import is_multiround, result_rounds
    from .criteria_scoring import compute_official_criteria_rankings
    from .faculty_service import compute_criteria_rankings
    if is_multiround(event) or len(result_rounds(event)) > 1:
        return [], 'Overall multiround aggregation is unavailable; round scores are not final championship results.'
    if event.scoring_categories.exists():
        if event.scoring_categories.filter(purpose='official', judge_mode='ranking').exists():
            return [], 'Ranking-mode championship aggregation requires an official final ranking calculator.'
        try:
            rankings = compute_official_criteria_rankings(event, round_id=result_rounds(event)[0]['id'])
        except ValueError as exc:
            return [], str(exc)
        entries = {row.id: row for row in event.criteria_entries.all()}
        if not rankings or len(rankings) != len(event.participant_ids) or not all(row['complete'] for row in rankings):
            return [], 'Complete approved judge scores are required.'
        rows = [{'id': row['participant_id'], 'rank': row['rank'], 'department_id': entries[row['participant_id']].department_id}
                for row in rankings if row['participant_id'] in entries]
    elif event.judging_event_id:
        from .models import JudgeScore
        candidates = list(event.judging_event.candidates.all())
        criteria = list(event.judging_event.criteria.values_list('id', flat=True))
        judges = list(event.assigned_judges.filter(is_active=True).values_list('id', flat=True))
        locked = set(JudgeScore.objects.filter(candidate__event=event.judging_event, is_locked=True).values_list('candidate_id', 'criterion_id', 'judge_id'))
        if not candidates or not criteria or not judges or not all((candidate.id, criterion, judge) in locked
                for candidate in candidates for criterion in criteria for judge in judges):
            return [], 'Complete locked judge scores are required.'
        from .models import Department
        department_names = defaultdict(list)
        for department in Department.objects.all():
            department_names[department.name.casefold()].append(department.pk)
        departments = {row.id: (department_names[row.department.casefold()][0]
                       if len(department_names[row.department.casefold()]) == 1 else None) for row in candidates}
        rows, previous, rank = [], None, 0
        for index, row in enumerate(compute_criteria_rankings(event), 1):
            if row['final_score'] != previous:
                rank = index
            previous = row['final_score']
            rows.append({'id': row['candidate_id'], 'rank': rank, 'department_id': departments[row['candidate_id']]})
    else:
        return [], 'No supported official final rankings are available.'
    if len({row['rank'] for row in rows}) != len(rows) and rules['tie_policy'] != 'shared':
        return [], 'Tied criteria rankings require resolution under the official judging rules.'
    return rows, ''


@transaction.atomic
def reconcile_championship_points(event, *, force=False, publishing=False):
    current = Event.objects.select_for_update().get(pk=event.pk)
    cfg = dict(current.result_processing_config or {})
    rules = cfg.get('championship_scoring')
    if not rules:
        return None
    if not rules['automatic_calculation'] and not (force or publishing):
        return cfg.get('championship_awards', {})
    classification = rules['classification']
    table = {row['rank']: row[classification] for row in rules['placements']}
    awards, pending = defaultdict(int), ''
    if event.scoring_method == 'criteria':
        rows, pending = criteria_placements(event, rules)
        if rules['approval_required'] and not cfg.get('approved'):
            rows, pending = [], 'Authorized final-result approval is required.'
        if not event.apply_championship_points:
            rows, pending = [], ''
        for row in rows:
            if row['department_id']:
                awards[str(row['department_id'])] += table.get(row['rank'], 0)
    else:
        teams = list(event.bracket_teams.all())
        team_ids = {team.pk for team in teams}
        matches = list(event.bracket_matches.filter(is_automatic_advance=False).order_by('match_number'))
        complete = bool(matches) and all(m.status in TERMINAL for m in matches)
        confirmed = [m for m in matches if m.status in TERMINAL
                     and m.team_a_id in team_ids and m.team_b_id in team_ids
                     and m.team_a_id != m.team_b_id
                     and (m.winner_id is None or m.winner_id in {m.team_a_id, m.team_b_id})]
        complete = complete and len(confirmed) == len(matches)
        wins = Counter(m.winner_id for m in confirmed if m.winner_id)
        losses = Counter(m.loser_id for m in confirmed if m.loser_id)
        placements = {}
        if rules['method'] in {'placement', 'combined'}:
            if complete and (not rules['approval_required'] or cfg.get('approved')):
                placements, pending = _match_placements(event, teams, confirmed, rules)
                if len(placements) != len(teams):
                    placements = {}
            else:
                pending = 'Final placement awards await completed matches and authorized approval.'
        champion = next((team_id for team_id, rank in placements.items() if rank == 1), None)
        if not placements and complete:
            from .bracket_progression import _is_finals_match
            finals = [match for match in confirmed if _is_finals_match(match)]
            if finals:
                champion = finals[-1].winner_id
        for team in teams:
            value = wins[team.id] * rules['win_points'][classification] if rules['method'] in {'per_win', 'combined'} else 0
            value += table.get(placements.get(team.id), 0)
            team.points, team.loss_count = value, losses[team.id]
            team.is_champion = team.id == champion
            team.save(update_fields=['points', 'loss_count', 'is_champion'])
            if team.department_id:
                awards[str(team.department_id)] += value
        cfg['calculated_placements'] = {str(key): value for key, value in placements.items()}
    cfg['championship_awards'], cfg['championship_pending'] = dict(awards), pending
    event.result_processing_config = cfg
    event.save(update_fields=['result_processing_config'])
    return dict(awards)


def invalidate_scoring_publication(event, user=None, reason='Scoring results changed.'):
    cfg = dict(event.result_processing_config or {})
    if cfg.get('published_at') or event.results_finalized:
        cfg['championship_requires_republication'] = True
    for key in ('published_at', 'published_by', 'tabulation_confirmed_at', 'approved'):
        cfg.pop(key, None)
    cfg['ready_for_publication'] = False
    event.results_finalized = False
    event.result_processing_config = cfg
    event.save(update_fields=['result_processing_config', 'results_finalized', 'updated_at'])
    from .audit_service import record_audit
    record_audit(user=user, action='Withdrew Championship Publication', module='Championship',
                 event_name=event.name, description=reason, metadata={'event_id': event.pk})


@transaction.atomic
def reopen_match_for_correction(event, match_id, user, reason):
    from .models import ScoreSheet
    from .audit_service import record_audit
    Event.objects.select_for_update().get(pk=event.pk)
    match = BracketMatch.objects.select_for_update().filter(
        pk=match_id, event=event, is_automatic_advance=False, status__in=TERMINAL,
    ).first()
    if not match:
        raise ScoringRulesError('Choose a confirmed actual match from this event.')
    descendants, queue = set(), [match.next_match_winner_id, match.next_match_loser_id]
    while queue:
        key = queue.pop()
        if key is None or key in descendants:
            continue
        descendants.add(key)
        row = BracketMatch.objects.select_for_update().get(pk=key, event=event)
        if row.status in TERMINAL:
            raise ScoringRulesError('A dependent match is already confirmed. Reopen dependent matches first, starting with the final.')
        queue.extend([row.next_match_winner_id, row.next_match_loser_id])
    old = {'winner': match.winner_id, 'loser': match.loser_id, 'score_a': match.score_a, 'score_b': match.score_b}
    for target_id, team_id in ((match.next_match_winner_id, match.winner_id), (match.next_match_loser_id, match.loser_id)):
        if target_id and team_id:
            target = BracketMatch.objects.select_for_update().get(pk=target_id, event=event)
            if target.team_a_id == team_id:
                target.team_a = None
            if target.team_b_id == team_id:
                target.team_b = None
            target.save(update_fields=['team_a', 'team_b', 'updated_at'])
    match.winner, match.loser = None, None
    match.score_a, match.score_b, match.status = '', '', BracketMatch.STATUS_PENDING
    match.save()
    ScoreSheet.objects.filter(event=event, match=match).update(status=ScoreSheet.STATUS_PENDING)
    cfg = dict(event.result_processing_config or {})
    cfg.pop('championship_placements', None)
    cfg.pop('legacy_championship_awarded', None)
    event.result_processing_config = cfg
    event.status = Event.STATUS_ACTIVE
    event.save(update_fields=['result_processing_config', 'status', 'updated_at'])
    invalidate_scoring_publication(event, user, reason)
    if cfg.get('championship_scoring'):
        reconcile_championship_points(event, force=True)
    else:
        from .bracket_progression import recompute_points
        recompute_points(event)
    record_audit(user=user, action='Reopened Match for Correction', module='Championship',
                 event_name=event.name, description=reason,
                 metadata={'event_id': event.pk, 'match_id': match.pk, 'previous': old, 'updated': {'status': 'pending'}})


def official_department_awards(event_ids=None):
    from .public_service import published_events, standings_eligible
    events = published_events()
    if event_ids is not None:
        events = events.filter(pk__in=event_ids)
    events = events.prefetch_related('bracket_teams', 'bracket_matches', 'scoring_categories')
    for event in events:
        if not standings_eligible(event):
            continue
        cfg = event.result_processing_config or {}
        if cfg.get('championship_scoring'):
            if cfg.get('championship_pending') or 'published_championship_awards' not in cfg:
                continue
            for department_id, value in cfg['published_championship_awards'].items():
                yield event, int(department_id), value
        else:
            for team in event.bracket_teams.all():
                if team.department_id:
                    yield event, team.department_id, team.points
