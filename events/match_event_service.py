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
ALLOWED_FORMATS = {'single_elimination', 'round_robin'}
DEFAULT_POINTS = [
    {'label': '1st Place', 'points': 15},
    {'label': '2nd Place', 'points': 10},
    {'label': '3rd Place', 'points': 7},
    {'label': '4th Place', 'points': 5},
]


def normalize_tournament_type(value):
    """Normalize legacy display labels and stored keys to one safe key."""
    return '_'.join(
        str(value or '').strip().lower().replace('-', ' ').replace('_', ' ').split()
    )


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


def _validated_faculty(data, user_model):
    raw_id = (data.get('faculty_account') or '').strip()
    if not raw_id:
        raise MatchEventValidationError('Faculty In Charge is required.')
    if not raw_id.isdigit():
        raise MatchEventValidationError('Faculty In Charge is invalid.')
    faculty = user_model.objects.filter(pk=int(raw_id), is_active=True).filter(
        models_q_for_faculty()
    ).distinct().first()
    if not faculty:
        raise MatchEventValidationError('The selected Faculty In Charge is not authorized.')
    return faculty


def models_q_for_faculty():
    from django.db.models import Q
    return Q(is_staff=True) | Q(groups__name__iexact='Faculty') | Q(groups__name__iexact='Scorer')


def build_match_blueprint(team_ids, tournament_type, include_third_place=False, draw_order=None):
    """Return the canonical match graph used by preview and persistence."""
    ordered_ids = [int(team_id) for team_id in (draw_order or team_ids)]
    if draw_order is None:
        random.SystemRandom().shuffle(ordered_ids)
    if len(ordered_ids) < 2:
        raise MatchEventValidationError('Select at least two active teams.')
    if include_third_place and len(ordered_ids) < 4:
        raise MatchEventValidationError('A third place match requires at least four teams.')

    matches = []
    if tournament_type == 'round_robin':
        number = 1
        for index, team_a_id in enumerate(ordered_ids):
            for team_b_id in ordered_ids[index + 1:]:
                matches.append({
                    'key': f'rr-{number}',
                    'number': number,
                    'round': 'Pool Play',
                    'team_a_id': team_a_id,
                    'team_b_id': team_b_id,
                    'next_winner_key': None,
                    'next_loser_key': None,
                })
                number += 1
        return {'draw_order': ordered_ids, 'matches': matches}

    if tournament_type != 'single_elimination':
        raise MatchEventValidationError('Event Type is invalid.')

    bracket_size = 1
    while bracket_size < len(ordered_ids):
        bracket_size *= 2
    round_count = bracket_size.bit_length() - 1
    byes = bracket_size - len(ordered_ids)
    opening_pairs = []
    cursor = 0
    for _ in range(byes):
        opening_pairs.append((ordered_ids[cursor], None))
        cursor += 1
    while cursor < len(ordered_ids):
        opening_pairs.append((ordered_ids[cursor], ordered_ids[cursor + 1]))
        cursor += 2

    round_keys = []
    number = 1
    for round_index in range(round_count):
        count = bracket_size // (2 ** (round_index + 1))
        keys = [f'r{round_index + 1}-m{slot + 1}' for slot in range(count)]
        round_keys.append(keys)
        if round_index == round_count - 1:
            round_name = 'Finals'
        elif round_index == round_count - 2:
            round_name = 'Semi Finals'
        elif round_index == round_count - 3:
            round_name = 'Quarter Finals'
        else:
            round_name = f'Round {round_index + 1}'
        for slot, key in enumerate(keys):
            team_a_id, team_b_id = opening_pairs[slot] if round_index == 0 else (None, None)
            next_key = (
                round_keys[round_index + 1][slot // 2]
                if round_index + 1 < len(round_keys) else None
            )
            matches.append({
                'key': key,
                'number': number,
                'round': round_name,
                'team_a_id': team_a_id,
                'team_b_id': team_b_id,
                'next_winner_key': next_key,
                'next_loser_key': None,
            })
            number += 1

    # Round keys are not all known while matches are appended; fill winner links now.
    match_by_key = {match['key']: match for match in matches}
    for round_index, keys in enumerate(round_keys[:-1]):
        for slot, key in enumerate(keys):
            match_by_key[key]['next_winner_key'] = round_keys[round_index + 1][slot // 2]

    if include_third_place:
        third_key = 'third-place'
        matches.append({
            'key': third_key,
            'number': number,
            'round': 'Third Place',
            'team_a_id': None,
            'team_b_id': None,
            'next_winner_key': None,
            'next_loser_key': None,
        })
        if len(round_keys) >= 2:
            for semifinal_key in round_keys[-2]:
                match_by_key[semifinal_key]['next_loser_key'] = third_key
    return {'draw_order': ordered_ids, 'matches': matches}


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
        status__in=[BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_ONGOING]
    ).exists()
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
    created_by_key = {}
    for row in blueprint['matches']:
        match = BracketMatch.objects.create(
            event=event,
            client_key=row['key'],
            match_number=row['number'],
            round_name=row['round'],
            team_a=snapshots_by_source.get(row['team_a_id']),
            team_b=snapshots_by_source.get(row['team_b_id']),
            status=BracketMatch.STATUS_PENDING,
        )
        created_by_key[row['key']] = match
    for row in blueprint['matches']:
        match = created_by_key[row['key']]
        match.next_match_winner = created_by_key.get(row['next_winner_key'])
        match.next_match_loser = created_by_key.get(row['next_loser_key'])
        update_fields = ['next_match_winner', 'next_match_loser']
        if (
            row['team_a_id'] is not None
            and row['team_b_id'] is None
            and match.team_a
            and match.next_match_winner
        ):
            match.winner = match.team_a
            match.status = BracketMatch.STATUS_COMPLETED
            match.remarks = 'Auto-advance (Bye)'
            target = match.next_match_winner
            if target.team_a is None:
                target.team_a = match.team_a
                target.save(update_fields=['team_a'])
            elif target.team_b is None:
                target.team_b = match.team_a
                target.save(update_fields=['team_b'])
            update_fields += ['winner', 'status', 'remarks']
        match.save(update_fields=update_fields)
    return list(event.bracket_matches.order_by('match_number'))


def _auto_schedule(event, matches):
    start = event.daily_start_time
    end = event.daily_end_time
    if not start or not end or start >= end:
        raise MatchEventValidationError('Daily end time must be later than daily start time.')
    current_date = event.event_date
    current = datetime.combine(current_date, start)
    daily_end = datetime.combine(current_date, end)
    for match in matches:
        if current > daily_end:
            current_date += timedelta(days=1)
            if current_date > event.end_date:
                raise MatchEventValidationError(
                    'The selected date range and daily hours cannot fit every match.'
                )
            current = datetime.combine(current_date, start)
            daily_end = datetime.combine(current_date, end)
        match.match_date = current_date
        match.match_time = current.time()
        match.venue = event.venue
        match.save(update_fields=['match_date', 'match_time', 'venue'])
        current += timedelta(hours=1)


def _manual_schedule(event, matches, data):
    rows = _json_list(data.get('schedule_rows'), 'Manual schedule')
    if len(rows) != len(matches):
        raise MatchEventValidationError('Provide a schedule row for every generated match.')
    rows_by_key = {str(row.get('match_key', '')): row for row in rows}
    match_keys = {match.client_key for match in matches}
    if not all(match_keys) or set(rows_by_key) != match_keys:
        raise MatchEventValidationError('Manual schedule rows do not match the generated bracket.')
    for match in matches:
        row = rows_by_key[match.client_key]
        match_date = _parse_date(row.get('date'), f'Match {match.match_number} date')
        match_time = _parse_time(row.get('time'), f'Match {match.match_number} time', required=True)
        venue = str(row.get('venue', '')).strip()
        if not event.event_date <= match_date <= event.end_date:
            raise MatchEventValidationError('Every match date must be within the event date range.')
        if not venue:
            raise MatchEventValidationError('Every manual schedule row needs a venue.')
        match.match_date = match_date
        match.match_time = match_time
        match.venue = venue[:200]
        match.save(update_fields=['match_date', 'match_time', 'venue'])


def _notify_faculty(event, actor, created):
    faculty = event.faculty_account
    action = ADDITION if created else CHANGE
    LogEntry.objects.create(
        user=actor,
        content_type=ContentType.objects.get_for_model(Event),
        object_id=str(event.pk),
        object_repr=event.name,
        action_flag=action,
        change_message=f'Assigned {faculty.get_full_name() or faculty.username} as Faculty In Charge.',
    )
    if faculty.email:
        transaction.on_commit(lambda: send_mail(
            subject=f'EventTab assignment: {event.name}',
            message=(
                f'You have been assigned as Faculty In Charge for {event.name}, '
                f'scheduled from {event.event_date:%B %d, %Y} to {event.end_date:%B %d, %Y}.'
            ),
            from_email=None,
            recipient_list=[faculty.email],
            fail_silently=True,
        ))


@transaction.atomic
def save_match_event(data, actor, instance=None):
    """Create or update a match event and atomically rebuild its pending blueprint."""
    from django.contrib.auth import get_user_model

    created = instance is None
    event = instance or Event(created_by=actor)
    name = _required(data, 'event_name', 'Event Name')
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

    tournament_type = normalize_tournament_type(
        _required(data, 'tournament_type', 'Event Type')
    )
    if tournament_type == 'double_elimination':
        raise MatchEventValidationError(
            'Double Elimination is not available because the current engine does not support a complete loser bracket.'
        )
    if tournament_type not in ALLOWED_FORMATS:
        raise MatchEventValidationError('Event Type is invalid.')
    if (
        instance is not None
        and normalize_tournament_type(instance.tournament_type or 'single_elimination') != tournament_type
        and instance.bracket_matches.filter(
            status__in=[BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_ONGOING]
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

    teams = _validated_team_ids(data)
    draw_order = _validated_draw_order(data, teams)
    faculty = _validated_faculty(data, get_user_model())
    points = _validated_points(data)
    previous_faculty_id = event.faculty_account_id

    event.name = name[:200]
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
    requested_third_place = (
        data.get('include_third_place') == 'on' and tournament_type == 'single_elimination'
    )
    if requested_third_place and len(teams) < 4:
        raise MatchEventValidationError('A third place match requires at least four teams.')
    event.include_third_place = requested_third_place
    event.seeding_method = 'random_draw'
    event.bracket_draw_order = draw_order
    event.schedule_mode = schedule_mode
    event.daily_start_time = _parse_time(data.get('daily_start_time'), 'Daily Start Time')
    event.daily_end_time = _parse_time(data.get('daily_end_time'), 'Daily End Time')
    event.faculty_account = faculty
    event.faculty_in_charge = faculty.get_full_name().strip() or faculty.username
    event.auto_update_bracket = data.get('auto_update_bracket') == 'on'
    event.allow_result_editing = data.get('allow_result_editing') == 'on'
    event.require_faculty_confirmation = data.get('require_faculty_confirmation') == 'on'
    event.apply_championship_points = data.get('apply_championship_points') == 'on'
    event.championship_points_config = points
    event.num_teams = len(teams)
    event.save()

    snapshots_by_source, preserve_matches = _sync_teams(event, teams)
    blueprint = build_match_blueprint(
        [team.id for team in teams],
        tournament_type,
        include_third_place=requested_third_place,
        draw_order=draw_order,
    )
    matches = (
        list(event.bracket_matches.order_by('match_number'))
        if preserve_matches else _generate_matches(event, snapshots_by_source, blueprint)
    )
    if preserve_matches:
        if len(matches) != len(blueprint['matches']):
            raise MatchEventValidationError(
                'The stored bracket no longer matches this format and cannot be regenerated after results exist.'
            )
        for match, blueprint_row in zip(matches, blueprint['matches']):
            if not match.client_key:
                match.client_key = blueprint_row['key']
                match.save(update_fields=['client_key'])
    if schedule_mode == Event.SCHEDULE_AUTO:
        _auto_schedule(event, matches)
    else:
        _manual_schedule(event, matches, data)

    if created or previous_faculty_id != faculty.id:
        _notify_faculty(event, actor, created)
    return event
