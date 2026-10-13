"""Anonymous GET pages backed by the existing EventTab records and workflows."""
import logging
from functools import wraps
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import Case, CharField, Exists, F, OuterRef, Q, Value, When
from django.db.models.functions import Coalesce, Concat
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from events.faculty_service import leaderboard_rows
from events.models import BracketMatch, Department, Event
from events.public_service import (
    PUBLIC_TIMEZONE, TERMINAL_MATCH_STATUSES, criteria_condition, criteria_results,
    event_kind, fixture_queryset, image_url, is_multiround,
    public_events, public_fixtures, public_participants, published_events,
    publication_datetime, result_is_published, result_rounds, serialize_event, standings_eligible,
)


logger = logging.getLogger(__name__)
PAGE_SIZE = 12
TEMPLATES = {
    'home': 'landinghome.html', 'schedules': 'landingschedule.html',
    'games': 'landinggames.html', 'leaderboard': 'landingleaderboard.html',
    'results': 'landingresult.html',
}


def public_page(active, template=None):
    """GET-only pages; transient database failures yield a safe retry state."""
    def decorator(view):
        @require_safe
        @never_cache
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            context = {
                'active': active, 'timezone_label': 'Asia/Manila',
                'filters': filters_for(request), 'categories': [], 'venues': [],
            }
            try:
                context.update(view(request, *args, **kwargs))
                return render(request, 'landingpage/' + (template or TEMPLATES[active]), context)
            except DatabaseError:
                logger.exception('Public portal data unavailable')
                context['data_error'] = 'Event information is temporarily unavailable. Please try again shortly.'
                context['retry_url'] = request.get_full_path()
                return render(request, 'landingpage/' + (template or TEMPLATES[active]), context, status=503)
        return wrapped
    return decorator


def filters_for(request):
    filters = {name: request.GET.get(name, '').strip()[:200]
               for name in ('q', 'type', 'category', 'status', 'date', 'venue')}
    if filters['type'] not in {'match', 'criteria'}:
        filters['type'] = 'all'
    if filters['status'] not in {'upcoming', 'ongoing', 'completed'}:
        filters['status'] = 'all'
    if filters['date']:
        try:
            if parse_date(filters['date']) is None:
                filters['date'] = ''
        except ValueError:
            filters['date'] = ''
    return filters


def apply_filters(qs, filters, include_schedule=False):
    if filters['q']:
        qs = qs.filter(Q(name__icontains=filters['q']) | Q(category__icontains=filters['q'])
                       | Q(sport_type__icontains=filters['q']))
    if filters['type'] != 'all':
        qs = qs.filter(public_kind=filters['type'])
    if filters['category']:
        qs = qs.filter(category=filters['category'])
    if not include_schedule:
        if filters['date']:
            qs = qs.filter(event_date=parse_date(filters['date']))
        if filters['venue']:
            qs = qs.filter(venue=filters['venue'])
        today = timezone.localdate(timezone=PUBLIC_TIMEZONE)
        if filters['status'] == 'completed':
            qs = qs.filter(status=Event.STATUS_COMPLETED)
        elif filters['status'] == 'upcoming':
            qs = qs.filter(Q(status=Event.STATUS_UPCOMING) | Q(event_date__gt=today)).exclude(status=Event.STATUS_COMPLETED)
        elif filters['status'] == 'ongoing':
            qs = qs.filter(status=Event.STATUS_ACTIVE, event_date__lte=today)
    return qs


def filter_options():
    return {
        'categories': list(public_events().exclude(category='').order_by('category').values_list('category', flat=True).distinct()),
        'venues': sorted(set(public_events().exclude(venue='').values_list('venue', flat=True)) | set(
            BracketMatch.objects.filter(event__in=public_events()).exclude(venue='').values_list('venue', flat=True))),
    }


def paginate(request, rows):
    page = Paginator(rows, PAGE_SIZE).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    return page, {'page_obj': page, 'query_string': params.urlencode()}


def schedule_queryset(filters, future_only=False):
    """Database union keeps mixed event/fixture schedules paginated and ordered."""
    events = apply_filters(public_events(), filters, include_schedule=True)
    dated_matches = BracketMatch.objects.filter(
        event_id=OuterRef('pk'), is_automatic_advance=False, match_date__isnull=False,
    )
    today = timezone.localdate(timezone=PUBLIC_TIMEZONE)
    event_rows = events.annotate(has_fixtures=Exists(dated_matches)).filter(has_fixtures=False).annotate(
        public_event_id=F('pk'), event_name=F('name'), event_category=F('category'),
        event_kind=F('public_kind'), round=Value('', output_field=CharField()),
        date=F('event_date'), time=F('event_time'), public_venue=F('venue'),
        participants=Value('', output_field=CharField()),
        public_status=Case(
            When(status=Event.STATUS_COMPLETED, then=Value('completed')),
            When(Q(status=Event.STATUS_UPCOMING) | Q(event_date__gt=today), then=Value('upcoming')),
            default=Value('ongoing'), output_field=CharField(),
        ),
    )
    match_rows = BracketMatch.objects.filter(
        event__in=events, is_automatic_advance=False, match_date__isnull=False,
    ).annotate(
        public_event_id=F('event_id'), event_name=F('event__name'), event_category=F('event__category'),
        event_kind=Case(When(criteria_condition('event__'), then=Value('criteria')),
                        default=Value('match'), output_field=CharField()),
        round=F('round_name'), date=F('match_date'), time=F('match_time'),
        public_venue=Case(When(venue='', then=F('event__venue')), default=F('venue'), output_field=CharField()),
        participants=Concat(Coalesce('team_a__name', Value('TBD')), Value(' vs '),
                            Coalesce('team_b__name', Value('TBD')), output_field=CharField()),
        public_status=Case(
            When(status__in=['completed', 'forfeit'], then=Value('completed')),
            When(status='ongoing', then=Value('ongoing')),
            default=Value('upcoming'), output_field=CharField(),
        ),
    )
    fields = ('public_event_id', 'event_name', 'event_category', 'event_kind', 'round',
              'date', 'time', 'public_venue', 'participants', 'public_status')
    rows = []
    for qs in (event_rows, match_rows):
        if future_only:
            qs = qs.filter(date__gte=today)
        if filters['date']:
            qs = qs.filter(date=parse_date(filters['date']))
        if filters['venue']:
            qs = qs.filter(public_venue=filters['venue'])
        if filters['status'] != 'all':
            qs = qs.filter(public_status=filters['status'])
        rows.append(qs.order_by().values(*fields))
    return rows[0].union(rows[1], all=True).order_by('date', 'time', 'event_name', 'round', 'public_event_id')


def serialize_schedule(row):
    return {
        'event_id': row['public_event_id'], 'event_name': row['event_name'],
        'detail_url': reverse('public_event_detail', args=[row['public_event_id']]),
        'type': row['event_kind'], 'type_label': 'Match-Based' if row['event_kind'] == 'match' else 'Criteria-Based',
        'category': row['event_category'], 'round': row['round'],
        'date': row['date'], 'time': row['time'], 'venue': row['public_venue'],
        'participants': row['participants'], 'status': row['public_status'],
        'status_label': row['public_status'].title(),
    }


@public_page('home')
def home(request):
    featured_filters = {
        'type': request.GET.get('featured_type', 'all'),
        'q': request.GET.get('featured_q', '').strip()[:200],
        'sort': request.GET.get('featured_sort', 'date'),
    }
    if featured_filters['type'] not in {'match', 'criteria'}:
        featured_filters['type'] = 'all'
    if featured_filters['sort'] not in {'date', 'name'}:
        featured_filters['sort'] = 'date'
    competitions = public_events().exclude(status=Event.STATUS_COMPLETED)
    if featured_filters['type'] != 'all':
        competitions = competitions.filter(public_kind=featured_filters['type'])
    if featured_filters['q']:
        competitions = competitions.filter(
            Q(name__icontains=featured_filters['q']) | Q(category__icontains=featured_filters['q'])
            | Q(sport_type__icontains=featured_filters['q']),
        )
    ordering = ('name', 'pk') if featured_filters['sort'] == 'name' else ('event_date', 'pk')
    featured_page = Paginator(competitions.order_by(*ordering), 6).get_page(request.GET.get('featured_page'))
    featured_events = []
    for event in featured_page:
        card = serialize_event(event)
        card['banner_key'] = featured_banner_key(event)
        featured_events.append(card)
    featured_params = {
        'featured_type': featured_filters['type'], 'featured_q': featured_filters['q'],
        'featured_sort': featured_filters['sort'],
    }
    upcoming = dict(filters_for(request), status='upcoming')
    return {
        'page_title': 'EventTab — One event. One community.',
        'featured_events': featured_events, 'featured_filters': featured_filters,
        'featured_page_obj': featured_page, 'featured_query_string': urlencode(featured_params),
        'upcoming_schedules': [serialize_schedule(row) for row in schedule_queryset(upcoming, future_only=True)[:5]],
    }


def featured_banner_key(event):
    """Choose decorative category artwork, never a substitute event record."""
    name = ' '.join((event.name, event.sport_type, event.special_event_type)).lower()
    for keyword, key in (
        ('pageant', 'pageant'), ('duet', 'duet'), ('dance', 'dance'), ('dancing', 'dance'),
        ('basketball', 'basketball'), ('badminton', 'badminton'),
        ('singing', 'singing'), ('vocal', 'singing'),
    ):
        if keyword in name:
            return key
    return ''


@public_page('games')
def games(request):
    page, context = paginate(request, apply_filters(
        public_events(), filters_for(request),
    ).order_by('event_date', 'name', 'pk'))
    context.update(filter_options(), page_title='Games & Events — EventTab',
                   events=[serialize_event(event) for event in page])
    return context


@public_page('schedules')
def schedules(request):
    page, context = paginate(request, schedule_queryset(filters_for(request)))
    context.update(filter_options(), page_title='Event Schedules — EventTab',
                   schedules=[serialize_schedule(row) for row in page])
    return context


@public_page('games', 'event_detail.html')
def event_detail(request, pk):
    event = get_object_or_404(public_events().select_related('judging_event'), pk=pk)
    if event_kind(event) == 'match':
        rounds = [{'name': name, 'status_label': ''} for name in event.bracket_matches.order_by(
            'round_name').values_list('round_name', flat=True).distinct() if name]
    else:
        rounds = [{'name': row['name'], 'status_label': ''} for row in result_rounds(event)]
    fixtures_page, pagination = paginate(request, fixture_queryset(event))
    return dict(pagination, page_title=f'{event.name} — EventTab', event=serialize_event(event),
                participants=public_participants(event), rounds=rounds,
                fixtures=public_fixtures(event, fixtures_page))


@public_page('results')
def results(request):
    page, context = paginate(request, apply_filters(
        published_events(), filters_for(request),
    ).order_by('-updated_at', '-pk'))
    cards = []
    for event in page:
        card = serialize_event(event)
        if event_kind(event) == 'criteria' and not is_multiround(event) and len(result_rounds(event)) <= 1:
            card['official_placements'], _ = criteria_results(event)
        card['official_placements'].sort(key=lambda row: row['rank'] or 999999)
        cards.append(card)
    context.update(filter_options(), page_title='Official Results — EventTab', events=cards)
    return context


@public_page('results', 'result_detail.html')
def result_detail(request, pk):
    event = get_object_or_404(published_events().select_related('judging_event'), pk=pk)
    if not result_is_published(event):
        raise Http404('Published results unavailable')
    kind = event_kind(event)
    rows, rounds, selected = [], [], ''
    notice = 'Final scores from officially published matches.'
    fixtures = []
    if kind == 'criteria':
        rounds = result_rounds(event)
        selected = request.GET.get('round_id', rounds[-1]['id'])
        if selected not in {row['id'] for row in rounds}:
            raise Http404('Round unavailable')
        rows, notice = criteria_results(event, selected)
    else:
        fixtures = fixture_queryset(event).filter(
            status__in=TERMINAL_MATCH_STATUSES, updated_at__lte=publication_datetime(event),
        )
        if fixture_queryset(event).filter(updated_at__gt=publication_datetime(event)).exists():
            notice = 'Updated match outcomes remain private until authorized republication.'
        # These are persisted championship points, not an invented placement rule.
        if standings_eligible(event):
            placements = (event.result_processing_config or {}).get('calculated_placements', {})
            rows = [{'rank': placements.get(str(team.pk)), 'name': team.name,
                     'department': team.department.name if team.department else '',
                     'points': team.points, 'score': '',
                     'placement': {1: 'Champion', 2: 'Runner-up', 3: 'Third place'}.get(placements.get(str(team.pk)), 'Champion' if team.is_champion else '')}
                    for team in event.bracket_teams.select_related('department').order_by('-points', 'seed', 'pk')]
            rows.sort(key=lambda row: row['rank'] or 999999)
    page, pagination = paginate(request, fixtures if kind == 'match' else rows)
    return dict(pagination, page_title=f'Published results: {event.name} — EventTab',
                event=serialize_event(event), result_kind=kind,
                result_rows=rows if kind == 'match' else list(page),
                fixtures=public_fixtures(event, page) if kind == 'match' else [], result_notice=notice,
                result_rounds=rounds, selected_round=selected,
                published_date=serialize_event(event)['published_date'])


@public_page('leaderboard')
def leaderboard(request):
    filters = filters_for(request)
    candidates = apply_filters(published_events(), filters).select_related('judging_event')
    eligible_ids = [event.pk for event in candidates if standings_eligible(event)]
    rows = leaderboard_rows(event_ids=eligible_ids)
    logos = {department.name: image_url(department.logo) for department in Department.objects.filter(
        name__in=[row['department'] for row in rows],
    )}
    result_params = {name: filters[name] for name in ('type', 'category') if filters[name] and filters[name] != 'all'}
    result_url = reverse('public_results') + ('?' + urlencode(result_params) if result_params else '')
    for row in rows:
        row.update(logo_url=logos.get(row['department'], ''), result_url=result_url)
    page, pagination = paginate(request, rows)
    return dict(pagination, **filter_options(), page_title='Team Standings — EventTab', standings=list(page))
