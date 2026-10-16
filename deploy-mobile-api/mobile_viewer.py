"""Flutter public API, sharing the Web public portal's disclosure policy."""
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .faculty_service import leaderboard_rows
from .models import Department, SystemSettings
from .public_service import (
    criteria_results, event_kind, event_status, fixture_queryset, image_url,
    public_events, public_fixtures, public_participants, published_events,
    result_is_published, result_rounds, standings_eligible,
)


def category_group(event):
    name = event.category.lower().replace('-', '_').replace(' ', '_')
    if 'sport' in name:
        return 'esports' if 'esport' in name else 'sports'
    return 'academic' if 'academic' in name else 'socio_cultural'


def overview(event):
    return {'id': event.judging_event_id if event_kind(event) == 'criteria' else event.pk,
            'event_id': event.pk, 'title': event.name, 'category': event.category,
            'division': event.division, 'venue': event.venue,
            'date_display': event.event_date.strftime('%b %d, %Y'),
            'time_display': event.event_time.strftime('%I:%M %p') if event.event_time else '',
            'description': event.mechanics, 'status': event_status(event),
            'event_classification': 'Criteria-Based' if event_kind(event) == 'criteria' else 'Match-Based',
            'faculty_in_charge': event.faculty_in_charge}


def team_payload(team):
    return {'id': team.pk if team else None, 'name': team.name if team else 'TBD',
            'abbreviation': team.name[:3].upper() if team else 'TBD',
            'logo_icon': image_url(team.source_team.image) if team and team.source_team else '',
            'color': '#00C5D9'}


def match_cards(event):
    matches = list(fixture_queryset(event).select_related('team_a__source_team', 'team_b__source_team'))
    fixtures = public_fixtures(event, matches)
    cards = []
    for match, fixture in zip(matches, fixtures):
        cards.append({**overview(event), 'id': match.pk, 'source': 'bracket', 'event_type': 'match',
                      'event_name': event.name, 'match_title': event.name, 'sport': event.sport_type or event.category,
                      'category_group': category_group(event), 'round': match.round_name,
                      'round_label_display': match.round_name, 'game_number': match.match_number,
                      'scheduled_time': f'{match.match_date or event.event_date}T{match.match_time or event.event_time or "00:00:00"}',
                      'date_display': (match.match_date or event.event_date).strftime('%b %d, %Y'),
                      'time_display': (match.match_time or event.event_time).strftime('%I:%M %p') if match.match_time or event.event_time else '',
                      'venue': match.venue or event.venue, 'status': fixture['status'],
                      'team_a': team_payload(match.team_a), 'team_b': team_payload(match.team_b),
                      'team_a_name': fixture['name_a'], 'team_b_name': fixture['name_b'],
                      'teams_label': f'{fixture["name_a"]} vs {fixture["name_b"]}',
                      'score_a': fixture['score_a'] if fixture['is_official'] else None,
                      'score_b': fixture['score_b'] if fixture['is_official'] else None,
                      'winner': fixture['winner'], 'published': fixture['is_official']})
    return cards


def bracket_payload(event, cards):
    rounds = {}
    for card in cards:
        rounds.setdefault(card['round'], []).append({**card, 'is_official': card['published']})
    return {'event_id': event.pk, 'event_name': event.name,
            'rounds': [{'name': name, 'matches': matches} for name, matches in rounds.items()]}


def catalog():
    rows = []
    for event in public_events().select_related('judging_event'):
        if event_kind(event) == 'criteria':
            # The Flutter detail route uses the existing JudgingEvent identity.
            if not event.judging_event_id:
                continue
            rows.append({**overview(event), 'id': event.judging_event_id,
                         'judging_event_id': event.judging_event_id, 'event_type': 'criteria', 'source': 'judging',
                         'event_name': event.name, 'match_title': event.name, 'category_group': category_group(event),
                         'category_type': category_group(event), 'sport': event.category,
                         'image_url': image_url(event.image), 'teams_label': '',
                         'scheduled_time': f'{event.event_date}T{event.event_time or "00:00:00"}',
                         'published': result_is_published(event)})
        else:
            cards = match_cards(event)
            if cards:
                # One catalogue entry per event, with a real match ID for the detail route.
                featured = next((row for row in cards if row['status'] == 'ongoing'), cards[0])
                rows.append({**featured, 'status': event_status(event), 'match_count': len(cards)})
    return sorted(rows, key=lambda row: (row['scheduled_time'], row['event_id']))


def counts(rows):
    return {'all': len(rows), 'total': len(rows),
            **{key: sum(row['status'] == key for row in rows) for key in ('ongoing', 'upcoming', 'completed')},
            'live': sum(row['status'] == 'ongoing' for row in rows),
            **{key: sum(row['event_type'] == key for row in rows) for key in ('match', 'criteria')}}


def rankings_payload(sport=None, category=None):
    events = list(published_events())
    eligible = [event.pk for event in events if standings_eligible(event)
                and (not sport or sport in ('all', 'overall') or sport == (event.sport_type or event.category))
                and (not category or category == 'all' or category == category_group(event))]
    rows = leaderboard_rows(event_ids=eligible)
    departments = {row.name: row for row in Department.objects.filter(name__in=[row['department'] for row in rows])}
    output = []
    for row in rows:
        department = departments.get(row['department'])
        output.append({**row, 'team_id': department.pk if department else None, 'name': row['department'],
                       'abbreviation': department.code if department else '', 'color': department.delegation_color if department else '#00C5D9',
                       'logo_icon': image_url(department.logo) if department else ''})
    return {'rankings': output, 'sports': ['overall', *sorted({event.sport_type or event.category for event in events})],
            'categories': [{'id': key, 'label': label, 'icon': icon} for key, label, icon in (
                ('all', 'All Events', 'grid'), ('academic', 'Academic', 'school'),
                ('esports', 'Esports', 'sports_esports'), ('sports', 'Sports', 'sports_soccer'),
                ('socio_cultural', 'Socio Cultural', 'theater_comedy'))]}


@api_view(['GET'])
@permission_classes([AllowAny])
def events(request):
    rows = catalog()
    for key in ('status', 'type', 'category'):
        value = request.query_params.get(key)
        if value and value != 'all':
            field = {'type': 'event_type', 'category': 'category_group'}.get(key, key)
            value = 'ongoing' if key == 'status' and value == 'live' else value
            rows = [row for row in rows if row[field] == value]
    query = request.query_params.get('q', '').strip().lower()
    if query:
        rows = [row for row in rows if query in ' '.join(str(row.get(key, '')) for key in
                ('title', 'event_name', 'teams_label', 'venue', 'sport')).lower()]
    return Response({'events': rows, 'counts': counts(rows)})


@api_view(['GET'])
@permission_classes([AllowAny])
def rankings(request):
    return Response(rankings_payload(request.query_params.get('sport'), request.query_params.get('category')))


@api_view(['GET'])
@permission_classes([AllowAny])
def dashboard(request):
    rows = catalog()
    settings = SystemSettings.objects.filter(pk=1).first()
    return Response({'welcome_title': 'Welcome to EventTab',
                     'intramurals_banner': settings.intramurals_name if settings else 'EventTab Intramurals',
                     'counts': counts(rows), 'featured': next(iter(rows), None),
                     'ongoing': [row for row in rows if row['status'] == 'ongoing'][:6],
                     'upcoming': [row for row in rows if row['status'] == 'upcoming'][:6],
                     'latest_results': [row for row in rows if row['published']][:8],
                     'top_rankings': rankings_payload()['rankings'][:3],
                     'announcements_preview': [], 'notification_count': 0, 'notifications': []})


@api_view(['GET'])
@permission_classes([AllowAny])
def criteria_detail(request, judging_event_id):
    event = get_object_or_404(public_events().filter(public_kind='criteria'), judging_event_id=judging_event_id)
    rounds = result_rounds(event)
    participants = public_participants(event)
    placements, message = criteria_results(event)
    results = [{'rank': row['rank'], 'contestant_name': row['name'], 'department': row['department'],
                'award': row['placement'], 'score': None} for row in placements]
    stages = [{'id': row['id'], 'name': row['name'], 'description': '', 'is_active': False} for row in rounds]
    info = {**overview(event), 'competition_structure': ' / '.join(row['name'] for row in rounds),
            'contestant_count': len(participants), 'current_stage': ''}
    return Response({'event_type': 'criteria', 'overview': info,
                     'schedule': {**info, 'stages': stages},
                     'contestants': [{'id': row['id'], 'number': index, 'name': row['name'],
                                      'department': row['department'], 'photo': row['logo_url'], 'status': 'Competing'}
                                     for index, row in enumerate(participants, 1)],
                     'results': results, 'results_message': message, 'show_scores': False,
                     'awards': [{'title': row['award'], 'recipient': row['contestant_name'],
                                 'department': row['department']} for row in results[:3]]})


@api_view(['GET'])
@permission_classes([AllowAny])
def match_detail(request, match_id):
    event = get_object_or_404(public_events().filter(public_kind='match'), bracket_matches__pk=match_id)
    cards = match_cards(event)
    return Response({'event_type': 'match', 'overview': {**overview(event), 'team_count': event.bracket_teams.count(),
                                                       'tournament_format': event.tournament_type},
                     'schedule': cards, 'bracket': {'events': [bracket_payload(event, cards)]},
                     'results': [row for row in cards if row['published']]})


@api_view(['GET'])
@permission_classes([AllowAny])
def live(request):
    rows = [row for event in public_events().filter(public_kind='match') for row in match_cards(event)]
    sport = request.query_params.get('sport')
    if sport and sport != 'all':
        rows = [row for row in rows if row['sport'] == sport]
    ongoing = [row for row in rows if row['status'] == 'ongoing']
    return Response({'featured': next(iter(ongoing or rows), None), 'ongoing': ongoing,
                     'scoreboard': ongoing, 'updates': [], 'sports': sorted({row['sport'] for row in rows}),
                     'live_count': len(ongoing), 'live_matches': ongoing,
                     'upcoming_matches': [row for row in rows if row['status'] == 'upcoming'],
                     'recent_results': [row for row in rows if row['published']]})


@api_view(['GET'])
@permission_classes([AllowAny])
def bracket(request):
    events = public_events().filter(public_kind='match')
    event_id = request.query_params.get('event_id')
    if event_id:
        from rest_framework.exceptions import ValidationError
        try:
            event_id = int(event_id)
        except (TypeError, ValueError):
            raise ValidationError({'event_id': 'Use an integer event ID.'})
        events = events.filter(pk=event_id)
    return Response({'events': [bracket_payload(event, match_cards(event)) for event in events]})


@api_view(['GET'])
@permission_classes([AllowAny])
def profile(request):
    return Response({'display_name': 'Guest Viewer', 'mode': 'VIEWER MODE', 'is_guest': True,
                     'stats': {'saved_events': 0, 'recently_viewed': 0, 'live_events': counts(catalog())['live']}})


@api_view(['GET'])
@permission_classes([AllowAny])
def announcements(request):
    # There is no public announcements model in the authoritative backend.
    return Response({'announcements': []})


@api_view(['GET'])
@permission_classes([AllowAny])
def notifications(request):
    return Response({'notifications': [], 'unread_count': 0})


@api_view(['GET'])
@permission_classes([AllowAny])
def about(request):
    settings = SystemSettings.objects.filter(pk=1).first()
    return Response({'system_description': 'EventTab Intramurals Management System',
                     'school_name': settings.school_name if settings else 'EventTab',
                     'current_intramurals': settings.intramurals_name if settings else '',
                     'version': '1.0.0', 'contact': ''})
