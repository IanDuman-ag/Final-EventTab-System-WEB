"""
Deterministic bracket progression engine.

Single source of truth for what happens when a scoresheet is approved/finalized:
  1. Sync the scoresheet's scores/winner onto its BracketMatch.
  2. Advance the winner to `next_match_winner` (and loser to `next_match_loser`).
  3. Recompute team points + champion designation for the event leaderboard.

Used by both the admin approval flow and the tabulator verify-and-lock flow so
the bracket behaves identically no matter who approves the result.
"""

# Points model (deterministic): each win is worth WIN_POINTS, and the overall
# champion (winner of a finals match with no further match) gets a bonus.
WIN_POINTS = 3
CHAMPION_BONUS = 5


def _is_finals_match(match):
    """The championship is the terminal match (no onward winner match) whose round
    name is the final — explicitly excluding 'semi final' / 'quarter final', which
    also contain the substring 'final'. Prefer Grand Final when present."""
    if match.next_match_winner_id is not None:
        return False
    name = (match.round_name or '').lower()
    if 'semi' in name or 'quarter' in name:
        return False
    if 'grand' in name:
        return True
    return 'final' in name or 'championship' in name


def link_bracket_advancement(event):
    """
    Ensure every match (except the last) has a `next_match_winner` link so winners
    can auto-advance. Admin-built layouts are saved without these links, so we
    infer them from round order: match i in round R feeds match i//2 in round R+1.

    Rounds are ordered by their first match_number. Safe to call repeatedly.
    Skips events that already have an authored advancement graph (including
    double-elimination winners/losers links) so inferred SE wiring cannot
    overwrite them.
    """
    from events.models import BracketMatch

    matches = list(
        BracketMatch.objects.filter(event=event).order_by('match_number')
    )
    if not matches:
        return

    if any(m.next_match_winner_id or m.next_match_loser_id for m in matches):
        return

    # Group matches into rounds, preserving encounter order.
    rounds = []
    seen = {}
    for m in matches:
        key = m.round_name or ''
        if key not in seen:
            seen[key] = []
            rounds.append(seen[key])
        seen[key].append(m)

    # Link each round's matches to the next round's matches (i -> i//2).
    for r_idx in range(len(rounds) - 1):
        current = rounds[r_idx]
        nxt = rounds[r_idx + 1]
        if not nxt:
            continue
        for i, m in enumerate(current):
            target = nxt[i // 2] if (i // 2) < len(nxt) else nxt[-1]
            if m.next_match_winner_id != target.id:
                m.next_match_winner = target
                m.save(update_fields=['next_match_winner'])


def recompute_points(event):
    """Recompute each team's wins-based points, loss count, and champion flag."""
    from events.models import BracketMatch, BracketTeam

    completed = BracketMatch.objects.filter(
        event=event, status=BracketMatch.STATUS_COMPLETED,
    ).select_related('winner', 'loser')

    wins = {}
    losses = {}
    champion_id = None
    for m in completed:
        if m.winner_id:
            wins[m.winner_id] = wins.get(m.winner_id, 0) + 1
        if m.loser_id:
            losses[m.loser_id] = losses.get(m.loser_id, 0) + 1
        if m.winner_id and _is_finals_match(m):
            champion_id = m.winner_id

    for team in BracketTeam.objects.filter(event=event):
        w = wins.get(team.id, 0)
        team.loss_count = losses.get(team.id, 0)
        team.points = w * WIN_POINTS + (CHAMPION_BONUS if team.id == champion_id else 0)
        team.is_champion = (team.id == champion_id)
        team.save(update_fields=['loss_count', 'points', 'is_champion'])

    return champion_id


def _place_team(match, team):
    """Seat a team into the first open slot of a match. Returns True if placed."""
    if team is None:
        return False
    if match.team_a_id == team.id or match.team_b_id == team.id:
        return False
    if match.team_a_id is None:
        match.team_a = team
        match.save(update_fields=['team_a'])
        return True
    if match.team_b_id is None:
        match.team_b = team
        match.save(update_fields=['team_b'])
        return True
    return False


def _advance_winner(match):
    if match.winner_id and match.next_match_winner_id:
        _place_team(match.next_match_winner, match.winner)


def _advance_loser(match):
    if match.loser_id and match.next_match_loser_id:
        _place_team(match.next_match_loser, match.loser)


def _auto_complete_lone_team_match(match, depth=0):
    """
    If a match has exactly one seated team and every feeder match is already
    completed (so no opponent can still arrive — typical after a winners-bracket
    bye), auto-advance that team.
    """
    from events.models import BracketMatch

    if depth > 16 or match is None:
        return
    match.refresh_from_db()
    if match.status == BracketMatch.STATUS_COMPLETED:
        return
    seated = [team for team in (match.team_a, match.team_b) if team is not None]
    if len(seated) != 1:
        return

    feeders = list(
        BracketMatch.objects.filter(event=match.event).filter(
            models_q_feeds(match)
        )
    )
    if not feeders:
        return
    if not all(feeder.status == BracketMatch.STATUS_COMPLETED for feeder in feeders):
        return

    lone = seated[0]
    match.winner = lone
    match.loser = None
    match.status = BracketMatch.STATUS_COMPLETED
    match.remarks = 'Auto-advance (Bye)'
    match.save(update_fields=['winner', 'loser', 'status', 'remarks'])
    _advance_winner(match)
    if match.next_match_winner_id:
        _auto_complete_lone_team_match(match.next_match_winner, depth=depth + 1)


def models_q_feeds(match):
    from django.db.models import Q
    return Q(next_match_winner=match) | Q(next_match_loser=match)


def apply_scoresheet_to_bracket(scoresheet):
    """
    Sync an approved/finalized scoresheet onto its bracket match, advance the
    winner (and loser), recompute points, and return a small result dict.
    """
    from events.models import BracketMatch

    match = getattr(scoresheet, 'match', None)
    if match is None:
        return {'updated': False, 'reason': 'no_match'}

    # Make sure advancement links exist (idempotent) before we push winners.
    link_bracket_advancement(match.event)
    match.refresh_from_db()

    # 1. Sync scores + winner/loser onto the match.
    try:
        match.score_a = f"{scoresheet.score_team_a:.1f}"
        match.score_b = f"{scoresheet.score_team_b:.1f}"
    except (TypeError, ValueError):
        pass

    if scoresheet.winner_id:
        match.winner = scoresheet.winner
        if match.winner_id == match.team_a_id:
            match.loser = match.team_b
        elif match.winner_id == match.team_b_id:
            match.loser = match.team_a

    match.status = BracketMatch.STATUS_COMPLETED
    match.save()

    # 2. Advance the winner and loser through the authored bracket graph.
    _advance_winner(match)
    _advance_loser(match)

    # 3. Resolve losers-bracket byes created when a winners-bracket bye leaves
    #    only one team dropping into a losers match.
    if match.next_match_loser_id:
        _auto_complete_lone_team_match(match.next_match_loser)
    if match.next_match_winner_id:
        _auto_complete_lone_team_match(match.next_match_winner)

    # 4. Recompute the event leaderboard standings.
    champion_id = recompute_points(match.event)

    is_championship = _is_finals_match(match)
    if is_championship and champion_id:
        ev = match.event
        if ev.status != ev.STATUS_COMPLETED:
            ev.status = ev.STATUS_COMPLETED
            ev.save(update_fields=['status'])

    return {
        'updated': True,
        'match_number': match.match_number,
        'is_championship': is_championship,
        'champion_id': champion_id,
    }
