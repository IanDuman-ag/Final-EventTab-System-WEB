import math
import random
from events.models import BracketTeam, BracketMatch


def order_teams_for_draw(teams, draw_method="seeded"):
    """
    Return teams ordered for first-round placement.
      - 'random'  → shuffled (Random Draw)
      - 'seeded'  → standard seed pairing (1 vs N, 2 vs N-1, ...) (Seeded Draw)
    """
    method = (draw_method or 'seeded').strip().lower()
    ordered = sorted(teams, key=lambda t: (t.seed or 0))
    if method in ('random', 'random_draw', 'randomized'):
        shuffled = ordered[:]
        random.shuffle(shuffled)
        return shuffled
    # Seeded draw: fold the list so top seeds meet bottom seeds first.
    out = []
    i, j = 0, len(ordered) - 1
    while i < j:
        out.append(ordered[i])
        out.append(ordered[j])
        i += 1
        j -= 1
    if i == j:
        out.append(ordered[i])
    return out

def generate_single_elimination(event, teams, round_name_prefix="Round"):
    """
    Generates a single elimination bracket for a list of BracketTeam objects.
    Returns the created matches.
    """
    if not teams:
        return []

    num_teams = len(teams)
    
    # Calculate power of 2
    bracket_size = 1
    while bracket_size < num_teams:
        bracket_size *= 2
        
    num_byes = bracket_size - num_teams
    num_matches = bracket_size - 1
    
    matches = []
    
    # Initialize the match tree
    # We will represent it as a flat array where match i's parents are 2i+1 and 2i+2
    # Match 0 is the final.
    match_objects = []
    
    # We need to determine how many rounds there are
    num_rounds = int(math.log2(bracket_size))
    
    # Create match records back to front
    match_number = num_matches
    round_matches = {}
    
    for r in range(num_rounds):
        round_matches[r] = []
        matches_in_round = 2 ** r
        
        round_title = "Finals" if r == 0 else "Semi Finals" if r == 1 else "Quarter Finals" if r == 2 else f"{round_name_prefix} {num_rounds - r}"
        
        for i in range(matches_in_round):
            m = BracketMatch(
                event=event,
                match_number=match_number,
                round_name=round_title,
                status=BracketMatch.STATUS_PENDING
            )
            # We don't save yet until we link them
            round_matches[r].append(m)
            match_number -= 1

    # Save all matches (we have to save them from final upwards, or save all and then update foreign keys)
    # Actually, the easiest is to save all, then update the next_match_winner
    all_created_matches = []
    for r in range(num_rounds - 1, -1, -1):
        for m in round_matches[r]:
            m.save()
            all_created_matches.append(m)
            
    for r in range(num_rounds - 1):
        for i, m in enumerate(round_matches[r]):
            child1 = round_matches[r+1][2*i]
            child2 = round_matches[r+1][2*i + 1]
            child1.next_match_winner = m
            child2.next_match_winner = m
            child1.save()
            child2.save()

    # Place teams in the first round (which is round num_rounds - 1)
    first_round_matches = round_matches[num_rounds - 1]
    
    # Simple placement for now (can be improved with real seeding 1 vs 16, etc.)
    team_idx = 0
    for m in first_round_matches:
        if team_idx < len(teams):
            m.team_a = teams[team_idx]
            team_idx += 1
        
        # If we have teams left and we're not assigning a bye to this match yet
        # Byes mean team_b is None, and team_a automatically advances
        if team_idx < len(teams) and num_byes < (len(first_round_matches) * 2 - team_idx):
             m.team_b = teams[team_idx]
             team_idx += 1
        else:
             # Bye case
             num_byes -= 1
             
        if m.team_b is None and m.team_a is not None:
             # Auto-advance team A
             m.winner = m.team_a
             m.status = BracketMatch.STATUS_COMPLETED
             m.remarks = "Auto-advance (Bye)"
             
             if m.next_match_winner:
                 # Push team A to the next match
                 next_m = m.next_match_winner
                 if next_m.team_a is None:
                     next_m.team_a = m.team_a
                 else:
                     next_m.team_b = m.team_a
                 next_m.save()
        
        m.save()
        
    return all_created_matches


def generate_double_elimination(event, teams):
    """
    Generates a basic double elimination bracket.
    """
    # For now, fallback to single elimination + basic loser tracking.
    # A true generic double elimination is extremely complex. We'll start with basic winner tree.
    return generate_single_elimination(event, teams, round_name_prefix="Winner Bracket R")

def generate_round_robin(event, teams):
    """
    Generates round robin matches.
    """
    matches = []
    match_number = 1
    for i in range(len(teams)):
        for j in range(i + 1, len(teams)):
            m = BracketMatch.objects.create(
                event=event,
                match_number=match_number,
                round_name="Pool Play",
                team_a=teams[i],
                team_b=teams[j],
                status=BracketMatch.STATUS_PENDING
            )
            matches.append(m)
            match_number += 1
    return matches


def generate_best_of_series(event, teams, best_of=3):
    """
    Generates a single-elimination tree where every matchup is a best-of-N series.

    The bracket structure (rounds, advancement links) is identical to single
    elimination; each match is annotated as a "Best of N" series so the score
    fields capture the series result (e.g. 2-1). Winners still advance via
    `next_match_winner`, so the existing progression engine works unchanged.
    """
    matches = generate_single_elimination(event, teams)
    label = f"Best of {best_of}"
    for m in matches:
        # Preserve any existing remark (e.g. bye auto-advance) and tag the series.
        m.remarks = f"{label}{(' — ' + m.remarks) if m.remarks else ''}"
        m.save(update_fields=['remarks'])
    return matches
