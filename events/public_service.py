"""Whitelisted, read-only projections for the public intramurals portal.

Event publication is independent of result publication. Never return staff
serializers or score breakdowns from these helpers.
"""
from zoneinfo import ZoneInfo

from django.db.models import Case, CharField, Q, Value, When
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .criteria_scoring import compute_official_criteria_rankings
from .faculty_service import compute_criteria_rankings
from .models import BracketMatch, CriteriaEventEntry, Event, JudgeScore, RegistryCandidate, Team


PUBLIC_TIMEZONE = ZoneInfo('Asia/Manila')
TERMINAL_MATCH_STATUSES = (BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT)


def criteria_condition(prefix=''):
    """Prefer an explicit scoring method, then recognize legacy judging events."""
    return Q(**{prefix + 'scoring_method': 'criteria'}) | (
        ~Q(**{prefix + 'scoring_method': 'match'})
        & (Q(**{prefix + 'judging_event__isnull': False})
           | (~Q(**{prefix + 'judging_criteria_config': []})
              & ~Q(**{prefix + 'category__iexact': 'sports'})))
    )


def public_events():
    return Event.objects.filter(
        publication_status=Event.PUBLICATION_PUBLISHED,
        status__in=[Event.STATUS_UPCOMING, Event.STATUS_ACTIVE, Event.STATUS_COMPLETED],
    ).annotate(public_kind=Case(
        When(criteria_condition(), then=Value('criteria')),
        default=Value('match'), output_field=CharField(),
    ))


def published_events():
    # The existing publish_results workflow stamps published_at. Listing an
    # event or finalizing an internal result alone does not grant public access.
    return public_events().filter(
        results_finalized=True, status=Event.STATUS_COMPLETED,
        result_processing_config__published_at__isnull=False,
    ).exclude(result_processing_config__published_at='').exclude(
        result_processing_config__published_at=None,
    ).filter(
        Q(result_processing_config__verification_mode__isnull=True)
        | Q(result_processing_config__verification_mode=None)
        | Q(result_processing_config__verification_mode__in=['', 'admin_only'])
        | (Q(result_processing_config__verification_mode='tabulator_verification')
           & Q(result_processing_config__tabulation_confirmed_at__isnull=False)
           & ~Q(result_processing_config__tabulation_confirmed_at='')
           & ~Q(result_processing_config__tabulation_confirmed_at=None))
        | (Q(result_processing_config__verification_mode='tabulator_admin_approval')
           & Q(result_processing_config__approved=True)
           & Q(result_processing_config__tabulation_confirmed_at__isnull=False)
           & ~Q(result_processing_config__tabulation_confirmed_at='')
           & ~Q(result_processing_config__tabulation_confirmed_at=None))
    )


def result_is_published(event):
    cfg = event.result_processing_config or {}
    mode = cfg.get('verification_mode') or 'admin_only'
    championship = cfg.get('championship_scoring') or {}
    return bool(
        event.publication_status == Event.PUBLICATION_PUBLISHED
        and event.status == Event.STATUS_COMPLETED and event.results_finalized
        and publication_datetime(event)
        and (not championship.get('approval_required') or cfg.get('approved') is True)
        and mode in {'admin_only', 'tabulator_verification', 'tabulator_admin_approval'}
        and (mode not in {'tabulator_verification', 'tabulator_admin_approval'}
             or cfg.get('tabulation_confirmed_at'))
        and (mode != 'tabulator_admin_approval' or cfg.get('approved') is True)
    )


def publication_datetime(event):
    try:
        value = parse_datetime(str((event.result_processing_config or {}).get('published_at') or ''))
    except ValueError:
        return None
    if value and timezone.is_naive(value):
        value = timezone.make_aware(value, PUBLIC_TIMEZONE)
    return timezone.localtime(value, PUBLIC_TIMEZONE) if value else None


def event_kind(event):
    if hasattr(event, 'public_kind'):
        return event.public_kind
    if event.scoring_method == 'criteria':
        return 'criteria'
    if event.scoring_method == 'match':
        return 'match'
    return 'criteria' if event.judging_event_id or (
        event.judging_criteria_config and event.category.lower() != 'sports'
    ) else 'match'


def image_url(image):
    return image.url if image else ''


def event_status(event):
    if event.status == Event.STATUS_COMPLETED:
        return 'completed'
    today = timezone.localdate(timezone=PUBLIC_TIMEZONE)
    if event.status == Event.STATUS_UPCOMING or event.event_date > today:
        return 'upcoming'
    return 'ongoing'


def serialize_event(event):
    kind = event_kind(event)
    status = event_status(event)
    published = result_is_published(event)
    published_at = publication_datetime(event)
    return {
        'id': event.pk, 'name': event.name, 'category': event.category,
        'type': kind, 'type_label': 'Match-Based' if kind == 'match' else 'Criteria-Based',
        'status': status, 'status_label': status.title(),
        'date': event.event_date, 'time': event.event_time, 'venue': event.venue,
        'division': event.division, 'image_url': image_url(event.image),
        'detail_url': reverse('public_event_detail', args=[event.pk]),
        'result_url': reverse('public_result_detail', args=[event.pk]) if published else '',
        'has_results': published, 'description': event.mechanics,
        'format_label': event.tournament_type if kind == 'match' else event.get_event_format_display(),
        'published_date': published_at,
        'summary': ('Published round results' if kind == 'criteria' and is_multiround(event)
                    else 'Official published results'),
        'official_placements': ([{'name': team.name, 'rank': (event.result_processing_config or {}).get('calculated_placements', {}).get(str(team.pk))}
                                 for team in event.bracket_teams.all()
                                 if str(team.pk) in (event.result_processing_config or {}).get('calculated_placements', {})]
                                if published and kind == 'match' else []),
    }


def public_participants(event):
    if event_kind(event) == 'match':
        return [{
            'id': team.pk, 'name': team.name,
            'department': team.department.name if team.department else '',
            'logo_url': image_url(team.source_team.image) if team.source_team else '',
        } for team in event.bracket_teams.select_related('department', 'source_team')]
    from .participant_removal_service import active_participant_ids, active_mobile_candidate_ids
    ids = active_participant_ids(event)
    entries = list(CriteriaEventEntry.objects.filter(
        event=event, pk__in=ids,
    ).select_related('department', 'source_individual', 'source_team'))
    if entries:
        return [{
            'id': entry.pk,
            'name': (entry.source_individual.name if entry.source_individual else entry.display_name),
            'department': entry.department.name if entry.department else '',
            'logo_url': image_url(entry.source_individual.image) if entry.source_individual else (
                image_url(entry.source_team.image) if entry.source_team else ''),
        } for entry in entries]
    if event.participant_ids:
        model = RegistryCandidate if event.participation_type == 'individual' else Team
        return [{
            'id': person.pk, 'name': person.name,
            'department': person.department.name if person.department else '',
            'logo_url': image_url(person.image),
        } for person in model.objects.filter(
            pk__in=ids, status=model.STATUS_ACTIVE,
        ).select_related('department')]
    if event.judging_event_id:
        return [{'id': person.pk, 'name': person.name, 'department': person.department,
                 'logo_url': image_url(person.photo)} for person in event.judging_event.candidates.filter(pk__in=active_mobile_candidate_ids(event))]
    return []


def fixture_queryset(event):
    return event.bracket_matches.filter(is_automatic_advance=False).select_related(
        'team_a', 'team_b', 'winner',
    ).order_by('match_number', 'pk')


def public_fixtures(event, matches=None):
    published = result_is_published(event)
    rows = []
    for match in fixture_queryset(event) if matches is None else matches:
        official = (published and match.status in TERMINAL_MATCH_STATUSES
                    and match.updated_at <= publication_datetime(event))
        status = {'pending': 'upcoming', 'ongoing': 'ongoing',
                  'completed': 'completed', 'forfeit': 'completed'}.get(match.status, 'upcoming')
        rows.append({
            'id': match.pk, 'round': match.round_name,
            'name_a': match.team_a.name if match.team_a else 'TBD',
            'name_b': match.team_b.name if match.team_b else 'TBD',
            'date': match.match_date, 'time': match.match_time,
            'venue': match.venue or event.venue, 'status': status, 'status_label': status.title(),
            'score_a': match.score_a if official else '', 'score_b': match.score_b if official else '',
            'winner': match.winner.name if official and match.winner else '',
            'is_official': official,
        })
    return rows


def public_result_bracket(event, matches):
    """Read the published bracket's authored rounds and advancement links only."""
    matches = list(matches)
    published = [(match, fixture) for match, fixture in zip(matches, public_fixtures(event, matches))
                 if fixture['is_official']]
    visible_ids = {match.pk for match, _ in published}
    lanes = {}
    for match, fixture in published:
        name = match.round_name or 'Matches'
        lane = 'losers' if 'loser' in name.lower() else 'finals' if 'grand' in name.lower() else 'winners'
        rounds = lanes.setdefault(lane, {})
        column = rounds.setdefault(name, {'name': name, 'matches': []})
        column['matches'].append({
            **fixture, 'number': match.match_number,
            'automatic_advance': match.is_automatic_advance,
            'next_winner': match.next_match_winner_id if match.next_match_winner_id in visible_ids else '',
            'next_loser': match.next_match_loser_id if match.next_match_loser_id in visible_ids else '',
        })
    return [{'key': key, 'rounds': list(lanes[key].values()),
             'slots': max(len(column['matches']) for column in lanes[key].values())}
            for key in ('winners', 'losers', 'finals') if key in lanes]


def result_rounds(event):
    configured = [row for row in event.rounds_config or [] if isinstance(row, dict)]
    rounds = [{'id': str(row.get('id') or row.get('stage_number') or index + 1),
               'name': row.get('name') or f'Round {index + 1}'}
              for index, row in enumerate(configured)]
    known = {row['id'] for row in rounds}
    for rid in event.scoring_categories.order_by('assigned_round_id').values_list(
        'assigned_round_id', flat=True,
    ).distinct():
        if rid not in known:
            rounds.append({'id': rid, 'name': rid or 'Final results'})
    return rounds or [{'id': '', 'name': 'Final results'}]


def is_multiround(event):
    configured = [row for row in event.rounds_config or [] if isinstance(row, dict)]
    return len(configured) > 1 or event.event_format in {
        Event.FORMAT_MULTIPLE_STAGE, Event.FORMAT_MULTIPLE, Event.FORMAT_PRELIM_FINAL,
    }


def criteria_results(event, round_id=None):
    """Return placements only; absent public score policy means totals stay private."""
    if not result_is_published(event):
        return [], 'Official results have not been published.'
    from .criteria_faculty_service import approved_final_rankings, criteria_rounds
    from .faculty_service import FacultyServiceError
    try:
        final_results = approved_final_rankings(event)
    except FacultyServiceError:
        return [], 'Approved final placements are unavailable.'
    final_round_id = criteria_rounds(event)[-1]['id']
    if final_results is not None and round_id is not None and str(round_id) != final_round_id:
        return [], 'Only approved final placements have been published for this event.'
    if final_results is not None and (round_id is None or str(round_id) == final_round_id):
        if event.criteria_score_submissions.filter(is_active=True, updated_at__gt=publication_datetime(event)).exists():
            return [], 'Updated competition results are awaiting authorized republication.'
        participants = {row['id']: row for row in public_participants(event)}
        if any(row['participant_id'] not in participants for row in final_results):
            return [], 'Approved final participants are unavailable.'
        rows = [{'rank': row['rank'], 'name': participants[row['participant_id']]['name'],
                 'department': participants[row['participant_id']]['department'],
                 'points': '', 'score': '', 'placement': placement(row['rank'])}
                for row in final_results]
        return rows, 'Official final placements. Individual judge scores remain private.'
    modern = event.scoring_categories.exists()
    multi = is_multiround(event) or (modern and len(result_rounds(event)) > 1)
    if modern:
        rounds = result_rounds(event)
        rid = rounds[-1]['id'] if round_id is None else str(round_id)
        if rid not in {row['id'] for row in rounds}:
            return [], 'The selected round is unavailable.'
        if event.criteria_score_submissions.filter(
            assigned_round_id=rid, is_active=True, updated_at__gt=publication_datetime(event),
        ).exists():
            return [], 'Updated round results are awaiting authorized republication.'
        if event.scoring_categories.filter(assigned_round_id=rid, purpose='official', judge_mode='ranking').exists():
            return [], 'Calculated placements are unavailable for this scoring method.'
        try:
            rankings = compute_official_criteria_rankings(event, round_id=rid)
        except ValueError:
            return [], 'Published placements are unavailable for this scoring configuration.'
        participants = {row['id']: row for row in public_participants(event)}
        if not rankings or len(rankings) != len(participants) or not all(row['complete'] for row in rankings):
            return [], 'This round is awaiting complete approved results.'
        rows = [{'rank': row['rank'], 'name': participants[row['participant_id']]['name'],
                 'department': participants[row['participant_id']]['department'], 'points': '', 'score': '',
                 'placement': f"Round rank {row['rank']}" if multi else placement(row['rank'])}
                for row in rankings]
        return rows, ('Published round-specific rankings. Overall standings are unavailable because '
                      'the existing scoring system does not calculate a combined multiround result.'
                      if multi else 'Official placements. Individual judge scores remain private.')
    if multi:
        return [], 'Round-specific and overall placements are unavailable for legacy multiround scoring.'
    if not event.judging_event_id:
        return [], 'No published placements are available.'
    if JudgeScore.objects.filter(
        candidate__event=event.judging_event, submitted_at__gt=publication_datetime(event),
    ).exists():
        return [], 'Updated competition results are awaiting authorized republication.'
    from .participant_removal_service import active_mobile_candidate_ids
    candidate_ids = active_mobile_candidate_ids(event)
    criterion_ids = list(event.judging_event.criteria.values_list('pk', flat=True))
    judge_ids = list(event.assigned_judges.filter(is_active=True).values_list('pk', flat=True))
    locked = set(JudgeScore.objects.filter(
        candidate_id__in=candidate_ids, criterion_id__in=criterion_ids,
        judge_id__in=judge_ids, is_locked=True,
    ).values_list('candidate_id', 'criterion_id', 'judge_id'))
    if not candidate_ids or not criterion_ids or not judge_ids or not all(
        (candidate, criterion, judge) in locked
        for candidate in candidate_ids for criterion in criterion_ids for judge in judge_ids
    ):
        return [], 'Official placements are awaiting complete verified results.'
    return [{'rank': row['rank'], 'name': row['name'], 'department': row['department'],
             'points': '', 'score': '', 'placement': placement(row['rank'])}
            for row in compute_criteria_rankings(event)], 'Official placements. Individual judge scores remain private.'


def placement(rank):
    return {1: 'Champion', 2: '1st Runner-up', 3: '2nd Runner-up'}.get(rank, f'Rank {rank}')


def standings_eligible(event):
    if not result_is_published(event):
        return False
    if event_kind(event) == 'criteria':
        if is_multiround(event) or len(result_rounds(event)) > 1:
            return False
        return bool(criteria_results(event)[0])
    matches = event.bracket_matches.filter(is_automatic_advance=False)
    return (matches.exists() and not matches.exclude(status__in=TERMINAL_MATCH_STATUSES).exists()
            and not matches.filter(updated_at__gt=publication_datetime(event)).exists())
