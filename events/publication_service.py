"""Read-only match publication review using existing championship rules."""
from collections import Counter

from .models import BracketMatch, ScoreSheet
from .points_settings_service import _match_placements, match_championship_rules


def can_publish_event(user, event):
    """Administrators publish any event; faculty publish only their assigned sports events."""
    if not user or not user.is_authenticated or not user.is_active:
        return False
    roles = {name.casefold() for name in user.groups.values_list('name', flat=True)}
    if user.is_staff and not roles.intersection({'faculty', 'faculty in-charge', 'tabulator', 'judge', 'judges', 'scorer', 'viewers'}):
        return True
    if roles.intersection({'tabulator', 'judge', 'judges', 'scorer', 'viewers'}):
        return False
    from .faculty_service import event_type_label, faculty_can_access
    return bool(roles.intersection({'faculty', 'faculty in-charge'}) and
                event_type_label(event) == 'Match-Based' and faculty_can_access(user, event) and
                event.faculty_account_id == user.pk)


def faculty_publication_workflow(event):
    """Assigned faculty confirmation replaces separate approval for sports events."""
    from .faculty_service import event_type_label
    faculty = event.faculty_account if event.faculty_account_id else None
    if not faculty or not faculty.is_active or event_type_label(event) != 'Match-Based':
        return False
    roles = {name.casefold() for name in faculty.groups.values_list('name', flat=True)}
    return bool(roles.intersection({'faculty', 'faculty in-charge'}) and
                not roles.intersection({'tabulator', 'judge', 'judges', 'scorer', 'viewers'}))


def _placement_source(event, team_id, rank, matches, cfg):
    if str(team_id) in cfg.get('championship_placements', {}):
        return 'Authorized placement override'
    if not rank:
        return 'Not determined'
    if event.tournament_type == 'round_robin':
        return 'Confirmed Round Robin standings'
    from .bracket_progression import _is_finals_match
    if rank in (1, 2):
        deciding = [match for match in matches if _is_finals_match(match)]
    elif rank in (3, 4) and event.tournament_type == 'single_elimination':
        deciding = [match for match in matches if 'third' in match.round_name.lower() or '3rd' in match.round_name.lower()]
    else:
        deciding = [match for match in matches if match.loser_id == team_id]
    if not deciding:
        return 'Not determined'
    match = deciding[-1]
    return f'Game {match.match_number}: {match.round_name}'


def match_publication_review(event):
    cfg = event.result_processing_config or {}
    rules = match_championship_rules(event)
    matches = list(event.bracket_matches.filter(is_automatic_advance=False).select_related(
        'team_a', 'team_b', 'winner').order_by('match_number'))
    teams = list(event.bracket_teams.select_related('department'))
    completed = sum(m.status in {BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT} for m in matches)
    errors, approval_errors, placements = [], [], {}
    if not matches or completed != len(matches):
        errors.append('All required matches must be completed and confirmed.')
        if any(('third' in m.round_name.lower() or '3rd' in m.round_name.lower()) and m.status not in {'completed', 'forfeit'} for m in matches):
            errors.append('Third-Place Playoff is awaiting confirmation.')
        if any(m.round_name == 'Grand Final Reset' and m.status not in {'completed', 'forfeit'} for m in matches):
            errors.append('Grand Final reset is awaiting confirmation.')
    if ScoreSheet.objects.filter(event=event, status=ScoreSheet.STATUS_PENDING).exists():
        errors.append('Recorded match results are still pending confirmation.')
    team_ids = {t.pk for t in teams}
    if any(m.team_a_id not in team_ids or m.team_b_id not in team_ids or
           m.team_a_id == m.team_b_id or m.winner_id not in {m.team_a_id, m.team_b_id} or
           m.loser_id not in {m.team_a_id, m.team_b_id} or m.winner_id == m.loser_id for m in matches):
        errors.append('Each match needs valid participants and a confirmed winner and loser.')
    if not rules:
        errors.append('Championship scoring configuration is unavailable. Ask an administrator to configure this event.')
    else:
        try:
            if (rules['method'] not in {'placement', 'per_win', 'combined'} or
                    rules['classification'] not in {'major', 'minor'} or
                    rules['tie_policy'] not in {'official', 'shared', 'manual'} or
                    not rules['placements'] or
                    len({r['rank'] for r in rules['placements']}) != len(rules['placements']) or
                    any(type(r['rank']) is not int or r['rank'] < 1 or
                        any(type(r[c]) is not int or r[c] < 0 for c in ('major', 'minor'))
                        for r in rules['placements']) or
                    any(type(rules['win_points'][c]) is not int or rules['win_points'][c] < 0 for c in ('major', 'minor'))):
                errors.append('Championship scoring rules are invalid.')
        except (KeyError, TypeError, ValueError):
            errors.append('Championship scoring rules are invalid.')
    if rules and not errors:
        try:
            if event.results_finalized and cfg.get('published_at') and cfg.get('calculated_placements'):
                placements = {int(key): value for key, value in cfg['calculated_placements'].items()}
                pending = ''
            else:
                placements, pending = _match_placements(event, teams, matches, rules)
            if pending:
                errors.append(pending)
            if not set(placements).issubset(team_ids) or 1 not in placements.values() or any(type(rank) is not int or rank < 1 for rank in placements.values()):
                errors.append('Resolve all official final placements before submission.')
            if rules['tie_policy'] != 'shared' and len(set(placements.values())) != len(placements):
                errors.append('Resolve tied final placements before publication.')
        except (KeyError, TypeError, ValueError):
            errors.append('Championship scoring rules or final placements are invalid.')
    if not faculty_publication_workflow(event):
        if rules and rules.get('approval_required') and not cfg.get('approved'):
            approval_errors.append('Authorized final-result approval is required.')
        mode = cfg.get('verification_mode') or 'admin_only'
        if mode in {'tabulator_verification', 'tabulator_admin_approval'} and not cfg.get('tabulation_confirmed_at'):
            approval_errors.append('Tabulator verification is required.')
        if mode == 'tabulator_admin_approval' and not cfg.get('approved'):
            approval_errors.append('Admin approval is required.')
    wins = Counter(m.winner_id for m in matches)
    rows = []
    try:
        table = {r['rank']: r[rules['classification']] for r in rules['placements']} if rules else {}
        for team in teams:
            rank = placements.get(team.pk)
            points = table.get(rank, 0) if rules and rules['method'] in {'placement', 'combined'} else 0
            if rules and rules['method'] in {'per_win', 'combined'}:
                points += wins[team.pk] * rules['win_points'][rules['classification']]
            if not team.department_id:
                errors.append(f'{team.name} needs a department mapping before publication.')
            source = _placement_source(event, team.pk, rank, matches, cfg)
            rows.append({'source': source, 'name': team.name, 'department': team.department, 'rank': rank,
                         'placement': {1: 'Champion', 2: 'Runner-up', 3: 'Third place'}.get(rank, f'Place {rank}' if rank else 'Unranked'),
                         'placement_points': table.get(rank, 0), 'points': points})
    except (KeyError, TypeError, ValueError):
        errors.append('Championship placement points are invalid.')
    published = bool(event.results_finalized and cfg.get('published_at'))
    return {'matches': matches, 'completed': completed, 'total': len(matches),
            'all_confirmed': bool(matches) and completed == len(matches) and not ScoreSheet.objects.filter(
                event=event, status=ScoreSheet.STATUS_PENDING).exists(),
            'last_match': max((m.match_date for m in matches if m.match_date), default=None),
            'standings': sorted(rows, key=lambda row: row['rank'] or 999999),
            'errors': errors, 'approval_errors': approval_errors, 'published': published,
            'can_insert_playoff': not published and event.tournament_type == 'single_elimination' and len(teams) >= 4 and not any('third' in m.round_name.lower() or '3rd' in m.round_name.lower() for m in matches),
            'submitted': bool(cfg.get('publication_submitted_at')),
            'review_ready': not errors and not published,
            'can_publish': not errors and not approval_errors and not published}


def insert_missing_third_place(event, user):
    """Authorized, additive repair; never regenerate or edit confirmed outcomes."""
    from django.db import transaction
    from .audit_service import record_audit
    roles = {name.casefold() for name in user.groups.values_list('name', flat=True)}
    if not user.is_staff or roles.intersection({'faculty', 'faculty in-charge', 'tabulator', 'judge', 'judges', 'scorer', 'viewers'}):
        raise ValueError('Only an authorized administrator can insert a missing playoff.')
    with transaction.atomic():
        event = type(event).objects.select_for_update().get(pk=event.pk)
        if event.results_finalized or (event.result_processing_config or {}).get('published_at'):
            raise ValueError('Published tournaments cannot be modified.')
        if event.tournament_type != 'single_elimination' or event.bracket_teams.count() < 4:
            raise ValueError('A third-place playoff requires Single Elimination with at least four teams.')
        matches = list(event.bracket_matches.all())
        if any('third' in m.round_name.lower() or '3rd' in m.round_name.lower() for m in matches):
            return
        from .bracket_progression import _is_finals_match
        finals = [m for m in matches if _is_finals_match(m)]
        if len(finals) != 1:
            raise ValueError('A unique championship Final is required.')
        semis = [m for m in matches if m.next_match_winner_id == finals[0].pk]
        if len(semis) != 2 or any(m.is_automatic_advance or not m.loser_id or m.status not in {'completed', 'forfeit'} for m in semis):
            raise ValueError('Two confirmed semifinal losers are required before inserting the playoff.')
        if any(m.next_match_loser_id for m in semis):
            raise ValueError('Semifinal loser links already exist and require review.')
        number = max((m.match_number for m in matches), default=0) + 1
        playoff = BracketMatch.objects.create(event=event, match_number=number,
            round_name='Third Place', client_key='third-place', team_a_id=semis[0].loser_id,
            team_b_id=semis[1].loser_id, venue=event.venue)
        for match in semis:
            match.next_match_loser = playoff
            match.save(update_fields=['next_match_loser'])
        cfg = dict(event.result_processing_config or {})
        for key in ('approved', 'tabulation_confirmed_at'):
            cfg.pop(key, None)
        cfg['ready_for_publication'] = False
        event.result_processing_config = cfg
        event.status = event.STATUS_ACTIVE
        event.include_third_place = True
        event.save(update_fields=['include_third_place', 'result_processing_config', 'status'])
        record_audit(user=user, action='Inserted Missing Third-Place Playoff',
            description=f'Inserted only the missing playoff for "{event.name}".', module='Results', event_name=event.name)
