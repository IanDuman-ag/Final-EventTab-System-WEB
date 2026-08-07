from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth.models import Group
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Count
from django.shortcuts import redirect, render, get_object_or_404
from django.utils import timezone
from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
import json
import csv
import re
import random
import string
from datetime import datetime, timedelta
import logging

from events.models import (
    Event, Department, Team, RegistryCandidate,
    Portion, CandidateNumber, Contestant, JudgeAssignment,
    ScoringCriterion, Chairperson, BracketMatch, JudgeActivityLog,
)
from events.mobile_sync import (
    delete_mobile_for_event,
    sync_event_to_mobile,
    sync_judge_to_mobile_events,
)


ROLE_CHOICES = {
    'super-admin': 'Super Admin',
    'admin': 'Admin',
    'tabulator': 'Tabulator',
}

ASSIGNMENT_ROLE_LABELS = {'Tabulator', 'Judge', 'Scorer'}
ASSIGNMENT_GROUP_ALIASES = {
    'tabulator': 'Tabulator',
    'judge': 'Judge',
    'judges': 'Judge',
    'scorer': 'Scorer',
}
RESERVED_DEPARTMENT_NAMES = {'admin', 'tabulator', 'judge', 'judges', 'viewers', 'scorer'}


def normalize_event_availability_status(status):
    status = (status or Event.STATUS_ACTIVE).strip().lower()
    if status in {Event.STATUS_INACTIVE, 'deactivate', 'deactivated'}:
        return Event.STATUS_INACTIVE
    return Event.STATUS_ACTIVE


def resolve_department(dept_val):
    """Resolve a department from an id, name, or code string."""
    val = (dept_val or '').strip()
    if not val:
        return None
    if val.isdigit():
        return Department.objects.filter(pk=int(val)).first()
    dept = Department.objects.filter(name=val).first()
    if dept:
        return dept
    return Department.objects.filter(code__iexact=val).first()


def parse_team_members(members_raw):
    """Return a comma-separated member string and count from stored value."""
    if isinstance(members_raw, list):
        names = [str(m).strip() for m in members_raw if str(m).strip()]
        return ', '.join(names), len(names)
    raw = (members_raw or '').strip()
    if not raw:
        return '', 0
    if raw.startswith('['):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                names = [str(m).strip() for m in parsed if str(m).strip()]
                return ', '.join(names), len(names)
        except (json.JSONDecodeError, TypeError):
            pass
    names = [m.strip() for m in raw.split(',') if m.strip()]
    return ', '.join(names), len(names)


def serialize_registry_teams():
    """Serialize active Team registry rows for event team picker."""
    teams = []
    for team in Team.objects.filter(status=Team.STATUS_ACTIVE).select_related('department').order_by('name'):
        members_str, player_count = parse_team_members(team.members)
        teams.append({
            'id': team.id,
            'name': team.name,
            'code': team.code,
            'department': team.department.name if team.department else '',
            'members': members_str,
            'player_count': player_count,
            'coach': team.coach or '',
        })
    return teams


def serialize_registry_candidates():
    """Serialize active registry candidates for event picker."""
    rows = []
    for cand in RegistryCandidate.objects.filter(
        status=RegistryCandidate.STATUS_ACTIVE,
    ).select_related('department').order_by('number', 'name'):
        rows.append({
            'id': cand.id,
            'number': cand.number,
            'name': cand.name,
            'department': cand.department.name if cand.department else '',
            'department_id': cand.department_id,
        })
    return rows


def resolve_event_candidate_payload(entry):
    """Normalize candidate JSON from admin forms, optionally from registry."""
    registry_id = entry.get('registry_id')
    if registry_id:
        reg = RegistryCandidate.objects.filter(
            id=registry_id,
            status=RegistryCandidate.STATUS_ACTIVE,
        ).select_related('department').first()
        if reg:
            try:
                number = int(reg.number)
            except (TypeError, ValueError):
                number = reg.number
            return {
                'registry_id': reg.id,
                'number': number,
                'name': reg.name,
                'department': reg.department.name if reg.department else '',
                'description': (entry.get('description') or '').strip(),
            }
    number = entry.get('number')
    try:
        number = int(number) if number not in (None, '') else None
    except (TypeError, ValueError):
        pass
    return {
        'registry_id': registry_id,
        'number': number,
        'name': (entry.get('name') or '').strip(),
        'department': (entry.get('department') or '').strip(),
        'description': (entry.get('description') or '').strip(),
    }


def serialize_event_teams(event):
    """Serialize BracketTeam rows for admin event dialogs."""
    registry_by_name = {
        t.name.lower(): t.id for t in Team.objects.filter(status=Team.STATUS_ACTIVE)
    }
    registry_by_code = {
        t.code.lower(): t.id for t in Team.objects.filter(status=Team.STATUS_ACTIVE)
    }
    teams = []
    for t in event.bracket_teams.select_related('department').order_by('seed', 'name'):
        members_str, _ = parse_team_members(t.members)
        registry_id = registry_by_name.get(t.name.lower()) or registry_by_code.get(t.name.lower())
        teams.append({
            'registry_id': registry_id,
            'name': t.name,
            'code': t.name,
            'department': t.department.name if t.department else '',
            'members': members_str,
            'seed': t.seed or 0,
        })
    return teams


def serialize_event_candidates(event):
    """Serialize judging candidates for admin event dialogs."""
    if not event.judging_event_id:
        return []
    from events.models import Candidate
    registry_by_name = {
        c.name.lower(): c.id for c in RegistryCandidate.objects.filter(status=RegistryCandidate.STATUS_ACTIVE)
    }
    registry_by_number = {
        str(c.number).lower(): c.id for c in RegistryCandidate.objects.filter(status=RegistryCandidate.STATUS_ACTIVE)
    }
    return [
        {
            'registry_id': registry_by_number.get(str(c.number).lower()) or registry_by_name.get(c.name.lower()),
            'name': c.name,
            'number': c.number,
            'department': c.department or '',
            'description': c.description or '',
        }
        for c in Candidate.objects.filter(event=event.judging_event).order_by('number')
    ]


def _parse_event_candidates_json(raw):
    """Parse candidates_data JSON from admin event forms."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    resolved = []
    for index, entry in enumerate(parsed, start=1):
        if not isinstance(entry, dict):
            continue
        item = resolve_event_candidate_payload(entry)
        if not item.get('name'):
            continue
        if not item.get('number'):
            item['number'] = index
        resolved.append(item)
    return resolved


def _resolve_event_assignees(user_ids, scoring_method):
    """Return assignees for match (Scorer) or criteria (Judge) events."""
    User = get_user_model()
    ids = [int(j) for j in user_ids if str(j).isdigit()]
    if not ids:
        return User.objects.none()
    qs = User.objects.filter(id__in=ids)
    if scoring_method == 'match':
        return qs.filter(groups__name__iexact='Scorer').distinct()
    return qs.filter(
        Q(groups__name__iexact='Judge') | Q(groups__name__iexact='Judges')
    ).distinct()


def _parse_event_points(post):
    """Parse points configuration from admin event forms."""

    def _int_or_none(key):
        val = post.get(key, '').strip()
        if not val:
            return None
        try:
            return max(0, int(val))
        except (ValueError, TypeError):
            return None

    return {
        'champion_points': _int_or_none('champion_points'),
        'first_runner_points': _int_or_none('first_runner_points'),
        'second_runner_points': _int_or_none('second_runner_points'),
        'participation_points': _int_or_none('participation_points'),
    }


def get_event_status_meta(event, today=None):
    today = today or timezone.localdate()
    availability_status = normalize_event_availability_status(event.status)

    if availability_status == Event.STATUS_INACTIVE:
        return {
            'status': Event.STATUS_INACTIVE,
            'status_label': 'Deactivated',
            'availability_status': Event.STATUS_INACTIVE,
        }

    if event.event_date and event.event_date > today:
        status = Event.STATUS_UPCOMING
        label = 'Upcoming'
    elif event.event_date and event.event_date < today:
        status = Event.STATUS_COMPLETED
        label = 'Completed'
    else:
        status = 'ongoing'
        label = 'Ongoing'

    return {
        'status': status,
        'status_label': label,
        'availability_status': Event.STATUS_ACTIVE,
    }


def get_event_status_counts(events, today=None):
    today = today or timezone.localdate()
    counts = {
        'upcoming': 0,
        'ongoing': 0,
        'completed': 0,
        'inactive': 0,
    }
    for event in events:
        counts[get_event_status_meta(event, today)['status']] += 1
    return counts


TOURNAMENT_TYPE_LABELS = {
    'single_elimination': 'Single Elimination',
    'double_elimination': 'Double Elimination',
    'round_robin': 'Round Robin',
    'best_of_3': 'Best of 3',
    'best_of_5': 'Best of 5',
}


def _tournament_type_label(raw):
    key = (raw or '').strip().lower()
    if not key:
        return '—'
    return TOURNAMENT_TYPE_LABELS.get(key, raw.replace('_', ' ').title())


def get_bracket_status_meta(has_bracket, total=0, completed=0, ongoing=0, locked=False):
    """
    Listing bracket status (separate from tournament status):
    Not Generated | Generated | Locked
    Progress fields still reflect actual-match completion for the progress column.
    """
    pct = int(round(completed / total * 100)) if total else 0
    if not has_bracket or total == 0:
        return {
            'bracket_status': 'not_generated',
            'bracket_status_label': 'Not Generated',
            'match_completed': 0,
            'match_total': 0,
            'match_progress_pct': 0,
        }
    if locked:
        status_key, status_label = 'locked', 'Locked'
    else:
        status_key, status_label = 'generated', 'Generated'
    return {
        'bracket_status': status_key,
        'bracket_status_label': status_label,
        'match_completed': completed,
        'match_total': total,
        'match_progress_pct': pct,
    }


def get_tournament_status_meta(event, today=None):
    """
    Tournament lifecycle status for the brackets listing.
    Maps existing Event.status / dates onto Upcoming, Ongoing, Completed, Cancelled.
    Postponed is reserved for an explicit status value if present.
    """
    today = today or timezone.localdate()
    raw = (event.status or '').strip().lower()
    if raw in ('postponed',):
        return {'tournament_status': 'postponed', 'tournament_status_label': 'Postponed'}
    if raw in ('cancelled', 'canceled', Event.STATUS_INACTIVE):
        return {'tournament_status': 'cancelled', 'tournament_status_label': 'Cancelled'}

    availability = normalize_event_availability_status(event.status)
    if availability == Event.STATUS_INACTIVE:
        return {'tournament_status': 'cancelled', 'tournament_status_label': 'Cancelled'}

    end = event.end_date or event.event_date
    if event.event_date and event.event_date > today:
        return {'tournament_status': 'upcoming', 'tournament_status_label': 'Upcoming'}
    if end and end < today:
        return {'tournament_status': 'completed', 'tournament_status_label': 'Completed'}
    if availability == Event.STATUS_COMPLETED or raw == Event.STATUS_COMPLETED:
        return {'tournament_status': 'completed', 'tournament_status_label': 'Completed'}
    if availability == Event.STATUS_UPCOMING and event.event_date and event.event_date > today:
        return {'tournament_status': 'upcoming', 'tournament_status_label': 'Upcoming'}
    return {'tournament_status': 'ongoing', 'tournament_status_label': 'Ongoing'}


def _bracket_listing_sport_label(event):
    sport_type = (event.sport_type or '').strip()
    if sport_type == 'Custom':
        custom = (event.sport_custom_name or '').strip()
        return custom or 'Custom'
    if sport_type:
        return sport_type
    category = (event.category or '').strip()
    if category and category.lower() != 'sports':
        return category
    return 'Not set'


def _bracket_listing_queryset():
    """Match-Based Events only — never Criteria-Based / judged events."""
    return (
        _match_event_queryset()
        .exclude(scoring_method='criteria')
        .exclude(scoring_method__iexact='criteria')
    )


def _serialize_bracket_listing_row(event, *, team_count, match_stats, today):
    from events.match_event_service import normalize_tournament_type

    ms = match_stats.get(event.id, {})
    actual_total = int(ms.get('total') or 0)
    actual_completed = int(ms.get('completed') or 0)
    actual_ongoing = int(ms.get('ongoing') or 0)
    has_structure = actual_total > 0
    tournament_type = normalize_tournament_type(event.tournament_type or 'single_elimination')
    tournament_meta = get_tournament_status_meta(event, today)
    bracket_meta = get_bracket_status_meta(
        has_structure,
        total=actual_total,
        completed=actual_completed,
        ongoing=actual_ongoing,
        locked=bool(event.bracket_locked),
    )
    participants = int(team_count or 0)
    participant_label = f'{participants} Team' if participants == 1 else f'{participants} Teams'
    classification = (event.event_classification or '').strip().lower()
    classification_label = ''
    if classification == Event.CLASSIFICATION_MAJOR:
        classification_label = 'Major Event'
    elif classification == Event.CLASSIFICATION_MINOR:
        classification_label = 'Minor Event'

    return {
        'id': event.id,
        'name': event.name,
        'sport': _bracket_listing_sport_label(event),
        'division': (event.division or '').strip() or '—',
        'classification': classification,
        'classification_label': classification_label,
        'tournament_type': tournament_type,
        'tournament_type_label': _tournament_type_label(tournament_type),
        'participant_count': participants,
        'participant_label': participant_label,
        'has_bracket': has_structure,
        'bracket_locked': bool(event.bracket_locked),
        'edit_url': f'/admin/events/match/{event.id}/edit/',
        'is_round_robin': tournament_type == 'round_robin',
        **tournament_meta,
        **bracket_meta,
        'match_progress_label': (
            f'{actual_completed} of {actual_total} actual matches completed — {bracket_meta["match_progress_pct"]}%'
            if has_structure else 'No actual matches yet'
        ),
    }


def build_tournament_bracket_listing(today=None):
    """Eligible Match-Based rows + summary counts for the Tournament Brackets page."""
    from events.models import BracketMatch, BracketTeam

    today = today or timezone.localdate()
    events = list(_bracket_listing_queryset().order_by('-created_at', '-id'))
    event_ids = [event.id for event in events]

    team_counts = {
        row['event_id']: row['c']
        for row in BracketTeam.objects.filter(event_id__in=event_ids)
        .values('event_id')
        .annotate(c=Count('id'))
    }
    match_stats = {
        row['event_id']: row
        for row in BracketMatch.objects.filter(event_id__in=event_ids, is_automatic_advance=False)
        .values('event_id')
        .annotate(
            total=Count('id'),
            completed=Count(
                'id',
                filter=Q(status__in=[BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT]),
            ),
            ongoing=Count('id', filter=Q(status=BracketMatch.STATUS_ONGOING)),
        )
    }

    event_rows = [
        _serialize_bracket_listing_row(
            event,
            team_count=team_counts.get(event.id, 0),
            match_stats=match_stats,
            today=today,
        )
        for event in events
    ]

    generated = sum(1 for row in event_rows if row['has_bracket'])
    not_generated = sum(1 for row in event_rows if not row['has_bracket'])
    completed = sum(1 for row in event_rows if row['tournament_status'] == 'completed')

    return {
        'event_rows': event_rows,
        'summary': {
            'match_based_events': len(event_rows),
            'generated_brackets': generated,
            'not_generated': not_generated,
            'completed_tournaments': completed,
        },
        'filter_options': {
            'sports': sorted({row['sport'] for row in event_rows if row['sport'] and row['sport'] != 'Not set'}),
            'divisions': sorted({row['division'] for row in event_rows if row['division'] and row['division'] != '—'}),
            'formats': [
                {'value': 'single_elimination', 'label': 'Single Elimination'},
                {'value': 'double_elimination', 'label': 'Double Elimination'},
                {'value': 'round_robin', 'label': 'Round Robin'},
            ],
        },
    }


def authenticate_username(request, username, password):
    """Authenticate by username and password."""
    return authenticate(request, username=username, password=password)


def authenticate_email(request, email, password):
    user = authenticate(request, username=email, password=password)
    if user is not None:
        return user

    User = get_user_model()
    user_match = User.objects.filter(email__iexact=email).first()
    if user_match is None:
        return None

    return authenticate(request, username=user_match.get_username(), password=password)


def user_has_role(user, role):
    if role == 'super-admin':
        return user.is_superuser

    if role == 'admin':
        return user.is_staff or user.groups.filter(name__iexact='Admin').exists()

    return user.groups.filter(name__iexact=ROLE_CHOICES.get(role, '')).exists()


def get_admin_rows():
    User = get_user_model()
    admin_users = User.objects.filter(is_staff=True).order_by('id')
    admin_rows = []

    for user in admin_users:
        groups = list(user.groups.values_list('name', flat=True))
        department = next(
            (name for name in groups if name not in {'Admin', 'Tabulator', 'Judge', 'Judges', 'Viewers'}),
            'Unassigned',
        )
        if user.is_superuser:
            role = 'Super Admin'
            department = 'System'
        else:
            role = 'Admin'

        if user.last_login:
            delta = timezone.now() - user.last_login
            if delta.days:
                last_login = f'{delta.days}d ago'
            else:
                hours = delta.seconds // 3600
                last_login = f'{hours}h ago' if hours else 'Just now'
        else:
            last_login = 'Never'

        admin_rows.append({
            'user': user,
            'department': department,
            'role': role,
            'status': 'Active' if user.is_active else 'Deactivated',
            'last_login': last_login,
        })

    return admin_users, admin_rows


def get_assignment_role(user):
    group_names = [name.strip() for name in user.groups.values_list('name', flat=True)]
    normalized_names = {name.lower(): name for name in group_names}

    for group_name, role_label in ASSIGNMENT_GROUP_ALIASES.items():
        if group_name in normalized_names:
            return role_label

    return None


def _save_assignment_access_code(user, access_code):
    if not access_code:
        return
    from events.models import AssignmentAccountProfile
    profile, _ = AssignmentAccountProfile.objects.get_or_create(user=user)
    profile.access_code = access_code
    profile.save(update_fields=['access_code'])


def _access_code_prefix(role_label):
    """Judge → JDG, Scorer → SCR (3-letter role prefix)."""
    if role_label == 'Judge':
        return 'JDG'
    if role_label == 'Scorer':
        return 'SCR'
    return ''


def _allocate_access_code(role_label, exclude_user_id=None):
    """
    Next available access code for the role: JDG01, JDG02… / SCR01, SCR02…
    Uses the highest existing numeric suffix for that prefix and increments.
    """
    import re
    from events.models import AssignmentAccountProfile

    prefix = _access_code_prefix(role_label)
    if not prefix:
        return ''

    pattern = re.compile(rf'^{re.escape(prefix)}(\d+)$', re.IGNORECASE)
    qs = AssignmentAccountProfile.objects.filter(access_code__istartswith=prefix)
    if exclude_user_id is not None:
        qs = qs.exclude(user_id=exclude_user_id)

    max_n = 0
    for code in qs.values_list('access_code', flat=True):
        match = pattern.match((code or '').strip())
        if match:
            max_n = max(max_n, int(match.group(1)))

    next_n = max_n + 1
    while True:
        candidate = f'{prefix}{next_n:02d}'
        taken = AssignmentAccountProfile.objects.filter(access_code__iexact=candidate)
        if exclude_user_id is not None:
            taken = taken.exclude(user_id=exclude_user_id)
        if not taken.exists():
            return candidate
        next_n += 1


def get_assignment_account_rows(account_type='all'):
    User = get_user_model()
    assignment_users = (
        User.objects
        .filter(
            Q(groups__name__iexact='Tabulator')
            | Q(groups__name__iexact='Judge')
            | Q(groups__name__iexact='Judges')
            | Q(groups__name__iexact='Scorer')
        )
        .exclude(is_staff=True)
        .exclude(is_superuser=True)
        .prefetch_related('groups', 'assignment_profile')
        .distinct()
        .order_by('id')
    )

    # Build lookup: department name (lowered) -> department code
    dept_lookup = {}
    for dept in Department.objects.all():
        dept_lookup[dept.name.lower()] = dept.code

    account_rows = []
    for user in assignment_users:
        role = get_assignment_role(user)
        if role is None:
            continue

        if account_type == 'tabulator' and role != 'Tabulator':
            continue
        if account_type == 'judge_scorer' and role not in ('Judge', 'Scorer'):
            continue

        # Match user's groups against department names to find their dept code
        dept_code = ''
        for group in user.groups.all():
            gname = group.name.lower()
            if gname in dept_lookup:
                dept_code = dept_lookup[gname]
                break

        full_name = user.get_full_name().strip() or user.username
        profile = getattr(user, 'assignment_profile', None)
        access_code = profile.access_code if profile and profile.access_code else '—'
        account_rows.append({
            'user': user,
            'full_name': full_name,
            'role': role,
            'department_code': dept_code,
            'access_code': access_code,
            'status': 'Active' if user.is_active else 'Inactive',
            'status_key': 'active' if user.is_active else 'inactive',
            'contact': 'Not set',
            'initials': ''.join(part[:1] for part in full_name.split()[:2]).upper() or user.username[:2].upper(),
        })

    return account_rows


def get_assignment_account_context(request, account_type='all'):
    status_filter = request.GET.get('status', 'all').strip().lower()
    role_filter = request.GET.get('role', 'all').strip().lower()
    dept_filter = request.GET.get('dept', 'all').strip()
    search_query = request.GET.get('q', '').strip()

    account_rows = get_assignment_account_rows(account_type)
    total_user_count = len(account_rows)
    active_user_count = sum(1 for row in account_rows if row['status_key'] == 'active')

    if search_query:
        query_lower = search_query.lower()
        account_rows = [
            row for row in account_rows
            if query_lower in row['full_name'].lower()
            or query_lower in row['user'].username.lower()
            or query_lower in (row['user'].email or '').lower()
            or query_lower in row['role'].lower()
            or query_lower in row['department_code'].lower()
            or query_lower in (row.get('access_code') or '').lower()
        ]

    if role_filter != 'all' and account_type != 'tabulator':
        account_rows = [row for row in account_rows if row['role'].lower() == role_filter]

    if status_filter != 'all':
        account_rows = [row for row in account_rows if row['status_key'] == status_filter]

    if dept_filter != 'all' and account_type == 'all':
        account_rows = [row for row in account_rows if row['department_code'].lower() == dept_filter.lower()]

    # Department codes for the filter dropdown
    department_options = list(
        Department.objects.values_list('code', flat=True).order_by('code')
    )

    paginator = Paginator(account_rows, 10)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    start_index = page_obj.start_index() if paginator.count else 0
    end_index = page_obj.end_index() if paginator.count else 0

    return {
        'account_rows': page_obj.object_list,
        'account_type': account_type,
        'total_user_count': total_user_count,
        'filtered_user_count': len(account_rows),
        'active_user_count': active_user_count,
        'selected_status': status_filter,
        'selected_role': role_filter,
        'selected_dept': dept_filter,
        'search_query': search_query,
        'department_options': department_options,
        'page_obj': page_obj,
        'start_index': start_index,
        'end_index': end_index,
    }


def get_department_rows():
    departments = Department.objects.all().order_by('name')
    department_rows = []

    if not departments.exists():
        # Fallback to Group-based logic for legacy support
        reserved_groups = {'Admin', 'Tabulator', 'Judge', 'Judges', 'Viewers', 'Scorer'}
        department_groups = Group.objects.exclude(name__in=reserved_groups).order_by('name').prefetch_related('user_set')
        for group in department_groups:
            members = list(group.user_set.all())
            student_count = sum(1 for user in members if not user.is_staff and not user.is_superuser)
            dean_user = next((user for user in members if user.is_staff or user.is_superuser), None)
            dean_name = dean_user.get_full_name().strip() if dean_user else ''
            if not dean_name:
                dean_name = dean_user.username if dean_user else 'Not Assigned'

            department_rows.append({
                'id': group.id,
                'name': group.name,
                'short_name': ''.join(part[:1] for part in group.name.split()[:4]).upper() or group.name[:4].upper(),
                'description': f'{group.name} Department',
                'dean': dean_name,
                'est_year': '--',
                'students': student_count,
                'color': '#244b89',
                'status': 'active',
                'logo': None,
                'code': ''.join(part[:1] for part in group.name.split()[:4]).upper() or group.name[:4].upper(),
                'unit_number': '',
                'head': dean_name if dean_name != 'Not Assigned' else '',
                'remarks': '',
            })
    else:
        for dept in departments:
            try:
                group = Group.objects.get(name=dept.name)
                student_count = group.user_set.filter(is_staff=False, is_superuser=False).count()
            except Group.DoesNotExist:
                student_count = 0

            department_rows.append({
                'id': dept.id,
                'name': dept.name,
                'short_name': dept.code,
                'description': dept.remarks or f'{dept.name} Department',
                'dean': dept.head or 'Not Assigned',
                'est_year': dept.unit_number or '--',
                'students': student_count,
                'color': dept.delegation_color,
                'status': dept.status,
                'logo': dept.logo.url if dept.logo else None,
                'code': dept.code,
                'unit_number': dept.unit_number,
                'head': dept.head,
                'remarks': dept.remarks,
            })

    return department_rows


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_create_department(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    try:
        if request.content_type and 'application/json' in request.content_type:
            data = json.loads(request.body)
        else:
            data = request.POST

        name = data.get('name', '').strip()
        code = data.get('code', '').strip()
        delegation_color = data.get('color', '#022068')
        head = data.get('head', '').strip()
        status = data.get('status', 'active')
        remarks = data.get('remarks', '').strip()

        if not name or not code:
            return JsonResponse({'success': False, 'message': 'Name and Code are required.'}, status=400)

        Group.objects.get_or_create(name=name)

        dept, created = Department.objects.update_or_create(
            code=code,
            defaults={
                'name': name,
                'delegation_color': delegation_color,
                'head': head,
                'status': status,
                'remarks': remarks,
            }
        )

        if 'logo' in request.FILES:
            dept.logo = request.FILES['logo']
            dept.save()

        ct = ContentType.objects.get_for_model(Department)
        LogEntry.objects.create(
            user=request.user,
            content_type_id=ct.id,
            object_id=str(dept.id),
            object_repr=f'Department: {name}',
            action_flag=ADDITION if created else CHANGE,
            change_message=f'{"Created" if created else "Updated"} department "{name}" (code: {code}).',
        )

        return JsonResponse({'success': True, 'message': f'Department {name} created.', 'id': dept.id})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_view_department(request, dept_id):
    """Return department detail as JSON for the View modal."""
    dept = get_object_or_404(Department, id=dept_id)
    try:
        group = Group.objects.get(name=dept.name)
        student_count = group.user_set.filter(is_staff=False, is_superuser=False).count()
    except Group.DoesNotExist:
        student_count = 0
    return JsonResponse({
        'id': dept.id,
        'name': dept.name,
        'code': dept.code,
        'unit_number': dept.unit_number,
        'delegation_color': dept.delegation_color,
        'head': dept.head,
        'status': dept.status,
        'remarks': dept.remarks,
        'students': student_count,
        'logo': dept.logo.url if dept.logo else None,
    })


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_edit_department(request, dept_id):
    """Update an existing Department (supports logo upload via multipart)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    dept = get_object_or_404(Department, id=dept_id)
    old_name = dept.name
    try:
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip()
        if not name or not code:
            return JsonResponse({'success': False, 'message': 'Name and Code are required.'}, status=400)

        dept.name = name
        dept.code = code
        dept.unit_number = request.POST.get('unit_number', '').strip()
        dept.delegation_color = request.POST.get('color', dept.delegation_color)
        dept.head = request.POST.get('head', '').strip()
        dept.status = request.POST.get('status', dept.status)
        dept.remarks = request.POST.get('remarks', '').strip()

        if 'logo' in request.FILES:
            dept.logo = request.FILES['logo']

        dept.save()
        Group.objects.get_or_create(name=name)
        if old_name != name:
            replacement_name = '' if name.lower() in RESERVED_DEPARTMENT_NAMES else name
            Event.objects.filter(department__iexact=old_name).update(department=replacement_name)

        # Log the edit
        from django.contrib.admin.models import LogEntry, CHANGE as LOG_CHANGE
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(Department)
        changes = []
        if old_name != name:
            changes.append(f'name changed from "{old_name}" to "{name}"')
        LogEntry.objects.create(
            user=request.user,
            content_type_id=ct.id,
            object_id=str(dept.id),
            object_repr=f'Department: {name}',
            action_flag=LOG_CHANGE,
            change_message=f'Updated department "{name}". ' + ('; '.join(changes) if changes else 'Details updated.')
        )

        return JsonResponse({'success': True, 'message': f'Department {name} updated.'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_delete_department(request, dept_id):
    """Delete a Department (or fall back to deleting a Group if no Department exists)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    # Try Department first
    try:
        dept = Department.objects.get(id=dept_id)
        name = dept.name
        Event.objects.filter(department__iexact=name).update(department='')
        dept.delete()
        return JsonResponse({'success': True, 'message': f'Department "{name}" deleted successfully.'})
    except Department.DoesNotExist:
        pass

    # Fallback: legacy Group-based row
    try:
        group = Group.objects.get(id=dept_id)
        name = group.name
        if name in {'Admin', 'Tabulator', 'Judge', 'Judges', 'Viewers', 'Scorer'}:
            return JsonResponse(
                {'success': False, 'message': f'Cannot delete reserved role "{name}".'},
                status=400,
            )
        Event.objects.filter(department__iexact=name).update(department='')
        group.delete()
        return JsonResponse({'success': True, 'message': f'Department "{name}" deleted successfully.'})
    except Group.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Department not found.'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_bulk_delete_departments(request):
    """Delete multiple departments at once. Body: {ids: [1,2,3]}"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'success': False, 'message': 'Invalid JSON.'}, status=400)

    ids = payload.get('ids') or []
    if not isinstance(ids, list) or not ids:
        return JsonResponse({'success': False, 'message': 'No department IDs provided.'}, status=400)

    deleted_count = 0
    skipped_count = 0
    reserved = {'Admin', 'Tabulator', 'Judge', 'Judges', 'Viewers', 'Scorer'}

    for raw_id in ids:
        try:
            dept_id = int(raw_id)
        except (TypeError, ValueError):
            skipped_count += 1
            continue

        # Try Department first
        try:
            dept = Department.objects.get(id=dept_id)
            Event.objects.filter(department__iexact=dept.name).update(department='')
            dept.delete()
            deleted_count += 1
            continue
        except Department.DoesNotExist:
            pass

        # Fallback: Group
        try:
            group = Group.objects.get(id=dept_id)
            if group.name in reserved:
                skipped_count += 1
                continue
            Event.objects.filter(department__iexact=group.name).update(department='')
            group.delete()
            deleted_count += 1
        except Group.DoesNotExist:
            skipped_count += 1

    msg_parts = [f'{deleted_count} department(s) deleted']
    if skipped_count:
        msg_parts.append(f'{skipped_count} skipped')
    return JsonResponse({
        'success': True,
        'message': '. '.join(msg_parts) + '.',
        'deleted': deleted_count,
        'skipped': skipped_count,
    })


def _detect_user_role(user):
    """Return the highest-priority role key for this user, or None."""
    if user.is_superuser:
        return 'super-admin'
    if user.is_staff or user.groups.filter(name__iexact='Admin').exists():
        return 'admin'
    if user.groups.filter(name__iexact='Tabulator').exists():
        return 'tabulator'
    if (
        user.groups.filter(name__iexact='Judge').exists()
        or user.groups.filter(name__iexact='Judges').exists()
    ):
        return 'judge'
    return None


_ROLE_REDIRECTS = {
    'super-admin': 'superadmin_dashboard',
    'admin': 'admin_dashboard',
    'tabulator': 'faculty_dashboard',
}

_ROLE_LABELS = {
    'super-admin': 'Super Admin',
    'admin': 'Admin',
    'tabulator': 'Faculty',
    'judge': 'Judge',
}


@never_cache
@ensure_csrf_cookie
def login_view(request):
    context = {}

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        context['username'] = username

        user = authenticate_username(request, username, password)
        if user is None:
            context['error'] = 'Invalid username or password.'
            return render(request, 'login.html', context)

        role = _detect_user_role(user)
        if role is None:
            context['error'] = 'Your account has no assigned role. Contact an administrator.'
            return render(request, 'login.html', context)

        login(request, user)
        request.session['login_role'] = role

        try:
            from events.audit_service import record_audit
            record_audit(
                user=user,
                action='Logged In',
                description=f'{user.get_full_name() or user.username} logged in as {_ROLE_LABELS.get(role, role)}.',
                module='Authentication',
                request=request,
            )
        except Exception:
            pass

        redirect_name = _ROLE_REDIRECTS.get(role)
        if redirect_name:
            return redirect(redirect_name)

        context['success'] = (
            f'Logged in as {_ROLE_LABELS[role]}. '
            'Open the EventTab judge mobile app and sign in with this username and password.'
        )

    return render(request, 'login.html', context)


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_dashboard(request):
    ctx = get_assignment_account_context(request)
    # Add event stats for the dashboard metrics
    all_events = list(Event.objects.all())
    today = timezone.localdate()
    status_counts = get_event_status_counts(all_events, today)
    ctx['ongoing_count'] = status_counts['ongoing']
    ctx['upcoming_count'] = status_counts['upcoming']
    ctx['total_events_count'] = len(all_events)
    ctx['completed_events_count'] = status_counts['completed']
    ctx['inactive_count'] = status_counts['inactive']
    total = ctx['total_events_count'] or 1
    ctx['completed_pct'] = int(ctx['completed_events_count'] / total * 100)

    # Total participants and teams across all events
    total_participants = sum(int(ev.max_participants or 0) for ev in all_events)
    total_teams = sum(int(ev.num_teams or 0) for ev in all_events)
    ctx['total_participants'] = total_participants
    ctx['total_teams'] = total_teams

    # Top categories breakdown (count of events + sum of participants per category)
    cat_data = {}
    for ev in all_events:
        cat = (ev.category or 'Other').strip() or 'Other'
        if cat not in cat_data:
            cat_data[cat] = {'count': 0, 'participants': 0}
        cat_data[cat]['count'] += 1
        cat_data[cat]['participants'] += int(ev.max_participants or 0)
    top_categories = sorted(
        [{'name': k, 'count': v['count'], 'participants': v['participants']} for k, v in cat_data.items()],
        key=lambda x: x['participants'], reverse=True
    )[:5]
    total_part_for_pct = total_participants or 1
    for c in top_categories:
        c['pct'] = int(round(c['participants'] / total_part_for_pct * 100))
    ctx['top_categories'] = top_categories

    # Recent events (last 5 by date desc)
    recent = sorted(all_events, key=lambda e: (e.event_date or today), reverse=True)[:5]
    recent_events = []
    for ev in recent:
        sm = get_event_status_meta(ev, today)
        recent_events.append({
            'id': ev.id,
            'name': ev.name,
            'category': ev.category,
            'status': sm['status'],
            'status_label': sm['status_label'],
            'num_teams': ev.num_teams or 0,
            'max_participants': ev.max_participants or 0,
            'date_label': ev.event_date.strftime('%b %d, %Y') if ev.event_date else '—',
        })
    ctx['recent_events'] = recent_events

    # Upcoming events list
    upcoming_list = []
    for ev in all_events:
        if ev.event_date and ev.event_date >= today:
            days_left = (ev.event_date - today).days
            upcoming_list.append({
                'id': ev.id,
                'name': ev.name,
                'category': ev.category,
                'num_teams': ev.num_teams or 0,
                'day': ev.event_date.day,
                'month_short': ev.event_date.strftime('%b').upper(),
                'days_left': days_left,
            })
    upcoming_list.sort(key=lambda x: x['days_left'])
    ctx['upcoming_events_list'] = upcoming_list[:5]

    # Recent activity from Django LogEntry (last 5)
    recent_actions = LogEntry.objects.select_related('user').order_by('-action_time')[:5]
    activity_rows = []
    for action in recent_actions:
        delta = timezone.now() - action.action_time
        if delta.days >= 1:
            ago = f'{delta.days}d ago'
        elif delta.seconds >= 3600:
            ago = f'{delta.seconds // 3600}h ago'
        elif delta.seconds >= 60:
            ago = f'{delta.seconds // 60}m ago'
        else:
            ago = 'just now'
        activity_rows.append({
            'message': action.change_message or f'{action.get_action_flag_display()} {action.object_repr}',
            'object_repr': action.object_repr,
            'user': action.user.get_full_name() or action.user.username if action.user else 'System',
            'ago': ago,
        })
    ctx['recent_activity'] = activity_rows

    # Events overview chart (last 8 weeks: events created + total participants per week)
    from datetime import timedelta as _td
    chart_labels = []
    chart_events = []
    chart_parts = []
    for i in range(7, -1, -1):
        week_end = today - _td(days=i * 7)
        week_start = week_end - _td(days=6)
        evs_in_week = [e for e in all_events if e.event_date and week_start <= e.event_date <= week_end]
        chart_labels.append(week_end.strftime('%b %d'))
        chart_events.append(len(evs_in_week))
        chart_parts.append(sum(int(e.max_participants or 0) for e in evs_in_week))
    ctx['chart_labels_json'] = json.dumps(chart_labels)
    ctx['chart_events_json'] = json.dumps(chart_events)
    ctx['chart_participants_json'] = json.dumps(chart_parts)

    # Status doughnut data
    ctx['status_counts_json'] = json.dumps({
        'ongoing': status_counts['ongoing'],
        'upcoming': status_counts['upcoming'],
        'completed': status_counts['completed'],
        'cancelled': status_counts['inactive'],
    })

    # Participation heatmap (5 weeks × 7 days, count of events per day)
    heatmap_weeks = []
    for w in range(4, -1, -1):
        week_end = today - _td(days=w * 7)
        week_start = week_end - _td(days=6)
        days = []
        for d in range(7):
            day_date = week_start + _td(days=d)
            count = sum(1 for e in all_events if e.event_date == day_date)
            days.append({'date': day_date.strftime('%Y-%m-%d'), 'count': count})
        heatmap_weeks.append({
            'label_start': week_start.strftime('%b %d'),
            'label_end': week_end.strftime('%b %d'),
            'days': days,
        })
    ctx['heatmap_weeks'] = heatmap_weeks

    # Today's date label for the topbar pill
    ctx['today_range_label'] = today.strftime('%b %d, %Y')
    ctx['month_range_label'] = today.replace(day=1).strftime('%b 1') + ' – ' + today.strftime('%b %d, %Y')

    # Serialize events for the calendar (rendered as JSON in template)
    events_json = []
    for ev in all_events:
        status_meta = get_event_status_meta(ev, today)
        events_json.append({
            'id': ev.id,
            'title': ev.name,
            'date': ev.event_date.strftime('%Y-%m-%d') if ev.event_date else '',
            'time': ev.event_time.strftime('%H:%M') if ev.event_time else '',
            'category': ev.category,
            'venue': ev.venue,
            'status': status_meta['status'],
            'status_label': status_meta['status_label'],
            'availability_status': status_meta['availability_status'],
        })
    ctx['events_json'] = json.dumps(events_json)
    return render(request, 'admindash/admindashboard.html', ctx)


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_manage_account(request):
    return redirect('admin_tabulator_accounts')


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_tabulator_accounts(request):
    ctx = get_assignment_account_context(request, account_type='tabulator')
    return render(request, 'admindash/facultyaccount.html', ctx)


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_judge_scorer_accounts(request):
    ctx = get_assignment_account_context(request, account_type='judge_scorer')
    return render(request, 'admindash/judgescoreraccount.html', ctx)


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_generate_report(request):
    """Generate Reports — preview, export, and history for administrators."""
    from django.contrib.admin.models import LogEntry, CHANGE
    from django.contrib.contenttypes.models import ContentType
    from events.admin_report_service import (
        REPORT_CATALOG,
        build_report_preview,
        export_report_csv,
        export_report_excel,
        export_report_pdf,
        filter_context,
        history_rows,
        save_report_history,
    )
    from events.models import AdminReportHistory
    from events.audit_service import record_audit

    ctx_filters = filter_context()
    preview = None
    selected = {
        'category': request.GET.get('category') or request.POST.get('category') or 'events',
        'report_type': request.GET.get('report_type') or request.POST.get('report_type') or 'event_summary',
        'date_from': request.GET.get('date_from') or request.POST.get('date_from') or '',
        'date_to': request.GET.get('date_to') or request.POST.get('date_to') or '',
        'event_id': request.GET.get('event_id') or request.POST.get('event_id') or 'all',
        'event_type': request.GET.get('event_type') or request.POST.get('event_type') or 'all',
        'category_filter': request.GET.get('category_filter') or request.POST.get('category_filter') or 'all',
        'classification': request.GET.get('classification') or request.POST.get('classification') or 'all',
        'department': request.GET.get('department') or request.POST.get('department') or 'all',
        'faculty_id': request.GET.get('faculty_id') or request.POST.get('faculty_id') or 'all',
        'judge_id': request.GET.get('judge_id') or request.POST.get('judge_id') or 'all',
        'status': request.GET.get('status') or request.POST.get('status') or 'all',
        'export_format': request.POST.get('export_format') or 'pdf',
    }

    def _filters_from_selected():
        return {
            'date_from': selected['date_from'],
            'date_to': selected['date_to'],
            'event_id': '' if selected['event_id'] == 'all' else selected['event_id'],
            'event_type': selected['event_type'],
            'category': selected['category_filter'],
            'classification': selected['classification'],
            'department': selected['department'],
            'faculty_id': selected['faculty_id'],
            'judge_id': selected['judge_id'],
            'status': selected['status'],
        }

    if request.method == 'POST':
        action = (request.POST.get('action') or 'preview').strip().lower()
        history_id = request.POST.get('history_id', '').strip()

        if action == 'delete_history' and history_id.isdigit():
            AdminReportHistory.objects.filter(pk=int(history_id)).delete()
            messages.success(request, 'Report history entry deleted.')
            return redirect('admin_generate_report')

        if action == 'download_history' and history_id.isdigit():
            hist = AdminReportHistory.objects.filter(pk=int(history_id)).first()
            if hist and hist.file:
                resp = HttpResponse(hist.file.read(), content_type='application/octet-stream')
                resp['Content-Disposition'] = f'attachment; filename="{hist.name[:40]}.{hist.export_format or "bin"}"'
                return resp
            messages.error(request, 'Report file not found. Generate the report again.')
            return redirect('admin_generate_report')

        filters = _filters_from_selected()
        preview = build_report_preview(selected['report_type'], filters)

        if action in ('export', 'download_pdf', 'download_excel', 'download_csv'):
            export_format = selected['export_format']
            if action == 'download_pdf':
                export_format = 'pdf'
            elif action == 'download_excel':
                export_format = 'excel'
            elif action == 'download_csv':
                export_format = 'csv'

            try:
                if export_format in ('excel', 'xlsx'):
                    content = export_report_excel(preview)
                    fname = f"EventTab_{selected['report_type']}_{timezone.localdate().strftime('%Y%m%d')}.xlsx"
                    ctype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                elif export_format == 'csv':
                    content = export_report_csv(preview).encode('utf-8')
                    fname = f"EventTab_{selected['report_type']}_{timezone.localdate().strftime('%Y%m%d')}.csv"
                    ctype = 'text/csv'
                else:
                    content = export_report_pdf(preview)
                    fname = f"EventTab_{selected['report_type']}_{timezone.localdate().strftime('%Y%m%d')}.pdf"
                    ctype = 'application/pdf'

                save_report_history(
                    user=request.user,
                    preview=preview,
                    export_format='xlsx' if export_format in ('excel', 'xlsx') else export_format,
                    file_content=content if isinstance(content, (bytes, bytearray)) else content.encode('utf-8'),
                )
                LogEntry.objects.create(
                    user=request.user,
                    content_type_id=ContentType.objects.get_for_model(Event).id,
                    object_id='0',
                    object_repr=preview.get('title', 'Report')[:200],
                    action_flag=CHANGE,
                    change_message=f'Generated {export_format.upper()} report.',
                )
                try:
                    record_audit(
                        user=request.user,
                        action='Generated Reports',
                        description=f"Generated {preview.get('title')} ({export_format.upper()}).",
                        module='Reports',
                        request=request,
                    )
                except Exception:
                    pass

                resp = HttpResponse(content, content_type=ctype)
                resp['Content-Disposition'] = f'attachment; filename="{fname}"'
                return resp
            except Exception as exc:
                messages.error(request, f'Report export failed: {exc}')
                return redirect('admin_generate_report')

        # preview action — fall through to render with preview

    return render(request, 'admindash/generatereport.html', {
        'catalog': REPORT_CATALOG,
        'selected': selected,
        'preview': preview,
        'history': history_rows(25),
        'filter_events': ctx_filters['events'],
        'departments': ctx_filters['departments'],
        'categories': ctx_filters['categories'],
        'faculty_users': ctx_filters['faculty_users'],
        'judge_users': ctx_filters['judge_users'],
    })


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_departments(request):
    all_rows = get_department_rows()
    search_query = request.GET.get('q', '').strip()
    department_rows = all_rows
    if search_query:
        query_lower = search_query.lower()
        department_rows = [
            row for row in department_rows
            if query_lower in row['name'].lower()
            or query_lower in row['dean'].lower()
            or query_lower in row['description'].lower()
        ]
    with_dean_count = sum(1 for row in all_rows if row['dean'] != 'Not Assigned')
    active_department_count = sum(1 for row in all_rows if row.get('status', 'active') == 'active')
    total_students = sum(row['students'] for row in all_rows)
    # Collect unique dean names for filter dropdown
    unique_deans = sorted({row['dean'] for row in all_rows if row['dean'] != 'Not Assigned'})
    return render(request, 'admindash/department.html', {
        'department_rows': department_rows,
        'total_department_count': len(all_rows),
        'active_department_count': active_department_count,
        'visible_department_count': len(department_rows),
        'search_query': search_query,
        'with_dean_count': with_dean_count,
        'total_students_count': total_students,
        'unique_deans': unique_deans,
        'new_department_count': 0,
    })


def get_active_departments():
    return list(
        Department.objects.filter(status=Department.STATUS_ACTIVE).order_by('name')
    )


def get_team_rows():
    rows = []
    for team in Team.objects.select_related('department').order_by('name'):
        members_display, player_count = parse_team_members(team.members)
        rows.append({
            'id': team.id,
            'name': team.name,
            'code': team.code,
            'department_id': team.department_id,
            'department_name': team.department.name if team.department else '—',
            'department_code': team.department.code if team.department else '',
            'image_url': team.image.url if team.image else '',
            'members': team.members,
            'members_display': members_display or '—',
            'player_count': player_count,
            'coach': team.coach or '—',
            'status': team.status,
            'status_label': 'Active' if team.status == Team.STATUS_ACTIVE else 'Inactive',
            'avatar_tone': team.id % 5,
        })
    return rows


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_teams(request):
    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'all').strip().lower()
    dept_filter = request.GET.get('dept', 'all').strip()

    team_rows = get_team_rows()
    if search_query:
        q = search_query.lower()
        team_rows = [
            row for row in team_rows
            if q in row['name'].lower()
            or q in row['code'].lower()
            or q in row['department_name'].lower()
            or q in row['coach'].lower()
            or q in row['members_display'].lower()
        ]
    if status_filter != 'all':
        team_rows = [row for row in team_rows if row['status'] == status_filter]
    if dept_filter != 'all':
        team_rows = [
            row for row in team_rows
            if str(row['department_id'] or '') == dept_filter
        ]

    paginator = Paginator(team_rows, 10)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    start_index = page_obj.start_index() if paginator.count else 0
    end_index = page_obj.end_index() if paginator.count else 0

    all_teams = Team.objects.all()
    total_team_count = all_teams.count()
    active_team_count = all_teams.filter(status=Team.STATUS_ACTIVE).count()
    total_player_count = sum(parse_team_members(team.members)[1] for team in all_teams)
    active_team_percent = round((active_team_count / total_team_count) * 100) if total_team_count else 0

    return render(request, 'admindash/teams.html', {
        'team_rows': page_obj.object_list,
        'total_team_count': total_team_count,
        'active_team_count': active_team_count,
        'active_team_percent': active_team_percent,
        'total_player_count': total_player_count,
        'filtered_team_count': len(team_rows),
        'active_departments': get_active_departments(),
        'search_query': search_query,
        'selected_status': status_filter,
        'selected_dept': dept_filter,
        'page_obj': page_obj,
        'start_index': start_index,
        'end_index': end_index,
    })


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_candidates(request):
    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'all').strip().lower()
    dept_filter = request.GET.get('dept', 'all').strip()

    candidate_rows = get_candidate_rows()
    if search_query:
        q = search_query.lower()
        candidate_rows = [
            row for row in candidate_rows
            if q in row['name'].lower()
            or q in row['number'].lower()
            or q in row['department_name'].lower()
        ]
    if status_filter != 'all':
        candidate_rows = [row for row in candidate_rows if row['status'] == status_filter]
    if dept_filter != 'all':
        candidate_rows = [
            row for row in candidate_rows
            if str(row['department_id'] or '') == dept_filter
        ]

    paginator = Paginator(candidate_rows, 10)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    start_index = page_obj.start_index() if paginator.count else 0
    end_index = page_obj.end_index() if paginator.count else 0

    all_candidates = RegistryCandidate.objects.all()
    total_candidate_count = all_candidates.count()
    active_candidate_count = all_candidates.filter(status=RegistryCandidate.STATUS_ACTIVE).count()
    active_candidate_percent = round((active_candidate_count / total_candidate_count) * 100) if total_candidate_count else 0

    return render(request, 'admindash/candidate.html', {
        'candidate_rows': page_obj.object_list,
        'total_candidate_count': total_candidate_count,
        'active_candidate_count': active_candidate_count,
        'active_candidate_percent': active_candidate_percent,
        'filtered_candidate_count': len(candidate_rows),
        'active_departments': get_active_departments(),
        'search_query': search_query,
        'selected_status': status_filter,
        'selected_dept': dept_filter,
        'page_obj': page_obj,
        'start_index': start_index,
        'end_index': end_index,
    })


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_create_team(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    is_multipart = (request.content_type or '').startswith('multipart/form-data')
    data = request.POST if is_multipart else _parse_json_request(request)
    name = (data.get('name') or '').strip()
    code = (data.get('code') or '').strip()
    coach = (data.get('coach') or '').strip()
    members = data.get('members', '')
    status = (data.get('status') or Team.STATUS_ACTIVE).strip().lower()
    dept_id = data.get('department_id')
    image = request.FILES.get('image') if is_multipart else None

    if not name or not code:
        return JsonResponse({'success': False, 'message': 'Team name and team code are required.'}, status=400)
    if Team.objects.filter(code__iexact=code).exists():
        return JsonResponse({'success': False, 'message': 'Team code already exists.'}, status=400)
    if status not in (Team.STATUS_ACTIVE, Team.STATUS_INACTIVE):
        status = Team.STATUS_ACTIVE

    department = None
    if dept_id:
        department = Department.objects.filter(
            id=dept_id,
            status=Department.STATUS_ACTIVE,
        ).first()
        if department is None:
            return JsonResponse({'success': False, 'message': 'Select a valid active department.'}, status=400)

    members_str, _ = parse_team_members(members)
    team = Team.objects.create(
        name=name,
        code=code,
        department=department,
        image=image,
        members=members_str,
        coach=coach,
        status=status,
    )
    return JsonResponse({'success': True, 'message': f'Team "{team.name}" created.', 'id': team.id})


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_view_team(request, team_id):
    team = get_object_or_404(Team.objects.select_related('department'), id=team_id)
    members_display, player_count = parse_team_members(team.members)
    member_names = [m.strip() for m in members_display.split(',') if m.strip()] if members_display else []
    return JsonResponse({
        'id': team.id,
        'name': team.name,
        'code': team.code,
        'department_name': team.department.name if team.department else '—',
        'image_url': team.image.url if team.image else '',
        'coach': team.coach or '—',
        'status': team.status,
        'status_label': 'Active' if team.status == Team.STATUS_ACTIVE else 'Inactive',
        'player_count': player_count,
        'members': member_names,
        'members_display': members_display or '—',
    })


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_edit_team(request, team_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    team = get_object_or_404(Team, id=team_id)
    is_multipart = (request.content_type or '').startswith('multipart/form-data')
    data = request.POST if is_multipart else _parse_json_request(request)
    name = (data.get('name') or '').strip()
    code = (data.get('code') or '').strip()
    coach = (data.get('coach') or '').strip()
    members = data.get('members', '')
    status = (data.get('status') or team.status).strip().lower()
    dept_id = data.get('department_id')
    image = request.FILES.get('image') if is_multipart else None

    if not name or not code:
        return JsonResponse({'success': False, 'message': 'Team name and team code are required.'}, status=400)
    if Team.objects.filter(code__iexact=code).exclude(id=team.id).exists():
        return JsonResponse({'success': False, 'message': 'Team code already exists.'}, status=400)
    if status not in (Team.STATUS_ACTIVE, Team.STATUS_INACTIVE):
        status = team.status

    department = None
    if dept_id:
        department = Department.objects.filter(
            id=dept_id,
            status=Department.STATUS_ACTIVE,
        ).first()
        if department is None:
            return JsonResponse({'success': False, 'message': 'Select a valid active department.'}, status=400)

    members_str, _ = parse_team_members(members)
    team.name = name
    team.code = code
    team.department = department
    team.members = members_str
    team.coach = coach
    team.status = status
    if image:
        if team.image:
            team.image.delete(save=False)
        team.image = image
    team.save()
    return JsonResponse({'success': True, 'message': f'Team "{team.name}" updated.'})


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_delete_team(request, team_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    team = get_object_or_404(Team, id=team_id)
    name = team.name
    try:
        team.delete()
    except IntegrityError:
        return JsonResponse({
            'success': False,
            'message': (
                f'Cannot delete "{name}" because it is still linked to existing records. '
                'Remove those links first, then try again.'
            ),
        }, status=400)
    return JsonResponse({'success': True, 'message': f'Team "{name}" deleted.'})


def get_candidate_rows():
    rows = []
    for cand in RegistryCandidate.objects.select_related('department').order_by('number', 'name'):
        rows.append({
            'id': cand.id,
            'number': cand.number,
            'name': cand.name,
            'department_id': cand.department_id,
            'department_name': cand.department.name if cand.department else '—',
            'department_code': cand.department.code if cand.department else '',
            'image_url': cand.image.url if cand.image else '',
            'status': cand.status,
            'status_label': 'Active' if cand.status == RegistryCandidate.STATUS_ACTIVE else 'Inactive',
            'avatar_tone': cand.id % 5,
        })
    return rows


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_create_candidate(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    is_multipart = (request.content_type or '').startswith('multipart/form-data')
    data = request.POST if is_multipart else _parse_json_request(request)
    number = (data.get('number') or '').strip()
    name = (data.get('name') or '').strip()
    status = (data.get('status') or RegistryCandidate.STATUS_ACTIVE).strip().lower()
    dept_id = data.get('department_id')
    image = request.FILES.get('image') if is_multipart else None

    if not number or not name:
        return JsonResponse({'success': False, 'message': 'Candidate number and name are required.'}, status=400)
    if RegistryCandidate.objects.filter(number__iexact=number).exists():
        return JsonResponse({'success': False, 'message': 'Candidate number already exists.'}, status=400)
    if status not in (RegistryCandidate.STATUS_ACTIVE, RegistryCandidate.STATUS_INACTIVE):
        status = RegistryCandidate.STATUS_ACTIVE

    department = None
    if dept_id:
        department = Department.objects.filter(
            id=dept_id,
            status=Department.STATUS_ACTIVE,
        ).first()
        if department is None:
            return JsonResponse({'success': False, 'message': 'Select a valid active department.'}, status=400)

    cand = RegistryCandidate.objects.create(
        number=number,
        name=name,
        department=department,
        image=image,
        status=status,
    )
    return JsonResponse({'success': True, 'message': f'Candidate "{cand.name}" created.', 'id': cand.id})


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_view_candidate(request, candidate_id):
    cand = get_object_or_404(RegistryCandidate.objects.select_related('department'), id=candidate_id)
    return JsonResponse({
        'id': cand.id,
        'number': cand.number,
        'name': cand.name,
        'department_name': cand.department.name if cand.department else '—',
        'image_url': cand.image.url if cand.image else '',
        'status': cand.status,
        'status_label': 'Active' if cand.status == RegistryCandidate.STATUS_ACTIVE else 'Inactive',
    })


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_edit_candidate(request, candidate_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    cand = get_object_or_404(RegistryCandidate, id=candidate_id)
    is_multipart = (request.content_type or '').startswith('multipart/form-data')
    data = request.POST if is_multipart else _parse_json_request(request)
    number = (data.get('number') or '').strip()
    name = (data.get('name') or '').strip()
    status = (data.get('status') or cand.status).strip().lower()
    dept_id = data.get('department_id')
    image = request.FILES.get('image') if is_multipart else None
    remove_image = str(data.get('remove_image') or '').strip().lower() in ('1', 'true', 'yes', 'on')

    if not number or not name:
        return JsonResponse({'success': False, 'message': 'Candidate number and name are required.'}, status=400)
    if RegistryCandidate.objects.filter(number__iexact=number).exclude(id=cand.id).exists():
        return JsonResponse({'success': False, 'message': 'Candidate number already exists.'}, status=400)
    if status not in (RegistryCandidate.STATUS_ACTIVE, RegistryCandidate.STATUS_INACTIVE):
        status = cand.status

    department = None
    if dept_id:
        department = Department.objects.filter(
            id=dept_id,
            status=Department.STATUS_ACTIVE,
        ).first()
        if department is None:
            return JsonResponse({'success': False, 'message': 'Select a valid active department.'}, status=400)

    cand.number = number
    cand.name = name
    cand.department = department
    if remove_image and cand.image:
        cand.image.delete(save=False)
        cand.image = None
    if image:
        if cand.image:
            cand.image.delete(save=False)
        cand.image = image
    cand.status = status
    cand.save()
    return JsonResponse({'success': True, 'message': f'Candidate "{cand.name}" updated.'})


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_delete_candidate(request, candidate_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    cand = get_object_or_404(RegistryCandidate, id=candidate_id)
    name = cand.name
    try:
        cand.delete()
    except IntegrityError:
        return JsonResponse({
            'success': False,
            'message': (
                f'Cannot delete "{name}" because it is still linked to existing records. '
                'Remove those links first, then try again.'
            ),
        }, status=400)
    return JsonResponse({'success': True, 'message': f'Candidate "{name}" deleted.'})


def _split_full_name(full_name):
    name_parts = (full_name or '').strip().split()
    if not name_parts:
        return '', ''
    if len(name_parts) == 1:
        return name_parts[0], ''
    return name_parts[0], ' '.join(name_parts[1:])


def _generate_username_from_name(full_name):
    """
    Use the official name the admin entered as the username.
    Only appends _2, _3… if that exact name is already taken — never a random nickname.
    """
    User = get_user_model()
    base = (full_name or '').strip()
    # Django usernames allow letters, digits, and @/./+/-/_
    if base and not re.fullmatch(r'[\w.@+-]+', base, flags=re.UNICODE):
        base = re.sub(r'[^\w.@+-]', '_', base, flags=re.UNICODE)
        base = re.sub(r'_+', '_', base).strip('_')
    base = (base or 'user')[:150]

    candidate = base
    n = 2
    while User.objects.filter(username__iexact=candidate).exists():
        suffix = f'_{n}'
        candidate = base[: max(1, 150 - len(suffix))] + suffix
        n += 1
    return candidate


def _normalize_assignment_role(role):
    role_label = (role or '').strip().title()
    if role_label not in ASSIGNMENT_ROLE_LABELS:
        return None
    return role_label


def _apply_assignment_role(user, role, is_active):
    role_label = _normalize_assignment_role(role)
    if role_label is None:
        raise ValueError('Select Tabulator, Judge, or Scorer as the account role.')

    assignment_groups = Group.objects.filter(
        Q(name__iexact='Tabulator')
        | Q(name__iexact='Judge')
        | Q(name__iexact='Judges')
        | Q(name__iexact='Scorer')
    )
    user.groups.remove(*assignment_groups)
    role_group, _ = Group.objects.get_or_create(name=role_label)
    user.groups.add(role_group)
    user.is_staff = False
    user.is_superuser = False
    user.is_active = is_active
    return role_label


def _assignment_user_or_none(account_id):
    User = get_user_model()
    try:
        user = User.objects.prefetch_related('groups').get(id=account_id, is_staff=False, is_superuser=False)
    except User.DoesNotExist:
        return None

    if get_assignment_role(user) is None:
        return None
    return user


def _assignment_payload(request):
    payload = _parse_json_request(request)
    return {
        'full_name': (payload.get('full_name') or '').strip(),
        'username': (payload.get('username') or '').strip(),
        'email': (payload.get('email') or '').strip(),
        'password': payload.get('password') or '',
        'access_code': (payload.get('access_code') or '').strip(),
        'regenerate_access_code': bool(payload.get('regenerate_access_code')),
        'role': (payload.get('role') or '').strip(),
        'is_active': bool(payload.get('is_active', True)),
    }


CODE_ROLES = {'Judge', 'Scorer'}

logger = logging.getLogger(__name__)


def _normalize_gmail(email):
    """Validate and normalize an email address. Returns (ok, value_or_error)."""
    value = (email or '').strip()
    if not value:
        return False, 'Gmail is required.'
    try:
        validate_email(value)
    except ValidationError:
        return False, 'Enter a valid Gmail address.'
    return True, value


def _email_smtp_configured():
    """True when real SMTP credentials are available (not console-only)."""
    backend = (getattr(settings, 'EMAIL_BACKEND', '') or '').lower()
    if 'console' in backend or 'locmem' in backend or 'dummy' in backend:
        return False
    return bool(getattr(settings, 'EMAIL_HOST_USER', '') and getattr(settings, 'EMAIL_HOST_PASSWORD', ''))


def _send_assignment_credentials_email(*, to_email, full_name, role_label, access_code=None, username=None, password=None):
    """Send access credentials to the given Gmail. Returns True only on real SMTP success."""
    if not _email_smtp_configured():
        logger.warning(
            'Skipping real email to %s — set EMAIL_HOST_USER and EMAIL_HOST_PASSWORD in .env for Gmail SMTP.',
            to_email,
        )
        return False

    display_name = (full_name or username or 'there').strip() or 'there'
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None) or settings.EMAIL_HOST_USER

    if access_code:
        subject = f'EventTab {role_label} Access Code: {access_code}'
        text_body = (
            f'Hello {display_name},\n\n'
            f'Your EventTab {role_label} account has been created.\n\n'
            f'========================================\n'
            f'  YOUR ACCESS CODE:  {access_code}\n'
            f'========================================\n\n'
            f'Use this access code to sign in to the EventTab app.\n\n'
            f'— EventTab Admin'
        )
        html_body = f'''<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;background:#f5f7fb;padding:24px;color:#0f172a;">
  <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:12px;padding:28px;border:1px solid #e2e8f0;">
    <h2 style="margin:0 0 8px;color:#0b3a6e;">EventTab Access Code</h2>
    <p style="margin:0 0 20px;color:#475569;">Hello {display_name},</p>
    <p style="margin:0 0 16px;">Your EventTab <strong>{role_label}</strong> account has been created.</p>
    <div style="background:#eff6ff;border:2px solid #2563eb;border-radius:10px;padding:18px;text-align:center;margin:20px 0;">
      <div style="font-size:12px;letter-spacing:0.08em;text-transform:uppercase;color:#1d4ed8;font-weight:700;">Access Code</div>
      <div style="font-size:32px;font-weight:800;letter-spacing:0.12em;color:#0b3a6e;margin-top:8px;">{access_code}</div>
    </div>
    <p style="margin:0;color:#475569;">Use this access code to sign in to the EventTab app.</p>
    <p style="margin:24px 0 0;color:#94a3b8;font-size:13px;">— EventTab Admin</p>
  </div>
</body></html>'''
    else:
        subject = f'Your EventTab {role_label} Account'
        text_body = (
            f'Hello {display_name},\n\n'
            f'Your EventTab {role_label} account has been created.\n\n'
            f'Username: {username}\n'
            f'Password: {password}\n\n'
            f'Sign in at the EventTab web portal.\n\n'
            f'— EventTab Admin'
        )
        html_body = None

    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=from_email,
            to=[to_email],
        )
        if html_body:
            message.attach_alternative(html_body, 'text/html')
        sent = message.send(fail_silently=False)
        return bool(sent)
    except Exception:
        logger.exception('Failed to send assignment credentials email to %s', to_email)
        return False


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def next_assignment_access_code(request):
    """Preview the next sequential access code for Judge (JDG##) or Scorer (SCR##)."""
    role_raw = (request.GET.get('role') or '').strip()
    role_label = _normalize_assignment_role(role_raw)
    if role_label not in CODE_ROLES:
        return JsonResponse({'success': False, 'message': 'Role must be Judge or Scorer.'}, status=400)

    exclude_id = request.GET.get('exclude')
    exclude_user_id = None
    if exclude_id and str(exclude_id).isdigit():
        exclude_user_id = int(exclude_id)

    code = _allocate_access_code(role_label, exclude_user_id=exclude_user_id)
    return JsonResponse({'success': True, 'access_code': code, 'role': role_label})


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def create_assignment_account(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    payload = _assignment_payload(request)
    role_label = _normalize_assignment_role(payload['role'])
    if role_label is None:
        return JsonResponse({'success': False, 'message': 'Select Tabulator, Judge, or Scorer as the account role.'}, status=400)

    User = get_user_model()

    # ── Judge / Scorer: name + access-code flow ──────────────────────────────
    if role_label in CODE_ROLES:
        full_name = payload['full_name']
        if not full_name:
            return JsonResponse({'success': False, 'message': 'Name is required.'}, status=400)
        email_ok, email_or_err = _normalize_gmail(payload['email'])
        if not email_ok:
            return JsonResponse({'success': False, 'message': email_or_err}, status=400)
        email = email_or_err
        if User.objects.filter(email__iexact=email).exists():
            return JsonResponse({'success': False, 'message': 'Gmail is already in use.'}, status=400)
        # Always allocate sequential role codes (JDG01 / SCR01…) server-side
        access_code = _allocate_access_code(role_label)
        if not access_code:
            return JsonResponse({'success': False, 'message': 'Could not allocate an access code for this role.'}, status=500)
        try:
            auto_username = _generate_username_from_name(full_name)
            first_name, last_name = _split_full_name(full_name)
            user = User.objects.create_user(username=auto_username, email=email, password=access_code)
            user.first_name = first_name
            user.last_name = last_name
            _apply_assignment_role(user, role_label, payload['is_active'])
            user.save()
            _save_assignment_access_code(user, access_code)
            if role_label == 'Judge':
                sync_judge_to_mobile_events(user)
            LogEntry.objects.create(
                user=request.user,
                content_type_id=ContentType.objects.get_for_model(User).id,
                object_id=user.id,
                object_repr=str(user),
                action_flag=ADDITION,
                change_message=f'Created {role_label} account (access-code flow)',
            )
            email_sent = _send_assignment_credentials_email(
                to_email=email,
                full_name=full_name,
                role_label=role_label,
                access_code=access_code,
            )
            message = f'{role_label} account for {full_name} created.'
            if email_sent:
                message += f' Access code sent to {email}.'
            elif not _email_smtp_configured():
                message += (
                    ' Account saved, but email was NOT sent — add EMAIL_HOST_USER and '
                    'EMAIL_HOST_PASSWORD (Gmail App Password) to .env, then recreate or regenerate the code.'
                )
            else:
                message += f' Account saved, but email to {email} could not be sent. Check SMTP settings.'
            return JsonResponse({
                'success': True,
                'message': message,
                'name': full_name,
                'access_code': access_code,
                'email_sent': email_sent,
                'email_configured': _email_smtp_configured(),
            })
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)}, status=500)

    # ── Tabulator: username / password flow ─────────────────────────────────
    if not payload['username'] or not payload['password']:
        return JsonResponse({'success': False, 'message': 'Username and password are required.'}, status=400)

    email_ok, email_or_err = _normalize_gmail(payload['email'])
    if not email_ok:
        return JsonResponse({
            'success': False,
            'message': email_or_err if email_or_err != 'Gmail is required.'
            else 'Gmail is required so faculty can be notified when assigned to an event.',
        }, status=400)
    email = email_or_err

    if User.objects.filter(username__iexact=payload['username']).exists():
        return JsonResponse({'success': False, 'message': 'Username already exists.'}, status=400)
    if User.objects.filter(email__iexact=email).exists():
        return JsonResponse({'success': False, 'message': 'Gmail is already in use.'}, status=400)

    try:
        if payload['full_name']:
            first_name, last_name = _split_full_name(payload['full_name'])
        else:
            first_name, last_name = payload['username'], ''
        user = User.objects.create_user(
            username=payload['username'],
            email=email,
            password=payload['password'],
            first_name=first_name,
            last_name=last_name,
        )
        _apply_assignment_role(user, role_label, payload['is_active'])
        user.save()
        if role_label == 'Judge':
            sync_judge_to_mobile_events(user)

        LogEntry.objects.create(
            user=request.user,
            content_type_id=ContentType.objects.get_for_model(User).id,
            object_id=user.id,
            object_repr=str(user),
            action_flag=ADDITION,
            change_message=f'Created {role_label} account',
        )
        return JsonResponse({
            'success': True,
            'message': (
                f'{role_label} account for {payload["username"]} created. '
                f'Assignment notifications will be sent to {email}.'
            ),
        })
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def update_assignment_account(request, account_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    account_user = _assignment_user_or_none(account_id)
    if account_user is None:
        return JsonResponse({'success': False, 'message': 'Account not found.'}, status=404)

    payload = _assignment_payload(request)
    role_label = _normalize_assignment_role(payload['role'])
    if role_label is None:
        return JsonResponse({'success': False, 'message': 'Select Tabulator, Judge, or Scorer as the account role.'}, status=400)

    User = get_user_model()

    # ── Judge / Scorer: name + optional new access-code ──────────────────────
    if role_label in CODE_ROLES:
        full_name = payload['full_name']
        if not full_name:
            return JsonResponse({'success': False, 'message': 'Name is required.'}, status=400)
        email_ok, email_or_err = _normalize_gmail(payload['email'])
        if not email_ok:
            return JsonResponse({'success': False, 'message': email_or_err}, status=400)
        email = email_or_err
        if User.objects.filter(email__iexact=email).exclude(id=account_user.id).exists():
            return JsonResponse({'success': False, 'message': 'Gmail is already in use.'}, status=400)
        try:
            first_name, last_name = _split_full_name(full_name)
            account_user.first_name = first_name
            account_user.last_name = last_name
            account_user.email = email
            new_code = ''
            if payload.get('regenerate_access_code'):
                new_code = _allocate_access_code(role_label, exclude_user_id=account_user.id)
                if not new_code:
                    return JsonResponse({'success': False, 'message': 'Could not allocate a new access code.'}, status=500)
                account_user.set_password(new_code)
                _save_assignment_access_code(account_user, new_code)
            _apply_assignment_role(account_user, role_label, payload['is_active'])
            account_user.save()
            if role_label == 'Judge':
                sync_judge_to_mobile_events(account_user)
            LogEntry.objects.create(
                user=request.user,
                content_type_id=ContentType.objects.get_for_model(User).id,
                object_id=account_user.id,
                object_repr=str(account_user),
                action_flag=CHANGE,
                change_message=f'Updated {role_label} account (access-code flow)',
            )
            email_sent = False
            if new_code:
                email_sent = _send_assignment_credentials_email(
                    to_email=email,
                    full_name=full_name,
                    role_label=role_label,
                    access_code=new_code,
                )
            resp = {'success': True, 'message': f'Account for {full_name} updated.', 'email_sent': email_sent}
            if new_code:
                resp['access_code'] = new_code
                if email_sent:
                    resp['message'] += f' New access code sent to {email}.'
                else:
                    resp['message'] += f' New code saved, but email to {email} could not be sent.'
            return JsonResponse(resp)
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)}, status=500)

    # ── Tabulator: username / password flow ─────────────────────────────────
    if not payload['username']:
        return JsonResponse({'success': False, 'message': 'Username is required.'}, status=400)

    email_ok, email_or_err = _normalize_gmail(payload['email'])
    if not email_ok:
        return JsonResponse({
            'success': False,
            'message': email_or_err if email_or_err != 'Gmail is required.'
            else 'Gmail is required so faculty can be notified when assigned to an event.',
        }, status=400)
    email = email_or_err

    if User.objects.filter(username__iexact=payload['username']).exclude(id=account_user.id).exists():
        return JsonResponse({'success': False, 'message': 'Username already exists.'}, status=400)
    if User.objects.filter(email__iexact=email).exclude(id=account_user.id).exists():
        return JsonResponse({'success': False, 'message': 'Gmail is already in use.'}, status=400)

    try:
        account_user.username = payload['username']
        account_user.email = email
        if payload['full_name']:
            first_name, last_name = _split_full_name(payload['full_name'])
            account_user.first_name = first_name
            account_user.last_name = last_name
        if payload['password']:
            account_user.set_password(payload['password'])
        _apply_assignment_role(account_user, role_label, payload['is_active'])
        account_user.save()
        if role_label == 'Judge':
            sync_judge_to_mobile_events(account_user)

        LogEntry.objects.create(
            user=request.user,
            content_type_id=ContentType.objects.get_for_model(User).id,
            object_id=account_user.id,
            object_repr=str(account_user),
            action_flag=CHANGE,
            change_message=f'Updated {role_label} account',
        )
        return JsonResponse({
            'success': True,
            'message': f'Account for {payload["username"]} updated. Notifications go to {email}.',
        })
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def delete_assignment_account(request, account_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    account_user = _assignment_user_or_none(account_id)
    if account_user is None:
        return JsonResponse({'success': False, 'message': 'Account not found.'}, status=404)

    username = account_user.username
    try:
        User = get_user_model()
        LogEntry.objects.create(
            user=request.user,
            content_type_id=ContentType.objects.get_for_model(User).id,
            object_id=account_user.id,
            object_repr=str(account_user),
            action_flag=DELETION,
            change_message='Deleted assignment account',
        )
        account_user.delete()
        return JsonResponse({'success': True, 'message': f'Account {username} deleted.'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def bulk_delete_assignment_accounts(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    payload = _parse_json_request(request)
    ids = payload.get('ids') or []
    if not isinstance(ids, list) or not ids:
        return JsonResponse({'success': False, 'message': 'No account IDs provided.'}, status=400)

    User = get_user_model()
    deleted = 0
    skipped = 0
    ct_id = ContentType.objects.get_for_model(User).id

    for raw_id in ids:
        try:
            uid = int(raw_id)
        except (TypeError, ValueError):
            skipped += 1
            continue

        account_user = _assignment_user_or_none(uid)
        if account_user is None:
            skipped += 1
            continue

        try:
            LogEntry.objects.create(
                user=request.user,
                content_type_id=ct_id,
                object_id=account_user.id,
                object_repr=str(account_user),
                action_flag=DELETION,
                change_message='Bulk deleted assignment account',
            )
            account_user.delete()
            deleted += 1
        except Exception:
            skipped += 1

    if deleted == 0:
        return JsonResponse({'success': False, 'message': 'No accounts were deleted.'}, status=400)

    msg = f'{deleted} account{"s" if deleted != 1 else ""} deleted.'
    if skipped:
        msg += f' {skipped} skipped.'
    return JsonResponse({'success': True, 'message': msg, 'deleted': deleted})


def get_session_events(request):
    """
    Kept for backward compatibility — returns an empty list.
    Events are now stored in the database via the Event model.
    """
    return []





@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_manage_events(request):
    if request.method == 'POST' and request.POST.get('form_action') == 'create_event':
        event_name = request.POST.get('event_name', '').strip()
        category   = request.POST.get('category', '').strip()
        event_date = request.POST.get('event_date', '').strip()
        venue      = request.POST.get('venue', '').strip()

        if not event_name or not category or not event_date or not venue:
            messages.error(request, 'Event name, category, date, and venue are required.')
            return redirect('admin_manage_events')

        def _int_or_none(val):
            try:
                return int(val) if val else None
            except (ValueError, TypeError):
                return None

        event_time_str = request.POST.get('event_time', '').strip()
        event_time_val = None
        if event_time_str:
            try:
                from datetime import time as dt_time
                parts = event_time_str.split(':')
                event_time_val = dt_time(int(parts[0]), int(parts[1]))
            except (ValueError, IndexError):
                event_time_val = None

        scoring_method    = request.POST.get('scoring_method', '').strip()
        points = _parse_event_points(request.POST)
        event = Event.objects.create(
            name              = event_name[:200],
            category          = category,
            division          = request.POST.get('division', '').strip(),
            department        = '',
            event_date        = event_date,
            event_time        = event_time_val,
            venue             = venue,
            image             = request.FILES.get('event_image'),
            status            = normalize_event_availability_status(request.POST.get('status', 'active')),
            max_participants  = _int_or_none(request.POST.get('max_participants', '')),
            num_teams         = _int_or_none(request.POST.get('num_teams', '')),
            scoring_method    = scoring_method,
            tournament_type   = request.POST.get('tournament_type', '').strip(),
            faculty_in_charge = request.POST.get('faculty_in_charge', '').strip(),
            student_in_charge = request.POST.get('student_in_charge', '').strip(),
            mechanics         = request.POST.get('mechanics', '').strip(),
            scoring_criteria  = request.POST.get('scoring_criteria', '').strip(),
            champion_points   = points['champion_points'],
            first_runner_points = points['first_runner_points'],
            second_runner_points = points['second_runner_points'],
            participation_points = points['participation_points'],
            created_by        = request.user,
        )
        # Save assigned scorers (match) or judges (criteria)
        assignee_ids = request.POST.getlist('assigned_judges')
        assignees = _resolve_event_assignees(assignee_ids, scoring_method)
        event.assigned_judges.set(assignees)
        tabulator_ids = [
            int(x) for x in request.POST.getlist('assigned_tabulators') if str(x).isdigit()
        ]
        TabUser = get_user_model()
        event.assigned_tabulators.set(
            TabUser.objects.filter(id__in=tabulator_ids, groups__name__iexact='Tabulator').distinct()
        )
        # Create teams (match-based) if provided
        teams_json = request.POST.get('teams_data', '').strip()
        if teams_json:
            try:
                teams_list = json.loads(teams_json)
                from events.models import BracketTeam
                for i, t in enumerate(teams_list):
                    registry_id = t.get('registry_id')
                    team_name = (t.get('name') or '').strip()
                    dept_obj = None
                    members_str = (t.get('members') or '').strip()
                    if registry_id:
                        reg = Team.objects.filter(
                            id=registry_id,
                            status=Team.STATUS_ACTIVE,
                        ).select_related('department').first()
                        if reg:
                            team_name = reg.name
                            dept_obj = reg.department
                            members_str, _ = parse_team_members(reg.members)
                    if not team_name:
                        continue
                    if dept_obj is None:
                        dept_obj = resolve_department(t.get('department'))
                    if not members_str:
                        members_str, _ = parse_team_members(t.get('members'))
                    BracketTeam.objects.create(
                        event=event,
                        name=team_name,
                        department=dept_obj,
                        members=members_str,
                        seed=i + 1,
                    )
            except (json.JSONDecodeError, TypeError):
                pass

        scoring_method = request.POST.get('scoring_method', '').strip()
        candidates_json = request.POST.get('candidates_data', '').strip()
        explicit_candidates = (
            _parse_event_candidates_json(candidates_json)
            if scoring_method == 'criteria' else None
        )
        sync_event_to_mobile(event, explicit_candidates=explicit_candidates)
        messages.success(request, f'Event "{event_name}" created and published to the judge mobile app.')
        return redirect('admin_manage_events')

    status_filter   = request.GET.get('status', 'all').strip().lower()
    if status_filter == Event.STATUS_ACTIVE:
        status_filter = 'ongoing'
    category_filter = request.GET.get('category', 'all').strip().lower()
    schedule_filter = request.GET.get('schedule', '').strip().lower()
    search_query    = request.GET.get('q', '').strip()

    event_qs = Event.objects.all().prefetch_related('assigned_judges', 'assigned_tabulators')

    if search_query:
        event_qs = event_qs.filter(
            Q(name__icontains=search_query)
            | Q(category__icontains=search_query)
            | Q(venue__icontains=search_query)
        )
    if category_filter != 'all':
        event_qs = event_qs.filter(category__iexact=category_filter)
    if schedule_filter:
        event_qs = event_qs.filter(event_date__icontains=schedule_filter)

    def _derive_event_type(category):
        """Map a category to a scoring type used by the Manage Events tabs."""
        c = (category or '').strip().lower()
        if c in ('sports', 'esports', 'e-sports'):
            return 'match', 'Match Result Based'
        return 'criteria', 'Criteria Based'

    def _event_icon(category):
        c = (category or '').strip().lower()
        if c == 'sports':
            return '🏅'
        if c in ('esports', 'e-sports'):
            return '🎮'
        if c in ('socio-cultural', 'socio cultural', 'sociocultural'):
            return '🎭'
        if c == 'academic':
            return '🎓'
        return '📌'

    today = timezone.localdate()
    event_rows = []
    for ev in event_qs:
        status_meta = get_event_status_meta(ev, today)
        if status_filter != 'all' and status_meta['status'] != status_filter:
            continue

        type_key, type_label = _derive_event_type(ev.category)
        event_rows.append({
            'id':               ev.id,
            'name':             ev.name,
            'category':         ev.category,
            'division':         ev.division,
            'event_type_key':   type_key,
            'event_type_label': type_label,
            'event_icon':       _event_icon(ev.category),
            'schedule_label':   ev.schedule_label,
            'event_date':       ev.event_date.strftime('%Y-%m-%d') if ev.event_date else '',
            'event_time':       ev.event_time_str,
            'venue':            ev.venue,
            'image_url':        ev.image.url if ev.image else '',
            'status':           status_meta['status'],
            'status_label':     status_meta['status_label'],
            'availability_status': status_meta['availability_status'],
            'max_participants': str(ev.max_participants) if ev.max_participants else '',
            'num_teams':        str(ev.num_teams) if ev.num_teams else '',
            'faculty_in_charge': ev.faculty_in_charge,
            'student_in_charge': ev.student_in_charge,
            'mechanics':        ev.mechanics,
            'scoring_criteria': ev.scoring_criteria,
            'scoring_method':   ev.scoring_method or type_key,
            'tournament_type':  ev.tournament_type or '',
            'champion_points':  ev.champion_points if ev.champion_points is not None else '',
            'first_runner_points': ev.first_runner_points if ev.first_runner_points is not None else '',
            'second_runner_points': ev.second_runner_points if ev.second_runner_points is not None else '',
            'participation_points': ev.participation_points if ev.participation_points is not None else '',
            'assigned_judge_ids': list(ev.assigned_judges.values_list('id', flat=True)),
            'assigned_judges_display': ', '.join(
                u.get_full_name() or u.username
                for u in ev.assigned_judges.all()
            ),
            'assigned_tabulator_ids': list(ev.assigned_tabulators.values_list('id', flat=True)),
            'assigned_tabulators_display': ', '.join(
                u.get_full_name() or u.username
                for u in ev.assigned_tabulators.all()
            ),
            'teams_list': serialize_event_teams(ev),
            'candidates_list': serialize_event_candidates(ev),
        })
        if len(event_rows) >= 30:
            break

    all_events = list(Event.objects.all())
    status_counts = get_event_status_counts(all_events, today)
    active_count    = status_counts['ongoing']
    upcoming_count  = status_counts['upcoming']
    completed_count = status_counts['completed']
    inactive_count  = status_counts['inactive']
    total_events_count = len(all_events)
    category_names  = sorted({ev.category for ev in all_events if ev.category}) or ['Sports', 'Esports', 'Academic']

    create_category_options = sorted(set(category_names) | {'Academic', 'Sports', 'Esports', 'Socio-cultural'})
    create_division_options = ['Men', 'Women', 'Mixed']

    # All judge accounts for the assign-judges multi-select
    User = get_user_model()
    judge_accounts = list(
        User.objects.filter(
            Q(groups__name__iexact='Judge') | Q(groups__name__iexact='Judges')
        ).distinct().order_by('first_name', 'last_name', 'username')
    )
    judge_options = [
        {'id': u.id, 'label': u.get_full_name().strip() or u.username, 'username': u.username}
        for u in judge_accounts
    ]

    scorer_accounts = list(
        User.objects.filter(groups__name__iexact='Scorer').distinct().order_by(
            'first_name', 'last_name', 'username'
        )
    )
    scorer_options = [
        {'id': u.id, 'label': u.get_full_name().strip() or u.username, 'username': u.username}
        for u in scorer_accounts
    ]

    tabulator_accounts = list(
        User.objects.filter(groups__name__iexact='Tabulator').distinct().order_by(
            'first_name', 'last_name', 'username'
        )
    )
    tabulator_options = [
        {'id': u.id, 'label': u.get_full_name().strip() or u.username, 'username': u.username}
        for u in tabulator_accounts
    ]

    department_names = list(Department.objects.values_list('name', flat=True).order_by('name'))

    return render(request, 'admindash/event.html', {
        'event_rows':             event_rows,
        'active_count':           active_count,
        'upcoming_count':         upcoming_count,
        'completed_count':        completed_count,
        'inactive_count':         inactive_count,
        'total_events_count':     total_events_count,
        'category_names':         category_names,
        'create_category_options': create_category_options,
        'create_division_options': create_division_options,
        'judge_options':          judge_options,
        'scorer_options':         scorer_options,
        'tabulator_options':      tabulator_options,
        'department_options_json': json.dumps(department_names),
        'registry_teams_json': json.dumps(serialize_registry_teams()),
        'registry_candidates_json': json.dumps(serialize_registry_candidates()),
        'selected_category':      category_filter,
        'selected_status':        status_filter,
        'search_query':           search_query,
        'schedule_filter':        schedule_filter,
        'tab_data': {
            'portion':     _serialize_tab_records(Portion),
            'candidate':   _serialize_tab_records(CandidateNumber),
            'contestant':  _serialize_tab_records(Contestant),
            'judges':      _serialize_tab_records(JudgeAssignment),
            'criteria':    _serialize_tab_records(ScoringCriterion),
            'chairperson': _serialize_tab_records(Chairperson),
        },
        'tab_labels': [
            ('portion',     'Portion',      'Portions'),
            ('candidate',   'Candidate',    'Candidates'),
            ('contestant',  'Contestant',   'Contestants'),
            ('judges',      'Judge',        'Judges'),
            ('criteria',    'Criterion',    'Criteria'),
            ('chairperson', 'Chairperson',  'Chairpersons'),
        ],
    })


@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_edit_event(request, event_id):
    """Update an existing Event in the database."""
    if request.method != 'POST':
        return redirect('admin_manage_events')

    ev = get_object_or_404(Event, id=event_id)

    event_name = request.POST.get('event_name', '').strip()
    category   = request.POST.get('category', '').strip()
    event_date = request.POST.get('event_date', '').strip()
    venue      = request.POST.get('venue', '').strip()

    if not event_name or not category or not event_date or not venue:
        messages.error(request, 'Event name, category, date, and venue are required.')
        return redirect('admin_manage_events')

    def _int_or_none(val):
        try:
            return int(val) if val else None
        except (ValueError, TypeError):
            return None

    event_time_str = request.POST.get('event_time', '').strip()
    event_time_val = ev.event_time
    if event_time_str:
        try:
            from datetime import time as dt_time
            parts = event_time_str.split(':')
            event_time_val = dt_time(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            event_time_val = None

    ev.name              = event_name[:200]
    ev.category          = category
    ev.division          = request.POST.get('division', '').strip()
    ev.department        = ''
    ev.event_date        = event_date
    ev.event_time        = event_time_val
    ev.venue             = venue
    if 'event_image' in request.FILES:
        ev.image = request.FILES.get('event_image')
    ev.status            = normalize_event_availability_status(request.POST.get('status', 'active'))
    ev.max_participants  = _int_or_none(request.POST.get('max_participants', ''))
    ev.num_teams         = _int_or_none(request.POST.get('num_teams', ''))
    if request.POST.get('scoring_method', '').strip():
        ev.scoring_method = request.POST.get('scoring_method', '').strip()
    if request.POST.get('tournament_type', '').strip():
        ev.tournament_type = request.POST.get('tournament_type', '').strip()
    ev.faculty_in_charge = request.POST.get('faculty_in_charge', '').strip()
    ev.student_in_charge = request.POST.get('student_in_charge', '').strip()
    ev.mechanics         = request.POST.get('mechanics', '').strip()
    ev.scoring_criteria  = request.POST.get('scoring_criteria', '').strip()
    points = _parse_event_points(request.POST)
    ev.champion_points = points['champion_points']
    ev.first_runner_points = points['first_runner_points']
    ev.second_runner_points = points['second_runner_points']
    ev.participation_points = points['participation_points']
    ev.save()
    scoring_method = request.POST.get('scoring_method', '').strip() or ev.scoring_method
    # Save assigned scorers (match) or judges (criteria)
    assignee_ids = request.POST.getlist('assigned_judges')
    assignees = _resolve_event_assignees(assignee_ids, scoring_method)
    ev.assigned_judges.set(assignees)
    tabulator_ids = [
        int(x) for x in request.POST.getlist('assigned_tabulators') if str(x).isdigit()
    ]
    TabUser = get_user_model()
    ev.assigned_tabulators.set(
        TabUser.objects.filter(id__in=tabulator_ids, groups__name__iexact='Tabulator').distinct()
    )

    teams_json = request.POST.get('teams_data', '').strip()
    if teams_json:
        try:
            teams_list = json.loads(teams_json)
            from events.models import BracketTeam
            BracketTeam.objects.filter(event=ev).delete()
            for i, t in enumerate(teams_list):
                registry_id = t.get('registry_id')
                team_name = (t.get('name') or '').strip()
                dept_obj = None
                members_str = (t.get('members') or '').strip()
                if registry_id:
                    reg = Team.objects.filter(
                        id=registry_id,
                        status=Team.STATUS_ACTIVE,
                    ).select_related('department').first()
                    if reg:
                        team_name = reg.name
                        dept_obj = reg.department
                        members_str, _ = parse_team_members(reg.members)
                if not team_name:
                    continue
                if dept_obj is None:
                    dept_obj = resolve_department(t.get('department'))
                if not members_str:
                    members_str, _ = parse_team_members(t.get('members'))
                BracketTeam.objects.create(
                    event=ev,
                    name=team_name,
                    department=dept_obj,
                    members=members_str,
                    seed=i + 1,
                )
        except (json.JSONDecodeError, TypeError):
            pass

    scoring_method = request.POST.get('scoring_method', '').strip() or ev.scoring_method
    candidates_json = request.POST.get('candidates_data', '').strip()
    explicit_candidates = (
        _parse_event_candidates_json(candidates_json)
        if scoring_method == 'criteria' else None
    )
    sync_event_to_mobile(ev, explicit_candidates=explicit_candidates)

    messages.success(request, f'Event "{event_name}" updated and synced to the judge mobile app.')
    return redirect('admin_manage_events')


@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_delete_event(request, event_id):
    """Delete an Event from the database."""
    if request.method != 'POST':
        return redirect('admin_manage_events')

    ev = get_object_or_404(Event, id=event_id)
    name = ev.name
    delete_mobile_for_event(ev)
    ev.delete()
    messages.success(request, f'Event "{name}" deleted successfully.')
    return redirect('admin_manage_events')


@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_bulk_delete_events(request):
    """Delete multiple events at once. Body: {ids: [1,2,3]}"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'success': False, 'message': 'Invalid JSON.'}, status=400)

    ids = payload.get('ids') or []
    if not isinstance(ids, list) or not ids:
        return JsonResponse({'success': False, 'message': 'No event IDs provided.'}, status=400)

    deleted_count = 0
    for raw_id in ids:
        try:
            event_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        try:
            ev = Event.objects.get(id=event_id)
            delete_mobile_for_event(ev)
            ev.delete()
            deleted_count += 1
        except Event.DoesNotExist:
            continue

    return JsonResponse({
        'success': True,
        'message': f'{deleted_count} event(s) deleted successfully.',
        'deleted_count': deleted_count,
    })


def _match_event_queryset():
    """Include explicit match events and legacy sports events."""
    return Event.objects.filter(
        Q(scoring_method='match') | Q(category__iexact='Sports')
    ).distinct()


def _bracket_team_logo_url(bracket_team):
    if not bracket_team:
        return ''
    source = getattr(bracket_team, 'source_team', None)
    image = getattr(source, 'image', None) if source else None
    if not image:
        return ''
    try:
        return image.url
    except ValueError:
        return ''


def _serialize_match_event(event):
    from events.match_event_service import (
        heal_unconfirmed_match_labels,
        normalize_tournament_type,
    )
    from events.faculty_service import resolve_faculty_user_from_text

    heal_unconfirmed_match_labels(event)
    registry_ids = {
        name: pk for pk, name in Team.objects.filter(status=Team.STATUS_ACTIVE)
        .values_list('id', 'name')
    }
    matches = list(
        event.bracket_matches.select_related(
            'team_a',
            'team_b',
            'team_a__source_team',
            'team_b__source_team',
            'winner',
            'winner__source_team',
            'next_match_winner',
            'next_match_loser',
        ).order_by('match_number', 'id')
    )
    normalized_tournament_type = normalize_tournament_type(
        event.tournament_type or 'single_elimination'
    )
    has_losers_bracket = any(
        'loser' in (match.round_name or '').lower() for match in matches
    )
    has_grand_final = any(
        'grand' in (match.round_name or '').lower() for match in matches
    )
    has_loser_links = any(match.next_match_loser_id for match in matches)
    legacy_double_elimination = (
        normalized_tournament_type == 'double_elimination'
        and matches
        and not (has_losers_bracket and has_grand_final and has_loser_links)
    )
    faculty_id = event.faculty_account_id
    faculty_name = (
        event.faculty_account.get_full_name().strip() or event.faculty_account.username
        if event.faculty_account else (event.faculty_in_charge or '')
    )
    if not faculty_id and event.faculty_in_charge:
        resolved = resolve_faculty_user_from_text(event.faculty_in_charge)
        if resolved:
            faculty_id = resolved.id
            faculty_name = resolved.get_full_name().strip() or resolved.username
            Event.objects.filter(pk=event.pk, faculty_account__isnull=True).update(
                faculty_account_id=resolved.id
            )
    return {
        'id': event.id,
        'name': event.name,
        'academic_year': event.academic_year or '',
        'category': event.category or 'Sports',
        'classification': event.event_classification,
        'classification_label': event.get_event_classification_display() or '—',
        'division': event.division,
        'venue': event.venue,
        'start_date': event.event_date.isoformat() if event.event_date else '',
        'end_date': (event.end_date or event.event_date).isoformat() if event.event_date else '',
        'publication_status': event.publication_status,
        'publication_label': event.get_publication_status_display(),
        'tournament_type': normalized_tournament_type,
        'tournament_type_label': _tournament_type_label(event.tournament_type or 'single_elimination'),
        'include_third_place': event.include_third_place,
        'sport_type': event.sport_type or '',
        'sport_custom_name': event.sport_custom_name or '',
        'pairing_method': event.pairing_method or event.seeding_method or 'random_draw',
        'result_entry_format': event.result_entry_format or '',
        'tie_break_rules': event.tie_break_rules or [],
        'schedule_config': event.schedule_config or {},
        'assigned_scorer_id': event.assigned_scorer_id,
        'schedule_mode': event.schedule_mode,
        'daily_start_time': event.daily_start_time.strftime('%H:%M') if event.daily_start_time else '',
        'daily_end_time': event.daily_end_time.strftime('%H:%M') if event.daily_end_time else '',
        'faculty_account_id': faculty_id,
        'faculty_name': faculty_name,
        'scoresheet_template_id': event.scoresheet_template_id,
        'allow_result_editing': event.allow_result_editing,
        'require_faculty_confirmation': event.require_faculty_confirmation,
        'apply_championship_points': event.apply_championship_points,
        'points_config': event.championship_points_config or [],
        'actual_match_count': sum(1 for match in matches if not match.is_automatic_advance),
        'automatic_advance_count': sum(1 for match in matches if match.is_automatic_advance),
        'team_ids': [
            team.source_team_id or registry_ids.get(team.name)
            for team in event.bracket_teams.all()
            if team.source_team_id or team.name in registry_ids
        ],
        'draw_order': event.bracket_draw_order or [
            team.source_team_id or registry_ids.get(team.name)
            for team in event.bracket_teams.all()
            if team.source_team_id or team.name in registry_ids
        ],
        'legacy_double_elimination': legacy_double_elimination,
        'team_names': list(event.bracket_teams.values_list('name', flat=True)),
        'schedule_rows': [
            {
                'match_number': match.match_number,
                'match_key': match.client_key or f'legacy-{match.pk}',
                'round_name': match.round_name,
                'is_automatic_advance': match.is_automatic_advance,
                'dependency_label': _resolved_schedule_label(match),
                'playing_area': match.playing_area,
                'team_a': _resolved_side_name(match, 'a'),
                'team_b': _resolved_side_name(match, 'b'),
                'participant_a': {
                    'team_id': match.team_a.source_team_id if match.team_a else None,
                    'team_name': match.team_a.name if match.team_a else None,
                    'seed': match.team_a.seed if match.team_a else None,
                    'team_logo': _bracket_team_logo_url(match.team_a),
                } if match.team_a else None,
                'participant_b': {
                    'team_id': match.team_b.source_team_id if match.team_b else None,
                    'team_name': match.team_b.name if match.team_b else None,
                    'seed': match.team_b.seed if match.team_b else None,
                    'team_logo': _bracket_team_logo_url(match.team_b),
                } if match.team_b else None,
                'next_winner_key': (
                    match.next_match_winner.client_key
                    if match.next_match_winner_id and match.next_match_winner.client_key
                    else None
                ),
                'next_loser_key': (
                    match.next_match_loser.client_key
                    if match.next_match_loser_id and match.next_match_loser.client_key
                    else None
                ),
                'status': match.status,
                'score_a': match.score_a or '',
                'score_b': match.score_b or '',
                'winner_name': match.winner.name if match.winner_id else '',
                'winner_team_id': match.winner.source_team_id if match.winner_id else None,
                'date': match.match_date.isoformat() if match.match_date else '',
                'time': match.match_time.strftime('%H:%M') if match.match_time else '',
                'venue': match.venue,
            }
            for match in matches
        ],
    }


def _resolved_side_name(match, side):
    from events.match_event_service import MISSING_TEAM_LABEL, resolve_persisted_match_label
    team = match.team_a if side == 'a' else match.team_b
    if team is not None:
        return team.name or MISSING_TEAM_LABEL
    if match.is_automatic_advance and side == 'b':
        return 'Automatic Advance'
    label = resolve_persisted_match_label(match)
    if ' vs ' in label:
        left, right = label.split(' vs ', 1)
        return left if side == 'a' else right
    if side == 'a':
        return label
    return 'TBD'


def _resolved_schedule_label(match):
    from events.match_event_service import resolve_persisted_match_label
    return resolve_persisted_match_label(match)


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_event_selector(request):
    """Events landing redirects to Match-Based; sidebar holds the type dropdown."""
    return redirect('admin_match_events')


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_match_events(request, event_id=None):
    """List match events and process the create/edit wizard."""
    from events.match_event_service import MatchEventValidationError, save_match_event
    from events.models import BracketTeam

    instance = None
    if event_id is not None:
        instance = get_object_or_404(_match_event_queryset(), pk=event_id)
    if request.method == 'POST':
        try:
            event = save_match_event(request.POST, request.user, instance=instance)
        except MatchEventValidationError as exc:
            messages.error(request, str(exc))
            target = f"{request.path}?edit={event_id}" if event_id else request.path
            return redirect(target)
        messages.success(
            request,
            f'Event "{event.name}" {"created" if instance is None else "updated"} as '
            f'{event.get_publication_status_display()}.',
        )
        return redirect('admin_match_events')

    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'all').strip()
    queryset = (
        _match_event_queryset()
        .select_related('faculty_account')
        .prefetch_related('bracket_teams', 'bracket_matches__team_a', 'bracket_matches__team_b')
        .order_by('-updated_at')
    )
    if search_query:
        queryset = queryset.filter(
            Q(name__icontains=search_query)
            | Q(division__icontains=search_query)
            | Q(venue__icontains=search_query)
        )
    if status_filter in dict(Event.PUBLICATION_CHOICES):
        queryset = queryset.filter(publication_status=status_filter)

    events = list(queryset)
    rows = [_serialize_match_event(event) for event in events]
    User = get_user_model()
    from events.match_event_service import models_q_for_faculty
    faculty_options = User.objects.filter(is_active=True).filter(
        models_q_for_faculty()
    ).distinct().order_by('first_name', 'last_name', 'username')
    from events.models import ScoresheetTemplate
    match_templates = ScoresheetTemplate.objects.filter(
        event_type=ScoresheetTemplate.EVENT_MATCH,
        status=ScoresheetTemplate.STATUS_ACTIVE,
    ).order_by('name')
    teams = Team.objects.filter(status=Team.STATUS_ACTIVE).select_related('department').order_by('name')
    all_match_events = _match_event_queryset()
    from events.match_event_service import current_intramurals_season
    academic_year = current_intramurals_season()
    season_qs = all_match_events
    if academic_year:
        season_qs = season_qs.filter(academic_year=academic_year)
    season_event_names = list(season_qs.values_list('name', flat=True))
    return render(request, 'admindash/matchbasedevent.html', {
        'event_rows': rows,
        'event_rows_json': rows,
        'teams': teams,
        'faculty_options': faculty_options,
        'scoresheet_templates': match_templates,
        'total_count': all_match_events.count(),
        'draft_count': all_match_events.filter(publication_status=Event.PUBLICATION_DRAFT).count(),
        'published_count': all_match_events.filter(publication_status=Event.PUBLICATION_PUBLISHED).count(),
        'search_query': search_query,
        'status_filter': status_filter,
        'edit_event_id': request.GET.get('edit', ''),
        'academic_year': academic_year,
        'season_event_names_json': json.dumps(season_event_names),
    })


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_match_event_preview(request):
    """Issue the authoritative randomized draw used by the final save."""
    from events.match_event_service import (
        MatchEventValidationError,
        build_match_blueprint,
        normalize_tournament_type,
    )

    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST request required.'}, status=405)
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
        team_ids = list(dict.fromkeys(int(value) for value in payload.get('team_ids', [])))
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({'success': False, 'message': 'Participating teams are invalid.'}, status=400)
    teams = list(Team.objects.filter(
        id__in=team_ids,
        status=Team.STATUS_ACTIVE,
    ).values('id', 'name'))
    if len(teams) != len(team_ids):
        return JsonResponse({'success': False, 'message': 'One or more selected teams are unavailable.'}, status=400)
    tournament_type = normalize_tournament_type(payload.get('tournament_type', ''))
    include_third_place = bool(payload.get('include_third_place'))
    draw_order = payload.get('draw_order')
    if draw_order is not None:
        try:
            draw_order = [int(value) for value in draw_order]
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'message': 'Bracket draw is invalid.'}, status=400)
        if set(draw_order) != set(team_ids) or len(draw_order) != len(team_ids):
            return JsonResponse({'success': False, 'message': 'Bracket draw must include every selected team.'}, status=400)
    pairing_method = (payload.get('pairing_method') or 'random_draw').strip()
    if pairing_method not in ('random_draw', 'seeded_draw', 'manual_pairing'):
        pairing_method = 'random_draw'
    # Wizard now supports Random Draw only; ignore legacy pairing values for generation.
    pairing_method = 'random_draw'
    try:
        blueprint = build_match_blueprint(
            team_ids,
            tournament_type,
            include_third_place=include_third_place,
            draw_order=draw_order,
            pairing_method=pairing_method,
        )
    except MatchEventValidationError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)

    team_rows = list(Team.objects.filter(id__in=team_ids, status=Team.STATUS_ACTIVE))
    names = {team.id: team.name for team in team_rows}
    logos = {}
    for team in team_rows:
        if team.image:
            try:
                logos[team.id] = team.image.url
            except ValueError:
                logos[team.id] = ''
    seeds = {team_id: index for index, team_id in enumerate(blueprint['draw_order'], start=1)}

    from events.match_event_service import bracket_display_graph, serialize_blueprint_matches
    serialized = serialize_blueprint_matches(
        blueprint['matches'],
        names,
        logos=logos,
        seeds=seeds,
    )
    layout = bracket_display_graph(serialized, blueprint['draw_order'])
    return JsonResponse({
        'success': True,
        'draw_order': blueprint['draw_order'],
        'actual_match_count': blueprint.get('actual_match_count', len([
            m for m in serialized if not m.get('is_automatic_advance')
        ])),
        'automatic_advance_count': blueprint.get('automatic_advance_count', len([
            m for m in serialized if m.get('is_automatic_advance')
        ])),
        'matches': serialized,
        'layout': layout,
        'tournament_type': tournament_type,
    })


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_match_event_detail(request, event_id):
    event = get_object_or_404(
        _match_event_queryset().select_related('faculty_account').prefetch_related(
            'bracket_teams', 'bracket_matches__team_a', 'bracket_matches__team_b'
        ),
        pk=event_id,
    )
    return JsonResponse(_serialize_match_event(event))


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_delete_match_event(request, event_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST request required.'}, status=405)
    event = get_object_or_404(_match_event_queryset(), pk=event_id)
    name = event.name
    delete_mobile_for_event(event)
    event.delete()
    messages.success(request, f'Event "{name}" deleted.')
    return redirect('admin_match_events')


def _criteria_event_queryset():
    return Event.objects.filter(scoring_method='criteria').exclude(
        Q(category__iexact='Sports')
    ).distinct()


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_criteria_events(request, event_id=None):
    from events.criteria_event_service import (
        CriteriaEventValidationError,
        models_q_for_faculty,
        models_q_for_judges,
        save_criteria_event,
        serialize_criteria_event,
    )

    instance = None
    if event_id is not None:
        instance = get_object_or_404(_criteria_event_queryset(), pk=event_id)
    if request.method == 'POST':
        workflow_action = request.POST.get('workflow_action', '').strip()
        if workflow_action:
            from decimal import Decimal, InvalidOperation
            from events.models import EventScoringCategory, EventScoringCriterion

            def workflow_error(message, status=400):
                return JsonResponse({'success': False, 'message': message}, status=status)

            if workflow_action == 'save_event_draft':
                event_pk = request.POST.get('event_id')
                draft = (
                    _criteria_event_queryset().filter(pk=event_pk).first()
                    if event_pk else Event()
                )
                if event_pk and draft is None:
                    return workflow_error('Event draft was not found.', 404)
                name = request.POST.get('event_name', '').strip()
                classification = request.POST.get('event_classification', '').strip()
                participation = request.POST.get('participation_type', '').strip()
                venue = request.POST.get('venue', '').strip()
                start_date = request.POST.get('start_date', '').strip()
                if not all([name, classification, participation, venue, start_date]):
                    return workflow_error('Complete Event Name, classification, participation type, venue, and date first.')
                try:
                    from datetime import date
                    event_date = date.fromisoformat(start_date)
                except ValueError:
                    return workflow_error('Enter a valid event date.')
                draft.name = name[:200]
                draft.category = request.POST.get('event_category', 'Special Event').strip() or 'Special Event'
                draft.event_classification = classification
                draft.participation_type = participation
                draft.venue = venue[:200]
                draft.event_date = event_date
                draft.end_date = event_date
                draft.event_time = request.POST.get('event_time') or None
                draft.scoring_method = 'criteria'
                draft.publication_status = Event.PUBLICATION_DRAFT
                draft.status = Event.STATUS_UPCOMING
                if not draft.pk:
                    draft.created_by = request.user
                draft.save()
                return JsonResponse({'success': True, 'event': {'id': draft.id, 'name': draft.name}})

            event_pk = request.POST.get('event_id')
            event = _criteria_event_queryset().filter(pk=event_pk).first()
            if event is None:
                return workflow_error('Create the Event details in Step 1 first.', 404)
            if workflow_action == 'list_categories':
                categories = [{
                    'id': category.id, 'name': category.name, 'judge_mode': category.judge_mode,
                    'display_order': category.display_order,
                    'overall_weight_percent': float(category.overall_weight_percent),
                } for category in event.scoring_categories.all()]
                return JsonResponse({'success': True, 'categories': categories})
            if workflow_action == 'create_category':
                name = request.POST.get('category_name', '').strip()
                mode = request.POST.get('judge_mode', '').strip()
                try:
                    order = int(request.POST.get('display_order', ''))
                    weight = Decimal(request.POST.get('overall_weight_percent', ''))
                except (ValueError, InvalidOperation):
                    return workflow_error('Display Order and Overall Category Weighted % must be numeric.')
                if not name or mode not in {'scoring', 'ranking'} or order < 1 or not Decimal('0') < weight <= Decimal('100'):
                    return workflow_error('Enter a unique category name, judge mode, positive order, and a weight from 0 to 100.')
                if event.scoring_categories.filter(name__iexact=name).exists():
                    return workflow_error('A category with this name already exists for the Event.')
                if event.scoring_categories.filter(display_order=order).exists():
                    return workflow_error('This display order is already in use for the Event.')
                category = EventScoringCategory.objects.create(
                    event=event, name=name[:120], judge_mode=mode,
                    display_order=order, overall_weight_percent=weight,
                )
                return JsonResponse({'success': True, 'category': {
                    'id': category.id, 'name': category.name, 'judge_mode': category.judge_mode,
                    'display_order': category.display_order,
                    'overall_weight_percent': float(category.overall_weight_percent),
                }})
            if workflow_action == 'update_category':
                category = event.scoring_categories.filter(pk=request.POST.get('category_id')).first()
                if category is None:
                    return workflow_error('Category was not found for this Event.', 404)
                name = request.POST.get('category_name', '').strip()
                mode = request.POST.get('judge_mode', '').strip()
                try:
                    order = int(request.POST.get('display_order', ''))
                    weight = Decimal(request.POST.get('overall_weight_percent', ''))
                except (ValueError, InvalidOperation):
                    return workflow_error('Display Order and Overall Category Weighted % must be numeric.')
                if not name or mode not in {'scoring', 'ranking'} or order < 1 or not Decimal('0') < weight <= Decimal('100'):
                    return workflow_error('Enter a category name, judge mode, positive order, and a weight from 0 to 100.')
                if event.scoring_categories.exclude(pk=category.pk).filter(name__iexact=name).exists():
                    return workflow_error('A category with this name already exists for the Event.')
                if event.scoring_categories.exclude(pk=category.pk).filter(display_order=order).exists():
                    return workflow_error('This display order is already in use for the Event.')
                category.name, category.judge_mode = name[:120], mode
                category.display_order, category.overall_weight_percent = order, weight
                category.save()
                return JsonResponse({'success': True})
            if workflow_action == 'delete_category':
                category = event.scoring_categories.filter(pk=request.POST.get('category_id')).first()
                if category is None:
                    return workflow_error('Category was not found for this Event.', 404)
                category.delete()
                return JsonResponse({'success': True})
            if workflow_action == 'list_criteria':
                category = event.scoring_categories.filter(pk=request.POST.get('category_id')).first()
                if category is None:
                    return workflow_error('Category was not found for this Event.', 404)
                criteria = [{
                    'id': criterion.id, 'name': criterion.name,
                    'weight_percent': float(criterion.weight_percent),
                    'max_score': float(criterion.max_score) if criterion.max_score is not None else None,
                    'display_order': criterion.display_order,
                } for criterion in category.criteria.all()]
                return JsonResponse({'success': True, 'category': {
                    'id': category.id, 'name': category.name, 'judge_mode': category.judge_mode,
                }, 'criteria': criteria})
            if workflow_action == 'create_criterion':
                category = event.scoring_categories.filter(pk=request.POST.get('category_id')).first()
                if category is None:
                    return workflow_error('Select a category belonging to this Event.', 404)
                name = request.POST.get('criterion_name', '').strip()
                try:
                    weight = Decimal(request.POST.get('weight_percent', ''))
                    order = int(request.POST.get('display_order', ''))
                    max_score = Decimal(request.POST.get('max_score', '')) if category.judge_mode == 'scoring' else None
                except (ValueError, InvalidOperation):
                    return workflow_error('Criterion Weight, Max Score, and Order must be numeric.')
                if not name or order < 1 or not Decimal('0') < weight <= Decimal('100'):
                    return workflow_error('Enter a criterion name, positive order, and a weight from 0 to 100.')
                if category.judge_mode == 'scoring' and (max_score is None or max_score <= 0):
                    return workflow_error('Max Score must be greater than zero for Scoring Mode.')
                if category.criteria.filter(name__iexact=name).exists() or category.criteria.filter(display_order=order).exists():
                    return workflow_error('Criterion name and order must be unique within this category.')
                total = sum((row.weight_percent for row in category.criteria.all()), Decimal('0')) + weight
                if total > Decimal('100'):
                    return workflow_error('Criterion weights cannot exceed 100% within a category.')
                criterion = EventScoringCriterion.objects.create(
                    category=category, name=name[:120], weight_percent=weight,
                    max_score=max_score, display_order=order,
                )
                return JsonResponse({'success': True, 'criterion': {
                    'id': criterion.id, 'name': criterion.name, 'weight_percent': float(criterion.weight_percent),
                    'max_score': float(criterion.max_score) if criterion.max_score is not None else None,
                    'display_order': criterion.display_order,
                }})
            if workflow_action == 'update_criterion':
                category = event.scoring_categories.filter(pk=request.POST.get('category_id')).first()
                criterion = category.criteria.filter(pk=request.POST.get('criterion_id')).first() if category else None
                if criterion is None:
                    return workflow_error('Criterion was not found for this category.', 404)
                name = request.POST.get('criterion_name', '').strip()
                try:
                    weight = Decimal(request.POST.get('weight_percent', ''))
                    order = int(request.POST.get('display_order', ''))
                    max_score = Decimal(request.POST.get('max_score', '')) if category.judge_mode == 'scoring' else None
                except (ValueError, InvalidOperation):
                    return workflow_error('Criterion Weight, Max Score, and Order must be numeric.')
                if not name or order < 1 or not Decimal('0') < weight <= Decimal('100'):
                    return workflow_error('Enter a criterion name, positive order, and a weight from 0 to 100.')
                if category.judge_mode == 'scoring' and (max_score is None or max_score <= 0):
                    return workflow_error('Max Score must be greater than zero for Scoring Mode.')
                if category.criteria.exclude(pk=criterion.pk).filter(name__iexact=name).exists() or category.criteria.exclude(pk=criterion.pk).filter(display_order=order).exists():
                    return workflow_error('Criterion name and order must be unique within this category.')
                total = sum((row.weight_percent for row in category.criteria.exclude(pk=criterion.pk)), Decimal('0')) + weight
                if total > Decimal('100'):
                    return workflow_error('Criterion weights cannot exceed 100% within a category.')
                criterion.name, criterion.weight_percent = name[:120], weight
                criterion.max_score, criterion.display_order = max_score, order
                criterion.save()
                return JsonResponse({'success': True})
            if workflow_action == 'delete_criterion':
                category = event.scoring_categories.filter(pk=request.POST.get('category_id')).first()
                criterion = category.criteria.filter(pk=request.POST.get('criterion_id')).first() if category else None
                if criterion is None:
                    return workflow_error('Criterion was not found for this category.', 404)
                criterion.delete()
                return JsonResponse({'success': True})
            return workflow_error('Unsupported workflow action.')
        try:
            event = save_criteria_event(
                request.POST,
                request.user,
                files=request.FILES,
                instance=instance,
            )
        except CriteriaEventValidationError as exc:
            messages.error(request, str(exc))
            target = f"{request.path}?edit={event_id}" if event_id else f"{request.path}?create=1"
            return redirect(target)
        messages.success(
            request,
            f'Event "{event.name}" {"created" if instance is None else "updated"} as '
            f'{event.get_publication_status_display()}.',
        )
        return redirect('admin_criteria_events')

    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'all').strip()
    category_filter = request.GET.get('category', 'all').strip()
    sort = request.GET.get('sort', '-updated_at').strip()
    allowed_sorts = {
        'name', '-name', 'event_date', '-event_date', 'category', '-category',
        'publication_status', '-publication_status', 'updated_at', '-updated_at',
    }
    if sort not in allowed_sorts:
        sort = '-updated_at'

    queryset = (
        _criteria_event_queryset()
        .select_related('faculty_account', 'chief_judge')
        .prefetch_related('assigned_judges')
        .order_by(sort)
    )
    if search_query:
        queryset = queryset.filter(
            Q(name__icontains=search_query)
            | Q(category__icontains=search_query)
            | Q(division__icontains=search_query)
            | Q(venue__icontains=search_query)
        )
    if status_filter in dict(Event.PUBLICATION_CHOICES):
        queryset = queryset.filter(publication_status=status_filter)
    if category_filter and category_filter != 'all':
        queryset = queryset.filter(category__iexact=category_filter)

    paginator = Paginator(queryset, 10)
    page_obj = paginator.get_page(request.GET.get('page') or 1)
    rows = [serialize_criteria_event(event) for event in page_obj.object_list]
    User = get_user_model()
    judge_options = User.objects.filter(is_active=True).filter(models_q_for_judges()).distinct().order_by(
        'first_name', 'last_name', 'username'
    )
    faculty_options = User.objects.filter(is_active=True).filter(models_q_for_faculty()).distinct().order_by(
        'first_name', 'last_name', 'username'
    )
    from events.models import ScoresheetTemplate
    criteria_templates = ScoresheetTemplate.objects.filter(
        event_type=ScoresheetTemplate.EVENT_CRITERIA,
        status=ScoresheetTemplate.STATUS_ACTIVE,
    ).order_by('name')
    all_events = _criteria_event_queryset()
    return render(request, 'admindash/criteriabasedevent.html', {
        'event_rows': rows,
        'event_rows_json': rows,
        'page_obj': page_obj,
        'teams': Team.objects.filter(status=Team.STATUS_ACTIVE).select_related('department').order_by('name'),
        'candidates': RegistryCandidate.objects.filter(
            status=RegistryCandidate.STATUS_ACTIVE
        ).select_related('department').order_by('number', 'name'),
        'judge_options': judge_options,
        'faculty_options': faculty_options,
        'scoresheet_templates': criteria_templates,
        'total_count': all_events.count(),
        'draft_count': all_events.filter(publication_status=Event.PUBLICATION_DRAFT).count(),
        'published_count': all_events.filter(publication_status=Event.PUBLICATION_PUBLISHED).count(),
        'search_query': search_query,
        'status_filter': status_filter,
        'category_filter': category_filter,
        'sort': sort,
        'edit_event_id': request.GET.get('edit', ''),
        'open_create': request.GET.get('create', '') == '1',
    })


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_criteria_event_detail(request, event_id):
    from events.criteria_event_service import serialize_criteria_event
    event = get_object_or_404(
        _criteria_event_queryset().select_related('faculty_account', 'chief_judge').prefetch_related('assigned_judges'),
        pk=event_id,
    )
    return JsonResponse(serialize_criteria_event(event))


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_delete_criteria_event(request, event_id):
    from events.criteria_event_service import can_delete_criteria_event
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST request required.'}, status=405)
    event = get_object_or_404(_criteria_event_queryset(), pk=event_id)
    allowed, reason = can_delete_criteria_event(event, request.user)
    if not allowed:
        messages.error(request, reason)
        return redirect('admin_criteria_events')
    name = event.name
    delete_mobile_for_event(event)
    event.delete()
    messages.success(request, f'Event "{name}" deleted.')
    return redirect('admin_criteria_events')


# ──────────────────────────────────────────────────────────────────
# Tab record CRUD (Portion, Candidate No., Contestant, Judges,
# Criteria, Chairperson). All share the same simple shape:
# name + date_start + date_end + notes.
# ──────────────────────────────────────────────────────────────────

TAB_MODEL_MAP = {
    'portion': Portion,
    'candidate': CandidateNumber,
    'contestant': Contestant,
    'judges': JudgeAssignment,
    'criteria': ScoringCriterion,
    'chairperson': Chairperson,
}


def _get_tab_model_or_404(tab_key):
    """Return the model class for a tab key, or raise 404."""
    model = TAB_MODEL_MAP.get(tab_key)
    if model is None:
        from django.http import Http404
        raise Http404(f'Unknown tab: {tab_key}')
    return model


def _serialize_tab_records(model):
    """Serialize all records of a tab model for JSON consumption."""
    rows = []
    for rec in model.objects.all():
        rows.append({
            'id': rec.id,
            'name': rec.name,
            'date_start': rec.date_start.strftime('%Y-%m-%d') if rec.date_start else '',
            'date_end': rec.date_end.strftime('%Y-%m-%d') if rec.date_end else '',
            'date_start_label': rec.date_start.strftime('%B %d, %Y') if rec.date_start else '—',
            'date_end_label': rec.date_end.strftime('%B %d, %Y') if rec.date_end else '—',
            'notes': rec.notes or '',
        })
    return rows


def _parse_date_or_none(value):
    """Parse YYYY-MM-DD string to a date, or None."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_tab_create(request, tab_key):
    """Create a new tab record (Portion / Contestant / etc.)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    model = _get_tab_model_or_404(tab_key)
    name = (request.POST.get('name') or '').strip()
    if not name:
        return JsonResponse({'success': False, 'message': 'Name is required.'}, status=400)

    rec = model.objects.create(
        name=name[:200],
        date_start=_parse_date_or_none(request.POST.get('date_start', '')),
        date_end=_parse_date_or_none(request.POST.get('date_end', '')),
        notes=(request.POST.get('notes') or '').strip(),
    )
    return JsonResponse({
        'success': True,
        'message': f'{name} created.',
        'id': rec.id,
        'name': rec.name,
        'date_start': rec.date_start.strftime('%Y-%m-%d') if rec.date_start else '',
        'date_end': rec.date_end.strftime('%Y-%m-%d') if rec.date_end else '',
        'date_start_label': rec.date_start.strftime('%B %d, %Y') if rec.date_start else '—',
        'date_end_label': rec.date_end.strftime('%B %d, %Y') if rec.date_end else '—',
        'notes': rec.notes,
    })


@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_tab_update(request, tab_key, record_id):
    """Update an existing tab record."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    model = _get_tab_model_or_404(tab_key)
    rec = get_object_or_404(model, id=record_id)

    name = (request.POST.get('name') or '').strip()
    if not name:
        return JsonResponse({'success': False, 'message': 'Name is required.'}, status=400)

    rec.name = name[:200]
    rec.date_start = _parse_date_or_none(request.POST.get('date_start', ''))
    rec.date_end = _parse_date_or_none(request.POST.get('date_end', ''))
    rec.notes = (request.POST.get('notes') or '').strip()
    rec.save()

    return JsonResponse({
        'success': True,
        'message': f'{name} updated.',
        'id': rec.id,
        'name': rec.name,
        'date_start': rec.date_start.strftime('%Y-%m-%d') if rec.date_start else '',
        'date_end': rec.date_end.strftime('%Y-%m-%d') if rec.date_end else '',
        'date_start_label': rec.date_start.strftime('%B %d, %Y') if rec.date_start else '—',
        'date_end_label': rec.date_end.strftime('%B %d, %Y') if rec.date_end else '—',
        'notes': rec.notes,
    })


@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_tab_delete(request, tab_key, record_id):
    """Delete a tab record."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    model = _get_tab_model_or_404(tab_key)
    rec = get_object_or_404(model, id=record_id)
    name = rec.name
    rec.delete()
    return JsonResponse({'success': True, 'message': f'{name} deleted.'})


@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_brackets(request):
    """Tournament Brackets listing — Match-Based Events only."""
    listing = build_tournament_bracket_listing()
    return render(request, 'admindash/bracket.html', {
        'event_rows': listing['event_rows'],
        'event_rows_json': listing['event_rows'],
        'summary': listing['summary'],
        'filter_options': listing['filter_options'],
        'match_events_url': '/admin/events/match/',
        'total_events': listing['summary']['match_based_events'],
        'active_count': listing['summary']['generated_brackets'],
        'upcoming_count': listing['summary']['not_generated'],
    })




def get_tabulator_activity_score_rows(limit=60):
    """
    Recent admin-log activity from Tabulator-group users — proxy for “submitted scores”
    until a dedicated ScoreSheet model exists (see SYSTEM_FLOW.md).
    """
    User = get_user_model()
    tabulator_ids = list(
        User.objects.filter(groups__name__iexact='Tabulator')
        .values_list('id', flat=True)
        .distinct()
    )
    if not tabulator_ids:
        return []

    logs = (
        LogEntry.objects.filter(user_id__in=tabulator_ids)
        .select_related('user')
        .order_by('-action_time')[:limit]
    )
    action_labels = {
        ADDITION: 'Recorded',
        CHANGE: 'Updated',
        DELETION: 'Removed',
    }
    rows = []
    now = timezone.now()
    for entry in logs:
        delta = now - entry.action_time
        if delta.days:
            relative_time = f'{delta.days}d ago'
        else:
            minutes = delta.seconds // 60
            relative_time = f'{minutes}m ago' if minutes else 'Just now'

        rows.append({
            'tabulator': entry.user.get_username() if entry.user else '—',
            'item': (entry.object_repr or '—')[:56],
            'action': action_labels.get(entry.action_flag, 'Activity'),
            'detail': ((entry.change_message or '').strip() or '—')[:140],
            'relative_time': relative_time,
        })
    return rows


def _ensure_default_scoresheet_templates(user=None):
    """One-time helper for empty catalogs. Not called from the listing page so
    intentional deletes are never undone by re-seeding."""
    from events.models import ScoresheetTemplate
    from events.scoresheet_pdf import pack_template_layout

    if ScoresheetTemplate.objects.exists():
        return
    seeds = [
        {
            'name': 'Basketball Official Scoresheet',
            'event_type': ScoresheetTemplate.EVENT_MATCH,
            'category': 'Basketball',
            'description': 'Official match scoresheet for basketball games.',
        },
        {
            'name': 'Volleyball Official Scoresheet',
            'event_type': ScoresheetTemplate.EVENT_MATCH,
            'category': 'Volleyball',
            'description': 'Official match scoresheet for volleyball games.',
        },
        {
            'name': 'Singing Competition Scoresheet',
            'event_type': ScoresheetTemplate.EVENT_CRITERIA,
            'category': 'Singing',
            'description': 'Judging scoresheet for singing competitions.',
        },
        {
            'name': 'Dance Competition Scoresheet',
            'event_type': ScoresheetTemplate.EVENT_CRITERIA,
            'category': 'Dance',
            'description': 'Judging scoresheet for dance competitions.',
        },
        {
            'name': 'Pageant Scoresheet',
            'event_type': ScoresheetTemplate.EVENT_CRITERIA,
            'category': 'Pageant',
            'description': 'Judging scoresheet for pageant events.',
        },
    ]
    for seed in seeds:
        ScoresheetTemplate.objects.create(
            name=seed['name'],
            event_type=seed['event_type'],
            category=seed['category'],
            description=seed.get('description', ''),
            layout=pack_template_layout({}, seed['event_type']),
            created_by=user,
        )


def _serialize_scoresheet_template(template):
    from events.scoresheet_pdf import extract_fields, extract_order, resolve_elements

    layout = template.layout or []
    fields = extract_fields(layout, template.event_type)
    order = extract_order(layout, template.event_type)
    return {
        'id': template.id,
        'name': template.name,
        'event_type': template.event_type,
        'event_type_label': template.get_event_type_display(),
        'category': template.category or '—',
        'description': template.description or '',
        'status': template.status,
        'status_label': template.get_status_display(),
        'paper_size': template.paper_size,
        'orientation': template.orientation,
        'fields': fields,
        'order': order,
        'layout': layout,
        'elements': resolve_elements(layout, template.event_type, template.orientation),
        'assigned_event_count': template.assigned_events.count() if hasattr(template, 'assigned_events') else 0,
        'updated_at': timezone.localtime(template.updated_at).strftime('%b %d, %Y') if template.updated_at else '—',
        'updated_at_iso': template.updated_at.isoformat() if template.updated_at else '',
    }


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_scoresheets(request):
    from django.core.paginator import Paginator
    from django.db.models import Q
    from events.models import ScoresheetTemplate
    from events.scoresheet_pdf import (
        CRITERIA_FIELD_DEFS,
        MATCH_FIELD_DEFS,
        default_fields_for,
        default_order_for,
    )

    qs = ScoresheetTemplate.objects.all()
    q = (request.GET.get('q') or '').strip()
    event_type = (request.GET.get('event_type') or '').strip()
    status = (request.GET.get('status') or '').strip()
    category = (request.GET.get('category') or '').strip()
    sort = (request.GET.get('sort') or '-updated_at').strip()

    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(category__icontains=q) | Q(description__icontains=q))
    if event_type in dict(ScoresheetTemplate.EVENT_TYPE_CHOICES):
        qs = qs.filter(event_type=event_type)
    if status in dict(ScoresheetTemplate.STATUS_CHOICES):
        qs = qs.filter(status=status)
    if category:
        qs = qs.filter(category__icontains=category)

    sort_map = {
        'name': 'name',
        '-name': '-name',
        'event_type': 'event_type',
        '-event_type': '-event_type',
        'category': 'category',
        '-category': '-category',
        'updated_at': 'updated_at',
        '-updated_at': '-updated_at',
        'status': 'status',
        '-status': '-status',
    }
    qs = qs.order_by(sort_map.get(sort, '-updated_at'))

    paginator = Paginator(qs, 10)
    page_obj = paginator.get_page(request.GET.get('page') or 1)
    templates = [_serialize_scoresheet_template(t) for t in page_obj.object_list]
    categories = list(
        ScoresheetTemplate.objects.exclude(category='')
        .values_list('category', flat=True)
        .distinct()
        .order_by('category')[:50]
    )

    return render(request, 'admindash/scoresheet.html', {
        'templates': templates,
        'templates_json': templates,
        'page_obj': page_obj,
        'paginator': {
            'q': q,
            'event_type': event_type,
            'status': status,
            'category': category,
            'sort': sort,
        },
        'categories': categories,
        'match_fields': MATCH_FIELD_DEFS,
        'criteria_fields': CRITERIA_FIELD_DEFS,
        'field_defs_json': {
            'match': MATCH_FIELD_DEFS,
            'criteria': CRITERIA_FIELD_DEFS,
        },
        'default_order_json': {
            'match': default_order_for('match'),
            'criteria': default_order_for('criteria'),
        },
        'default_match_fields': default_fields_for('match'),
        'default_criteria_fields': default_fields_for('criteria'),
    })


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_scoresheet_template_save(request):
    from events.models import ScoresheetTemplate
    from events.scoresheet_pdf import pack_template_layout

    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required.'}, status=405)
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON body.'}, status=400)

    name = (data.get('name') or '').strip()
    if not name:
        return JsonResponse({'success': False, 'message': 'Template name is required.'}, status=400)

    template_id = data.get('id')
    template = None
    if template_id:
        template = get_object_or_404(ScoresheetTemplate, pk=template_id)

    event_type = (data.get('event_type') or ScoresheetTemplate.EVENT_MATCH).strip()
    if event_type not in dict(ScoresheetTemplate.EVENT_TYPE_CHOICES):
        event_type = ScoresheetTemplate.EVENT_MATCH
    orientation = (data.get('orientation') or ScoresheetTemplate.ORIENT_PORTRAIT).strip()
    if orientation not in dict(ScoresheetTemplate.ORIENTATION_CHOICES):
        orientation = ScoresheetTemplate.ORIENT_PORTRAIT

    fields = data.get('fields') if isinstance(data.get('fields'), dict) else {}
    order = data.get('order') if isinstance(data.get('order'), list) else None
    layout = pack_template_layout(fields, event_type, order, orientation)

    creating = template is None
    if creating:
        template = ScoresheetTemplate(created_by=request.user)
    template.name = name[:200]
    template.event_type = event_type
    template.category = (data.get('category') or '').strip()[:100]
    template.description = (data.get('description') or '').strip()
    template.status = (
        ScoresheetTemplate.STATUS_ACTIVE
        if (data.get('status') or 'active') == 'active'
        else ScoresheetTemplate.STATUS_ARCHIVED
    )
    template.paper_size = (data.get('paper_size') or 'a4').strip()[:20] or 'a4'
    template.orientation = orientation
    template.layout = layout
    template.save()
    return JsonResponse({
        'success': True,
        'message': f'Template {"created" if creating else "updated"}.',
        'template': _serialize_scoresheet_template(template),
    })


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_scoresheet_template_delete(request, template_id):
    from events.models import ScoresheetTemplate

    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required.'}, status=405)
    template = get_object_or_404(ScoresheetTemplate, pk=template_id)
    name = template.name
    assigned = template.assigned_events.count()
    template_pk = template.pk
    template.delete()
    still_exists = ScoresheetTemplate.objects.filter(pk=template_pk).exists()
    if still_exists:
        return JsonResponse({'success': False, 'message': f'Failed to delete "{name}".'}, status=500)
    msg = f'Template "{name}" deleted.'
    if assigned:
        msg += f' {assigned} event(s) will use auto-matched templates.'
    return JsonResponse({'success': True, 'message': msg, 'deleted_id': template_pk})


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_scoresheet_template_bulk_delete(request):
    """Delete multiple scoresheet templates. Body: {ids: [1,2,3]}"""
    from events.models import ScoresheetTemplate

    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required.'}, status=405)
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({'success': False, 'message': 'Invalid JSON.'}, status=400)

    raw_ids = payload.get('ids') or []
    if not isinstance(raw_ids, list) or not raw_ids:
        return JsonResponse({'success': False, 'message': 'No template IDs provided.'}, status=400)

    ids = []
    for raw in raw_ids:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    ids = list(dict.fromkeys(ids))
    if not ids:
        return JsonResponse({'success': False, 'message': 'No valid template IDs provided.'}, status=400)

    qs = ScoresheetTemplate.objects.filter(pk__in=ids)
    templates = list(qs)
    found_ids = [t.pk for t in templates]
    assigned_total = sum(t.assigned_events.count() for t in templates)
    if found_ids:
        ScoresheetTemplate.objects.filter(pk__in=found_ids).delete()
    remaining = ScoresheetTemplate.objects.filter(pk__in=found_ids).count()
    actually_deleted = len(found_ids) - remaining
    skipped = len(ids) - len(found_ids)
    deleted_ids = [i for i in found_ids if not ScoresheetTemplate.objects.filter(pk=i).exists()]

    msg = f'{actually_deleted} template(s) deleted.'
    if skipped:
        msg += f' {skipped} not found.'
    if assigned_total:
        msg += f' {assigned_total} event assignment(s) cleared.'
    if actually_deleted <= 0:
        return JsonResponse({
            'success': False,
            'message': 'No matching templates were deleted.',
            'deleted': 0,
            'skipped': skipped,
            'deleted_ids': [],
        }, status=404)
    return JsonResponse({
        'success': True,
        'message': msg,
        'deleted': actually_deleted,
        'skipped': skipped,
        'deleted_ids': deleted_ids,
    })


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_scoresheet_template_duplicate(request, template_id):
    from events.models import ScoresheetTemplate

    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required.'}, status=405)
    source = get_object_or_404(ScoresheetTemplate, pk=template_id)
    base = source.name
    if base.endswith(' (Copy)'):
        base = base[:-7]
    copy_name = f'{base} (Copy)'
    n = 2
    while ScoresheetTemplate.objects.filter(name=copy_name).exists():
        copy_name = f'{base} (Copy {n})'
        n += 1
    copy = ScoresheetTemplate.objects.create(
        name=copy_name[:200],
        event_type=source.event_type,
        category=source.category,
        description=source.description,
        status=source.status,
        paper_size=source.paper_size,
        orientation=source.orientation,
        layout=source.layout,
        created_by=request.user,
    )
    return JsonResponse({
        'success': True,
        'message': f'Template duplicated as "{copy.name}".',
        'template': _serialize_scoresheet_template(copy),
    })


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_scoresheet_preview(request):
    from events.scoresheet_pdf import pack_template_layout

    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required.'}, status=405)
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON body.'}, status=400)

    event_type = (data.get('event_type') or 'match').strip() or 'match'
    orientation = (data.get('orientation') or 'portrait').strip() or 'portrait'
    fields = data.get('fields') if isinstance(data.get('fields'), dict) else {}
    order = data.get('order') if isinstance(data.get('order'), list) else None
    packed = pack_template_layout(fields, event_type, order, orientation)
    return JsonResponse({
        'success': True,
        'elements': packed['elements'],
        'fields': packed['fields'],
        'order': packed['order'],
    })


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_scoresheet_sample_pdf(request):
    from events.scoresheet_pdf import (
        default_sample_payload,
        pack_template_layout,
        render_scoresheet_pdf,
    )

    event_type = 'match'
    orientation = 'portrait'
    paper_size = 'a4'
    layout = None
    if request.method == 'POST':
        try:
            data = json.loads(request.body.decode('utf-8') or '{}')
        except json.JSONDecodeError:
            data = {}
        event_type = (data.get('event_type') or 'match').strip() or 'match'
        orientation = (data.get('orientation') or 'portrait').strip() or 'portrait'
        paper_size = (data.get('paper_size') or 'a4').strip() or 'a4'
        fields = data.get('fields') if isinstance(data.get('fields'), dict) else {}
        order = data.get('order') if isinstance(data.get('order'), list) else None
        layout = pack_template_layout(fields, event_type, order, orientation)
    if layout is None:
        layout = pack_template_layout({}, event_type, None, orientation)

    pdf_bytes = render_scoresheet_pdf(
        layout,
        orientation=orientation,
        paper_size=paper_size,
        payload=default_sample_payload(),
        event_type=event_type,
        blank_unresolved=False,
    )
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="sample-scoresheet.pdf"'
    return response


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_approve_scoresheet(request, scoresheet_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST request required.'}, status=400)

    from events.models import ScoreSheet, BracketMatch
    try:
        scoresheet = ScoreSheet.objects.get(id=scoresheet_id)
    except ScoreSheet.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Scoresheet not found.'}, status=404)

    if scoresheet.status != ScoreSheet.STATUS_PENDING:
        return JsonResponse({'success': False, 'message': 'Scoresheet has already been processed.'}, status=400)

    # Approve/finalize the scoresheet
    scoresheet.status = ScoreSheet.STATUS_FINALIZED
    scoresheet.save()

    # Log so tabulator Approved Results can list admin approvals
    from django.contrib.admin.models import LogEntry, CHANGE
    from django.contrib.contenttypes.models import ContentType
    LogEntry.objects.create(
        user=request.user,
        content_type=ContentType.objects.get_for_model(ScoreSheet),
        object_id=scoresheet.id,
        object_repr=str(scoresheet),
        action_flag=CHANGE,
        change_message='Approved match result',
    )

    # Delegate bracket sync + winner advancement + points to the shared engine.
    from events.bracket_progression import apply_scoresheet_to_bracket
    result = apply_scoresheet_to_bracket(scoresheet)

    if not result.get('updated'):
        return JsonResponse({'success': True, 'message': 'Scoresheet approved. No linked bracket match to update.'})

    msg = f"Scoresheet for Match {result['match_number']} approved. Bracket updated."
    if result.get('is_championship'):
        msg = f"Championship result approved — winner crowned and leaderboard updated."
    return JsonResponse({'success': True, 'message': msg})


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_discrepancy_scoresheet(request, scoresheet_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST request required.'}, status=400)

    from events.models import ScoreSheet
    try:
        scoresheet = ScoreSheet.objects.get(id=scoresheet_id)
    except ScoreSheet.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Scoresheet not found.'}, status=404)

    remarks = request.POST.get('remarks', '').strip()
    scoresheet.status = ScoreSheet.STATUS_PENDING
    scoresheet.remarks = remarks
    scoresheet.save()

    return JsonResponse({
        'success': True,
        'message': 'Scoresheet marked with discrepancies and sent back to tabulators.'
    })






def _tabulator_display_name(user):
    full = user.get_full_name().strip()
    if full:
        return full
    return user.get_username()


def _tabulator_event_qs(user=None):
    """Events visible to a tabulator: assigned to them, or unassigned (legacy)."""
    from django.db.models import Count
    from events.models import Event

    qs = Event.objects.annotate(_tab_count=Count('assigned_tabulators', distinct=True))
    if user is None or not getattr(user, 'is_authenticated', False):
        return qs.order_by('-event_date')
    return qs.filter(
        Q(assigned_tabulators=user) | Q(_tab_count=0)
    ).distinct().order_by('-event_date')


def _tabulator_event_ids(user=None):
    """Set of event IDs the tabulator can access."""
    return set(_tabulator_event_qs(user).values_list('id', flat=True))


def _tabulator_can_access_event(user, event):
    if event is None:
        return False
    tab_ids = list(event.assigned_tabulators.values_list('id', flat=True))
    if not tab_ids:
        return True  # unassigned → any tabulator
    return user.id in tab_ids


def _get_tab_event_rows(request):
    """Build event rows for the tabulator from assigned (or unassigned) events."""
    from events.models import Event, ScoreSheet, BracketMatch
    from django.utils import timezone
    today = timezone.localdate()
    all_events = list(_tabulator_event_qs(request.user).prefetch_related('assigned_judges'))
    rows = []
    for ev in all_events:
        # Count scoresheets for this event
        total_sheets = ScoreSheet.objects.filter(event=ev).count()
        finalized_sheets = ScoreSheet.objects.filter(event=ev, status='finalized').count()
        encoded_sheets = ScoreSheet.objects.filter(
            event=ev, status__in=['confirmed', 'finalized']
        ).count()
        pending_sheets = total_sheets - finalized_sheets

        # Criteria events: approved only after tabulator approval log (not mere submit)
        judge_approved = False
        judge_in_progress = False
        if ev.judging_event_id:
            from events.models import JudgeScore, Candidate
            cand_ids = list(
                Candidate.objects.filter(event_id=ev.judging_event_id).values_list('id', flat=True)
            )
            submitted_keys = set(
                JudgeScore.objects.filter(
                    candidate_id__in=cand_ids, submitted_at__isnull=False, is_locked=True
                ).values_list('candidate_id', 'judge_id').distinct()
            )
            if submitted_keys:
                approved_keys = _tabulator_approved_judge_keys()
                pending_keys = submitted_keys - approved_keys
                judge_approved = len(pending_keys) == 0
                judge_in_progress = len(pending_keys) > 0

        # Determine status
        if total_sheets > 0 and pending_sheets == 0 and finalized_sheets > 0:
            tab_status = 'approved'
            tab_status_label = 'Approved Result'
        elif judge_approved and not judge_in_progress:
            tab_status = 'approved'
            tab_status_label = 'Approved Result'
        elif ev.status == 'completed':
            tab_status = 'completed'
            tab_status_label = 'Completed'
        elif encoded_sheets > 0 or judge_in_progress or pending_sheets > 0 and total_sheets > 0:
            tab_status = 'in_progress'
            tab_status_label = 'In Progress'
        elif ev.event_date and ev.event_date < today:
            tab_status = 'pending'
            tab_status_label = 'Pending Encode'
        else:
            tab_status = 'not_started'
            tab_status_label = 'Not Started'

        # Progress
        progress_pct = int(round(encoded_sheets / total_sheets * 100)) if total_sheets else 0

        # Judge/Scorer names from assigned_judges
        assignees = list(ev.assigned_judges.all())
        panel_members = []
        panel_initials = []
        for j in assignees:
            name = j.get_full_name().strip() or j.username
            parts = name.split()
            ini = ''.join(p[:1] for p in parts[:2]).upper() or j.username[:2].upper()
            role = get_assignment_role(j)
            if not role:
                role = 'Scorer' if (ev.scoring_method or '') == 'match' else 'Judge'
            panel_members.append({'name': name, 'role': role, 'initials': ini})
            if len(panel_initials) < 3:
                panel_initials.append(ini)
        extra_judges = max(0, len(assignees) - len(panel_initials))

        # Date range
        date_str = ev.event_date.strftime('%b %d, %Y') if ev.event_date else '—'

        # Action
        if tab_status == 'pending' or tab_status == 'not_started':
            action_label = 'Encode Scores'
            action_kind = 'primary'
        elif tab_status == 'in_progress':
            action_label = 'Review & Verify'
            action_kind = 'verify'
        elif tab_status in ('completed', 'approved'):
            action_label = 'View Results'
            action_kind = 'results'
        else:
            action_label = 'View Details'
            action_kind = 'link'

        rows.append({
            'id': ev.id,
            'title': ev.name,
            'category': ev.category or 'General',
            'category_lower': (ev.category or 'general').lower().replace(' ', '-'),
            'date_str': date_str,
            'panel_members': panel_members,
            'panel_initials': panel_initials,
            'extra_judges': extra_judges,
            'judge_count': len(assignees),
            'tab_status': tab_status,
            'tab_status_label': tab_status_label,
            'progress_pct': progress_pct,
            'total_sheets': total_sheets,
            'encoded_sheets': encoded_sheets,
            'action_label': action_label,
            'action_kind': action_kind,
        })
    return rows


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_dashboard(request):
    """Legacy tabulator home — redirect to Faculty portal."""
    return redirect('faculty_dashboard')




@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_approve_result(request, result_type, result_id):
    """Approve a pending result: finalize match sheets (+ bracket) or lock judge scores."""
    from events.models import ScoreSheet, JudgeScore
    from django.contrib.admin.models import LogEntry, CHANGE
    from django.contrib.contenttypes.models import ContentType
    from events.bracket_progression import apply_scoresheet_to_bracket

    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    try:
        if result_type == 'match':
            ss = ScoreSheet.objects.select_related('event').get(id=result_id)
            if not _tabulator_can_access_event(request.user, ss.event):
                return JsonResponse({'success': False, 'message': 'Not assigned to this event'}, status=403)
            if ss.status == ScoreSheet.STATUS_FINALIZED:
                return JsonResponse({'success': True, 'message': 'Result already approved'})
            ss.status = ScoreSheet.STATUS_FINALIZED
            ss.save(update_fields=['status', 'updated_at'])
            apply_scoresheet_to_bracket(ss)
            obj_repr = str(ss)
            ct = ContentType.objects.get_for_model(ScoreSheet)
            obj_id = ss.id
        elif result_type == 'judge':
            js = JudgeScore.objects.select_related('candidate', 'candidate__event').get(id=result_id)
            portal_ev = getattr(js.candidate.event, 'portal_event', None)
            if portal_ev is None:
                from events.models import Event
                portal_ev = Event.objects.filter(judging_event=js.candidate.event).first()
            if portal_ev and not _tabulator_can_access_event(request.user, portal_ev):
                return JsonResponse({'success': False, 'message': 'Not assigned to this event'}, status=403)
            JudgeScore.objects.filter(
                candidate=js.candidate,
                judge_id=js.judge_id,
                submitted_at__isnull=False,
            ).update(is_locked=True)
            obj_repr = str(js)
            ct = ContentType.objects.get_for_model(JudgeScore)
            obj_id = js.id
        else:
            return JsonResponse({'success': False, 'message': 'Invalid type'}, status=400)

        LogEntry.objects.create(
            user=request.user, content_type=ct, object_id=obj_id,
            object_repr=obj_repr, action_flag=CHANGE,
            change_message=f'Approved {result_type} result',
        )
        return JsonResponse({'success': True, 'message': 'Result approved'})
    except (ScoreSheet.DoesNotExist, JudgeScore.DoesNotExist):
        return JsonResponse({'success': False, 'message': 'Not found'}, status=404)


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_reject_result(request, result_type, result_id):
    """Reject a pending scoresheet or judge score back for correction."""
    from events.models import ScoreSheet, JudgeScore
    from django.contrib.admin.models import LogEntry, CHANGE
    from django.contrib.contenttypes.models import ContentType

    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    try:
        if result_type == 'match':
            ss = ScoreSheet.objects.select_related('event').get(id=result_id)
            if not _tabulator_can_access_event(request.user, ss.event):
                return JsonResponse({'success': False, 'message': 'Not assigned to this event'}, status=403)
            if ss.status == ScoreSheet.STATUS_FINALIZED:
                return JsonResponse({'success': False, 'message': 'Cannot reject a finalized result'}, status=400)
            due = _pending_result_due_info(ss.created_at)
            if due.get('is_overdue'):
                return JsonResponse({
                    'success': False,
                    'message': 'Overdue results can only be reviewed (approve), not rejected.',
                }, status=400)
            note = '[Rejected by tabulator — resubmit corrected scores]'
            remarks = (ss.remarks or '').strip()
            if note not in remarks:
                remarks = f'{remarks}\n{note}'.strip() if remarks else note
            ss.status = ScoreSheet.STATUS_PENDING
            ss.remarks = remarks
            ss.save(update_fields=['status', 'remarks', 'updated_at'])
            obj_repr = str(ss)
            ct = ContentType.objects.get_for_model(ScoreSheet)
            obj_id = ss.id
        elif result_type == 'judge':
            js = JudgeScore.objects.select_related('candidate', 'candidate__event').get(id=result_id)
            portal_ev = getattr(js.candidate.event, 'portal_event', None)
            if portal_ev is None:
                from events.models import Event
                portal_ev = Event.objects.filter(judging_event=js.candidate.event).first()
            if portal_ev and not _tabulator_can_access_event(request.user, portal_ev):
                return JsonResponse({'success': False, 'message': 'Not assigned to this event'}, status=403)
            due = _pending_result_due_info(js.submitted_at)
            if due.get('is_overdue'):
                return JsonResponse({
                    'success': False,
                    'message': 'Overdue results can only be reviewed (approve), not rejected.',
                }, status=400)
            JudgeScore.objects.filter(
                candidate=js.candidate,
                judge_id=js.judge_id,
            ).update(is_locked=False, submitted_at=None)
            obj_repr = str(js)
            ct = ContentType.objects.get_for_model(JudgeScore)
            obj_id = js.id
        else:
            return JsonResponse({'success': False, 'message': 'Invalid type'}, status=400)

        LogEntry.objects.create(
            user=request.user, content_type=ct, object_id=obj_id,
            object_repr=obj_repr, action_flag=CHANGE,
            change_message=f'Rejected {result_type} result',
        )
        return JsonResponse({'success': True, 'message': 'Result rejected'})
    except (ScoreSheet.DoesNotExist, JudgeScore.DoesNotExist):
        return JsonResponse({'success': False, 'message': 'Not found'}, status=404)


def _normalize_category_key(category):
    """Map event category strings to CSS/modifier keys."""
    if not category:
        return 'general'
    key = category.lower().replace(' ', '-').replace('_', '-')
    if key in ('socio-cultural', 'socio-culture', 'cultural'):
        return 'cultural'
    return key


def _pending_result_due_info(submitted_dt):
    """Return due datetime, display strings, and overdue flag for a submission."""
    from datetime import datetime, time
    from django.utils import timezone

    if not submitted_dt:
        return {
            'due_at': '',
            'due_time': '',
            'is_overdue': False,
            'status_key': 'awaiting',
            'status_label': 'AWAITING REVIEW',
        }

    tz = timezone.get_current_timezone()
    due_dt = timezone.make_aware(datetime.combine(submitted_dt.date(), time(23, 59, 59)), tz)
    is_overdue = timezone.now() > due_dt
    return {
        'due_at': due_dt.strftime('%b %d, %Y'),
        'due_time': due_dt.strftime('%I:%M %p'),
        'is_overdue': is_overdue,
        'status_key': 'overdue' if is_overdue else 'awaiting',
        'status_label': 'OVERDUE' if is_overdue else 'AWAITING REVIEW',
    }


def _judge_submitter_display(judge_id, judging_event=None):
    """Resolve judge display name + initials for pending/approved tables."""
    User = get_user_model()
    judge = None
    if judge_id:
        try:
            judge = User.objects.get(id=judge_id)
        except User.DoesNotExist:
            judge = None

    if judge is None and judging_event is not None:
        # Orphaned judge_id (account deleted): prefer event assignees
        assignees = list(judging_event.assigned_judges.all())
        if len(assignees) == 1:
            judge = assignees[0]
        else:
            from events.models import Event
            portal = Event.objects.filter(judging_event=judging_event).prefetch_related(
                'assigned_judges'
            ).first()
            if portal:
                portal_assignees = list(portal.assigned_judges.all())
                if len(portal_assignees) == 1:
                    judge = portal_assignees[0]

    if judge:
        name = (judge.get_full_name() or '').strip()
        if not name:
            name = (judge.first_name or judge.username or '').strip()
        if not name:
            name = f'Judge #{judge.id}'
        parts = name.split()
        if len(parts) >= 2:
            initials = (parts[0][0] + parts[1][0]).upper()
        else:
            initials = name[:2].upper()
        return name, initials

    return (f'Judge #{judge_id}' if judge_id else 'Unknown Judge'), 'JG'


def _build_pending_result_rows(request=None):
    """Collect pending match and judge results for the tabulator portal (scoped)."""
    from datetime import datetime
    from decimal import Decimal
    from django.db.models import Sum
    from django.utils import timezone
    from django.contrib.auth.models import User
    from events.models import ScoreSheet, JudgeScore, Event

    now = timezone.now()
    today = now.date()
    pending_rows = []
    user = request.user if request else None
    allowed_event_ids = _tabulator_event_ids(user) if user else None
    allowed_je_ids = set()
    if allowed_event_ids is not None:
        allowed_je_ids = set(
            Event.objects.filter(id__in=allowed_event_ids, judging_event_id__isnull=False)
            .values_list('judging_event_id', flat=True)
        )

    # pending = awaiting review; confirmed = legacy tabulator approve without finalize
    pending_sheets = ScoreSheet.objects.filter(
        status__in=['pending', 'confirmed']
    ).select_related(
        'event', 'match', 'match__team_a', 'match__team_b', 'tabulator'
    ).order_by('-created_at')
    if allowed_event_ids is not None:
        pending_sheets = pending_sheets.filter(event_id__in=allowed_event_ids)
    for ss in pending_sheets:
        team_a = ss.match.team_a.name if ss.match and ss.match.team_a else 'TBD'
        team_b = ss.match.team_b.name if ss.match and ss.match.team_b else 'TBD'
        sub = ss.tabulator
        submitted_dt = ss.created_at
        due = _pending_result_due_info(submitted_dt)
        score_a = ss.score_team_a.quantize(Decimal('0.01')) if ss.score_team_a is not None else Decimal('0')
        score_b = ss.score_team_b.quantize(Decimal('0.01')) if ss.score_team_b is not None else Decimal('0')
        cat_key = _normalize_category_key(ss.event.category)
        pending_rows.append({
            'id': ss.id,
            'type': 'match',
            'type_label': 'Match Result',
            'event_name': ss.event.name,
            'event_category': ss.event.category or 'General',
            'category_lower': cat_key,
            'entry': f'{team_a} vs {team_b}',
            'round_label': ss.match.round_name if ss.match else '',
            'score_display': f'{score_a:g} - {score_b:g}',
            'submitted_by': (sub.get_full_name() or sub.username) if sub else 'Unknown',
            'submitted_role': 'Scorer',
            'submitted_initials': sub.username[:2].upper() if sub else 'UN',
            'submitted_at': submitted_dt.strftime('%b %d, %Y'),
            'submitted_time': submitted_dt.strftime('%I:%M %p'),
            'submitted_dt': submitted_dt,
            'venue': ss.event.venue or (ss.match.venue if ss.match else '') or '—',
            'judges_names': ss.judges_names or '—',
            'remarks': ss.remarks or '',
            'review_lines': [
                {'label': team_a, 'value': f'{score_a:g}'},
                {'label': team_b, 'value': f'{score_b:g}'},
            ],
            'review_only': bool(due.get('is_overdue')),
            **due,
        })

    approved_judge_keys = _tabulator_approved_judge_keys()
    submitted_scores = JudgeScore.objects.filter(
        is_locked=True, submitted_at__isnull=False
    ).select_related(
        'candidate', 'candidate__event', 'candidate__event__category', 'criterion'
    ).prefetch_related(
        'candidate__event__assigned_judges',
    ).order_by('-submitted_at')
    if allowed_je_ids is not None:
        submitted_scores = submitted_scores.filter(candidate__event_id__in=allowed_je_ids)
    seen_judge_keys = set()
    for js in submitted_scores:
        cand = js.candidate
        judge_key = (cand.id, js.judge_id)
        if judge_key in approved_judge_keys or judge_key in seen_judge_keys:
            continue
        seen_judge_keys.add(judge_key)
        je = cand.event
        submitted_dt = js.submitted_at
        due = _pending_result_due_info(submitted_dt)
        cat = je.category
        cat_name = cat.name if cat else 'General'
        cat_key = _normalize_category_key(cat.category_type if cat else cat_name)
        cand_scores = JudgeScore.objects.filter(
            candidate=cand, judge_id=js.judge_id, submitted_at__isnull=False
        ).select_related('criterion')
        total = cand_scores.aggregate(total=Sum('score'))['total'] or Decimal('0')
        review_lines = [
            {'label': sc.criterion.name, 'value': f'{sc.score:g}'}
            for sc in cand_scores.order_by('criterion__order', 'criterion__name')
        ]
        judge_name, judge_initials = _judge_submitter_display(js.judge_id, je)
        pending_rows.append({
            'id': js.id,
            'type': 'judge',
            'type_label': 'Judge Score',
            'event_name': je.title,
            'event_category': cat_name,
            'category_lower': cat_key,
            'entry': f'Contestant #{cand.number}',
            'round_label': je.get_status_display(),
            'score_display': f'{total.quantize(Decimal("0.01")):g}',
            'submitted_by': judge_name,
            'submitted_role': 'Judge',
            'submitted_initials': judge_initials,
            'submitted_at': submitted_dt.strftime('%b %d, %Y') if submitted_dt else '—',
            'submitted_time': submitted_dt.strftime('%I:%M %p') if submitted_dt else '',
            'submitted_dt': submitted_dt,
            'venue': je.venue or '—',
            'judges_names': judge_name,
            'remarks': cand.description or '',
            'review_lines': review_lines,
            'review_only': bool(due.get('is_overdue')),
            **due,
        })

    epoch = timezone.make_aware(datetime(1970, 1, 1), timezone.get_current_timezone())
    pending_rows.sort(key=lambda row: row.get('submitted_dt') or epoch, reverse=True)

    today_count = sum(
        1 for row in pending_rows
        if row.get('submitted_dt') and row['submitted_dt'].date() == today
    )
    overdue_count = sum(1 for row in pending_rows if row.get('is_overdue'))

    return pending_rows, {
        'total_pending': len(pending_rows),
        'today_submissions': today_count,
        'awaiting_review': len(pending_rows),
        'overdue_count': overdue_count,
        'match_count': sum(1 for row in pending_rows if row['type'] == 'match'),
        'judge_count': sum(1 for row in pending_rows if row['type'] == 'judge'),
    }


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_pending_results(request):
    """Legacy tabulator page — redirected to Faculty portal."""
    return redirect('faculty_results_review')



def _tabulator_approved_judge_keys():
    """Return (candidate_id, judge_id) pairs explicitly approved by a tabulator."""
    from django.contrib.admin.models import LogEntry
    from django.contrib.contenttypes.models import ContentType
    from events.models import JudgeScore

    js_ct = ContentType.objects.get_for_model(JudgeScore)
    keys = set()
    for log in LogEntry.objects.filter(
        change_message='Approved judge result',
        content_type=js_ct,
    ):
        try:
            js = JudgeScore.objects.only('candidate_id', 'judge_id').get(id=int(log.object_id))
        except (JudgeScore.DoesNotExist, TypeError, ValueError):
            continue
        keys.add((js.candidate_id, js.judge_id))
    return keys


def _approver_display_fields(approver, viewer=None):
    """Build approver name/initials for approved-result rows."""
    if not approver:
        return {'approved_by_name': 'Tabulator', 'approved_by_initials': 'TA', 'approved_by_self': False}
    name = approver.get_full_name() or approver.username
    parts = name.split()
    if len(parts) >= 2:
        initials = (parts[0][0] + parts[1][0]).upper()
    else:
        initials = name[:2].upper() if name else 'TA'
    return {
        'approved_by_name': name,
        'approved_by_initials': initials,
        'approved_by_self': bool(viewer and approver.id == viewer.id),
    }


def _build_approved_result_rows(request=None):
    """Collect results explicitly approved by a tabulator via the approve action."""
    from datetime import datetime
    from decimal import Decimal
    from django.db.models import Sum
    from django.utils import timezone
    from django.contrib.admin.models import LogEntry
    from django.contrib.contenttypes.models import ContentType
    from django.contrib.auth.models import User
    from events.models import ScoreSheet, JudgeScore, Event

    approved_rows = []
    events_by_name = {ev.name: ev for ev in Event.objects.all()}
    completed_event_names = {ev.name for ev in Event.objects.filter(status=Event.STATUS_COMPLETED)}

    ss_ct = ContentType.objects.get_for_model(ScoreSheet)
    js_ct = ContentType.objects.get_for_model(JudgeScore)
    approval_logs = LogEntry.objects.filter(
        change_message__in=['Approved match result', 'Approved judge result'],
    ).select_related('user', 'content_type').order_by('-action_time')

    seen_match_ids = set()
    seen_judge_keys = set()

    for log in approval_logs:
        if log.content_type_id == ss_ct.id and log.change_message == 'Approved match result':
            try:
                ss_id = int(log.object_id)
            except (TypeError, ValueError):
                continue
            if ss_id in seen_match_ids:
                continue
            try:
                ss = ScoreSheet.objects.select_related(
                    'event', 'match', 'match__team_a', 'match__team_b', 'tabulator'
                ).get(id=ss_id)
            except ScoreSheet.DoesNotExist:
                continue
            seen_match_ids.add(ss_id)
            team_a = ss.match.team_a.name if ss.match and ss.match.team_a else 'TBD'
            team_b = ss.match.team_b.name if ss.match and ss.match.team_b else 'TBD'
            approved_dt = log.action_time
            score_a = ss.score_team_a.quantize(Decimal('0.01')) if ss.score_team_a is not None else Decimal('0')
            score_b = ss.score_team_b.quantize(Decimal('0.01')) if ss.score_team_b is not None else Decimal('0')
            cat_key = _normalize_category_key(ss.event.category)
            event_completed = ss.event.name in completed_event_names or ss.event.status == Event.STATUS_COMPLETED
            approved_rows.append({
                'id': ss.id,
                'type': 'match',
                'type_label': 'Match Result',
                'event_name': ss.event.name,
                'event_category': ss.event.category or 'General',
                'category_lower': cat_key,
                'entry': f'{team_a} vs {team_b}',
                'round_label': ss.match.round_name if ss.match else '',
                'score_display': f'{score_a:g} - {score_b:g}',
                'approved_at': approved_dt.strftime('%b %d, %Y'),
                'approved_time': approved_dt.strftime('%I:%M %p'),
                'approved_dt': approved_dt,
                'venue': ss.event.venue or (ss.match.venue if ss.match else '') or '—',
                'status_label': 'FINALIZED' if ss.status == 'finalized' else 'APPROVED',
                'event_completed': event_completed,
                'submitted_by': (ss.tabulator.get_full_name() or ss.tabulator.username) if ss.tabulator else 'Scorer',
                'detail_lines': [
                    {'label': team_a, 'value': f'{score_a:g}'},
                    {'label': team_b, 'value': f'{score_b:g}'},
                ],
                'remarks': ss.remarks or '',
                **_approver_display_fields(log.user, request.user if request else None),
            })
        elif log.content_type_id == js_ct.id and log.change_message == 'Approved judge result':
            try:
                js_id = int(log.object_id)
            except (TypeError, ValueError):
                continue
            try:
                js = JudgeScore.objects.select_related(
                    'candidate', 'candidate__event', 'candidate__event__category', 'criterion'
                ).get(id=js_id)
            except JudgeScore.DoesNotExist:
                continue
            cand = js.candidate
            judge_key = (cand.id, js.judge_id)
            if judge_key in seen_judge_keys:
                continue
            seen_judge_keys.add(judge_key)
            je = cand.event
            approved_dt = log.action_time
            cat = je.category
            cat_name = cat.name if cat else 'General'
            cat_key = _normalize_category_key(cat.category_type if cat else cat_name)
            cand_scores = JudgeScore.objects.filter(
                candidate=cand, judge_id=js.judge_id, submitted_at__isnull=False
            ).select_related('criterion')
            total = cand_scores.aggregate(total=Sum('score'))['total'] or Decimal('0')
            portal_event = events_by_name.get(je.title)
            event_completed = je.status == 'completed' or (portal_event and portal_event.status == Event.STATUS_COMPLETED)
            judge_user = User.objects.filter(id=js.judge_id).first()
            judge_name = (judge_user.get_full_name() or judge_user.username) if judge_user else f'Judge #{js.judge_id}'
            approved_rows.append({
                'id': js.id,
                'type': 'judge',
                'type_label': 'Judge Score',
                'event_name': je.title,
                'event_category': cat_name,
                'category_lower': cat_key,
                'entry': f'Contestant #{cand.number}',
                'round_label': je.get_status_display(),
                'score_display': f'{total.quantize(Decimal("0.01")):g}',
                'approved_at': approved_dt.strftime('%b %d, %Y'),
                'approved_time': approved_dt.strftime('%I:%M %p'),
                'approved_dt': approved_dt,
                'venue': je.venue or '—',
                'status_label': 'APPROVED',
                'event_completed': event_completed,
                'submitted_by': judge_name,
                'detail_lines': [
                    {'label': sc.criterion.name, 'value': f'{sc.score:g}'}
                    for sc in cand_scores.order_by('criterion__order', 'criterion__name')
                ],
                'remarks': cand.description or '',
                **_approver_display_fields(log.user, request.user if request else None),
            })

    # Fallback: confirmed/finalized scoresheets without an approval log (legacy / missed logs)
    viewer = request.user if request else None
    for ss in ScoreSheet.objects.filter(
        status__in=['confirmed', 'finalized']
    ).exclude(id__in=seen_match_ids).select_related(
        'event', 'match', 'match__team_a', 'match__team_b', 'tabulator'
    ).order_by('-updated_at'):
        team_a = ss.match.team_a.name if ss.match and ss.match.team_a else 'TBD'
        team_b = ss.match.team_b.name if ss.match and ss.match.team_b else 'TBD'
        approved_dt = ss.updated_at or ss.created_at
        score_a = ss.score_team_a.quantize(Decimal('0.01')) if ss.score_team_a is not None else Decimal('0')
        score_b = ss.score_team_b.quantize(Decimal('0.01')) if ss.score_team_b is not None else Decimal('0')
        cat_key = _normalize_category_key(ss.event.category)
        event_completed = ss.event.name in completed_event_names or ss.event.status == Event.STATUS_COMPLETED
        approved_rows.append({
            'id': ss.id,
            'type': 'match',
            'type_label': 'Match Result',
            'event_name': ss.event.name,
            'event_category': ss.event.category or 'General',
            'category_lower': cat_key,
            'entry': f'{team_a} vs {team_b}',
            'round_label': ss.match.round_name if ss.match else '',
            'score_display': f'{score_a:g} - {score_b:g}',
            'approved_at': approved_dt.strftime('%b %d, %Y'),
            'approved_time': approved_dt.strftime('%I:%M %p'),
            'approved_dt': approved_dt,
            'venue': ss.event.venue or (ss.match.venue if ss.match else '') or '—',
            'status_label': 'FINALIZED' if ss.status == 'finalized' else 'APPROVED',
            'event_completed': event_completed,
            'submitted_by': (ss.tabulator.get_full_name() or ss.tabulator.username) if ss.tabulator else 'Scorer',
            'detail_lines': [
                {'label': team_a, 'value': f'{score_a:g}'},
                {'label': team_b, 'value': f'{score_b:g}'},
            ],
            'remarks': ss.remarks or '',
            **_approver_display_fields(None, viewer),
        })
        seen_match_ids.add(ss.id)

    # Fallback: locked judge score submissions without an approval log
    locked_scores = JudgeScore.objects.filter(
        is_locked=True, submitted_at__isnull=False
    ).select_related(
        'candidate', 'candidate__event', 'candidate__event__category', 'criterion', 'judge'
    ).order_by('-submitted_at')
    for js in locked_scores:
        cand = js.candidate
        judge_key = (cand.id, js.judge_id)
        if judge_key in seen_judge_keys:
            continue
        seen_judge_keys.add(judge_key)
        je = cand.event
        approved_dt = js.submitted_at or timezone.now()
        cat = je.category
        cat_name = cat.name if cat else 'General'
        cat_key = _normalize_category_key(cat.category_type if cat else cat_name)
        cand_scores = JudgeScore.objects.filter(
            candidate=cand, judge_id=js.judge_id, submitted_at__isnull=False
        ).select_related('criterion')
        total = cand_scores.aggregate(total=Sum('score'))['total'] or Decimal('0')
        portal_event = events_by_name.get(je.title)
        event_completed = je.status == 'completed' or (portal_event and portal_event.status == Event.STATUS_COMPLETED)
        judge_name = (js.judge.get_full_name() or js.judge.username) if js.judge_id else f'Judge #{js.judge_id}'
        approved_rows.append({
            'id': js.id,
            'type': 'judge',
            'type_label': 'Judge Score',
            'event_name': je.title,
            'event_category': cat_name,
            'category_lower': cat_key,
            'entry': f'Contestant #{cand.number}',
            'round_label': je.get_status_display(),
            'score_display': f'{total.quantize(Decimal("0.01")):g}',
            'approved_at': approved_dt.strftime('%b %d, %Y'),
            'approved_time': approved_dt.strftime('%I:%M %p'),
            'approved_dt': approved_dt,
            'venue': je.venue or '—',
            'status_label': 'APPROVED',
            'event_completed': event_completed,
            'submitted_by': judge_name,
            'detail_lines': [
                {'label': sc.criterion.name, 'value': f'{sc.score:g}'}
                for sc in cand_scores.order_by('criterion__order', 'criterion__name')
            ],
            'remarks': cand.description or '',
            **_approver_display_fields(None, viewer),
        })

    # Scope to this tabulator's events
    if request and getattr(request, 'user', None):
        allowed_ids = _tabulator_event_ids(request.user)
        allowed_names = set(
            Event.objects.filter(id__in=allowed_ids).values_list('name', flat=True)
        )
        allowed_titles = set(
            Event.objects.filter(id__in=allowed_ids, judging_event__isnull=False)
            .values_list('judging_event__title', flat=True)
        )
        allowed = allowed_names | allowed_titles
        approved_rows = [r for r in approved_rows if r.get('event_name') in allowed]

    epoch = timezone.make_aware(datetime(1970, 1, 1), timezone.get_current_timezone())
    approved_rows.sort(key=lambda row: row.get('approved_dt') or epoch, reverse=True)

    now = timezone.now()
    this_month = sum(
        1 for row in approved_rows
        if row.get('approved_dt') and row['approved_dt'].month == now.month and row['approved_dt'].year == now.year
    )
    completed_events = len({row['event_name'] for row in approved_rows if row.get('event_completed')})
    latest = approved_rows[0] if approved_rows else None

    return approved_rows, {
        'total_approved': len(approved_rows),
        'this_month': this_month,
        'events_completed': completed_events,
        'match_count': sum(1 for row in approved_rows if row['type'] == 'match'),
        'judge_count': sum(1 for row in approved_rows if row['type'] == 'judge'),
        'latest_approval_date': latest['approved_at'] if latest else '—',
        'latest_approval_time': latest['approved_time'] if latest else '',
    }


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_approved_results(request):
    """Legacy tabulator page — redirected to Faculty portal."""
    return redirect('faculty_results_review')



@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_assigned_events(request):
    """Legacy tabulator page — redirected to Faculty portal."""
    return redirect('faculty_my_events')



@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_scoresheets(request):
    """Legacy tabulator page — redirected to Faculty portal."""
    return redirect('faculty_scoresheets')



def _tabulator_scoresheet_status(raw_status, event_status=None):
    """Map model status to tabulator display status."""
    from events.models import Event
    if raw_status in ('pending', 'in_progress', 'pending_review'):
        return 'pending_review', 'AWAITING REVIEW'
    if raw_status == 'confirmed':
        return 'approved', 'APPROVED'
    if raw_status in ('finalized', 'encoded'):
        if event_status == Event.STATUS_COMPLETED:
            return 'archived', 'ARCHIVED'
        return 'approved', 'APPROVED RESULT'
    if raw_status == 'submitted':
        return 'pending_review', 'AWAITING REVIEW'
    if raw_status == 'archived':
        return 'archived', 'ARCHIVED'
    return 'pending_review', 'AWAITING REVIEW'


def _tabulator_scoresheet_avatar_tone(user_id):
    tones = ('blue', 'teal', 'purple', 'orange', 'rose', 'indigo')
    return tones[user_id % len(tones)] if user_id else 'blue'


def _tabulator_scoresheet_submitter(user_id, fallback_role='Scorer'):
    user = get_user_model().objects.filter(id=user_id).first()
    if not user:
        return f'User #{user_id}', 'U?', fallback_role, _tabulator_scoresheet_avatar_tone(user_id or 0)
    name = user.get_full_name() or user.username
    parts = name.split()
    initials = (parts[0][0] + parts[1][0]).upper() if len(parts) >= 2 else name[:2].upper()
    role = 'Judge' if fallback_role == 'Judge' else 'Scorer'
    return name, initials, role, _tabulator_scoresheet_avatar_tone(user.id)


def _build_tabulator_scoresheet_rows(request=None):
    """Build unified score sheet rows from bracket ScoreSheet and JudgeScore data."""
    from datetime import datetime as dt
    from django.db.models import Max, Count, Q
    from events.models import Event, ScoreSheet, JudgeScore, JudgingEvent
    from django.contrib.contenttypes.models import ContentType
    from django.contrib.admin.models import LogEntry

    rows = []
    ss_ct = ContentType.objects.get_for_model(ScoreSheet)
    user = request.user if request else None
    allowed_event_ids = _tabulator_event_ids(user) if user else None

    portal_map = {}
    event_qs = Event.objects.select_related('judging_event')
    if allowed_event_ids is not None:
        event_qs = event_qs.filter(id__in=allowed_event_ids)
    for ev in event_qs:
        if ev.judging_event_id:
            portal_map[ev.judging_event_id] = ev

    je_map = {je.id: je for je in JudgingEvent.objects.select_related('category')}
    if allowed_event_ids is not None:
        je_map = {jid: je for jid, je in je_map.items() if jid in portal_map}

    judge_groups = (
        JudgeScore.objects
        .values('judge_id', 'candidate__event')
        .annotate(
            total_scores=Count('id'),
            locked_count=Count('id', filter=Q(is_locked=True)),
            latest_submit=Max('submitted_at'),
        )
        .order_by('-latest_submit')
    )
    if allowed_event_ids is not None:
        judge_groups = judge_groups.filter(candidate__event_id__in=list(portal_map.keys()))

    seq = 0
    for group in judge_groups:
        je = je_map.get(group['candidate__event'])
        if not je:
            continue
        portal_ev = portal_map.get(je.id)
        event_name = portal_ev.name if portal_ev else je.title
        category = portal_ev.category if portal_ev else (je.category.name if je.category else 'General')
        cat_key = _normalize_category_key(category)
        all_locked = group['locked_count'] == group['total_scores'] and group['total_scores'] > 0
        raw_status = 'submitted' if all_locked else 'in_progress'
        event_status = portal_ev.status if portal_ev else je.status
        status_key, status_label = _tabulator_scoresheet_status(raw_status, event_status)
        submit_dt = group['latest_submit']
        judge_name, judge_initials, judge_role, avatar_tone = _tabulator_scoresheet_submitter(
            group['judge_id'], 'Judge'
        )
        scoring = portal_ev.scoring_method if portal_ev and portal_ev.scoring_method else 'Criteria Based'
        type_label = f'{scoring} / {category}' if category else scoring
        seq += 1
        row_key = f"j-{group['judge_id']}-{group['candidate__event']}"
        rows.append({
            'row_key': row_key,
            'id': row_key,
            'source': 'judge',
            'entry_title': event_name,
            'event_name': event_name,
            'category': category,
            'category_lower': cat_key,
            'round_label': je.get_status_display(),
            'type_label': type_label,
            'submitted_by_name': judge_name,
            'submitted_by_initials': judge_initials,
            'submitted_by_role': judge_role,
            'avatar_tone': avatar_tone,
            'submitted_at': submit_dt.strftime('%b %d, %Y') if submit_dt else '—',
            'submitted_time': submit_dt.strftime('%I:%M %p') if submit_dt else '',
            'submitted_dt': submit_dt,
            'status_key': status_key,
            'status_label': status_label,
            'scores_display': '—',
            'event_date': je.date.strftime('%b %d, %Y') if je.date else '—',
            'event_time': je.time.strftime('%I:%M %p') if je.time else '—',
            'venue': je.venue or (portal_ev.venue if portal_ev else '—'),
            'reviewed_by': '—',
            'reviewed_at': '—',
            'remarks': '—',
            'file_name': '',
            'file_url': '',
            'view_url': '/tabulator/pending-results/?tab=judge',
            'review_url': '/tabulator/pending-results/?tab=judge',
        })

    event_judges_cache = {}
    for ev in Event.objects.prefetch_related('assigned_judges'):
        judges = ev.assigned_judges.all()
        if judges.exists():
            j = judges.first()
            full = f'{j.first_name} {j.last_name}'.strip() or j.username
            event_judges_cache[ev.id] = (full, j.id)
        else:
            event_judges_cache[ev.id] = (None, None)

    review_logs = {}
    for log in LogEntry.objects.filter(content_type=ss_ct, change_message__icontains='Verified').select_related('user'):
        oid = str(log.object_id or '')
        if oid.isdigit():
            review_logs[int(oid)] = log

    legacy_qs = ScoreSheet.objects.select_related(
        'event', 'match', 'match__team_a', 'match__team_b', 'tabulator'
    ).order_by('-created_at')
    if allowed_event_ids is not None:
        legacy_qs = legacy_qs.filter(event_id__in=allowed_event_ids)

    for s in legacy_qs:
        team_a = s.match.team_a.name if s.match and s.match.team_a else 'TBD'
        team_b = s.match.team_b.name if s.match and s.match.team_b else 'TBD'
        entry_title = f'{team_a} vs {team_b}'
        category = s.event.category or 'General'
        cat_key = _normalize_category_key(category)
        status_key, status_label = _tabulator_scoresheet_status(s.status, s.event.status)
        submit_dt = s.updated_at or s.created_at
        submitter_id = s.tabulator_id
        if not submitter_id:
            cached = event_judges_cache.get(s.event_id, (None, None))
            submitter_id = cached[1] or 0
        submitter_name, submitter_initials, submitter_role, avatar_tone = _tabulator_scoresheet_submitter(
            submitter_id or 0, 'Scorer'
        )
        if s.tabulator:
            submitter_name = s.tabulator.get_full_name() or s.tabulator.username
        elif s.judges_names:
            submitter_name = s.judges_names.split(',')[0].strip()

        tournament = s.event.tournament_type or 'Single Elimination'
        type_label = f'Match Result / {tournament}'
        score_a = s.score_team_a
        score_b = s.score_team_b
        scores_display = f'{team_a} {float(score_a):g} vs {team_b} {float(score_b):g}'

        review_log = review_logs.get(s.id)
        reviewed_by = '—'
        reviewed_at = '—'
        if review_log:
            reviewed_by = review_log.user.get_full_name() or review_log.user.username if review_log.user else 'Tabulator'
            reviewed_at = timezone.localtime(review_log.action_time).strftime('%b %d, %Y %I:%M %p')

        if s.status == 'pending':
            view_url = '/tabulator/pending-results/?tab=match'
            review_url = '/tabulator/pending-results/?tab=match'
        elif s.status in ('confirmed', 'finalized'):
            view_url = '/tabulator/approved-results/?tab=match'
            review_url = '/tabulator/approved-results/?tab=match'
        else:
            view_url = '/tabulator/pending-results/?tab=match'
            review_url = '/tabulator/pending-results/?tab=match'

        file_url = s.ocr_image.url if s.ocr_image else ''
        file_name = s.ocr_image.name.split('/')[-1] if s.ocr_image else ''

        seq += 1
        row_key = f'b-{s.id}'
        match = s.match
        rows.append({
            'row_key': row_key,
            'id': s.id,
            'source': 'bracket',
            'entry_title': entry_title,
            'event_name': s.event.name,
            'category': category,
            'category_lower': cat_key,
            'round_label': match.round_name if match else '—',
            'type_label': type_label,
            'submitted_by_name': submitter_name,
            'submitted_by_initials': submitter_initials,
            'submitted_by_role': submitter_role,
            'avatar_tone': avatar_tone,
            'submitted_at': submit_dt.strftime('%b %d, %Y'),
            'submitted_time': submit_dt.strftime('%I:%M %p'),
            'submitted_dt': submit_dt,
            'status_key': status_key,
            'status_label': status_label,
            'scores_display': scores_display,
            'event_date': (match.match_date or s.event.event_date).strftime('%b %d, %Y') if match else s.event.event_date.strftime('%b %d, %Y'),
            'event_time': match.match_time.strftime('%I:%M %p') if match and match.match_time else (
                s.event.event_time.strftime('%I:%M %p') if s.event.event_time else '—'
            ),
            'venue': (match.venue if match and match.venue else s.event.venue) or '—',
            'reviewed_by': reviewed_by,
            'reviewed_at': reviewed_at,
            'remarks': s.remarks or '—',
            'file_name': file_name,
            'file_url': file_url,
            'view_url': view_url,
            'review_url': review_url,
        })

    epoch = timezone.make_aware(datetime(1970, 1, 1))
    rows.sort(key=lambda r: r.get('submitted_dt') or epoch, reverse=True)

    stats = {
        'total_sheets': len(rows),
        'encoded_count': sum(1 for r in rows if r['status_key'] in ('encoded', 'approved')),
        'pending_review_count': sum(1 for r in rows if r['status_key'] == 'pending_review'),
        'archived_count': sum(1 for r in rows if r['status_key'] == 'archived'),
    }
    filter_options = {
        'event_types': sorted({r['category_lower'] for r in rows}),
        'events': sorted({r['event_name'] for r in rows}),
        'rounds': sorted({r['round_label'] for r in rows if r.get('round_label') and r['round_label'] != '—'}),
    }
    return rows, stats, filter_options


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_ocr_upload(request):
    """Legacy OCR page — redirect into the main Score Sheets flow."""
    return redirect('tabulator_scoresheets')


def _build_event_reports_context(request):
    """Build analytics context for the tabulator Event Reports page."""
    from collections import Counter
    from django.db.models import Q
    from django.contrib.admin.models import LogEntry
    from events.models import Event, BracketMatch, BracketTeam, ScoreSheet, Candidate

    event_id = request.GET.get('event', '').strip()
    section = request.GET.get('section', 'overview').strip() or 'overview'
    round_filter = request.GET.get('round', 'all').strip()
    venue_filter = request.GET.get('venue', 'all').strip()
    type_filter = request.GET.get('type', 'all').strip()
    search_q = (request.GET.get('q') or '').strip().lower()

    all_events = list(Event.objects.order_by('-event_date'))
    if event_id and event_id.isdigit():
        selected_event = Event.objects.filter(id=int(event_id)).first()
    else:
        selected_event = Event.objects.filter(status='active').order_by('-event_date').first() or (all_events[0] if all_events else None)

    if selected_event:
        scope_events = [selected_event]
        event_ids = [selected_event.id]
    else:
        scope_events = all_events
        event_ids = [ev.id for ev in all_events]

    matches_qs = BracketMatch.objects.filter(event_id__in=event_ids).select_related(
        'event', 'team_a', 'team_b', 'winner'
    )
    if round_filter != 'all':
        matches_qs = matches_qs.filter(round_name=round_filter)
    if venue_filter != 'all':
        matches_qs = matches_qs.filter(venue=venue_filter)
    if type_filter != 'all':
        matches_qs = matches_qs.filter(event__category__iexact=type_filter)
    if search_q:
        matches_qs = matches_qs.filter(
            Q(round_name__icontains=search_q)
            | Q(team_a__name__icontains=search_q)
            | Q(team_b__name__icontains=search_q)
            | Q(event__name__icontains=search_q)
        )

    matches = list(matches_qs.order_by('-match_date', '-match_number', '-id'))
    total_matches = len(matches)

    pending_sheet_match_ids = set(
        ScoreSheet.objects.filter(event_id__in=event_ids, status='pending').values_list('match_id', flat=True)
    )

    def match_bucket(m):
        if m.status == BracketMatch.STATUS_COMPLETED:
            return 'completed'
        if m.status == BracketMatch.STATUS_FORFEIT:
            return 'cancelled'
        if m.status == BracketMatch.STATUS_PENDING:
            return 'upcoming'
        if m.status == BracketMatch.STATUS_ONGOING or m.id in pending_sheet_match_ids:
            return 'pending_review'
        return 'upcoming'

    buckets = Counter(match_bucket(m) for m in matches)
    completed = buckets.get('completed', 0)
    pending_review = buckets.get('pending_review', 0)
    upcoming = buckets.get('upcoming', 0)
    cancelled = buckets.get('cancelled', 0)

    def pct(n):
        return round(n / total_matches * 100, 1) if total_matches else 0.0

    status_chart = [
        {'key': 'completed', 'label': 'Completed', 'count': completed, 'pct': pct(completed), 'color': '#10b981'},
        {'key': 'pending_review', 'label': 'Pending Review', 'count': pending_review, 'pct': pct(pending_review), 'color': '#f59e0b'},
        {'key': 'upcoming', 'label': 'Upcoming', 'count': upcoming, 'pct': pct(upcoming), 'color': '#3b82f6'},
        {'key': 'cancelled', 'label': 'Cancelled', 'count': cancelled, 'pct': pct(cancelled), 'color': '#ef4444'},
    ]

    type_counts = Counter()
    for m in matches:
        label = m.event.category or m.event.name
        type_counts[label] += 1
    type_icons = {'sports': '🏀', 'academic': '📚', 'esports': '🎮', 'cultural': '🎭', 'socio-cultural': '🎭'}
    type_chart = []
    max_type = max(type_counts.values()) if type_counts else 1
    for label, count in type_counts.most_common(8):
        key = label.lower().replace(' ', '-')
        type_chart.append({
            'label': label,
            'count': count,
            'pct': round(count / total_matches * 100) if total_matches else 0,
            'bar_pct': round(count / max_type * 100) if max_type else 0,
            'icon': type_icons.get(key.split('-')[0], '🏅'),
        })

    team_stats = []
    teams = BracketTeam.objects.filter(event_id__in=event_ids).select_related('department')
    for team in teams:
        ev_id = team.event_id
        gold = BracketMatch.objects.filter(
            event_id=ev_id, winner=team, status='completed', round_name__icontains='final'
        ).count()
        silver = BracketMatch.objects.filter(
            event_id=ev_id, status='completed', round_name__icontains='final'
        ).filter(Q(team_a=team) | Q(team_b=team)).exclude(winner=team).count()
        bronze = BracketMatch.objects.filter(
            event_id=ev_id, winner=team, status='completed', round_name__icontains='semi'
        ).count()
        total_medals = gold + silver + bronze
        initials = ''.join(w[:1] for w in team.name.split()[:2]).upper() or team.name[:2].upper()
        team_stats.append({
            'name': team.name,
            'initials': initials,
            'gold': gold,
            'silver': silver,
            'bronze': bronze,
            'total': total_medals,
            'event_name': team.event.name,
        })
    team_stats.sort(key=lambda t: (-t['gold'], -t['silver'], -t['bronze'], t['name']))
    for idx, row in enumerate(team_stats, start=1):
        row['rank'] = idx

    status_labels = {
        'completed': ('Completed', 'completed'),
        'pending_review': ('Pending Review', 'pending'),
        'upcoming': ('Upcoming', 'upcoming'),
        'cancelled': ('Cancelled', 'cancelled'),
    }
    recent_matches = []
    for m in matches[:8]:
        bucket = match_bucket(m)
        label, css = status_labels[bucket]
        team_a = m.team_a.name if m.team_a else 'TBD'
        team_b = m.team_b.name if m.team_b else 'TBD'
        when = ''
        if m.match_date:
            when = m.match_date.strftime('%b %d, %Y')
            if m.match_time:
                when += f' · {m.match_time.strftime("%I:%M %p")}'
        elif m.created_at:
            when = m.created_at.strftime('%b %d, %Y · %I:%M %p')
        recent_matches.append({
            'id': m.id,
            'event_id': m.event_id,
            'when': when or '—',
            'event_name': m.event.name,
            'round': m.round_name,
            'matchup': f'{team_a} vs {team_b}',
            'status_label': label,
            'status_key': css,
            'venue': m.venue or m.event.venue or '—',
        })

    rounds = sorted({m.round_name for m in matches if m.round_name})
    venues = sorted({m.venue or m.event.venue for m in matches if (m.venue or m.event.venue)})
    event_types = sorted({ev.category for ev in scope_events if ev.category})

    export_logs_qs = LogEntry.objects.filter(change_message__startswith='[TAB_REPORT]')
    if selected_event:
        export_logs_qs = export_logs_qs.filter(object_repr__icontains=selected_event.name)
    export_logs = export_logs_qs.select_related('user').order_by('-action_time')[:8]
    export_history = [{
        'title': log.object_repr[:70],
        'by': log.user.username if log.user else 'System',
        'at': log.action_time.strftime('%b %d, %Y %I:%M %p'),
    } for log in export_logs]

    sports_count = len({ev.category for ev in scope_events if ev.category})
    if selected_event:
        banner_title = selected_event.name
        banner_dates = selected_event.event_date.strftime('%b %d, %Y')
        if selected_event.event_time:
            banner_dates += f' · {selected_event.event_time.strftime("%I:%M %p")}'
        sports_label = selected_event.category or 'Event'
        sports_count = 1
    elif scope_events:
        dates = [ev.event_date for ev in scope_events if ev.event_date]
        banner_title = 'All Assigned Events'
        if dates:
            banner_dates = f'{min(dates).strftime("%b %d, %Y")} – {max(dates).strftime("%b %d, %Y")}'
        else:
            banner_dates = '—'
        sports_label = 'Sports'
    else:
        banner_title = 'No Events'
        banner_dates = '—'
        sports_label = 'Sports'

    judges_rows = []
    for ev in scope_events:
        for judge in ev.assigned_judges.all():
            judges_rows.append({
                'name': judge.get_full_name() or judge.username,
                'event': ev.name,
                'email': judge.email or '—',
            })

    participation_rows = []
    for ev in scope_events:
        teams_n = BracketTeam.objects.filter(event=ev).count()
        cand_n = Candidate.objects.filter(event=ev.judging_event).count() if ev.judging_event_id else 0
        participation_rows.append({
            'event': ev.name,
            'teams': teams_n,
            'candidates': cand_n,
            'participants': ev.max_participants or teams_n or cand_n,
            'judges': ev.assigned_judges.count(),
        })

    results_rows = []
    for ss in ScoreSheet.objects.filter(event_id__in=event_ids, status__in=['confirmed', 'finalized']).select_related('event', 'match', 'match__team_a', 'match__team_b')[:20]:
        ta = ss.match.team_a.name if ss.match and ss.match.team_a else 'TBD'
        tb = ss.match.team_b.name if ss.match and ss.match.team_b else 'TBD'
        results_rows.append({
            'event': ss.event.name,
            'match': f'{ta} vs {tb}',
            'round': ss.match.round_name if ss.match else '',
            'score': f'{ss.score_team_a:g} - {ss.score_team_b:g}',
            'status': ss.get_status_display(),
        })

    all_matches_rows = []
    for m in matches:
        bucket = match_bucket(m)
        label, css = status_labels[bucket]
        ta = m.team_a.name if m.team_a else 'TBD'
        tb = m.team_b.name if m.team_b else 'TBD'
        all_matches_rows.append({
            'when': m.match_date.strftime('%b %d, %Y') if m.match_date else '—',
            'event': m.event.name,
            'round': m.round_name,
            'matchup': f'{ta} vs {tb}',
            'venue': m.venue or m.event.venue or '—',
            'status_label': label,
            'status_key': css,
        })

    return {
        'selected_event': selected_event,
        'selected_event_id': selected_event.id if selected_event else '',
        'all_events': all_events,
        'section': section,
        'round_filter': round_filter,
        'venue_filter': venue_filter,
        'type_filter': type_filter,
        'search_query': request.GET.get('q', ''),
        'rounds': rounds,
        'venues': venues,
        'event_types': event_types,
        'banner_title': banner_title,
        'banner_dates': banner_dates,
        'sports_count': sports_count,
        'sports_label': sports_label if selected_event else f'{sports_count} Categories',
        'total_matches': total_matches,
        'completed_matches': completed,
        'pending_review': pending_review,
        'upcoming_matches': upcoming,
        'cancelled_matches': cancelled,
        'completed_pct': pct(completed),
        'pending_pct': pct(pending_review),
        'upcoming_pct': pct(upcoming),
        'cancelled_pct': pct(cancelled),
        'status_chart': status_chart,
        'type_chart': type_chart,
        'team_standings': team_stats[:5],
        'team_standings_all': team_stats,
        'recent_matches': recent_matches,
        'export_history': export_history,
        'judges_rows': judges_rows,
        'participation_rows': participation_rows,
        'results_rows': results_rows,
        'all_matches_rows': all_matches_rows,
        'donut_data_json': json.dumps([{'value': s['count'], 'color': s['color'], 'label': s['label']} for s in status_chart]),
    }


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_reports(request):
    """Legacy tabulator page — redirected to Faculty portal."""
    return redirect('faculty_dashboard')



# ── Tabulation sub-pages ────────────────────────────────────────

@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_tabulation(request):
    """Legacy tabulation hub — redirect to Pending Results (main verify flow)."""
    return redirect('tabulator_pending_results')


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_encode_score(request):
    """Legacy encode page — redirect to Pending Results."""
    return redirect('tabulator_pending_results')


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_save_scores(request, sheet_id):
    """Legacy encode save — redirect to Pending Results."""
    return redirect('tabulator_pending_results')


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_generate_report(request):
    """Generate and download a report (PDF/Excel/CSV) for tabulator."""
    if request.method != 'POST':
        return redirect('tabulator_reports')
    from events.models import Event
    from django.contrib.admin.models import LogEntry, CHANGE
    from django.contrib.contenttypes.models import ContentType
    from datetime import datetime

    event_name = request.POST.get('event', '').strip()
    report_type = request.POST.get('report_type', 'results').strip()
    export_format = request.POST.get('format', 'pdf').strip().lower()

    type_label_map = {
        'results': 'Results Summary',
        'breakdown': 'Score Breakdown',
        'judge': 'Judge Summary',
        'audit': 'Audit Trail',
        'summary': 'Summary',
    }
    type_label = type_label_map.get(report_type, report_type.title())

    event_obj = Event.objects.filter(name=event_name).first()
    event_ct = ContentType.objects.get_for_model(Event)

    LogEntry.objects.create(
        user=request.user,
        content_type_id=event_ct.id,
        object_id=str(event_obj.id) if event_obj else '1',
        object_repr=f"{event_name} {type_label} Report"[:200],
        action_flag=CHANGE,
        change_message=f"[TAB_REPORT] Generated {export_format.upper()} report. type={report_type}"
    )

    # Gather event details for the report
    from events.models import BracketTeam, BracketMatch, ScoreSheet, Candidate
    ev_info = {}
    if event_obj:
        teams_count = BracketTeam.objects.filter(event=event_obj).count()
        matches_count = BracketMatch.objects.filter(event=event_obj).count()
        matches_done = BracketMatch.objects.filter(event=event_obj, status='completed').count()
        sheets_count = ScoreSheet.objects.filter(event=event_obj).count()
        sheets_final = ScoreSheet.objects.filter(event=event_obj, status='finalized').count()
        judges_count = event_obj.assigned_judges.count()
        candidates_count = 0
        if hasattr(event_obj, 'judging_event') and event_obj.judging_event:
            candidates_count = Candidate.objects.filter(event=event_obj.judging_event).count()
        participants = event_obj.max_participants or teams_count or candidates_count or 0
        ev_info = {
            'category': event_obj.category,
            'date': event_obj.event_date.strftime('%b %d, %Y'),
            'venue': event_obj.venue,
            'status': event_obj.get_status_display(),
            'participants': participants,
            'teams': teams_count,
            'candidates': candidates_count,
            'judges': judges_count,
            'matches': matches_count,
            'matches_completed': matches_done,
            'scoresheets': sheets_count,
            'scoresheets_finalized': sheets_final,
            'faculty_in_charge': event_obj.faculty_in_charge,
            'student_in_charge': event_obj.student_in_charge,
        }

    if export_format == 'excel':
        from core.reports_generator import generate_excel_report
        stream = generate_excel_report(event_name, 'all', None, None, [], event_info=ev_info)
        fname = f"{event_name.replace(' ', '_')}_{report_type}_{datetime.now().strftime('%Y%m%d')}.xlsx"
        resp = HttpResponse(stream.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename="{fname}"'
        return resp
    elif export_format == 'csv':
        import csv as csv_mod
        resp = HttpResponse(content_type='text/csv')
        fname = f"{event_name.replace(' ', '_')}_{report_type}_{datetime.now().strftime('%Y%m%d')}.csv"
        resp['Content-Disposition'] = f'attachment; filename="{fname}"'
        writer = csv_mod.writer(resp)
        writer.writerow(['EVENT DETAILS REPORT'])
        writer.writerow([])
        writer.writerow(['Event', event_name])
        writer.writerow(['Report Type', type_label])
        writer.writerow(['Generated By', request.user.username])
        writer.writerow(['Date Generated', datetime.now().strftime('%Y-%m-%d %H:%M')])
        writer.writerow([])
        if ev_info:
            writer.writerow(['EVENT INFORMATION'])
            writer.writerow(['Category', ev_info.get('category', '')])
            writer.writerow(['Date', ev_info.get('date', '')])
            writer.writerow(['Venue', ev_info.get('venue', '')])
            writer.writerow(['Status', ev_info.get('status', '')])
            writer.writerow(['Participants', ev_info.get('participants', 0)])
            writer.writerow(['Teams', ev_info.get('teams', 0)])
            writer.writerow(['Candidates', ev_info.get('candidates', 0)])
            writer.writerow(['Judges Assigned', ev_info.get('judges', 0)])
            writer.writerow(['Total Matches', ev_info.get('matches', 0)])
            writer.writerow(['Matches Completed', ev_info.get('matches_completed', 0)])
            writer.writerow(['Scoresheets', ev_info.get('scoresheets', 0)])
            writer.writerow(['Scoresheets Finalized', ev_info.get('scoresheets_finalized', 0)])
            writer.writerow(['Faculty In Charge', ev_info.get('faculty_in_charge', '')])
            writer.writerow(['Student In Charge', ev_info.get('student_in_charge', '')])
        return resp
    else:
        from core.reports_generator import generate_pdf_report
        stream = generate_pdf_report(event_name, 'all', None, None, [], event_info=ev_info)
        fname = f"{event_name.replace(' ', '_')}_{report_type}_{datetime.now().strftime('%Y%m%d')}.pdf"
        resp = HttpResponse(stream.read(), content_type='application/pdf')
        resp['Content-Disposition'] = f'attachment; filename="{fname}"'
        return resp


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_verify_lock(request, sheet_id):
    """Legacy verify-lock — use Pending Results approve (finalizes + bracket)."""
    return redirect('tabulator_pending_results')


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_request_changes(request, sheet_id):
    """Legacy request-changes — use Pending Results reject."""
    return redirect('tabulator_pending_results')


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_review(request):
    """Legacy review page — redirect to Pending Results."""
    return redirect('tabulator_pending_results')


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_results(request):
    """Legacy results page — redirect to Approved Results."""
    return redirect('tabulator_approved_results')


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def _official_user_ids():
    """User IDs that belong to Judge or Scorer groups."""
    User = get_user_model()
    return set(
        User.objects.filter(
            Q(groups__name__iexact='Judge')
            | Q(groups__name__iexact='Judges')
            | Q(groups__name__iexact='Scorer')
        ).values_list('id', flat=True).distinct()
    )


def _activity_role_for_user_id(user_id):
    user = get_user_model().objects.filter(id=user_id).first()
    if not user:
        return 'Judge'
    return get_assignment_role(user) or 'Judge'


def _build_official_activity_rows():
    """
    Unified realtime activity feed for Judge and Scorer accounts.
    Sources: JudgeActivityLog + synthesized JudgeScore / ScoreSheet submissions.
    """
    from django.db.models import Max
    from events.models import JudgeActivityLog, JudgeScore, ScoreSheet, Event

    rows = []
    seen_submit_keys = set()

    for log in JudgeActivityLog.objects.select_related('event', 'candidate').order_by('-timestamp'):
        role = _activity_role_for_user_id(log.judge_id)
        if role not in ('Judge', 'Scorer'):
            # Keep orphaned historical logs under Judge for visibility
            role = 'Judge'
        label, action_key = _JUDGE_ACTION_META.get(log.action, ('Activity', 'default'))
        name, initials, role = _activity_user_display(log.judge_id, role)
        rows.append({
            'id': f'jal-{log.id}',
            'sort_dt': log.timestamp,
            'date': log.timestamp.strftime('%b %d, %Y'),
            'time': log.timestamp.strftime('%I:%M %p'),
            'user_id': log.judge_id,
            'user_name': name,
            'user_initials': initials,
            'user_role': role,
            'avatar_tone': _activity_avatar_tone(log.judge_id),
            'action_code': log.action,
            'action_label': label,
            'action_key': action_key,
            'event_name': log.event.title if log.event else '—',
            'round_label': log.event.get_status_display() if log.event else '—',
            'description': (log.details or log.get_action_display())[:200],
        })
        if log.action == 'submit_score' and log.candidate_id and log.judge_id:
            seen_submit_keys.add((log.judge_id, log.candidate_id))

    # Synthesize submit rows from JudgeScore when activity log is missing
    score_groups = (
        JudgeScore.objects.filter(submitted_at__isnull=False)
        .values('judge_id', 'candidate_id', 'candidate__event_id', 'candidate__number', 'candidate__name')
        .annotate(submitted_at=Max('submitted_at'))
        .order_by('-submitted_at')
    )
    je_cache = {}
    for g in score_groups:
        key = (g['judge_id'], g['candidate_id'])
        if key in seen_submit_keys:
            continue
        seen_submit_keys.add(key)
        je_id = g['candidate__event_id']
        if je_id not in je_cache:
            from events.models import JudgingEvent
            je_cache[je_id] = JudgingEvent.objects.filter(id=je_id).first()
        je = je_cache[je_id]
        name, initials = _judge_submitter_display(g['judge_id'], je)
        role = _activity_role_for_user_id(g['judge_id'])
        if not get_user_model().objects.filter(id=g['judge_id']).exists():
            # Orphaned account: prefer portal Event assignees (Scorer/Judge), then judging event
            role = 'Judge'
            assignees = []
            if je is not None:
                portal = Event.objects.filter(judging_event=je).prefetch_related(
                    'assigned_judges'
                ).first()
                if portal:
                    assignees = list(portal.assigned_judges.all())
                if not assignees:
                    assignees = list(je.assigned_judges.all())
            if len(assignees) == 1:
                role = get_assignment_role(assignees[0]) or role
                name = (
                    assignees[0].get_full_name()
                    or assignees[0].first_name
                    or assignees[0].username
                ).strip() or name
                parts = name.split()
                initials = (
                    (parts[0][0] + parts[1][0]).upper()
                    if len(parts) >= 2 else name[:2].upper()
                )
        if role not in ('Judge', 'Scorer'):
            role = 'Judge'
        submitted_at = g['submitted_at']
        if not submitted_at:
            continue
        rows.append({
            'id': f'js-{g["judge_id"]}-{g["candidate_id"]}',
            'sort_dt': submitted_at,
            'date': submitted_at.strftime('%b %d, %Y'),
            'time': submitted_at.strftime('%I:%M %p'),
            'user_id': g['judge_id'],
            'user_name': name,
            'user_initials': initials,
            'user_role': role,
            'avatar_tone': _activity_avatar_tone(g['judge_id'] or 0),
            'action_code': 'submit_score',
            'action_label': 'Submitted Scores',
            'action_key': 'submit',
            'event_name': je.title if je else '—',
            'round_label': je.get_status_display() if je else '—',
            'description': (
                f'{role} submitted scores for Contestant #{g["candidate__number"]} '
                f'{g["candidate__name"] or ""}'.strip()
            )[:200],
        })

    # Scorer match score sheets (ScoreSheet.tabulator is a Scorer)
    scorer_ids = set(
        get_user_model().objects.filter(groups__name__iexact='Scorer')
        .values_list('id', flat=True)
    )
    for ss in ScoreSheet.objects.filter(tabulator_id__in=scorer_ids).select_related(
        'event', 'match', 'match__team_a', 'match__team_b', 'tabulator'
    ).order_by('-created_at'):
        submitted_at = ss.updated_at or ss.created_at
        team_a = ss.match.team_a.name if ss.match and ss.match.team_a else 'TBD'
        team_b = ss.match.team_b.name if ss.match and ss.match.team_b else 'TBD'
        name, initials, _ = _activity_user_display(ss.tabulator_id, 'Scorer')
        action_label, action_key = 'Submitted Scores', 'submit'
        if ss.status == 'finalized':
            action_label, action_key = 'Finalized Scores', 'finalize'
        elif ss.status == 'confirmed':
            action_label, action_key = 'Submitted Scores', 'submit'
        rows.append({
            'id': f'ss-{ss.id}',
            'sort_dt': submitted_at,
            'date': submitted_at.strftime('%b %d, %Y'),
            'time': submitted_at.strftime('%I:%M %p'),
            'user_id': ss.tabulator_id,
            'user_name': name,
            'user_initials': initials,
            'user_role': 'Scorer',
            'avatar_tone': _activity_avatar_tone(ss.tabulator_id or 0),
            'action_code': action_key,
            'action_label': action_label,
            'action_key': action_key,
            'event_name': ss.event.name if ss.event else '—',
            'round_label': ss.match.round_name if ss.match else '—',
            'description': f'Match result: {team_a} vs {team_b} ({ss.status})'[:200],
        })

    rows.sort(key=lambda r: r.get('sort_dt') or timezone.now(), reverse=True)
    return rows


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_activity_logs(request):
    """Legacy tabulator page — redirected to Faculty portal."""
    return redirect('faculty_dashboard')



@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_activity_logs_feed(request):
    """JSON feed for near-realtime activity log polling."""
    tab = request.GET.get('tab', 'all').strip().lower()
    since = (request.GET.get('since') or '').strip()
    all_rows = _build_official_activity_rows()
    rows = all_rows
    if tab == 'judge':
        rows = [r for r in all_rows if r['user_role'] == 'Judge']
    elif tab == 'scorer':
        rows = [r for r in all_rows if r['user_role'] == 'Scorer']

    if since:
        newer = []
        for row in rows:
            if row['id'] == since:
                break
            newer.append(row)
        rows = newer

    payload = [{k: v for k, v in row.items() if k != 'sort_dt'} for row in rows[:20]]
    return JsonResponse({
        'success': True,
        'count': len(payload),
        'latest_id': payload[0]['id'] if payload else since,
        'rows': payload,
        'totals': {
            'all': len(all_rows),
            'judge': sum(1 for r in all_rows if r['user_role'] == 'Judge'),
            'scorer': sum(1 for r in all_rows if r['user_role'] == 'Scorer'),
        },
    })


_SCORER_ACTION_CHOICES = [
    ('submit', 'Submitted Scores'),
    ('update', 'Updated Scores'),
    ('finalize', 'Finalized Scores'),
    ('reopen', 'Reopened Scores'),
    ('delete', 'Deleted Scores'),
    ('view', 'Viewed Scores'),
    ('remark', 'Updated Remarks'),
    ('export', 'Exported Scores'),
]

_JUDGE_ACTION_META = {
    'submit_score': ('Submitted Scores', 'submit'),
    'edit_score': ('Updated Scores', 'update'),
    'view_scores': ('Viewed Scores', 'view'),
    'view_event': ('Viewed Scores', 'view'),
    'login': ('Logged In', 'view'),
    'logout': ('Logged Out', 'default'),
}

_SCORER_ACTION_KEYS = {
    'submit': ('Submitted Scores', 'submit'),
    'update': ('Updated Scores', 'update'),
    'finalize': ('Finalized Scores', 'finalize'),
    'reopen': ('Reopened Scores', 'reopen'),
    'delete': ('Deleted Scores', 'delete'),
    'view': ('Viewed Scores', 'view'),
    'remark': ('Updated Remarks', 'remark'),
    'export': ('Exported Scores', 'export'),
    'default': ('Updated Scores', 'update'),
}


def _parse_scorer_action_key(change_message, action_flag):
    msg = (change_message or '').lower()
    if '[tab_report]' in msg or 'exported' in msg or 'generated' in msg and 'report' in msg:
        return 'export'
    if 'verified' in msg or 'locked' in msg or 'approved match' in msg:
        return 'finalize'
    if 'requested changes' in msg or 'reverted' in msg or 'rejected' in msg:
        return 'reopen'
    if 'remark' in msg:
        return 'remark'
    if action_flag == DELETION or 'deleted' in msg:
        return 'delete'
    if 'viewed' in msg:
        return 'view'
    if 'adjusted' in msg:
        return 'update'
    if action_flag == ADDITION:
        return 'submit'
    return 'update'


def _filter_scorer_logs_by_action(qs, action_key):
    if action_key == 'all':
        return qs
    matching_ids = []
    for entry in qs.iterator():
        if _parse_scorer_action_key(entry.change_message, entry.action_flag) == action_key:
            matching_ids.append(entry.pk)
    return qs.filter(pk__in=matching_ids) if matching_ids else qs.none()


def _filter_scorer_logs_by_event(qs, event_name, ss_ct):
    sheet_ids = list(
        ScoreSheet.objects.filter(event__name=event_name).values_list('id', flat=True)
    )
    if not sheet_ids:
        return qs.none()
    id_strings = [str(i) for i in sheet_ids]
    return qs.filter(content_type=ss_ct, object_id__in=id_strings)


def _filter_scorer_logs_by_round(qs, round_name, ss_ct):
    sheet_ids = list(
        ScoreSheet.objects.filter(match__round_name=round_name).values_list('id', flat=True)
    )
    if not sheet_ids:
        return qs.none()
    id_strings = [str(i) for i in sheet_ids]
    return qs.filter(content_type=ss_ct, object_id__in=id_strings)


def _scoresheet_cache_for_log_entries(log_entries):
    from events.models import ScoreSheet
    ids = []
    for entry in log_entries:
        oid = str(entry.object_id or '')
        if oid.isdigit():
            ids.append(int(oid))
    if not ids:
        return {}
    sheets = ScoreSheet.objects.filter(id__in=ids).select_related(
        'event', 'match', 'match__team_a', 'match__team_b'
    )
    return {sheet.id: sheet for sheet in sheets}


def _activity_user_display(user_id, fallback_label='User'):
    user = get_user_model().objects.filter(id=user_id).first()
    if not user:
        return f'{fallback_label} #{user_id}', 'U' + str(user_id or '?')[-1], fallback_label
    name = (user.get_full_name() or '').strip() or (user.first_name or '').strip() or user.username
    parts = name.split()
    if len(parts) >= 2:
        initials = (parts[0][0] + parts[1][0]).upper()
    else:
        initials = name[:2].upper() if name else 'U?'
    role = get_assignment_role(user) or fallback_label
    return name, initials, role


def _activity_avatar_tone(user_id):
    tones = ('blue', 'teal', 'purple', 'orange', 'rose', 'indigo')
    return tones[user_id % len(tones)] if user_id else 'blue'


def _serialize_judge_activity_logs(logs):
    rows = []
    for log in logs:
        label, action_key = _JUDGE_ACTION_META.get(log.action, ('Activity', 'default'))
        name, initials, role = _activity_user_display(log.judge_id, 'Judge')
        rows.append({
            'date': log.timestamp.strftime('%b %d, %Y'),
            'time': log.timestamp.strftime('%I:%M %p'),
            'user_name': name,
            'user_initials': initials,
            'user_role': role,
            'avatar_tone': _activity_avatar_tone(log.judge_id),
            'action_label': label,
            'action_key': action_key,
            'event_name': log.event.title if log.event else '—',
            'round_label': log.event.get_status_display() if log.event else '—',
            'description': (log.details or log.get_action_display())[:200],
        })
    return rows


def _serialize_scorer_activity_logs(log_entries, sheet_cache):
    rows = []
    for entry in log_entries:
        action_key = _parse_scorer_action_key(entry.change_message, entry.action_flag)
        label, _ = _SCORER_ACTION_KEYS.get(action_key, _SCORER_ACTION_KEYS['default'])
        uid = entry.user_id or 0
        name, initials, role = _activity_user_display(uid, 'Scorer')
        sheet = None
        oid = str(entry.object_id or '')
        if oid.isdigit():
            sheet = sheet_cache.get(int(oid))
        event_name = sheet.event.name if sheet else _event_name_from_log_repr(entry.object_repr)
        round_label = sheet.match.round_name if sheet and sheet.match else '—'
        description = _scorer_log_description(entry, sheet)
        ts = timezone.localtime(entry.action_time)
        rows.append({
            'date': ts.strftime('%b %d, %Y'),
            'time': ts.strftime('%I:%M %p'),
            'user_name': name,
            'user_initials': initials,
            'user_role': role,
            'avatar_tone': _activity_avatar_tone(uid),
            'action_label': label,
            'action_key': action_key,
            'event_name': event_name,
            'round_label': round_label,
            'description': description[:200],
        })
    return rows


def _event_name_from_log_repr(object_repr):
    text = (object_repr or '').strip()
    if not text:
        return '—'
    if ' Score Sheet' in text:
        return text.split(' Score Sheet')[0].strip()
    return text[:80]


def _scorer_log_description(entry, sheet):
    msg = (entry.change_message or '').strip()
    msg_lower = msg.lower()
    if sheet and sheet.match:
        team_a = sheet.match.team_a.name if sheet.match.team_a else 'TBD'
        team_b = sheet.match.team_b.name if sheet.match.team_b else 'TBD'
        match_no = sheet.match.match_number
        if 'verified' in msg_lower or 'locked' in msg_lower:
            return f'Finalized scores for Match #{match_no}: {team_a} vs {team_b}'
        if 'requested' in msg_lower or 'reverted' in msg_lower:
            return f'Reopened scores for Match #{match_no}: {team_a} vs {team_b}'
        if 'approved' in msg_lower:
            return f'Approved match result for Match #{match_no}: {team_a} vs {team_b}'
        if 'rejected' in msg_lower:
            return f'Rejected match result for Match #{match_no}: {team_a} vs {team_b}'
        return f'Match #{match_no}: {team_a} vs {team_b}'
    if 'verified' in msg_lower or 'locked' in msg_lower:
        return f'Finalized scores for {entry.object_repr or "score sheet"}'
    if 'requested' in msg_lower or 'reverted' in msg_lower:
        return f'Reopened scores for {entry.object_repr or "score sheet"}'
    return msg or entry.object_repr or '—'


def _tabulator_activity_log_users(tab, ss_ct, tabulator_ids):
    User = get_user_model()
    if tab == 'judge':
        ids = JudgeActivityLog.objects.values_list('judge_id', flat=True).distinct()
        return User.objects.filter(id__in=ids).order_by('username')
    return User.objects.filter(id__in=tabulator_ids).order_by('username')


def _tabulator_activity_log_events(tab, ss_ct, tabulator_ids):
    from events.models import ScoreSheet, JudgingEvent
    if tab == 'judge':
        return list(
            JudgingEvent.objects.filter(
                id__in=JudgeActivityLog.objects.exclude(event_id__isnull=True).values_list('event_id', flat=True)
            ).values_list('title', flat=True).distinct().order_by('title')
        )
    event_names = set(
        ScoreSheet.objects.filter(
            id__in=[
                int(oid) for oid in LogEntry.objects.filter(content_type=ss_ct, user_id__in=tabulator_ids)
                .values_list('object_id', flat=True) if str(oid).isdigit()
            ]
        ).values_list('event__name', flat=True)
    )
    return sorted(event_names)


def _tabulator_activity_log_rounds(ss_ct, tabulator_ids):
    from events.models import ScoreSheet
    sheet_ids = [
        int(oid) for oid in LogEntry.objects.filter(content_type=ss_ct, user_id__in=tabulator_ids)
        .values_list('object_id', flat=True) if str(oid).isdigit()
    ]
    if not sheet_ids:
        return []
    return sorted({
        name for name in ScoreSheet.objects.filter(id__in=sheet_ids)
        .values_list('match__round_name', flat=True) if name
    })


def _export_tabulator_activity_logs(qs, tab):
    if tab == 'judge':
        rows = _serialize_judge_activity_logs(list(qs[:500]))
        user_header = 'Judge'
    else:
        sheet_cache = _scoresheet_cache_for_log_entries(list(qs[:500]))
        rows = _serialize_scorer_activity_logs(list(qs[:500]), sheet_cache)
        user_header = 'Scorer'

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{tab}_activity_logs.csv"'
    writer = csv.writer(response)
    writer.writerow(['Date', 'Time', user_header, 'Action', 'Event', 'Round', 'Description'])
    for row in rows:
        writer.writerow([
            row['date'], row['time'], row['user_name'], row['action_label'],
            row['event_name'], row['round_label'], row['description'],
        ])
    return response


def _export_unified_activity_logs(rows):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="judge_scorer_activity_logs.csv"'
    writer = csv.writer(response)
    writer.writerow(['Date', 'Time', 'Name', 'Role', 'Action', 'Event', 'Round', 'Description'])
    for row in rows[:1000]:
        writer.writerow([
            row.get('date', ''),
            row.get('time', ''),
            row.get('user_name', ''),
            row.get('user_role', ''),
            row.get('action_label', ''),
            row.get('event_name', ''),
            row.get('round_label', ''),
            row.get('description', ''),
        ])
    return response


def _activity_row_local_date(sort_dt):
    if not sort_dt:
        return None
    if timezone.is_aware(sort_dt):
        return timezone.localtime(sort_dt).date()
    return sort_dt.date()


@login_required
@user_passes_test(lambda user: user.is_superuser, login_url='login')
def superadmin_dashboard(request):
    """Legacy template removed — use core.superadmin_views.superadmin_dashboard."""
    return redirect('superadmin_dashboard')


@login_required
@user_passes_test(lambda user: user.is_superuser, login_url='login')
def deactivate_admin(request, admin_id):
    """Deactivate an admin account"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method'}, status=400)

    try:
        User = get_user_model()
        admin_user = User.objects.get(id=admin_id, is_staff=True)

        # Prevent deactivating the current user
        if admin_user.id == request.user.id:
            return JsonResponse({'success': False, 'message': 'Cannot deactivate your own account'}, status=400)

        # Deactivate the user
        admin_user.is_active = False
        admin_user.save()

        # Log the activity
        from django.contrib.admin.models import LogEntry, CHANGE
        LogEntry.objects.create(
            user=request.user,
            content_type_id=ContentType.objects.get_for_model(User).id,
            object_id=admin_user.id,
            object_repr=str(admin_user),
            action_flag=CHANGE,
            change_message=f'Deactivated admin account'
        )

        return JsonResponse({'success': True, 'message': f'{admin_user.username} has been deactivated'})

    except User.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Admin not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@login_required
@user_passes_test(lambda user: user.is_superuser, login_url='login')
def activate_admin(request, admin_id):
    """Activate an admin account"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method'}, status=400)

    try:
        User = get_user_model()
        admin_user = User.objects.get(id=admin_id, is_staff=True)
        admin_user.is_active = True
        admin_user.save()

        LogEntry.objects.create(
            user=request.user,
            content_type_id=ContentType.objects.get_for_model(User).id,
            object_id=admin_user.id,
            object_repr=str(admin_user),
            action_flag=CHANGE,
            change_message='Activated admin account'
        )

        return JsonResponse({'success': True, 'message': f'{admin_user.username} has been activated'})

    except User.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Admin not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


def _parse_json_request(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return {}


def _apply_admin_groups_and_role(user, role, department):
    role = (role or 'Admin').strip()
    department = (department or '').strip()

    user.groups.clear()
    user.is_staff = True
    user.is_superuser = role == 'Super Admin'

    if user.is_superuser:
        return

    admin_group, _ = Group.objects.get_or_create(name='Admin')
    user.groups.add(admin_group)
    if department:
        department_group, _ = Group.objects.get_or_create(name=department)
        user.groups.add(department_group)


@login_required
@user_passes_test(lambda user: user.is_superuser, login_url='login')
def create_admin(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method'}, status=400)

    payload = _parse_json_request(request)
    username = (payload.get('username') or '').strip()
    email = (payload.get('email') or '').strip()
    password = payload.get('password') or ''
    role = (payload.get('role') or 'Admin').strip()
    department = (payload.get('department') or '').strip()
    is_active = bool(payload.get('is_active', True))

    if not username or not email or not password:
        return JsonResponse({'success': False, 'message': 'Username, email, and password are required.'}, status=400)

    User = get_user_model()
    if User.objects.filter(username__iexact=username).exists():
        return JsonResponse({'success': False, 'message': 'Username already exists.'}, status=400)
    if User.objects.filter(email__iexact=email).exists():
        return JsonResponse({'success': False, 'message': 'Email already exists.'}, status=400)

    try:
        user = User.objects.create_user(username=username, email=email, password=password)
        _apply_admin_groups_and_role(user, role, department)
        user.is_active = is_active
        user.save()
        return JsonResponse({'success': True, 'message': f'Admin account for {username} created successfully.'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@login_required
@user_passes_test(lambda user: user.is_superuser, login_url='login')
def update_admin(request, admin_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method'}, status=400)

    payload = _parse_json_request(request)
    username = (payload.get('username') or '').strip()
    email = (payload.get('email') or '').strip()
    password = payload.get('password') or ''
    role = (payload.get('role') or 'Admin').strip()
    department = (payload.get('department') or '').strip()
    is_active = bool(payload.get('is_active', True))

    if not username or not email:
        return JsonResponse({'success': False, 'message': 'Username and email are required.'}, status=400)

    User = get_user_model()
    try:
        user = User.objects.get(id=admin_id, is_staff=True)
    except User.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Admin not found.'}, status=404)

    if User.objects.filter(username__iexact=username).exclude(id=user.id).exists():
        return JsonResponse({'success': False, 'message': 'Username already exists.'}, status=400)
    if User.objects.filter(email__iexact=email).exclude(id=user.id).exists():
        return JsonResponse({'success': False, 'message': 'Email already exists.'}, status=400)

    try:
        user.username = username
        user.email = email
        user.is_active = is_active
        _apply_admin_groups_and_role(user, role, department)
        if password:
            user.set_password(password)
        user.save()
        return JsonResponse({'success': True, 'message': f'Admin account for {username} updated successfully.'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@login_required
@user_passes_test(lambda user: user.is_superuser, login_url='login')
def delete_admin(request, admin_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method'}, status=400)

    User = get_user_model()
    try:
        user = User.objects.get(id=admin_id, is_staff=True)
    except User.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Admin not found.'}, status=404)

    if user.id == request.user.id:
        return JsonResponse({'success': False, 'message': 'You cannot delete your own account.'}, status=400)

    username = user.username
    try:
        user.delete()
        return JsonResponse({'success': True, 'message': f'Admin account {username} deleted.'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@login_required
@user_passes_test(lambda user: user.is_superuser, login_url='login')
def superadmin_admins(request):
    """Legacy admin.html removed — User Management lives at superadmin_users."""
    return redirect('superadmin_users')



@login_required
@user_passes_test(lambda user: user.is_superuser, login_url='login')
def superadmin_reports(request):
    from django.contrib.admin.models import LogEntry, DELETION, CHANGE
    activity_log_count = LogEntry.objects.count()
    if activity_log_count >= 1000:
        activity_log_count_label = f'{activity_log_count // 1000}k+'
    else:
        activity_log_count_label = str(activity_log_count)

    department_names = list(
        Department.objects.filter(status='active').order_by('name').values_list('name', flat=True)
    )
    
    # Dynamic Event Loading — only real events from the database
    events = list(Event.objects.all().order_by('-event_date').values_list('name', flat=True))

    total_events_count = Event.objects.count() or 1
    critical_logs_count = LogEntry.objects.filter(action_flag=DELETION).count()
    reports_generated_count = LogEntry.objects.filter(change_message__icontains='generated').count()
    latest_report_log = LogEntry.objects.filter(change_message__icontains='generated').order_by('-action_time').first()
    last_export_label = latest_report_log.action_time.strftime('%b %d, %Y') if latest_report_log else 'Never'

    recent_report_rows = []
    recent_logs = LogEntry.objects.filter(change_message__icontains='generated').select_related('user').order_by('-action_time')[:5]
    for log in recent_logs:
        export_format = 'Excel' if 'EXCEL' in log.change_message.upper() else 'PDF'
        recent_report_rows.append({
            'name': log.object_repr or 'System Report',
            'created_by': log.user.username if log.user else 'System',
            'created_at': log.action_time.strftime('%b %d, %Y %I:%M %p'),
            'format': export_format,
            'status': 'Ready',
        })

    return render(request, 'reports.html', {
        'activity_log_count': activity_log_count,
        'activity_log_count_label': activity_log_count_label,
        'events': events,
        'department_names': department_names,
        'total_events_count': total_events_count,
        'critical_logs_count': critical_logs_count,
        'reports_generated_count': reports_generated_count,
        'last_export_label': last_export_label,
        'recent_report_rows': recent_report_rows,
    })


@login_required
@user_passes_test(lambda user: user.is_superuser, login_url='login')
def generate_superadmin_report(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    from django.contrib.admin.models import LogEntry, CHANGE
    from django.contrib.contenttypes.models import ContentType
    from django.contrib import messages
    from datetime import datetime

    event_name = request.POST.get('event', '').strip()
    report_type = request.POST.get('report_type', 'summary').strip()
    department = request.POST.get('department', 'all').strip()
    export_format = request.POST.get('format', 'pdf').strip().lower()

    start_date_str = request.POST.get('start_date', '').strip()
    end_date_str = request.POST.get('end_date', '').strip()

    start_date = None
    end_date = None
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    included_metrics = request.POST.getlist('metrics')

    system_wide_types = {
        'championship', 'department_rankings', 'user_activity',
        'judge_performance', 'faculty_activity',
    }

    if report_type in system_wide_types:
        from events.superadmin_service import championship_standings, export_leaderboard_csv, recent_activities
        from openpyxl import Workbook
        import io as _io

        LogEntry.objects.create(
            user=request.user,
            content_type_id=ContentType.objects.get_for_model(Event).id,
            object_id='0',
            object_repr=f'{report_type.replace("_", " ").title()} Report'[:200],
            action_flag=CHANGE,
            change_message=f'Generated {export_format.upper()} report.',
        )

        if report_type in ('championship', 'department_rankings'):
            rows = championship_standings()
            if export_format == 'excel':
                wb = Workbook()
                ws = wb.active
                ws.title = 'Standings'
                ws.append(['Rank', 'Department', 'Total', 'Major', 'Minor'])
                for r in rows:
                    ws.append([r['rank'], r['department'], r['total_points'], r['major_points'], r['minor_points']])
                buf = _io.BytesIO()
                wb.save(buf)
                response = HttpResponse(
                    buf.getvalue(),
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                )
                response['Content-Disposition'] = f'attachment; filename="{report_type}_{datetime.now().strftime("%Y%m%d")}.xlsx"'
                return response
            content = export_leaderboard_csv(rows)
            response = HttpResponse(content, content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="{report_type}_{datetime.now().strftime("%Y%m%d")}.csv"'
            return response

        # Activity-style reports as CSV
        activities = recent_activities(200)
        buf = _io.StringIO()
        import csv as _csv
        writer = _csv.writer(buf)
        writer.writerow(['Time', 'User', 'Role', 'Title', 'Message', 'Module'])
        for a in activities:
            writer.writerow([a.get('time'), a.get('user'), a.get('role'), a.get('title'), a.get('message'), a.get('module')])
        response = HttpResponse(buf.getvalue(), content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{report_type}_{datetime.now().strftime("%Y%m%d")}.csv"'
        return response

    if not event_name or event_name == 'No events available':
        messages.error(request, 'Please select a valid event before generating a report.')
        return redirect('superadmin_reports')

    event_obj = Event.objects.filter(name=event_name).first()
    event_content_type = ContentType.objects.get_for_model(Event)

    # Log dynamic django administrative action to record export in history - autoreload
    LogEntry.objects.create(
        user=request.user,
        content_type_id=event_content_type.id,
        object_id=str(event_obj.id) if event_obj else '1',
        object_repr=f"{event_name} {report_type.title()} Report"[:200],
        action_flag=CHANGE,
        change_message=f"Generated {export_format.upper()} report."
    )

    if export_format == 'excel':
        from core.reports_generator import generate_excel_report
        excel_stream = generate_excel_report(event_name, department, start_date, end_date, included_metrics)
        filename = f"{event_name.replace(' ', '_')}_{report_type}_Report_{datetime.now().strftime('%Y%m%d')}.xlsx"
        response = HttpResponse(
            excel_stream.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    else:
        from core.reports_generator import generate_pdf_report
        pdf_stream = generate_pdf_report(event_name, department, start_date, end_date, included_metrics)
        filename = f"{event_name.replace(' ', '_')}_{report_type}_Report_{datetime.now().strftime('%Y%m%d')}.pdf"
        response = HttpResponse(pdf_stream.read(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


@login_required
@user_passes_test(lambda user: user.is_superuser, login_url='login')
def superadmin_activity_logs(request):
    selected_user = request.GET.get('user', 'all').strip()
    selected_type = request.GET.get('type', 'all').strip().lower()
    search_query = request.GET.get('q', '').strip()
    preset = request.GET.get('preset', 'all').strip().lower()
    time_display = request.GET.get('time_display', 'relative').strip().lower()
    export_format = request.GET.get('export', '').strip().lower()
    page_number = request.GET.get('page', 1)

    logs_qs = LogEntry.objects.select_related('user').order_by('-action_time')

    if selected_user != 'all' and selected_user.isdigit():
        logs_qs = logs_qs.filter(user_id=int(selected_user))

    action_map = {
        'add': ADDITION,
        'change': CHANGE,
        'delete': DELETION,
    }
    if selected_type in action_map:
        logs_qs = logs_qs.filter(action_flag=action_map[selected_type])

    now_local = timezone.localtime()
    if preset == 'today':
        logs_qs = logs_qs.filter(action_time__date=now_local.date())
    elif preset == 'week':
        week_start = now_local - timedelta(days=7)
        logs_qs = logs_qs.filter(action_time__gte=week_start)
    elif preset == 'security':
        logs_qs = logs_qs.filter(action_flag=DELETION)

    if search_query:
        logs_qs = logs_qs.filter(
            Q(user__username__icontains=search_query)
            | Q(object_repr__icontains=search_query)
            | Q(change_message__icontains=search_query)
        )

    logs_qs = logs_qs.select_related('user').order_by('-action_time')

    if export_format == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="activity_logs.csv"'
        writer = csv.writer(response)
        writer.writerow(['Time', 'User', 'Type', 'Object', 'Message'])
        for entry in logs_qs[:500]:
            writer.writerow([
                entry.action_time.strftime('%Y-%m-%d %H:%M:%S'),
                entry.user.username if entry.user else 'System',
                {ADDITION: 'Add', CHANGE: 'Change', DELETION: 'Delete'}.get(entry.action_flag, 'Update'),
                entry.object_repr,
                entry.change_message or '',
            ])
        return response

    users = (
        get_user_model()
        .objects.filter(id__in=LogEntry.objects.values_list('user_id', flat=True))
        .order_by('username')
    )

    filtered_count = logs_qs.count()
    critical_logs_count = logs_qs.filter(action_flag=DELETION).count()
    today_logs_count = logs_qs.filter(action_time__date=timezone.localdate()).count()
    latest_entry = logs_qs.first()

    # ── Smart title + message derivation ───────────────────────
    def _derive_log_entry(entry):
        """Return (title, clean_message, row_class, badge, status_pill) from a LogEntry."""
        msg_raw = (entry.change_message or '').strip()
        obj = entry.object_repr or ''
        username = entry.user.username if entry.user else 'System'

        # Strip internal markers
        msg_clean = msg_raw.replace('[TAB_REPORT] ', '').strip()
        if msg_clean and msg_clean[0].islower():
            msg_clean = msg_clean[0].upper() + msg_clean[1:]

        flag = entry.action_flag

        # ── Determine title from content ──────────────────────
        msg_lower = msg_clean.lower()
        obj_lower = obj.lower()

        if flag == ADDITION:
            if 'user' in obj_lower or 'account' in obj_lower:
                title = 'New Account Created'
            elif 'department' in obj_lower:
                title = 'Department Created'
            elif 'event' in obj_lower:
                title = 'Event Created'
            else:
                title = f'New Record Added'
            row_class, badge, pill = 'info', 'User Action', 'info'

        elif flag == DELETION:
            if 'user' in obj_lower or 'account' in obj_lower:
                title = 'Account Deleted'
            elif 'department' in obj_lower:
                title = 'Department Deleted'
            elif 'score' in obj_lower or 'sheet' in obj_lower:
                title = 'Score Sheet Deleted'
            elif 'event' in obj_lower:
                title = 'Event Deleted'
            else:
                title = 'Record Deleted'
            row_class, badge, pill = 'danger', 'Security', 'critical'

        else:  # CHANGE
            if 'verified and locked' in msg_lower:
                title = 'Score Sheet Verified & Locked'
                row_class, badge, pill = 'info', 'Tabulation', 'info'
            elif 'requested changes' in msg_lower or 'reverted to pending' in msg_lower:
                title = 'Score Sheet Sent Back for Changes'
                row_class, badge, pill = 'warning', 'Tabulation', 'warning'
            elif 'generated' in msg_lower and 'report' in msg_lower:
                title = 'Report Generated'
                row_class, badge, pill = 'info', 'Report', 'info'
            elif 'deactivated' in msg_lower:
                title = 'Account Deactivated'
                row_class, badge, pill = 'warning', 'Security', 'warning'
            elif 'activated' in msg_lower:
                title = 'Account Activated'
                row_class, badge, pill = 'info', 'Security', 'info'
            elif 'password' in msg_lower:
                title = 'Password Changed'
                row_class, badge, pill = 'warning', 'Security', 'warning'
            elif 'login' in msg_lower:
                title = 'User Login'
                row_class, badge, pill = 'info', 'Auth', 'info'
            elif 'logout' in msg_lower:
                title = 'User Logout'
                row_class, badge, pill = 'info', 'Auth', 'info'
            elif 'score' in msg_lower or 'encoded' in msg_lower:
                title = 'Score Encoded'
                row_class, badge, pill = 'info', 'Tabulation', 'info'
            elif 'department' in obj_lower:
                title = 'Department Updated'
                row_class, badge, pill = 'warning', 'Policy Update', 'warning'
            elif 'event' in obj_lower:
                title = 'Event Updated'
                row_class, badge, pill = 'warning', 'Policy Update', 'warning'
            elif 'user' in obj_lower or 'account' in obj_lower:
                title = 'Account Updated'
                row_class, badge, pill = 'warning', 'Policy Update', 'warning'
            else:
                title = 'Record Updated'
                row_class, badge, pill = 'warning', 'Policy Update', 'warning'

        # Build display message: "actor did X on object"
        if msg_clean:
            display_msg = msg_clean
        else:
            display_msg = f'{obj} was modified.'

        return title, display_msg, row_class, badge, pill

    # ── Build timeline ──────────────────────────────────────────
    grouped = {}
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)

    paginator = Paginator(list(logs_qs[:500]), 20)
    page_obj = paginator.get_page(page_number)
    start_index = page_obj.start_index() if paginator.count else 0
    end_index = page_obj.end_index() if paginator.count else 0

    for entry in page_obj.object_list:
        local_action_time = timezone.localtime(entry.action_time)
        entry_date = local_action_time.date()

        if entry_date == today:
            day_label = 'Today'
        elif entry_date == yesterday:
            day_label = 'Yesterday'
        else:
            day_label = entry_date.strftime('%b %d, %Y')

        delta = now_local - local_action_time
        if delta.total_seconds() < 60:
            relative_time = 'Just now'
        elif delta.total_seconds() < 3600:
            relative_time = f'{int(delta.total_seconds() // 60)}m ago'
        elif delta.days == 0:
            relative_time = f'{int(delta.total_seconds() // 3600)}h ago'
        else:
            relative_time = f'{delta.days}d ago'

        title, display_msg, row_class, badge, pill = _derive_log_entry(entry)

        grouped.setdefault(day_label, []).append({
            'time': local_action_time.strftime('%I:%M').lstrip('0') or '12:00',
            'period': local_action_time.strftime('%p'),
            'exact_time': local_action_time.strftime('%b %d, %Y %I:%M %p'),
            'relative_time': relative_time,
            'title': title,
            'message': display_msg,
            'actor': entry.user.username if entry.user else 'System',
            'row_class': row_class,
            'badge': badge,
            'status': pill,
        })

    timeline_rows = [{'label': label, 'entries': entries} for label, entries in grouped.items()]

    return render(request, 'activitylogs.html', {
        'logs_by_day': timeline_rows,
        'log_users': users,
        'selected_user': selected_user,
        'selected_type': selected_type,
        'search_query': search_query,
        'selected_preset': preset,
        'time_display': time_display,
        'filtered_count': filtered_count,
        'critical_count': critical_logs_count,
        'today_count': today_logs_count,
        'latest_activity': latest_entry.action_time.strftime('%b %d, %Y %I:%M %p') if latest_entry else 'N/A',
        'page_obj': page_obj,
        'start_index': start_index,
        'end_index': end_index,
    })


def logout_view(request):
    try:
        if request.user.is_authenticated:
            from events.audit_service import record_audit
            record_audit(
                user=request.user,
                action='Logged Out',
                description=f'{request.user.get_full_name() or request.user.username} logged out.',
                module='Authentication',
                request=request,
            )
    except Exception:
        pass
    logout(request)
    return redirect('login')

@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_generate_bracket(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method'}, status=400)
        
    try:
        import json
        from events.models import Event, Department, BracketTeam, BracketMatch
        from events.bracket_generator import (
            generate_single_elimination, generate_double_elimination,
            generate_round_robin, generate_best_of_series, order_teams_for_draw,
        )
        from events.bracket_progression import link_bracket_advancement

        data = json.loads(request.body)
        event_data = data.get('event')
        event_id = event_data.get('event_id') if isinstance(event_data, dict) else event_data
        format_type = event_data.get('tournament_format') if isinstance(event_data, dict) else data.get('tournament_format')
        teams_data = data.get('teams', [])

        # Draw method (Random Draw vs Seeded Draw) drives first-round placement.
        seeding = data.get('seeding') or {}
        draw_method = (seeding.get('method') or data.get('draw_method') or 'seeded').lower()
        if 'random' in draw_method:
            draw_method = 'random'
        else:
            draw_method = 'seeded'

        event = get_object_or_404(Event, id=event_id) if event_id else None
        if not event or not format_type:
            return JsonResponse({'success': False, 'message': 'Event and format are required.'}, status=400)

        force_reset = bool(data.get('force_reset'))
        reset_reason = str(data.get('reason') or '').strip()
        confirmed_qs = BracketMatch.objects.filter(
            event=event,
            is_automatic_advance=False,
            status__in=[BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT],
        ).filter(Q(winner__isnull=False) | Q(score_a__gt='') | Q(score_b__gt=''))
        confirmed_count = confirmed_qs.count()
        if confirmed_count and not force_reset:
            return JsonResponse({
                'success': False,
                'blocked': True,
                'message': (
                    'This bracket contains confirmed results or dependent matches and cannot be '
                    'regenerated normally. Use a controlled reset if you need to rebuild it.'
                ),
            }, status=409)
        if force_reset:
            if not request.user.is_staff:
                return JsonResponse({'success': False, 'message': 'Permission denied.'}, status=403)
            if confirmed_count and not reset_reason:
                return JsonResponse({
                    'success': False,
                    'message': 'A reason is required when confirmed results exist.',
                }, status=400)
            try:
                from events.audit_service import record_audit
                previous = list(
                    BracketMatch.objects.filter(event=event).values(
                        'id', 'match_number', 'round_name', 'status', 'score_a', 'score_b', 'is_automatic_advance'
                    )[:200]
                )
                record_audit(
                    user=request.user,
                    action='Updated Event',
                    description=(
                        f'Controlled bracket reset for "{event.name}". '
                        f'Reason: {reset_reason or "n/a"}. Removed {BracketMatch.objects.filter(event=event).count()} match rows.'
                    ),
                    module='Match-Based Event',
                    event_name=event.name,
                    request=request,
                    metadata={
                        'action': 'bracket_reset',
                        'reason': reset_reason,
                        'confirmed_results': confirmed_count,
                        'previous_matches': previous,
                    },
                )
            except Exception:
                pass

        # Auto-fill teams from the event's existing BracketTeams if none supplied.
        existing_teams = list(BracketTeam.objects.filter(event=event).order_by('seed'))
        if not teams_data and not existing_teams:
            return JsonResponse({'success': False, 'message': 'No teams found. Add teams or select an event that has teams.'}, status=400)

        # Check if matches already exist for this event
        if BracketMatch.objects.filter(event=event).exists():
             BracketMatch.objects.filter(event=event).delete()
        # Only wipe & recreate teams when the client sent a teams list; otherwise reuse existing.
        if teams_data:
            BracketTeam.objects.filter(event=event).delete()

        # Create / collect Teams
        bracket_teams = []
        team_id_to_obj = {}
        if teams_data:
            for index, team_data in enumerate(teams_data):
                department = resolve_department(team_data.get('department'))
                name = team_data.get('team_name') or team_data.get('name') or f'Team {index+1}'
                seed = team_data.get('seed_number', index + 1)
                members_raw = team_data.get('members', '')
                members_str, _ = parse_team_members(members_raw)

                team = BracketTeam.objects.create(
                    event=event,
                    name=name,
                    department=department,
                    members=members_str,
                    seed=seed
                )
                bracket_teams.append(team)
                team_id_to_obj[index] = team
        else:
            bracket_teams = existing_teams

        # If admin supplied a bracket_layout, use it exactly as-is.
        # Otherwise fall back to auto-generation by format.
        bracket_layout = data.get('bracket_layout')
        if bracket_layout and isinstance(bracket_layout, list) and len(bracket_layout) > 0:
            # Map JS team_id -> BracketTeam by matching team name + seed
            name_seed_map = {(t.name, t.seed): t for t in bracket_teams}

            def resolve_team(slot):
                """Return a BracketTeam or None for a layout slot."""
                if not slot or not isinstance(slot, dict):
                    return None
                stype = slot.get('type')
                if stype == 'team':
                    name = slot.get('name')
                    seed = slot.get('seed')
                    return name_seed_map.get((name, seed))
                # custom / bye / tbd → no DB team
                return None

            match_no = 1
            for round_block in bracket_layout:
                round_name = round_block.get('round_name') or 'Round'
                for match in round_block.get('matches', []):
                    a_team = resolve_team(match.get('team_a'))
                    b_team = resolve_team(match.get('team_b'))
                    BracketMatch.objects.create(
                        event=event,
                        match_number=match_no,
                        round_name=round_name,
                        team_a=a_team,
                        team_b=b_team,
                        status=BracketMatch.STATUS_PENDING,
                    )
                    match_no += 1
            # Admin layouts ship without advancement links — infer them so
            # winners auto-advance when results are approved later.
            link_bracket_advancement(event)
        else:
            # Generate matches based on format (auto-mode). Draw method orders
            # the teams for first-round placement (Random Draw vs Seeded Draw).
            ordered_teams = order_teams_for_draw(bracket_teams, draw_method)
            fmt = (format_type or '').strip()
            if fmt == 'Single Elimination':
                generate_single_elimination(event, ordered_teams)
            elif fmt == 'Double Elimination':
                generate_double_elimination(event, ordered_teams)
            elif fmt == 'Round Robin':
                generate_round_robin(event, ordered_teams)
            elif fmt == 'Best of 3':
                generate_best_of_series(event, ordered_teams, best_of=3)
            elif fmt == 'Best of 5':
                generate_best_of_series(event, ordered_teams, best_of=5)
            else:
                return JsonResponse({'success': False, 'message': 'Unsupported tournament format.'}, status=400)

        # Persist the chosen tournament type and lock the bracket against edits.
        event.tournament_type = format_type
        event.bracket_locked = True
        event.save(update_fields=['tournament_type', 'bracket_locked'])

        sync_event_to_mobile(event)
        return JsonResponse({
            'success': True,
            'message': 'Bracket generated, locked, and synced to the judge mobile app.',
            'locked': True,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)

@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_view_bracket(request, event_id):
    if request.method != 'GET':
        return JsonResponse({'success': False, 'message': 'Invalid method'}, status=400)
    try:
        from events.models import Event, BracketMatch, BracketTeam
        event = get_object_or_404(Event, id=event_id)
        matches_qs = BracketMatch.objects.filter(event=event).select_related(
            'team_a', 'team_b', 'winner', 'loser',
        ).order_by('match_number')

        matches_data = []
        for m in matches_qs:
            remarks = m.remarks or ''
            best_of = None
            if 'Best of 3' in remarks:
                best_of = 3
            elif 'Best of 5' in remarks:
                best_of = 5
            matches_data.append({
                'id': m.id,
                'match_number': m.match_number,
                'round_name': m.round_name,
                'team_a': m.team_a.name if m.team_a else 'TBD',
                'team_b': m.team_b.name if m.team_b else 'TBD',
                'team_a_id': m.team_a.id if m.team_a else None,
                'team_b_id': m.team_b.id if m.team_b else None,
                'winner': m.winner.name if m.winner else None,
                'winner_id': m.winner.id if m.winner else None,
                'score_a': m.score_a or '',
                'score_b': m.score_b or '',
                'status': m.status,
                'status_label': m.get_status_display(),
                'remarks': remarks,
                'best_of': best_of,
                'next_match_winner_id': m.next_match_winner_id,
            })

        teams_data = list(
            BracketTeam.objects.filter(event=event)
            .select_related('department')
            .order_by('seed')
            .values('id', 'name', 'seed', 'loss_count', 'members', 'department__name')
        )
        for t in teams_data:
            t['department_name'] = t.pop('department__name') or '—'

        actual_qs = matches_qs.filter(is_automatic_advance=False)
        total = actual_qs.count()
        completed = actual_qs.filter(
            status__in=[BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT]
        ).count()
        ongoing = actual_qs.filter(status=BracketMatch.STATUS_ONGOING).count()
        bracket_meta = get_bracket_status_meta(
            total > 0, total, completed, ongoing, locked=bool(event.bracket_locked)
        )

        rounds = []
        seen = set()
        for m in matches_qs:
            if m.round_name not in seen:
                seen.add(m.round_name)
                rounds.append(m.round_name)

        recent_results = []
        for m in matches_qs.filter(status=BracketMatch.STATUS_COMPLETED).order_by('-updated_at')[:6]:
            recent_results.append({
                'match_number': m.match_number,
                'round_name': m.round_name,
                'team_a': m.team_a.name if m.team_a else 'TBD',
                'team_b': m.team_b.name if m.team_b else 'TBD',
                'score_a': m.score_a or '—',
                'score_b': m.score_b or '—',
                'winner': m.winner.name if m.winner else '—',
            })

        judge_names = ', '.join(
            (f"{j.first_name} {j.last_name}".strip() or j.username)
            for j in event.assigned_judges.all()
        ) or 'None assigned'

        tt = (event.tournament_type or '').strip().lower()
        if tt == 'best_of_3':
            match_format = 3
        elif tt == 'best_of_5':
            match_format = 5
        else:
            match_format = next((m['best_of'] for m in matches_data if m.get('best_of')), 1)

        return JsonResponse({
            'success': True,
            'event': {
                'id': event.id,
                'name': event.name,
                'category': event.category,
                'division': event.division or '—',
                'venue': event.venue,
                'schedule_label': event.schedule_label,
                'event_time': event.event_time_str,
                'tournament_type': event.tournament_type or '',
                'tournament_type_label': _tournament_type_label(event.tournament_type),
                'scoring_method': event.scoring_method or 'match',
                'assigned_judges': judge_names,
                'bracket_locked': event.bracket_locked,
            },
            'teams': teams_data,
            'rounds': rounds,
            'matches': matches_data,
            'format': _tournament_type_label(event.tournament_type) or 'Single Elimination',
            'match_format': match_format,
            'best_of': match_format,
            'locked': event.bracket_locked,
            'progress': bracket_meta,
            'recent_results': recent_results,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)

@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_delete_bracket(request, event_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid method'}, status=400)
    try:
        from events.models import Event, BracketMatch, BracketTeam
        event = get_object_or_404(Event, id=event_id)
        BracketMatch.objects.filter(event=event).delete()
        BracketTeam.objects.filter(event=event).delete()
        return JsonResponse({'success': True, 'message': 'Bracket deleted successfully.'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)

@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_get_bracket_teams(request, event_id):
    if request.method != 'GET':
        return JsonResponse({'success': False, 'message': 'Invalid method'}, status=400)
    try:
        from events.models import Event, BracketTeam
        event = get_object_or_404(Event, id=event_id)
        teams = BracketTeam.objects.filter(event=event).select_related('department').order_by('seed')
        teams_data = []
        for t in teams:
            members_str, member_count = parse_team_members(t.members)
            teams_data.append({
                'id': t.id,
                'name': t.name,
                'department_id': t.department_id or '',
                'department_name': t.department.name if t.department else '',
                'seed': t.seed,
                'members': members_str,
                'member_count': member_count,
            })
        return JsonResponse({'success': True, 'teams': teams_data})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)
