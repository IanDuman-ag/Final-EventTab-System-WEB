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
    also contain the substring 'final'."""
    if match.next_match_winner_id is not None:
        return False
    name = (match.round_name or '').lower()
    if 'semi' in name or 'quarter' in name:
        return False
    return 'final' in name or 'championship' in name or 'grand' in name


def link_bracket_advancement(event):
    """
    Ensure every match (except the last) has a `next_match_winner` link so winners
    can auto-advance. Admin-built layouts are saved without these links, so we
    infer them from round order: match i in round R feeds match i//2 in round R+1.

    Rounds are ordered by their first match_number. Safe to call repeatedly.
    """
    from events.models import BracketMatch

    matches = list(
        BracketMatch.objects.filter(event=event).order_by('match_number')
    )
    if not matches:
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

    # 2. Advance the winner.
    if match.winner_id and match.next_match_winner_id:
        nxt = match.next_match_winner
        if nxt.team_a_id != match.winner_id and nxt.team_b_id != match.winner_id:
            if nxt.team_a_id is None:
                nxt.team_a = match.winner
            elif nxt.team_b_id is None:
                nxt.team_b = match.winner
            nxt.save()

    # 3. Advance the loser (double elimination / consolation).
    if match.loser_id and match.next_match_loser_id:
        nxt_l = match.next_match_loser
        if nxt_l.team_a_id != match.loser_id and nxt_l.team_b_id != match.loser_id:
            if nxt_l.team_a_id is None:
                nxt_l.team_a = match.loser
            elif nxt_l.team_b_id is None:
                nxt_l.team_b = match.loser
            nxt_l.save()

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
