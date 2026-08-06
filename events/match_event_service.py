"""Transactional validation, bracket generation, and scheduling for match events."""
import json
import random
from datetime import date, datetime, timedelta

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import BracketMatch, BracketTeam, Event, Team


class MatchEventValidationError(ValueError):
    pass


ALLOWED_DIVISIONS = {'Men', 'Women', 'Mixed', 'Open'}
ALLOWED_FORMATS = {'single_elimination', 'double_elimination', 'round_robin'}
ALLOWED_PAIRING = {'random_draw', 'seeded_draw', 'manual_pairing'}
ALLOWED_SPORT_TYPES = {
    'Basketball', 'Volleyball', 'Badminton', 'Table Tennis', 'Chess',
    'Sepak Takraw', 'Esports', 'Custom',
}
RESULT_FORMAT_BY_SPORT = {
    'Basketball': 'team_final_score',
    'Volleyball': 'best_of_sets',
    'Badminton': 'best_of_games',
    'Table Tennis': 'best_of_games',
    'Chess': 'win_draw_loss',
    'Sepak Takraw': 'best_of_sets',
    'Esports': 'maps_rounds_custom',
    'Custom': 'manual_winner',
}
MAJOR_POINTS = [
    {'label': '1st Place', 'points': 100},
    {'label': '2nd Place', 'points': 75},
    {'label': '3rd Place', 'points': 50},
    {'label': '4th Place', 'points': 25},
]
MINOR_POINTS = [
    {'label': '1st Place', 'points': 50},
    {'label': '2nd Place', 'points': 35},
    {'label': '3rd Place', 'points': 25},
    {'label': '4th Place', 'points': 15},
]
DEFAULT_POINTS = MAJOR_POINTS
DEFAULT_TIE_BREAKS = [
    'head_to_head',
    'score_difference',
    'points_scored',
    'fewest_penalties',
    'playoff_match',
    'manual_decision',
]
AUTOMATIC_ADVANCE_TOOLTIP = (
    'This team has no opponent in this round and will automatically proceed to the next round.'
)
MISSING_TEAM_LABEL = 'Team information unavailable'


def _team_name_from_lookup(team_id, names):
    """Resolve a team id to a display name. Never return the raw numeric id."""
    if team_id is None:
        return None
    try:
        key = int(team_id)
    except (TypeError, ValueError):
        import logging
        logging.getLogger(__name__).warning(
            'Invalid team id in bracket label resolution: %r', team_id
        )
        return MISSING_TEAM_LABEL
    name = names.get(key)
    if name:
        return str(name)
    import logging
    logging.getLogger(__name__).warning(
        'Missing team relationship for bracket participant id=%s', key
    )
    return MISSING_TEAM_LABEL


def resolve_side_label(match, side, names):
    """
    Build a user-facing label for one side of a match.
    Prefer seated team names; otherwise Winner/Loser dependency text.
    Never expose raw database ids.
    """
    team_id = match.get(f'team_{side}_id')
    stored = (match.get(f'label_{side}') or '').strip()
    if team_id is not None:
        return _team_name_from_lookup(team_id, names)
    if stored.startswith('Winner of') or stored.startswith('Loser of') or stored.startswith('Awaiting'):
        return stored
    if stored and not stored.isdigit():
        # Avoid leaking numeric ids that may have been stored historically.
        try:
            int(stored)
        except (TypeError, ValueError):
            return stored
        return MISSING_TEAM_LABEL
    return 'TBD'


def participant_payload(team_id, names, logos=None, seeds=None):
    """Structured participant info for API consumers."""
    if team_id is None:
        return None
    try:
        key = int(team_id)
    except (TypeError, ValueError):
        return {
            'team_id': None,
            'team_name': MISSING_TEAM_LABEL,
            'team_logo': '',
            'seed': None,
        }
    name = names.get(key)
    if not name:
        import logging
        logging.getLogger(__name__).warning(
            'Missing team relationship for bracket participant id=%s', key
        )
    return {
        'team_id': key,
        'team_name': str(name) if name else MISSING_TEAM_LABEL,
        'team_logo': (logos or {}).get(key, '') or '',
        'seed': (seeds or {}).get(key),
    }


def serialize_blueprint_matches(matches, names, logos=None, seeds=None):
    """
    Attach resolved display fields and structured participants to blueprint rows.
    IDs remain for relationships; display_* fields never use raw ids.
    """
    logos = logos or {}
    seeds = seeds or {}
    serialized = []
    for match in matches:
        team_a = resolve_side_label(match, 'a', names)
        team_b = resolve_side_label(match, 'b', names)
        is_advance = bool(match.get('is_automatic_advance') or match.get('synthetic_advance'))
        if is_advance and match.get('team_a_id') is not None:
            team_a = _team_name_from_lookup(match['team_a_id'], names)
            team_b = 'Automatic Advance'
            display_label = f'{team_a} — Automatic Advance'
        elif (
            team_a in ('', 'TBD')
            and (team_b.startswith('Winner of') or team_b.startswith('Loser of'))
            and match.get('team_a_id') is None
            and match.get('team_b_id') is None
            and not (match.get('label_a') or '').startswith(('Winner', 'Loser'))
        ):
            display_label = f'Awaiting {team_b}'
            team_a = display_label
            team_b = ''
        else:
            if not team_a:
                team_a = 'TBD'
            if not team_b:
                team_b = 'TBD'
            display_label = f'{team_a} vs {team_b}'

        dependency_label = 'Automatic Advance' if is_advance else display_label
        participant_a = participant_payload(match.get('team_a_id'), names, logos, seeds)
        participant_b = participant_payload(match.get('team_b_id'), names, logos, seeds)
        serialized.append({
            **match,
            'label_a': team_a,
            'label_b': team_b if not is_advance else 'Automatic Advance',
            'dependency_label': dependency_label,
            'team_a': team_a,
            'team_b': team_b if team_b else 'TBD',
            'display_label': display_label,
            'participant_a': participant_a,
            'participant_b': participant_b,
            'tooltip': AUTOMATIC_ADVANCE_TOOLTIP if is_advance else '',
        })
    return serialized


def match_display_name(bracket_team, fallback='TBD'):
    """Safe display name for a BracketTeam / Team snapshot."""
    if bracket_team is None:
        return fallback
    name = (getattr(bracket_team, 'name', None) or '').strip()
    if name:
        return name
    source = getattr(bracket_team, 'source_team', None)
    if source is not None:
        source_name = (getattr(source, 'name', None) or '').strip()
        if source_name:
            return source_name
    import logging
    logging.getLogger(__name__).warning(
        'Bracket team %s has no resolvable display name', getattr(bracket_team, 'pk', None)
    )
    return MISSING_TEAM_LABEL


def resolve_persisted_match_label(match):
    """Build schedule/bracket label from a saved BracketMatch row."""
    from django.db.models import Q

    if match.is_automatic_advance:
        return f'{match_display_name(match.team_a)} — Automatic Advance'

    left = match_display_name(match.team_a) if match.team_a_id else None
    right = match_display_name(match.team_b) if match.team_b_id else None
    stored = (match.dependency_label or '').strip()

    if left and right:
        return f'{left} vs {right}'

    # Rebuild dependency text from feeder matches when a side is unresolved.
    if left is None or right is None:
        feeders = list(
            BracketMatch.objects.filter(event_id=match.event_id).filter(
                Q(next_match_winner_id=match.pk) | Q(next_match_loser_id=match.pk)
            ).order_by('match_number', 'id')
        )
        pending = []
        for feeder in feeders:
            if feeder.is_automatic_advance:
                continue
            if feeder.match_number:
                via_loser = feeder.next_match_loser_id == match.pk
                prefix = 'Loser' if via_loser else 'Winner'
                pending.append(f'{prefix} of Game {feeder.match_number}')
        if left is None and pending:
            left = pending.pop(0)
        if right is None and pending:
            right = pending.pop(0)

    if left is None:
        if stored and ' vs ' in stored and not _label_contains_raw_id(stored):
            left = stored.split(' vs ', 1)[0].strip()
        else:
            left = 'TBD'
    if right is None:
        if stored and ' vs ' in stored and not _label_contains_raw_id(stored):
            right = stored.split(' vs ', 1)[1].strip()
        else:
            right = 'TBD'

    return f'{left} vs {right}'


def heal_unconfirmed_match_labels(event):
    """
    Repair stored dependency labels that accidentally contain raw team ids.
    Skips confirmed/completed non-advance matches so results stay intact.
    """
    updated = 0
    qs = event.bracket_matches.select_related('team_a', 'team_b')
    for match in qs:
        if (
            match.status in (BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_ONGOING)
            and not match.is_automatic_advance
        ):
            continue
        label = resolve_persisted_match_label(match)
        if match.dependency_label != label:
            match.dependency_label = label[:200]
            match.save(update_fields=['dependency_label'])
            updated += 1
    return updated


def _label_contains_raw_id(label):
    """True when a stored label looks like it embeds a bare numeric id."""
    import re
    if not label:
        return False
    parts = re.split(r'\s+vs\s+', label.strip(), maxsplit=1)
    for part in parts:
        token = part.strip()
        if token.isdigit():
            return True
    return False


def _opening_pairs(ordered_ids):
    """Pad to a power-of-two bracket and return first-round (team_a, team_b) pairs."""
    bracket_size = 1
    while bracket_size < len(ordered_ids):
        bracket_size *= 2
    byes = bracket_size - len(ordered_ids)
    opening_pairs = []
    cursor = 0
    for _ in range(byes):
        opening_pairs.append((ordered_ids[cursor], None))
        cursor += 1
    while cursor < len(ordered_ids):
        opening_pairs.append((ordered_ids[cursor], ordered_ids[cursor + 1]))
        cursor += 2
    return bracket_size, opening_pairs


def _blank_match(key, round_name, team_a_id=None, team_b_id=None):
    return {
        'key': key,
        'number': 0,
        'round': round_name,
        'team_a_id': team_a_id,
        'team_b_id': team_b_id,
        'next_winner_key': None,
        'next_loser_key': None,
        'is_automatic_advance': False,
        'label_a': '',
        'label_b': '',
        'dependency_label': '',
    }


def _se_round_name(round_index, round_count):
    """User-facing single-elimination round labels from bracket depth."""
    depth_from_final = round_count - 1 - round_index
    teams_in_round = 2 ** (depth_from_final + 1)
    if depth_from_final == 0:
        return 'Finals'
    if depth_from_final == 1:
        return 'Semifinals'
    if depth_from_final == 2:
        return 'Quarterfinals'
    if teams_in_round >= 32:
        return 'Round of 32'
    if teams_in_round >= 16:
        return 'Round of 16'
    return f'Round of {teams_in_round}'


def _wb_round_name(round_index, round_count):
    if round_index == round_count - 1:
        return 'Winners Final'
    if round_index == round_count - 2:
        return 'Winners Semifinals'
    if round_index == round_count - 3:
        return 'Winners Quarterfinals'
    return f'Winners Round {round_index + 1}'


def _lb_round_name(round_index, round_count):
    if round_index == round_count - 1:
        return 'Losers Final'
    return f'Losers Round {round_index + 1}'


def _place_seat(match, team_id):
    if match['team_a_id'] is None:
        match['team_a_id'] = team_id
    elif match['team_b_id'] is None and match['team_a_id'] != team_id:
        match['team_b_id'] = team_id


def _finalize_blueprint(matches):
    """
    Mark Automatic Advance (BYE) slots, cascade lone-team advances, drop orphan
    bracket nodes that can never host an actual game, then number only actual matches.
    """
    if not matches:
        return matches

    by_key = {match['key']: match for match in matches}
    for match in matches:
        # Pre-seated teams waiting for an opponent are NOT automatic advances.
        match['is_automatic_advance'] = False
        match['_resolved'] = False
        match['_winner_id'] = None

    def feeders_of(key):
        return [
            row for row in matches
            if row.get('next_winner_key') == key or row.get('next_loser_key') == key
        ]

    def resolve_advance(match, winner_id):
        match['is_automatic_advance'] = True
        match['_resolved'] = True
        match['_winner_id'] = winner_id
        match['next_loser_key'] = None
        nxt = by_key.get(match.get('next_winner_key'))
        if nxt and winner_id is not None:
            _place_seat(nxt, winner_id)

    # Only true opening BYE rows (no feeders, one seated team) are Automatic Advances.
    # With compressed brackets these rows are usually omitted; keep the guard for legacy graphs.
    for match in matches:
        feeders = feeders_of(match['key'])
        if (
            match['team_a_id'] is not None
            and match['team_b_id'] is None
            and not feeders
            and not match.get('synthetic_advance')
        ):
            resolve_advance(match, match['team_a_id'])

    # Cascade lone-team advances only when every feeder is already an Automatic Advance
    # (no opponent can still arrive from an actual unfinished match).
    progress = True
    while progress:
        progress = False
        for match in matches:
            if match['_resolved'] or match.get('synthetic_advance'):
                continue
            seated = [team for team in (match['team_a_id'], match['team_b_id']) if team is not None]
            if len(seated) != 1:
                continue
            feeders = feeders_of(match['key'])
            if feeders and all(feeder.get('is_automatic_advance') for feeder in feeders):
                resolve_advance(match, seated[0])
                progress = True

    # Drop orphan nodes that never receive participants (common after BYE compression).
    changed = True
    while changed:
        changed = False
        keep = []
        active_keys = {match['key'] for match in matches}
        for match in matches:
            feeders = [
                row for row in matches
                if row.get('next_winner_key') == match['key']
                or row.get('next_loser_key') == match['key']
            ]
            seated = match['team_a_id'] is not None or match['team_b_id'] is not None
            if (
                not seated
                and not feeders
                and match['key'] != 'gf'
                and not match.get('is_automatic_advance')
            ):
                for other in matches:
                    if other.get('next_winner_key') == match['key']:
                        other['next_winner_key'] = match.get('next_winner_key')
                    if other.get('next_loser_key') == match['key']:
                        other['next_loser_key'] = None
                changed = True
                continue
            keep.append(match)
        matches = keep
        by_key = {match['key']: match for match in matches}
        for match in matches:
            if match.get('next_winner_key') not in by_key:
                match['next_winner_key'] = None
            if match.get('next_loser_key') not in by_key:
                match['next_loser_key'] = None

    # Number only actual games. Automatic Advances keep number 0.
    number = 1
    for match in matches:
        if match.get('is_automatic_advance'):
            match['number'] = 0
        else:
            match['number'] = number
            number += 1

    def source_label(target_key, via_loser=False):
        sources = [
            row for row in matches
            if (row.get('next_loser_key') if via_loser else row.get('next_winner_key')) == target_key
        ]
        labels = []
        for source in sources:
            if source.get('is_automatic_advance') and source.get('team_a_id') is not None:
                continue
            game = source['number']
            if game:
                prefix = 'Loser' if via_loser else 'Winner'
                round_name = source.get('round') or ''
                if via_loser and 'Semi' in round_name:
                    labels.append(f'{prefix} of Semifinal Game {game}')
                else:
                    labels.append(f'{prefix} of Game {game}')
        return labels

    for match in matches:
        if match.get('is_automatic_advance'):
            # Keep ids only; names are resolved at serialization time.
            match['label_a'] = ''
            match['label_b'] = 'Automatic Advance'
            match['dependency_label'] = 'Automatic Advance'
            continue

        winner_labels = source_label(match['key'], via_loser=False)
        loser_labels = source_label(match['key'], via_loser=True)
        pending = winner_labels + loser_labels

        # Never store raw team ids in label fields — only dependency text or blanks.
        if match['team_a_id'] is not None:
            label_a = ''
        elif pending:
            label_a = pending.pop(0)
        else:
            label_a = 'TBD'

        if match['team_b_id'] is not None:
            label_b = ''
        elif pending:
            label_b = pending.pop(0)
        else:
            label_b = 'TBD'

        if match['team_a_id'] is None and match['team_b_id'] is None and (winner_labels or loser_labels):
            ordered = winner_labels + loser_labels
            if len(ordered) >= 2:
                label_a, label_b = ordered[0], ordered[1]
            elif len(ordered) == 1:
                label_a, label_b = ordered[0], 'TBD'

        match['label_a'] = label_a
        match['label_b'] = label_b
        # Temporary dependency text using dependency phrases only (no team ids).
        left = label_a or 'TBD'
        right = label_b or 'TBD'
        match['dependency_label'] = f'{left} vs {right}'

    for match in matches:
        match.pop('_resolved', None)
        match.pop('_winner_id', None)
    return matches


def count_actual_matches(matches):
    return sum(1 for match in matches if not match.get('is_automatic_advance'))


def count_automatic_advances(matches):
    return sum(1 for match in matches if match.get('is_automatic_advance'))


def _build_winners_bracket_rows(ordered_ids, key_prefix='wb'):
    """
    Build winners-bracket rows without Automatic Advance match nodes.
    Bye recipients are seated directly into the next round (and may meet there).
    Returns (matches, advances, final_key, round_keys).
    """
    from collections import defaultdict

    bracket_size, opening_pairs = _opening_pairs(ordered_ids)
    round_count = bracket_size.bit_length() - 1
    preseats = defaultdict(list)
    advances = []
    round_name_fn = _wb_round_name if key_prefix == 'wb' else _se_round_name

    for slot, (team_a_id, team_b_id) in enumerate(opening_pairs):
        if team_b_id is None and team_a_id is not None:
            # Seat the Automatic Advance recipient into the parent slot.
            parent_slot = slot // 2
            preseats[(1, parent_slot)].append(team_a_id)
            next_side = 'a' if len(preseats[(1, parent_slot)]) == 1 else 'b'
            advances.append({
                'team_id': team_a_id,
                'round': round_name_fn(0, round_count),
                'label': 'Automatic Advance',
                'opening_slot': slot,
                'parent_round': 1,
                'parent_slot': parent_slot,
                'next_side': next_side,
            })

    matches = []
    round_keys = []
    for round_index in range(round_count):
        count = bracket_size // (2 ** (round_index + 1))
        keys = [f'{key_prefix}-r{round_index + 1}-m{slot + 1}' for slot in range(count)]
        round_keys.append(keys)
        round_name = round_name_fn(round_index, round_count)
        for slot, key in enumerate(keys):
            if round_index == 0:
                team_a_id, team_b_id = opening_pairs[slot]
                if team_b_id is None:
                    continue
                matches.append(_blank_match(key, round_name, team_a_id, team_b_id))
                continue
            seated = preseats.get((round_index, slot), [])
            team_a_id = seated[0] if len(seated) > 0 else None
            team_b_id = seated[1] if len(seated) > 1 else None
            matches.append(_blank_match(key, round_name, team_a_id, team_b_id))

    match_by_key = {match['key']: match for match in matches}
    for round_index, keys in enumerate(round_keys[:-1]):
        for slot, key in enumerate(keys):
            if key not in match_by_key:
                continue
            match_by_key[key]['next_winner_key'] = round_keys[round_index + 1][slot // 2]
            match_by_key[key]['opening_slot'] = slot if round_index == 0 else None

    for advance in advances:
        parent_round = advance.get('parent_round', 1)
        parent_slot = advance.get('parent_slot', 0)
        if parent_round < len(round_keys) and parent_slot < len(round_keys[parent_round]):
            advance['next_winner_key'] = round_keys[parent_round][parent_slot]
        else:
            advance['next_winner_key'] = None

    final_key = round_keys[-1][0] if round_keys else None
    return matches, advances, final_key, round_keys


def _attach_synthetic_advances(matches, advances):
    """Expose Automatic Advances in the blueprint without numbering them as games."""
    attached = list(matches)
    for index, advance in enumerate(advances, start=1):
        attached.append({
            **_blank_match(f'aa-{index}', advance['round'], advance['team_id'], None),
            'is_automatic_advance': True,
            'number': 0,
            'label_a': '',
            'label_b': 'Automatic Advance',
            'dependency_label': 'Automatic Advance',
            'synthetic_advance': True,
            'next_winner_key': advance.get('next_winner_key'),
            'next_loser_key': None,
            'opening_slot': advance.get('opening_slot'),
            'next_side': advance.get('next_side'),
        })
    return attached


def bracket_display_graph(matches, draw_order=None):
    """
    Build connector edges and round columns for elimination bracket previews.
    Used by tests and mirrors the frontend tree relationships.
    """
    if not matches:
        return {'columns': [], 'edges': [], 'champion_from': None}

    advances = [m for m in matches if m.get('is_automatic_advance')]
    actual = [m for m in matches if not m.get('is_automatic_advance')]
    is_rr = all((m.get('round') or '') == 'Pool Play' for m in actual) and not advances
    if is_rr:
        return {
            'columns': [{'name': 'Pool Play', 'nodes': [
                {'key': m['key'], 'kind': 'match', 'number': m.get('number')} for m in actual
            ]}],
            'edges': [],
            'champion_from': None,
            'is_round_robin': True,
        }

    ordered = [int(x) for x in (draw_order or [])]
    if not ordered:
        seen = []
        for match in matches:
            for side in ('team_a_id', 'team_b_id'):
                tid = match.get(side)
                if tid is not None and tid not in seen:
                    seen.append(tid)
        ordered = seen

    bracket_size, opening_pairs = _opening_pairs(ordered) if ordered else (0, [])
    by_key = {m['key']: m for m in matches}
    advance_by_team = {}
    for advance in advances:
        tid = advance.get('team_a_id')
        if tid is not None:
            advance_by_team[int(tid)] = advance

    opening_nodes = []
    edges = []
    if opening_pairs:
        opening_round = opening_pairs and (
            next((m.get('round') for m in advances), None)
            or next((m.get('round') for m in actual if m.get('round')), 'Opening Round')
        )
        for slot, (team_a_id, team_b_id) in enumerate(opening_pairs):
            if team_b_id is None:
                advance = advance_by_team.get(int(team_a_id))
                if not advance:
                    continue
                opening_nodes.append({
                    'key': advance['key'],
                    'kind': 'advance',
                    'opening_slot': slot,
                    'round': advance.get('round') or opening_round,
                })
                nxt = advance.get('next_winner_key')
                if nxt and nxt in by_key:
                    edges.append({
                        'from_key': advance['key'],
                        'to_key': nxt,
                        'kind': 'automatic_advance',
                        'side': advance.get('next_side'),
                    })
            else:
                match = next(
                    (
                        m for m in actual
                        if {m.get('team_a_id'), m.get('team_b_id')} == {team_a_id, team_b_id}
                    ),
                    None,
                )
                if not match:
                    continue
                opening_nodes.append({
                    'key': match['key'],
                    'kind': 'match',
                    'opening_slot': slot,
                    'number': match.get('number'),
                    'round': match.get('round'),
                })
                nxt = match.get('next_winner_key')
                if nxt and nxt in by_key:
                    edges.append({
                        'from_key': match['key'],
                        'to_key': nxt,
                        'kind': 'winner_progression',
                    })

    later = [
        m for m in actual
        if m['key'] not in {n['key'] for n in opening_nodes}
        and 'Third Place' not in (m.get('round') or '')
    ]
    for match in later:
        nxt = match.get('next_winner_key')
        if nxt and nxt in by_key:
            edges.append({
                'from_key': match['key'],
                'to_key': nxt,
                'kind': 'winner_progression',
            })

    # Group later matches into columns by round order of first appearance.
    round_order = []
    rounds = {}
    if opening_nodes:
        round_name = opening_nodes[0].get('round') or 'Opening Round'
        rounds[round_name] = opening_nodes
        round_order.append(round_name)
    for match in later:
        name = match.get('round') or 'Round'
        if name not in rounds:
            rounds[name] = []
            round_order.append(name)
        rounds[name].append({
            'key': match['key'],
            'kind': 'match',
            'number': match.get('number'),
            'round': name,
        })

    final_matches = [
        m for m in actual
        if (m.get('round') or '') in ('Finals', 'Winners Final', 'Grand Final')
        and not m.get('next_winner_key')
    ]
    champion_from = final_matches[-1]['key'] if final_matches else None
    if champion_from:
        edges.append({
            'from_key': champion_from,
            'to_key': 'champion',
            'kind': 'champion',
        })

    columns = [{'name': name, 'nodes': rounds[name]} for name in round_order]
    if champion_from:
        columns.append({
            'name': 'Champion',
            'nodes': [{'key': 'champion', 'kind': 'champion'}],
        })

    return {
        'columns': columns,
        'edges': edges,
        'champion_from': champion_from,
        'is_round_robin': False,
        'bracket_size': bracket_size,
        'opening_slot_count': len(opening_pairs),
    }


def _build_double_elimination_matches(ordered_ids):
    """
    Build double-elimination with only actual games in the graph.
    Opening Automatic Advances are seat placements, not numbered matches.
    Grand Final Reset is never pre-generated (runtime only when WB finalist loses GF).
    Target actual matches: 2N-2.
    """
    n = len(ordered_ids)
    wb_matches, advances, wb_final_key, wb_round_keys = _build_winners_bracket_rows(ordered_ids, 'wb')
    matches = list(wb_matches)
    gf_key = 'gf'

    if n == 2:
        matches.append(_blank_match(gf_key, 'Grand Final'))
        match_by_key = {match['key']: match for match in matches}
        if wb_final_key and wb_final_key in match_by_key:
            match_by_key[wb_final_key]['next_winner_key'] = gf_key
            match_by_key[wb_final_key]['next_loser_key'] = gf_key
        return _attach_synthetic_advances(_finalize_blueprint(matches), advances)

    # Compact losers bracket: N-2 LB games + GF, with WB (N-1) => 2N-2 actual games.
    lb_count = max(0, n - 2)
    lb_keys = [f'lb-m{index + 1}' for index in range(lb_count)]
    for index, key in enumerate(lb_keys):
        round_name = 'Losers Final' if index == lb_count - 1 else f'Losers Round {index + 1}'
        matches.append(_blank_match(key, round_name))
    matches.append(_blank_match(gf_key, 'Grand Final'))
    match_by_key = {match['key']: match for match in matches}

    if wb_final_key and wb_final_key in match_by_key:
        match_by_key[wb_final_key]['next_winner_key'] = gf_key
        match_by_key[wb_final_key]['next_loser_key'] = lb_keys[-1] if lb_keys else gf_key

    for index, key in enumerate(lb_keys):
        match_by_key[key]['next_winner_key'] = lb_keys[index + 1] if index < len(lb_keys) - 1 else gf_key

    lb_cursor = 0
    for round_keys in wb_round_keys:
        for key in round_keys:
            match = match_by_key.get(key)
            if not match or key == wb_final_key:
                continue
            if lb_cursor < len(lb_keys):
                match['next_loser_key'] = lb_keys[lb_cursor]
                lb_cursor += 1

    return _attach_synthetic_advances(_finalize_blueprint(matches), advances)


def _build_single_elimination_matches(ordered_ids, include_third_place=False):
    matches, advances, _final_key, round_keys = _build_winners_bracket_rows(ordered_ids, 'r')
    match_by_key = {match['key']: match for match in matches}

    if include_third_place and len(round_keys) >= 2:
        third_key = 'third-place'
        matches.append(_blank_match(third_key, 'Third Place'))
        match_by_key = {match['key']: match for match in matches}
        for semifinal_key in round_keys[-2]:
            if semifinal_key in match_by_key:
                match_by_key[semifinal_key]['next_loser_key'] = third_key

    return _attach_synthetic_advances(_finalize_blueprint(matches), advances)


def normalize_tournament_type(value):
    """Normalize legacy display labels and stored keys to one safe key."""
    return '_'.join(
        str(value or '').strip().lower().replace('-', ' ').replace('_', ' ').split()
    )


def build_match_event_name(sport_type, sport_custom_name='', division=''):
    """
    Build a default Event Name from Sport/Game Type and Division.
    Examples: Men's Basketball, Chess – Open Division.
    """
    sport = (
        (sport_custom_name or '').strip()
        if (sport_type or '').strip() == 'Custom'
        else (sport_type or '').strip()
    )
    division = (division or '').strip()
    if not sport or not division:
        return ''
    if division in ('Men', 'Women'):
        return f"{division}'s {sport}"
    if division == 'Open':
        return f'{sport} – Open Division'
    if division == 'Mixed':
        return f'{sport} – Mixed Division'
    return f'{sport} – {division} Division'


def current_intramurals_season():
    """Return the active intramurals season key from system settings."""
    from .models import SystemSettings

    settings = SystemSettings.load()
    return (settings.academic_year or settings.intramurals_name or '').strip()


def ensure_unique_match_event_name(name, academic_year='', instance=None):
    """Event Name must be unique among match events in the same intramurals season."""
    season = (academic_year or current_intramurals_season()).strip()
    qs = Event.objects.filter(scoring_method='match', name__iexact=name.strip())
    if season:
        qs = qs.filter(academic_year=season)
    if instance is not None and instance.pk:
        qs = qs.exclude(pk=instance.pk)
    if qs.exists():
        season_label = season or 'the current intramurals season'
        raise MatchEventValidationError(
            f'An event named "{name.strip()}" already exists in {season_label}. '
            'Choose a unique Event Name.'
        )
    return name.strip()


def _required(data, key, label):
    value = (data.get(key) or '').strip()
    if not value:
        raise MatchEventValidationError(f'{label} is required.')
    return value


def _parse_date(value, label):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise MatchEventValidationError(f'{label} must be a valid date.')


def _parse_time(value, label, required=False):
    if not value and not required:
        return None
    try:
        return datetime.strptime(value, '%H:%M').time()
    except (TypeError, ValueError):
        raise MatchEventValidationError(f'{label} must be a valid time.')


def _json_list(value, label):
    try:
        parsed = json.loads(value or '[]')
    except (TypeError, json.JSONDecodeError):
        raise MatchEventValidationError(f'{label} is invalid.')
    if not isinstance(parsed, list):
        raise MatchEventValidationError(f'{label} is invalid.')
    return parsed


def _validated_points(data):
    rows = _json_list(data.get('points_config'), 'Championship points configuration')
    if not rows:
        rows = DEFAULT_POINTS
    cleaned = []
    for row in rows:
        label = str(row.get('label', '')).strip()
        try:
            points = int(row.get('points'))
        except (TypeError, ValueError):
            raise MatchEventValidationError('Every championship points row needs a numeric value.')
        if not label or points < 0:
            raise MatchEventValidationError('Championship point labels are required and values cannot be negative.')
        cleaned.append({'label': label[:50], 'points': points})
    return cleaned


def _validated_team_ids(data):
    raw_ids = _json_list(data.get('team_ids'), 'Participating teams')
    try:
        team_ids = list(dict.fromkeys(int(value) for value in raw_ids))
    except (TypeError, ValueError):
        raise MatchEventValidationError('Participating teams contain an invalid selection.')
    if len(team_ids) < 2:
        raise MatchEventValidationError('Select at least two active teams.')
    teams = list(
        Team.objects.filter(id__in=team_ids, status=Team.STATUS_ACTIVE)
        .select_related('department')
    )
    by_id = {team.id: team for team in teams}
    if len(by_id) != len(team_ids):
        raise MatchEventValidationError('One or more selected teams are unavailable.')
    return [by_id[team_id] for team_id in team_ids]


def _validated_faculty(data, user_model, required=True):
    raw_id = (data.get('faculty_account') or '').strip()
    if not raw_id:
        if required:
            raise MatchEventValidationError('Faculty In Charge is required.')
        return None
    if not raw_id.isdigit():
        raise MatchEventValidationError('Faculty In Charge is invalid.')
    faculty = user_model.objects.filter(pk=int(raw_id), is_active=True).filter(
        models_q_for_faculty()
    ).distinct().first()
    if not faculty:
        raise MatchEventValidationError('The selected Faculty In Charge is not authorized.')
    return faculty


def models_q_for_faculty():
    """Active Faculty portal accounts only (Tabulator / Faculty groups)."""
    from django.db.models import Q
    return (
        Q(groups__name__iexact='Tabulator')
        | Q(groups__name__iexact='Faculty')
    )


def build_match_blueprint(
    team_ids,
    tournament_type,
    include_third_place=False,
    draw_order=None,
    pairing_method='random_draw',
):
    """Return the canonical match graph used by preview and persistence."""
    ordered_ids = [int(team_id) for team_id in (draw_order or team_ids)]
    if draw_order is None:
        if pairing_method == 'seeded_draw':
            ordered_ids = list(ordered_ids)
        else:
            random.SystemRandom().shuffle(ordered_ids)
    if len(ordered_ids) < 2:
        raise MatchEventValidationError('Select at least two active teams.')
    if include_third_place and tournament_type == 'single_elimination' and len(ordered_ids) < 4:
        raise MatchEventValidationError('A third place match requires at least four teams.')

    matches = []
    if tournament_type == 'round_robin':
        number = 1
        for index, team_a_id in enumerate(ordered_ids):
            for team_b_id in ordered_ids[index + 1:]:
                row = _blank_match(f'rr-{number}', 'Pool Play', team_a_id, team_b_id)
                row['number'] = number
                matches.append(row)
                number += 1
        expected = len(ordered_ids) * (len(ordered_ids) - 1) // 2
        if len(matches) != expected:
            raise MatchEventValidationError('Round Robin match count is invalid.')
        return {
            'draw_order': ordered_ids,
            'matches': matches,
            'actual_match_count': len(matches),
            'automatic_advance_count': 0,
        }

    if tournament_type == 'double_elimination':
        matches = _build_double_elimination_matches(ordered_ids)
        return {
            'draw_order': ordered_ids,
            'matches': matches,
            'actual_match_count': count_actual_matches(matches),
            'automatic_advance_count': count_automatic_advances(matches),
        }

    if tournament_type != 'single_elimination':
        raise MatchEventValidationError('Tournament Format is invalid.')

    matches = _build_single_elimination_matches(
        ordered_ids,
        include_third_place=include_third_place,
    )
    return {
        'draw_order': ordered_ids,
        'matches': matches,
        'actual_match_count': count_actual_matches(matches),
        'automatic_advance_count': count_automatic_advances(matches),
    }


def _validated_draw_order(data, teams):
    draw_order = _json_list(data.get('draw_order'), 'Bracket draw')
    try:
        draw_order = [int(team_id) for team_id in draw_order]
    except (TypeError, ValueError):
        raise MatchEventValidationError('Bracket draw contains an invalid team.')
    selected_ids = [team.id for team in teams]
    if len(draw_order) != len(selected_ids) or set(draw_order) != set(selected_ids):
        raise MatchEventValidationError('Generate a current bracket preview before saving.')
    return draw_order


def _sync_teams(event, teams):
    existing = list(event.bracket_teams.all())
    existing_names = [team.name for team in existing]
    new_names = [team.name for team in teams]
    protected_matches_exist = event.bracket_matches.filter(
        status__in=[BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_ONGOING],
        is_automatic_advance=False,
    ).exclude(remarks__icontains='Automatic Advance').exclude(remarks__icontains='Bye').exists()
    if existing_names != new_names and protected_matches_exist:
        raise MatchEventValidationError(
            'Participating teams cannot change after a match has started or completed.'
        )
    if existing_names == new_names and protected_matches_exist:
        snapshots_by_source = {team.source_team_id: team for team in existing if team.source_team_id}
        if len(snapshots_by_source) != len(teams):
            snapshots_by_source = {
                source.id: next(snapshot for snapshot in existing if snapshot.name == source.name)
                for source in teams
            }
        return snapshots_by_source, True
    event.bracket_matches.all().delete()
    event.bracket_teams.all().delete()
    snapshots = [
        BracketTeam(
            event=event,
            source_team=team,
            name=team.name,
            department=team.department,
            members=team.members,
            seed=index,
        )
        for index, team in enumerate(teams, start=1)
    ]
    BracketTeam.objects.bulk_create(snapshots)
    created = list(event.bracket_teams.order_by('seed'))
    return {source.id: snapshot for source, snapshot in zip(teams, created)}, False


def _generate_matches(event, snapshots_by_source, blueprint):
    from events.bracket_progression import _auto_complete_lone_team_match

    names = {
        source_id: snapshot.name
        for source_id, snapshot in snapshots_by_source.items()
    }
    resolved_rows = {
        row['key']: row
        for row in serialize_blueprint_matches(
            [row for row in blueprint['matches'] if not row.get('synthetic_advance')],
            names,
        )
    }

    created_by_key = {}
    persistable = [
        row for row in blueprint['matches']
        if not row.get('synthetic_advance')
    ]
    for row in persistable:
        resolved = resolved_rows.get(row['key'], row)
        is_advance = bool(row.get('is_automatic_advance'))
        match = BracketMatch.objects.create(
            event=event,
            client_key=row['key'],
            match_number=row['number'],
            round_name=row['round'],
            team_a=snapshots_by_source.get(row['team_a_id']),
            team_b=snapshots_by_source.get(row['team_b_id']),
            status=BracketMatch.STATUS_PENDING,
            is_automatic_advance=is_advance,
            dependency_label=str(resolved.get('dependency_label') or resolved.get('display_label') or '')[:200],
            remarks='Automatic Advance (BYE)' if is_advance else '',
        )
        created_by_key[row['key']] = match
    for row in persistable:
        match = created_by_key[row['key']]
        match.next_match_winner = created_by_key.get(row['next_winner_key'])
        match.next_match_loser = created_by_key.get(row['next_loser_key'])
        update_fields = ['next_match_winner', 'next_match_loser']
        if row.get('is_automatic_advance') and match.team_a and match.next_match_winner:
            match.winner = match.team_a
            match.status = BracketMatch.STATUS_COMPLETED
            match.remarks = 'Automatic Advance (BYE)'
            match.is_automatic_advance = True
            target = match.next_match_winner
            if target.team_a is None:
                target.team_a = match.team_a
                target.save(update_fields=['team_a'])
            elif target.team_b is None:
                target.team_b = match.team_a
                target.save(update_fields=['team_b'])
            update_fields += ['winner', 'status', 'remarks', 'is_automatic_advance']
        match.save(update_fields=update_fields)

    for match in created_by_key.values():
        _auto_complete_lone_team_match(match)
        match.refresh_from_db()
        if match.remarks and 'bye' in match.remarks.lower() and not match.is_automatic_advance:
            match.is_automatic_advance = True
            if match.match_number != 0:
                match.match_number = 0
            match.remarks = 'Automatic Advance (BYE)'
            match.save(update_fields=['is_automatic_advance', 'match_number', 'remarks'])

    # Re-number actual matches after cascade advances so BYEs stay at 0.
    actual_number = 1
    ordered = list(event.bracket_matches.order_by('id'))
    for match in ordered:
        if match.is_automatic_advance:
            if match.match_number != 0:
                match.match_number = 0
                match.save(update_fields=['match_number'])
            continue
        if match.match_number != actual_number:
            match.match_number = actual_number
            match.save(update_fields=['match_number'])
        actual_number += 1

    return list(event.bracket_matches.order_by('match_number', 'id'))


def _schedule_config(event):
    config = event.schedule_config if isinstance(event.schedule_config, dict) else {}
    duration = int(config.get('match_duration_minutes') or 60)
    break_mins = int(config.get('break_between_matches_minutes') or 0)
    rest_mins = int(config.get('min_rest_minutes') or 0)
    courts = config.get('playing_areas') or []
    if not isinstance(courts, list) or not courts:
        courts = [event.venue or 'Court 1']
    courts = [str(name).strip()[:120] or f'Court {index + 1}' for index, name in enumerate(courts)]
    return {
        'duration': max(5, duration),
        'break': max(0, break_mins),
        'rest': max(0, rest_mins),
        'courts': courts,
    }


def _detect_schedule_conflicts(event, matches):
    """Return human-readable conflict warnings for overlapping team/venue usage."""
    warnings = []
    timed = [
        match for match in matches
        if not match.is_automatic_advance and match.match_date and match.match_time
    ]
    for index, left in enumerate(timed):
        left_start = datetime.combine(left.match_date, left.match_time)
        left_cfg = _schedule_config(event)
        left_end = left_start + timedelta(minutes=left_cfg['duration'])
        left_teams = {team.id for team in (left.team_a, left.team_b) if team}
        left_area = (left.playing_area or left.venue or '').strip().lower()
        for right in timed[index + 1:]:
            right_start = datetime.combine(right.match_date, right.match_time)
            right_end = right_start + timedelta(minutes=left_cfg['duration'])
            if left_end <= right_start or right_end <= left_start:
                continue
            right_teams = {team.id for team in (right.team_a, right.team_b) if team}
            overlap_teams = left_teams & right_teams
            if overlap_teams:
                warnings.append(
                    f'Game {left.match_number} and Game {right.match_number} overlap for the same team.'
                )
            right_area = (right.playing_area or right.venue or '').strip().lower()
            if left_area and right_area and left_area == right_area:
                warnings.append(
                    f'Game {left.match_number} and Game {right.match_number} share the same playing area.'
                )
    return warnings


def _auto_schedule(event, matches):
    start = event.daily_start_time
    end = event.daily_end_time
    if not start or not end or start >= end:
        raise MatchEventValidationError('Daily end time must be later than daily start time.')
    cfg = _schedule_config(event)
    slot_step = timedelta(minutes=cfg['duration'] + cfg['break'])
    rest_delta = timedelta(minutes=cfg['rest'])
    courts = cfg['courts']
    current_date = event.event_date
    day_start = datetime.combine(current_date, start)
    daily_end = datetime.combine(current_date, end)
    court_free_at = {name: day_start for name in courts}
    team_free_at = {}

    actual = [match for match in matches if not match.is_automatic_advance]
    for match in matches:
        if match.is_automatic_advance:
            match.match_date = None
            match.match_time = None
            match.venue = ''
            match.playing_area = ''
            match.save(update_fields=['match_date', 'match_time', 'venue', 'playing_area'])

    for match in actual:
        team_ids = [team.id for team in (match.team_a, match.team_b) if team]
        placed = False
        guard = 0
        while not placed and guard < 400:
            guard += 1
            court = min(courts, key=lambda name: court_free_at[name])
            candidate = max(
                [court_free_at[court], day_start]
                + [team_free_at[team_id] for team_id in team_ids if team_id in team_free_at]
            )
            if candidate + timedelta(minutes=cfg['duration']) > daily_end:
                current_date += timedelta(days=1)
                if current_date > event.end_date:
                    raise MatchEventValidationError(
                        'The selected date range and daily hours cannot fit every match.'
                    )
                day_start = datetime.combine(current_date, start)
                daily_end = datetime.combine(current_date, end)
                court_free_at = {name: day_start for name in courts}
                continue
            match.match_date = candidate.date()
            match.match_time = candidate.time()
            match.venue = event.venue
            match.playing_area = court
            match.save(update_fields=['match_date', 'match_time', 'venue', 'playing_area'])
            free_at = candidate + slot_step
            court_free_at[court] = free_at
            for team_id in team_ids:
                team_free_at[team_id] = candidate + timedelta(minutes=cfg['duration']) + rest_delta
            placed = True
        if not placed:
            raise MatchEventValidationError('Unable to auto-schedule all actual matches.')

    conflicts = _detect_schedule_conflicts(event, actual)
    if conflicts:
        config = event.schedule_config if isinstance(event.schedule_config, dict) else {}
        config = dict(config)
        config['conflicts'] = conflicts
        event.schedule_config = config
        event.save(update_fields=['schedule_config'])


def _manual_schedule(event, matches, data):
    actual = [match for match in matches if not match.is_automatic_advance]
    rows = _json_list(data.get('schedule_rows'), 'Manual schedule')
    rows_by_key = {str(row.get('match_key', '')): row for row in rows}
    match_keys = {match.client_key for match in actual}
    if not all(match_keys) or set(rows_by_key) != match_keys:
        raise MatchEventValidationError(
            'Manual schedule rows do not match the generated actual matches.'
        )
    for match in matches:
        if match.is_automatic_advance:
            match.match_date = None
            match.match_time = None
            match.venue = ''
            match.playing_area = ''
            match.save(update_fields=['match_date', 'match_time', 'venue', 'playing_area'])
            continue
        row = rows_by_key[match.client_key]
        match_date = _parse_date(row.get('date'), f'Match {match.match_number} date')
        match_time = _parse_time(row.get('time'), f'Match {match.match_number} time', required=True)
        venue = str(row.get('venue', '')).strip()
        playing_area = str(row.get('playing_area', '') or venue).strip()
        if not event.event_date <= match_date <= event.end_date:
            raise MatchEventValidationError('Every match date must be within the event date range.')
        if not venue:
            raise MatchEventValidationError('Every manual schedule row needs a venue.')
        match.match_date = match_date
        match.match_time = match_time
        match.venue = venue[:200]
        match.playing_area = playing_area[:120]
        match.save(update_fields=['match_date', 'match_time', 'venue', 'playing_area'])
    conflicts = _detect_schedule_conflicts(event, actual)
    if conflicts and (data.get('publication_status') or '') == Event.PUBLICATION_PUBLISHED:
        raise MatchEventValidationError('Resolve schedule conflicts before publishing: ' + '; '.join(conflicts[:3]))


def _notify_faculty(event, actor, created):
    """Log the assignment and email the faculty Gmail when they are assigned."""
    from django.conf import settings

    faculty = event.faculty_account
    if not faculty:
        return
    action = ADDITION if created else CHANGE
    LogEntry.objects.create(
        user=actor,
        content_type=ContentType.objects.get_for_model(Event),
        object_id=str(event.pk),
        object_repr=event.name,
        action_flag=action,
        change_message=f'Assigned {faculty.get_full_name() or faculty.username} as Faculty In Charge.',
    )
    email = (faculty.email or '').strip()
    if not email:
        return

    display_name = faculty.get_full_name().strip() or faculty.username
    start = event.event_date.strftime('%B %d, %Y') if event.event_date else 'TBD'
    end = (event.end_date or event.event_date)
    end_label = end.strftime('%B %d, %Y') if end else start
    subject = f'EventTab assignment: {event.name}'
    message = (
        f'Hello {display_name},\n\n'
        f'You have been assigned as Faculty In Charge for "{event.name}".\n\n'
        f'Schedule: {start} to {end_label}\n'
        f'Venue: {event.venue or "—"}\n\n'
        f'Sign in to the Faculty portal to manage this event.\n\n'
        f'— EventTab Admin'
    )
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None) or None

    def _send():
        try:
            send_mail(
                subject=subject,
                message=message,
                from_email=from_email,
                recipient_list=[email],
                fail_silently=True,
            )
        except Exception:
            pass

    transaction.on_commit(_send)


@transaction.atomic
def save_match_event(data, actor, instance=None):
    """Create or update a match event and atomically rebuild its pending blueprint."""
    from django.contrib.auth import get_user_model

    created = instance is None
    event = instance or Event(created_by=actor)
    classification = _required(data, 'event_classification', 'Event Classification')
    division = _required(data, 'division', 'Division')
    venue = _required(data, 'venue', 'Venue')
    start_date = _parse_date(_required(data, 'start_date', 'Start Date'), 'Start Date')
    end_date = _parse_date(_required(data, 'end_date', 'End Date'), 'End Date')
    if end_date < start_date:
        raise MatchEventValidationError('End Date cannot precede Start Date.')
    if classification not in dict(Event.CLASSIFICATION_CHOICES):
        raise MatchEventValidationError('Event Classification is invalid.')
    if division not in ALLOWED_DIVISIONS:
        raise MatchEventValidationError('Division is invalid.')

    sport_type_early = (data.get('sport_type') or '').strip()
    sport_custom_early = (data.get('sport_custom_name') or '').strip()
    name = (data.get('event_name') or '').strip()
    if not name:
        name = build_match_event_name(sport_type_early, sport_custom_early, division)
    if not name:
        raise MatchEventValidationError('Event Name is required.')
    academic_year = current_intramurals_season()
    ensure_unique_match_event_name(name, academic_year=academic_year, instance=instance)

    tournament_type = normalize_tournament_type(
        _required(data, 'tournament_type', 'Tournament Format')
    )
    if tournament_type not in ALLOWED_FORMATS:
        raise MatchEventValidationError('Tournament Format is invalid.')
    if (
        instance is not None
        and normalize_tournament_type(instance.tournament_type or 'single_elimination') != tournament_type
        and instance.bracket_matches.filter(
            status__in=[BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_ONGOING],
            is_automatic_advance=False,
        ).exists()
    ):
        raise MatchEventValidationError(
            'Event format cannot change after a match has started or completed.'
        )
    if (
        instance is not None
        and instance.bracket_matches.filter(
            status__in=[BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_ONGOING],
            client_key='',
            is_automatic_advance=False,
        ).exists()
    ):
        raise MatchEventValidationError(
            'This legacy bracket has recorded results and cannot be safely regenerated or remapped.'
        )

    publication = (data.get('publication_status') or Event.PUBLICATION_DRAFT).strip()
    if publication not in dict(Event.PUBLICATION_CHOICES):
        raise MatchEventValidationError('Publication status is invalid.')
    schedule_mode = (data.get('schedule_mode') or Event.SCHEDULE_AUTO).strip()
    if schedule_mode not in dict(Event.SCHEDULE_MODE_CHOICES):
        raise MatchEventValidationError('Schedule generation mode is invalid.')

    sport_type = (data.get('sport_type') or '').strip()
    if sport_type and sport_type not in ALLOWED_SPORT_TYPES:
        raise MatchEventValidationError('Sport/Game Type is invalid.')
    sport_custom = (data.get('sport_custom_name') or '').strip()
    if sport_type == 'Custom' and not sport_custom and publication == Event.PUBLICATION_PUBLISHED:
        raise MatchEventValidationError('Enter a custom sport or game name.')
    pairing_method = 'random_draw'
    if (data.get('pairing_method') or '').strip() not in ('', 'random_draw'):
        # Seeded / manual pairing removed from the wizard; force Random Draw.
        pairing_method = 'random_draw'
    result_entry_format = (data.get('result_entry_format') or '').strip()
    if not result_entry_format and sport_type:
        result_entry_format = RESULT_FORMAT_BY_SPORT.get(sport_type, 'manual_winner')

    publishing = publication == Event.PUBLICATION_PUBLISHED
    min_participants = 2
    if tournament_type == 'single_elimination' and data.get('include_third_place') == 'on':
        min_participants = 4
    try:
        teams = _validated_team_ids(data)
    except MatchEventValidationError:
        if publishing:
            raise
        teams = []
    if publishing and len(teams) < min_participants:
        raise MatchEventValidationError(
            f'Select at least {min_participants} participants for this tournament format.'
        )
    draw_order = (
        _validated_draw_order(data, teams)
        if len(teams) >= 2
        else [team.id for team in teams]
    )
    faculty = _validated_faculty(data, get_user_model(), required=publishing)
    points = _validated_points(data)
    tie_break_rules = _json_list(data.get('tie_break_rules'), 'Tie-breaking rules')
    if not tie_break_rules:
        tie_break_rules = list(DEFAULT_TIE_BREAKS)
    previous_faculty_id = event.faculty_account_id

    scorer = None
    raw_scorer = (data.get('assigned_scorer') or '').strip()
    if raw_scorer:
        if not raw_scorer.isdigit():
            raise MatchEventValidationError('Assigned Scorer is invalid.')
        scorer = get_user_model().objects.filter(pk=int(raw_scorer), is_active=True).first()
        if not scorer:
            raise MatchEventValidationError('The selected Assigned Scorer was not found.')

    playing_areas = _json_list(data.get('playing_areas'), 'Playing areas')
    playing_areas = [str(name).strip()[:120] for name in playing_areas if str(name).strip()]
    schedule_config = {
        'first_match_date': (data.get('first_match_date') or '').strip(),
        'match_duration_minutes': int(data.get('match_duration_minutes') or 60),
        'break_between_matches_minutes': int(data.get('break_between_matches_minutes') or 10),
        'min_rest_minutes': int(data.get('min_rest_minutes') or 30),
        'playing_area_count': int(data.get('playing_area_count') or max(1, len(playing_areas) or 1)),
        'playing_areas': playing_areas or [venue[:120]],
        'round_robin_mode': (data.get('round_robin_mode') or 'single').strip(),
    }

    event.name = name[:200]
    event.academic_year = academic_year[:40]
    event.category = 'Sports'
    event.scoring_method = 'match'
    event.event_classification = classification
    event.division = division
    event.venue = venue[:200]
    event.event_date = start_date
    event.end_date = end_date
    event.publication_status = publication
    event.status = Event.STATUS_UPCOMING if start_date > timezone.localdate() else Event.STATUS_ACTIVE
    event.tournament_type = tournament_type
    event.sport_type = sport_type[:80]
    event.sport_custom_name = sport_custom[:120] if sport_type == 'Custom' else ''
    event.result_entry_format = result_entry_format[:40]
    event.pairing_method = pairing_method
    event.tie_break_rules = tie_break_rules
    event.schedule_config = schedule_config
    event.assigned_scorer = scorer
    requested_third_place = (
        data.get('include_third_place') == 'on' and tournament_type == 'single_elimination'
    )
    if requested_third_place and len(teams) < 4 and publishing:
        raise MatchEventValidationError('A third place match requires at least four teams.')
    event.include_third_place = requested_third_place and len(teams) >= 4
    event.seeding_method = pairing_method if pairing_method in {'random_draw', 'seeded_draw'} else 'random_draw'
    event.bracket_draw_order = draw_order
    event.schedule_mode = schedule_mode
    event.daily_start_time = _parse_time(data.get('daily_start_time'), 'Daily Start Time')
    event.daily_end_time = _parse_time(data.get('daily_end_time'), 'Daily End Time')
    event.faculty_account = faculty
    event.faculty_in_charge = (
        (faculty.get_full_name().strip() or faculty.username) if faculty else ''
    )
    event.auto_update_bracket = True
    event.allow_result_editing = True
    event.require_faculty_confirmation = True
    event.apply_championship_points = data.get('apply_championship_points') == 'on'
    event.championship_points_config = points
    event.num_teams = len(teams)

    raw_tpl = (data.get('scoresheet_template') or '').strip()
    if raw_tpl:
        from .models import ScoresheetTemplate
        tpl = ScoresheetTemplate.objects.filter(
            pk=raw_tpl,
            event_type=ScoresheetTemplate.EVENT_MATCH,
            status=ScoresheetTemplate.STATUS_ACTIVE,
        ).first()
        event.scoresheet_template = tpl
    else:
        # Prefer sport/game type over category (all match events are Sports).
        from .models import ScoresheetTemplate
        sport_label = sport_custom if sport_type == 'Custom' else sport_type
        tpl = None
        if sport_label:
            tpl = ScoresheetTemplate.objects.filter(
                event_type=ScoresheetTemplate.EVENT_MATCH,
                status=ScoresheetTemplate.STATUS_ACTIVE,
                category__iexact=sport_label,
            ).first()
        event.scoresheet_template = tpl

    if publishing:
        if not sport_type:
            raise MatchEventValidationError('Sport/Game Type is required to publish.')
        if not result_entry_format:
            raise MatchEventValidationError('Result Entry Format is required to publish.')
        if not faculty:
            raise MatchEventValidationError('Faculty In Charge is required to publish.')
        if event.apply_championship_points and not points:
            raise MatchEventValidationError('Championship points are required to publish.')
        if len(teams) < min_participants:
            raise MatchEventValidationError(
                f'Select at least {min_participants} participants before publishing.'
            )

    event.save()
    if faculty and event.faculty_account_id != faculty.id:
        Event.objects.filter(pk=event.pk).update(
            faculty_account_id=faculty.id,
            faculty_in_charge=faculty.get_full_name().strip() or faculty.username,
        )
        event.faculty_account = faculty

    if len(teams) < 2:
        return event

    snapshots_by_source, preserve_matches = _sync_teams(event, teams)
    blueprint = build_match_blueprint(
        [team.id for team in teams],
        tournament_type,
        include_third_place=requested_third_place,
        draw_order=draw_order,
        pairing_method=pairing_method,
    )
    matches = (
        list(event.bracket_matches.order_by('match_number', 'id'))
        if preserve_matches else _generate_matches(event, snapshots_by_source, blueprint)
    )
    if preserve_matches:
        persistable = [row for row in blueprint['matches'] if not row.get('synthetic_advance')]
        if len(matches) != len(persistable):
            raise MatchEventValidationError(
                'The stored bracket no longer matches this format and cannot be regenerated after results exist.'
            )
        for match, blueprint_row in zip(matches, persistable):
            if not match.client_key:
                match.client_key = blueprint_row['key']
                match.save(update_fields=['client_key'])
    if schedule_mode == Event.SCHEDULE_AUTO:
        _auto_schedule(event, matches)
    else:
        _manual_schedule(event, matches, data)

    if faculty and (created or previous_faculty_id != faculty.id):
        _notify_faculty(event, actor, created)
    return event
