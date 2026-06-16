from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth.models import Group
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.shortcuts import redirect, render, get_object_or_404
from django.utils import timezone
from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.contrib.contenttypes.models import ContentType
import json
import csv
import re
import random
import string
from datetime import datetime, timedelta

from events.models import (
    Event, Department,
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


def serialize_event_teams(event):
    """Serialize BracketTeam rows for admin event dialogs."""
    teams = []
    for t in event.bracket_teams.select_related('department').order_by('seed', 'name'):
        members_str, _ = parse_team_members(t.members)
        teams.append({
            'name': t.name,
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
    return [
        {
            'name': c.name,
            'number': c.number,
            'department': '',
            'description': c.description or '',
        }
        for c in Candidate.objects.filter(event=event.judging_event).order_by('number')
    ]


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


def get_bracket_status_meta(has_bracket, total=0, completed=0, ongoing=0):
    """Bracket lifecycle: Not Generated → Generated → In Progress → Completed."""
    if not has_bracket or total == 0:
        return {
            'bracket_status': 'not_generated',
            'bracket_status_label': 'Not Generated',
            'match_completed': 0,
            'match_total': 0,
            'match_progress_pct': 0,
        }
    if completed >= total:
        status_key, status_label = 'completed', 'Completed'
    elif completed > 0 or ongoing > 0:
        status_key, status_label = 'in_progress', 'In Progress'
    else:
        status_key, status_label = 'generated', 'Generated'
    pct = int(round(completed / total * 100)) if total else 0
    return {
        'bracket_status': status_key,
        'bracket_status_label': status_label,
        'match_completed': completed,
        'match_total': total,
        'match_progress_pct': pct,
    }


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


def get_assignment_account_rows():
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
        .prefetch_related('groups')
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

        # Match user's groups against department names to find their dept code
        dept_code = ''
        for group in user.groups.all():
            gname = group.name.lower()
            if gname in dept_lookup:
                dept_code = dept_lookup[gname]
                break

        full_name = user.get_full_name().strip() or user.username
        account_rows.append({
            'user': user,
            'full_name': full_name,
            'role': role,
            'department_code': dept_code,
            'status': 'Active' if user.is_active else 'Inactive',
            'status_key': 'active' if user.is_active else 'inactive',
            'contact': 'Not set',
            'initials': ''.join(part[:1] for part in full_name.split()[:2]).upper() or user.username[:2].upper(),
        })

    return account_rows


def get_assignment_account_context(request):
    status_filter = request.GET.get('status', 'all').strip().lower()
    role_filter = request.GET.get('role', 'all').strip().lower()
    dept_filter = request.GET.get('dept', 'all').strip()
    search_query = request.GET.get('q', '').strip()

    account_rows = get_assignment_account_rows()
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
        ]

    if role_filter != 'all':
        account_rows = [row for row in account_rows if row['role'].lower() == role_filter]

    if status_filter != 'all':
        account_rows = [row for row in account_rows if row['status_key'] == status_filter]

    if dept_filter != 'all':
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
    'tabulator': 'tabulator_dashboard',
}

_ROLE_LABELS = {
    'super-admin': 'Super Admin',
    'admin': 'Admin',
    'tabulator': 'Tabulator',
    'judge': 'Judge',
}


@never_cache
@ensure_csrf_cookie
def login_view(request):
    context = {}

    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')

        context['email'] = email

        user = authenticate_email(request, email, password)
        if user is None:
            context['error'] = 'Invalid email or password.'
            return render(request, 'login.html', context)

        role = _detect_user_role(user)
        if role is None:
            context['error'] = 'Your account has no assigned role. Contact an administrator.'
            return render(request, 'login.html', context)

        login(request, user)
        request.session['login_role'] = role

        redirect_name = _ROLE_REDIRECTS.get(role)
        if redirect_name:
            return redirect(redirect_name)

        context['success'] = (
            f'Logged in as {_ROLE_LABELS[role]}. '
            'Open the EventTab judge mobile app and sign in with this email and password.'
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
    return render(request, 'admindash/manageacc.html', get_assignment_account_context(request))


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_analytics(request):
    """Analytics overview page (dashboard with deeper metrics)."""
    from datetime import timedelta as _td
    from collections import OrderedDict

    all_events = list(Event.objects.all())
    today = timezone.localdate()
    status_counts = get_event_status_counts(all_events, today)

    total_events = len(all_events)
    total_participants = sum(int(ev.max_participants or 0) for ev in all_events)
    total_teams = sum(int(ev.num_teams or 0) for ev in all_events)
    completed_events = status_counts['completed']
    participation_rate = int(round(completed_events / total_events * 100)) if total_events else 0

    # Top categories by participants
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

    # Participation by Department (sum of participants from events whose
    # department code matches; falls back to event count if none match)
    dept_rows = []
    departments = Department.objects.all()
    for dept in departments:
        evs = [e for e in all_events if (e.department or '').lower() == dept.name.lower() or (e.department or '').lower() == dept.code.lower()]
        participants = sum(int(e.max_participants or 0) for e in evs)
        teams = sum(int(e.num_teams or 0) for e in evs)
        dept_rows.append({
            'name': dept.name[:14],
            'participants': participants,
            'teams': teams,
        })
    dept_rows.sort(key=lambda x: x['participants'], reverse=True)
    dept_rows = dept_rows[:6]

    # Daily series for "Events & Participants Over Time" — last 31 days
    daily_labels = []
    daily_events = []
    daily_participants = []
    for i in range(30, -1, -1):
        day = today - _td(days=i)
        evs = [e for e in all_events if e.event_date == day]
        if i % 5 == 0 or i == 0:
            daily_labels.append(day.strftime('%b %d'))
        else:
            daily_labels.append('')
        daily_events.append(len(evs))
        daily_participants.append(sum(int(e.max_participants or 0) for e in evs))

    # Event Completion Rate (by month, last 6 months)
    months = []
    cursor = today.replace(day=1)
    for _ in range(6):
        months.append(cursor)
        prev_month_end = cursor - _td(days=1)
        cursor = prev_month_end.replace(day=1)
    months.reverse()

    completion_labels = []
    completion_rates = []
    monthly_total_labels = []
    monthly_avg_participants = []
    for m_start in months:
        next_month = (m_start.replace(day=28) + _td(days=4)).replace(day=1)
        m_end = next_month - _td(days=1)
        in_month = [e for e in all_events if e.event_date and m_start <= e.event_date <= m_end]
        total_in = len(in_month)
        completed_in = sum(1 for e in in_month if get_event_status_meta(e, today)['status'] == 'completed')
        rate = int(round(completed_in / total_in * 100)) if total_in else 0
        completion_labels.append(m_start.strftime('%b'))
        completion_rates.append(rate)

        avg_p = int(round(sum(int(e.max_participants or 0) for e in in_month) / total_in)) if total_in else 0
        monthly_total_labels.append(m_start.strftime('%b'))
        monthly_avg_participants.append(avg_p)

    # Status counts for doughnut
    status_doughnut = {
        'ongoing': status_counts['ongoing'],
        'upcoming': status_counts['upcoming'],
        'completed': status_counts['completed'],
        'cancelled': status_counts['inactive'],
    }

    # Event performance table (top 5 by participants)
    perf_rows = []
    for ev in sorted(all_events, key=lambda e: int(e.max_participants or 0), reverse=True)[:5]:
        sm = get_event_status_meta(ev, today)
        completion = 100 if sm['status'] == 'completed' else (75 if sm['status'] == 'ongoing' else 0)
        perf_rows.append({
            'name': ev.name,
            'category': ev.category,
            'status': sm['status'],
            'status_label': sm['status_label'],
            'participants': ev.max_participants or 0,
            'teams': ev.num_teams or 0,
            'completion': completion,
        })

    # Participant Engagement metrics (computed from current data)
    User = get_user_model()
    new_participants = User.objects.filter(date_joined__gte=today - _td(days=30)).count()
    returning_participants = max(total_participants - new_participants, 0)
    avg_events_per_participant = round(total_events / total_participants, 2) if total_participants else 0
    avg_minutes_per_event = 144  # ~2.4 hours; static placeholder until time-tracking exists

    return render(request, 'admindash/analytics.html', {
        'total_events': total_events,
        'total_participants': total_participants,
        'total_teams': total_teams,
        'completed_events': completed_events,
        'participation_rate': participation_rate,
        'top_categories': top_categories,
        'dept_rows': dept_rows,
        'daily_labels_json': json.dumps(daily_labels),
        'daily_events_json': json.dumps(daily_events),
        'daily_participants_json': json.dumps(daily_participants),
        'completion_labels_json': json.dumps(completion_labels),
        'completion_rates_json': json.dumps(completion_rates),
        'monthly_avg_labels_json': json.dumps(monthly_total_labels),
        'monthly_avg_participants_json': json.dumps(monthly_avg_participants),
        'status_doughnut_json': json.dumps(status_doughnut),
        'dept_rows_json': json.dumps(dept_rows),
        'perf_rows': perf_rows,
        'new_participants': new_participants,
        'returning_participants': returning_participants,
        'avg_events_per_participant': avg_events_per_participant,
        'avg_minutes_per_event': avg_minutes_per_event,
        'avg_hours_per_event': round(avg_minutes_per_event / 60, 1),
        'date_range_label': today.replace(day=1).strftime('%b 1') + ' – ' + today.strftime('%b %d, %Y'),
    })


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_generate_report(request):
    """Generate Report page — admin-level, uses existing report generator."""
    from datetime import timedelta as _td
    from django.contrib.admin.models import LogEntry, CHANGE
    from django.contrib.contenttypes.models import ContentType

    all_events = list(Event.objects.all().order_by('-event_date'))
    today = timezone.localdate()
    status_counts = get_event_status_counts(all_events, today)

    # Summary metrics for the preview panel
    total_events = len(all_events)
    total_participants = sum(int(ev.max_participants or 0) for ev in all_events)
    total_teams = sum(int(ev.num_teams or 0) for ev in all_events)
    total_matches = BracketMatch.objects.count()
    completed_events = status_counts['completed']
    completion_rate = int(round(completed_events / total_events * 100)) if total_events else 0

    # Category breakdown
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

    # Event summary table (top 5)
    event_summary = []
    for ev in all_events[:5]:
        sm = get_event_status_meta(ev, today)
        event_summary.append({
            'name': ev.name,
            'category': ev.category,
            'status': sm['status'],
            'status_label': sm['status_label'],
            'participants': ev.max_participants or 0,
            'teams': ev.num_teams or 0,
            'matches': BracketMatch.objects.filter(event=ev).count(),
        })

    # Weekly events chart (last 8 weeks)
    chart_labels = []
    chart_events = []
    for i in range(7, -1, -1):
        week_end = today - _td(days=i * 7)
        week_start = week_end - _td(days=6)
        evs_in_week = [e for e in all_events if e.event_date and week_start <= e.event_date <= week_end]
        chart_labels.append(week_end.strftime('%b %d'))
        chart_events.append(len(evs_in_week))

    # Departments and categories for filter dropdowns
    departments = list(Department.objects.values_list('name', flat=True).order_by('name'))
    category_names = sorted({ev.category for ev in all_events if ev.category})
    division_names = sorted({ev.division for ev in all_events if ev.division})

    # Handle POST — generate & export
    if request.method == 'POST':
        report_type = request.POST.get('report_type', 'event_summary').strip()
        export_format = request.POST.get('export_format', 'pdf').strip().lower()
        event_filter = request.POST.get('event_filter', '').strip()
        dept_filter = request.POST.get('dept_filter', '').strip()
        cat_filter = request.POST.get('cat_filter', '').strip()
        status_filter = request.POST.get('status_filter', '').strip()
        round_filter = request.POST.get('round_filter', '').strip()
        div_filter = request.POST.get('div_filter', '').strip()
        start_date_str = request.POST.get('start_date', '').strip()
        end_date_str = request.POST.get('end_date', '').strip()
        included_metrics = request.POST.getlist('metrics')

        # Metadata + filters passed through to the report generators.
        # Note: the UI status filter is event-level (upcoming/ongoing/...), which
        # does not map to match/scoresheet status, so it is not forwarded to the
        # per-match queries to avoid filtering everything out.
        report_info = {
            'generated_by': request.user.get_full_name() or request.user.username,
            'round_filter': round_filter or None,
            'status_filter': None,
            'category_filter': cat_filter or None,
            'division_filter': div_filter or None,
            'remarks': request.POST.get('remarks', '').strip() or None,
        }

        # Log the export action
        event_ct = ContentType.objects.get_for_model(Event)
        LogEntry.objects.create(
            user=request.user,
            content_type_id=event_ct.id,
            object_id='0',
            object_repr=f'{report_type.replace("_", " ").title()} Report'[:200],
            action_flag=CHANGE,
            change_message=f'Generated {export_format.upper()} report.',
        )

        # Delegate to existing report generator
        from datetime import datetime as _dt
        start_date = None
        end_date = None
        if start_date_str:
            try:
                start_date = _dt.strptime(start_date_str, '%Y-%m-%d').date()
            except ValueError:
                pass
        if end_date_str:
            try:
                end_date = _dt.strptime(end_date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        event_name = event_filter or (all_events[0].name if all_events else '')
        dept_name = dept_filter or 'all'

        try:
            if export_format in ('xlsx', 'excel'):
                from core.reports_generator import generate_excel_report
                stream = generate_excel_report(event_name, dept_name, start_date, end_date, included_metrics, report_info)
                fname = f'EventTab_{report_type}_{today.strftime("%Y%m%d")}.xlsx'
                resp = HttpResponse(
                    stream.read(),
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                )
                resp['Content-Disposition'] = f'attachment; filename="{fname}"'
                return resp
            elif export_format == 'csv':
                import csv as _csv
                import io
                output = io.StringIO()
                writer = _csv.writer(output)
                writer.writerow(['Event Name', 'Category', 'Status', 'Participants', 'Teams', 'Date'])
                for ev in all_events:
                    sm = get_event_status_meta(ev, today)
                    writer.writerow([ev.name, ev.category, sm['status_label'],
                                     ev.max_participants or 0, ev.num_teams or 0,
                                     ev.event_date.strftime('%Y-%m-%d') if ev.event_date else ''])
                fname = f'EventTab_{report_type}_{today.strftime("%Y%m%d")}.csv'
                resp = HttpResponse(output.getvalue(), content_type='text/csv')
                resp['Content-Disposition'] = f'attachment; filename="{fname}"'
                return resp
            else:
                from core.reports_generator import generate_pdf_report
                stream = generate_pdf_report(event_name, dept_name, start_date, end_date, included_metrics, report_info)
                fname = f'EventTab_{report_type}_{today.strftime("%Y%m%d")}.pdf'
                resp = HttpResponse(stream.read(), content_type='application/pdf')
                resp['Content-Disposition'] = f'attachment; filename="{fname}"'
                return resp
        except Exception as e:
            messages.error(request, f'Report generation failed: {e}')
            return redirect('admin_generate_report')

    return render(request, 'admindash/generatereport.html', {
        'all_events': all_events,
        'total_events': total_events,
        'total_participants': total_participants,
        'total_teams': total_teams,
        'total_matches': total_matches,
        'completed_events': completed_events,
        'completion_rate': completion_rate,
        'status_counts': status_counts,
        'top_categories': top_categories,
        'event_summary': event_summary,
        'chart_labels_json': json.dumps(chart_labels),
        'chart_events_json': json.dumps(chart_events),
        'status_counts_json': json.dumps({
            'ongoing': status_counts['ongoing'],
            'upcoming': status_counts['upcoming'],
            'completed': status_counts['completed'],
            'cancelled': status_counts['inactive'],
        }),
        'departments': departments,
        'category_names': category_names,
        'division_names': division_names,
        'date_range_label': today.replace(day=1).strftime('%b 1') + ' – ' + today.strftime('%b %d, %Y'),
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


def _split_full_name(full_name):
    name_parts = (full_name or '').strip().split()
    if not name_parts:
        return '', ''
    if len(name_parts) == 1:
        return name_parts[0], ''
    return name_parts[0], ' '.join(name_parts[1:])


def _generate_username_from_name(full_name):
    """Auto-generate a unique username slug from a display name."""
    User = get_user_model()
    slug = re.sub(r'[^a-z0-9]', '_', full_name.strip().lower())
    slug = re.sub(r'_+', '_', slug).strip('_')[:16] or 'user'
    for _ in range(30):
        suffix = ''.join(random.choices(string.digits, k=4))
        candidate = f"{slug}_{suffix}"
        if not User.objects.filter(username__iexact=candidate).exists():
            return candidate
    return 'user_' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))


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
        'role': (payload.get('role') or '').strip(),
        'is_active': bool(payload.get('is_active', True)),
    }


CODE_ROLES = {'Judge', 'Scorer'}


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
        access_code = payload['access_code']
        if not full_name:
            return JsonResponse({'success': False, 'message': 'Name is required.'}, status=400)
        if not access_code:
            return JsonResponse({'success': False, 'message': 'Generate an access code before creating the account.'}, status=400)
        try:
            auto_username = _generate_username_from_name(full_name)
            first_name, last_name = _split_full_name(full_name)
            user = User.objects.create_user(username=auto_username, password=access_code)
            user.first_name = first_name
            user.last_name = last_name
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
                change_message=f'Created {role_label} account (access-code flow)',
            )
            return JsonResponse({
                'success': True,
                'message': f'{role_label} account for {full_name} created.',
                'username': auto_username,
                'access_code': access_code,
            })
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)}, status=500)

    # ── Tabulator: standard username / email / password flow ─────────────────
    if not payload['username'] or not payload['email'] or not payload['password']:
        return JsonResponse({'success': False, 'message': 'Username, email, and password are required.'}, status=400)

    if User.objects.filter(username__iexact=payload['username']).exists():
        return JsonResponse({'success': False, 'message': 'Username already exists.'}, status=400)
    if User.objects.filter(email__iexact=payload['email']).exists():
        return JsonResponse({'success': False, 'message': 'Email already exists.'}, status=400)

    try:
        if payload['full_name']:
            first_name, last_name = _split_full_name(payload['full_name'])
        else:
            first_name, last_name = payload['username'], ''
        user = User.objects.create_user(
            username=payload['username'],
            email=payload['email'],
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
        return JsonResponse({'success': True, 'message': f'{role_label} account for {payload["username"]} created.'})
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
        try:
            first_name, last_name = _split_full_name(full_name)
            account_user.first_name = first_name
            account_user.last_name = last_name
            if payload['access_code']:
                account_user.set_password(payload['access_code'])
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
            resp = {'success': True, 'message': f'Account for {full_name} updated.'}
            if payload['access_code']:
                resp['access_code'] = payload['access_code']
            return JsonResponse(resp)
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)}, status=500)

    # ── Tabulator: standard username / email / password flow ─────────────────
    if not payload['username'] or not payload['email']:
        return JsonResponse({'success': False, 'message': 'Username and email are required.'}, status=400)

    if User.objects.filter(username__iexact=payload['username']).exclude(id=account_user.id).exists():
        return JsonResponse({'success': False, 'message': 'Username already exists.'}, status=400)
    if User.objects.filter(email__iexact=payload['email']).exclude(id=account_user.id).exists():
        return JsonResponse({'success': False, 'message': 'Email already exists.'}, status=400)

    try:
        account_user.username = payload['username']
        account_user.email = payload['email']
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
        return JsonResponse({'success': True, 'message': f'Account for {payload["username"]} updated.'})
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
            scoring_method    = request.POST.get('scoring_method', '').strip(),
            tournament_type   = request.POST.get('tournament_type', '').strip(),
            faculty_in_charge = request.POST.get('faculty_in_charge', '').strip(),
            student_in_charge = request.POST.get('student_in_charge', '').strip(),
            mechanics         = request.POST.get('mechanics', '').strip(),
            scoring_criteria  = request.POST.get('scoring_criteria', '').strip(),
            created_by        = request.user,
        )
        # Save assigned judges (multi-select)
        judge_ids = request.POST.getlist('assigned_judges')
        if judge_ids:
            User = get_user_model()
            judges = User.objects.filter(
                id__in=[int(j) for j in judge_ids if j.isdigit()],
            ).filter(
                Q(groups__name__iexact='Judge') | Q(groups__name__iexact='Judges')
            )
            event.assigned_judges.set(judges)
        # Create teams (match-based) if provided
        teams_json = request.POST.get('teams_data', '').strip()
        if teams_json:
            try:
                teams_list = json.loads(teams_json)
                from events.models import BracketTeam
                for i, t in enumerate(teams_list):
                    team_name = (t.get('name') or '').strip()
                    if not team_name:
                        continue
                    dept_obj = resolve_department(t.get('department'))
                    members_raw = (t.get('members') or '').strip()
                    members_str, _ = parse_team_members(members_raw)
                    BracketTeam.objects.create(
                        event=event,
                        name=team_name,
                        department=dept_obj,
                        members=members_str,
                        seed=i + 1,
                    )
            except (json.JSONDecodeError, TypeError):
                pass

        # Create candidates (criteria-based) if provided
        candidates_json = request.POST.get('candidates_data', '').strip()
        if candidates_json and event.judging_event:
            try:
                cand_list = json.loads(candidates_json)
                from events.models import Candidate as CandModel
                for c in cand_list:
                    cand_name = (c.get('name') or '').strip()
                    if not cand_name:
                        continue
                    CandModel.objects.create(
                        event=event.judging_event,
                        name=cand_name,
                        number=c.get('number', 0),
                        description=(c.get('description') or '').strip(),
                    )
            except (json.JSONDecodeError, TypeError):
                pass

        sync_event_to_mobile(event)
        messages.success(request, f'Event "{event_name}" created and published to the judge mobile app.')
        return redirect('admin_manage_events')

    status_filter   = request.GET.get('status', 'all').strip().lower()
    if status_filter == Event.STATUS_ACTIVE:
        status_filter = 'ongoing'
    category_filter = request.GET.get('category', 'all').strip().lower()
    schedule_filter = request.GET.get('schedule', '').strip().lower()
    search_query    = request.GET.get('q', '').strip()

    event_qs = Event.objects.all()

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
            'assigned_judge_ids': list(ev.assigned_judges.values_list('id', flat=True)),
            'assigned_judges_display': ', '.join(
                u.get_full_name() or u.username
                for u in ev.assigned_judges.all()
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
        'department_options_json': json.dumps(department_names),
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
    ev.save()
    # Save assigned judges
    judge_ids = request.POST.getlist('assigned_judges')
    User = get_user_model()
    judges = User.objects.filter(
        id__in=[int(j) for j in judge_ids if j.isdigit()],
    ).filter(
        Q(groups__name__iexact='Judge') | Q(groups__name__iexact='Judges')
    ) if judge_ids else User.objects.none()
    ev.assigned_judges.set(judges)

    teams_json = request.POST.get('teams_data', '').strip()
    if teams_json:
        try:
            teams_list = json.loads(teams_json)
            from events.models import BracketTeam
            BracketTeam.objects.filter(event=ev).delete()
            for i, t in enumerate(teams_list):
                team_name = (t.get('name') or '').strip()
                if not team_name:
                    continue
                dept_obj = resolve_department(t.get('department'))
                members_raw = (t.get('members') or '').strip()
                members_str, _ = parse_team_members(members_raw)
                BracketTeam.objects.create(
                    event=ev,
                    name=team_name,
                    department=dept_obj,
                    members=members_str,
                    seed=i + 1,
                )
        except (json.JSONDecodeError, TypeError):
            pass

    candidates_json = request.POST.get('candidates_data', '').strip()
    if candidates_json and ev.judging_event_id:
        try:
            cand_list = json.loads(candidates_json)
            from events.models import Candidate as CandModel
            CandModel.objects.filter(event=ev.judging_event).delete()
            for c in cand_list:
                cand_name = (c.get('name') or '').strip()
                if not cand_name:
                    continue
                CandModel.objects.create(
                    event=ev.judging_event,
                    name=cand_name,
                    number=c.get('number', 0),
                    description=(c.get('description') or '').strip(),
                )
        except (json.JSONDecodeError, TypeError):
            pass

    sync_event_to_mobile(ev)

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
    """Display the tournament brackets management page."""
    from events.models import BracketMatch, BracketTeam
    all_events = list(Event.objects.all())
    today = timezone.localdate()

    events_with_bracket = set(
        BracketMatch.objects.values_list('event_id', flat=True).distinct()
    )
    team_counts = {}
    for row in BracketTeam.objects.values('event_id'):
        team_counts[row['event_id']] = team_counts.get(row['event_id'], 0) + 1

    match_stats = {
        row['event_id']: row
        for row in BracketMatch.objects.values('event_id').annotate(
            total=Count('id'),
            completed=Count('id', filter=Q(status=BracketMatch.STATUS_COMPLETED)),
            ongoing=Count('id', filter=Q(status=BracketMatch.STATUS_ONGOING)),
        )
    }

    event_rows = []
    for ev in all_events[:50]:
        status_meta = get_event_status_meta(ev, today)
        bracket_team_count = team_counts.get(ev.id, 0)
        has_bracket = ev.id in events_with_bracket
        ms = match_stats.get(ev.id, {})
        bracket_meta = get_bracket_status_meta(
            has_bracket,
            total=ms.get('total', 0),
            completed=ms.get('completed', 0),
            ongoing=ms.get('ongoing', 0),
        )
        event_rows.append({
            'id': ev.id,
            'name': ev.name,
            'category': ev.category,
            'division': ev.division,
            'department': ev.department,
            'schedule_label': ev.schedule_label,
            'event_date': ev.event_date.strftime('%Y-%m-%d') if ev.event_date else '',
            'event_time': ev.event_time_str,
            'venue': ev.venue,
            'status': status_meta['status'],
            'status_label': status_meta['status_label'],
            'num_teams': str(ev.num_teams) if ev.num_teams else '',
            'bracket_team_count': bracket_team_count,
            'tournament_type': ev.tournament_type or '',
            'tournament_type_label': _tournament_type_label(ev.tournament_type),
            'scoring_method': ev.scoring_method or '',
            'bracket_locked': ev.bracket_locked,
            'has_bracket': has_bracket,
            **bracket_meta,
        })

    status_counts = get_event_status_counts(all_events, today)
    total_events = len(all_events)
    active_count = status_counts['ongoing']
    upcoming_count = status_counts['upcoming']
    
    departments = Department.objects.all()

    return render(request, 'admindash/bracket.html', {
        'event_rows': event_rows,
        'total_events': total_events,
        'active_count': active_count,
        'upcoming_count': upcoming_count,
        'departments': departments,
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


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_scoresheets(request):
    from events.models import ScoreSheet, Event, BracketMatch
    from django.db import models

    # Auto-generate mock scoresheets for testing if none exist
    if ScoreSheet.objects.count() == 0:
        import datetime
        from events.models import BracketTeam, Department
        
        # If there are no bracket matches, let's create a mock event, teams, and matches
        if BracketMatch.objects.count() == 0:
            event = Event.objects.first()
            if not event:
                event = Event.objects.create(
                    name="Intramural Sports 2026",
                    category="Sports",
                    event_date=datetime.date.today(),
                    venue="University Gymnasium",
                    status="active"
                )
            
            # Fetch or create departments
            depts = list(Department.objects.all()[:4])
            if len(depts) < 4:
                names = ["College of Engineering", "College of Science", "College of Business", "College of Arts"]
                codes = ["COE", "COS", "COB", "COA"]
                for i, name in enumerate(names):
                    if len(depts) < 4 and not Department.objects.filter(code=codes[i]).exists():
                        d = Department.objects.create(name=name, code=codes[i])
                        depts.append(d)
            
            # Create bracket teams
            teams = []
            for i, dept in enumerate(depts):
                t = BracketTeam.objects.create(
                    event=event,
                    name=f"{dept.code} Tigers",
                    department=dept,
                    seed=i + 1
                )
                teams.append(t)
            
            # Generate brackets
            from events.bracket_generator import generate_single_elimination
            generate_single_elimination(event, teams)
            
        matches = BracketMatch.objects.filter(team_a__isnull=False, team_b__isnull=False)[:4]
        for idx, match in enumerate(matches):
            ScoreSheet.objects.create(
                event=match.event,
                match=match,
                tabulator=request.user,
                score_team_a=85.00 + (idx * 2.5),
                score_team_b=82.00 + (idx * 1.5),
                winner=match.team_a if idx % 2 == 0 else match.team_b,
                judges_names="Judge Angela, Judge Brandon, Judge Carter",
                remarks="Fair play, no penalties applied.",
                status=ScoreSheet.STATUS_PENDING
            )

    # Get filters
    event_id = request.GET.get('event')
    status_filter = request.GET.get('status')
    search_query = request.GET.get('q', '').strip()

    scoresheets = ScoreSheet.objects.all().select_related(
        'event', 'match', 'tabulator', 'winner', 'match__team_a', 'match__team_b'
    )

    if event_id:
        scoresheets = scoresheets.filter(event_id=event_id)
    if status_filter:
        scoresheets = scoresheets.filter(status=status_filter)
    if search_query:
        scoresheets = scoresheets.filter(
            models.Q(tabulator__username__icontains=search_query) |
            models.Q(event__name__icontains=search_query) |
            models.Q(match__round_name__icontains=search_query) |
            models.Q(remarks__icontains=search_query) |
            models.Q(judges_names__icontains=search_query)
        )

    events = Event.objects.all()

    return render(request, 'admindash/scoresheet.html', {
        'scoresheets': scoresheets,
        'events': events,
        'selected_event': event_id,
        'selected_status': status_filter,
        'search_query': search_query,
    })


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


def _get_tab_event_rows(request):
    """Build event rows for the tabulator from real Event data."""
    from events.models import Event, ScoreSheet, BracketMatch
    from django.utils import timezone
    today = timezone.localdate()
    all_events = list(Event.objects.all().order_by('-event_date'))
    rows = []
    for ev in all_events:
        # Count scoresheets for this event
        total_sheets = ScoreSheet.objects.filter(event=ev).count()
        encoded_sheets = ScoreSheet.objects.filter(event=ev, status__in=['confirmed', 'finalized']).count()
        pending_sheets = total_sheets - encoded_sheets

        # Determine status
        if ev.status == 'completed':
            tab_status = 'completed'
            tab_status_label = 'Completed'
        elif encoded_sheets > 0 and pending_sheets == 0 and total_sheets > 0:
            tab_status = 'in_progress'
            tab_status_label = 'Ready for Verification'
        elif encoded_sheets > 0:
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

        # Judge initials from assigned_judges
        judges = list(ev.assigned_judges.all()[:3])
        panel_initials = []
        for j in judges:
            name = j.get_full_name().strip() or j.username
            parts = name.split()
            ini = ''.join(p[:1] for p in parts[:2]).upper() or j.username[:2].upper()
            panel_initials.append(ini)
        extra_judges = ev.assigned_judges.count() - len(judges)

        # Date range
        date_str = ev.event_date.strftime('%b %d, %Y') if ev.event_date else '—'

        # Action
        if tab_status == 'pending' or tab_status == 'not_started':
            action_label = 'Encode Scores'
            action_kind = 'primary'
        elif tab_status == 'in_progress':
            action_label = 'Review & Verify'
            action_kind = 'verify'
        elif tab_status == 'completed':
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
            'panel_initials': panel_initials,
            'extra_judges': extra_judges,
            'judge_count': ev.assigned_judges.count(),
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
    """Tabulator home: pending results, upcoming assignments, recent activity."""
    from events.models import Event, ScoreSheet, JudgeScore, JudgingEvent, Candidate
    from django.contrib.admin.models import LogEntry, ADDITION, CHANGE
    from django.utils import timezone

    event_rows = _get_tab_event_rows(request)
    total_assigned = len(event_rows)
    pending_encode = sum(1 for r in event_rows if r['tab_status'] in ('pending', 'not_started'))
    verified = sum(1 for r in event_rows if r['tab_status'] == 'completed')
    in_progress = sum(1 for r in event_rows if r['tab_status'] == 'in_progress')
    completed_count = verified

    # Pending results: scoresheets awaiting verification + unverified judge scores
    pending_results = []
    pending_sheets = ScoreSheet.objects.filter(status='pending').select_related(
        'event', 'match', 'match__team_a', 'match__team_b', 'tabulator'
    ).order_by('-created_at')[:10]
    for ss in pending_sheets:
        team_a = ss.match.team_a.name if ss.match and ss.match.team_a else 'TBD'
        team_b = ss.match.team_b.name if ss.match and ss.match.team_b else 'TBD'
        submitter = ss.tabulator
        pending_results.append({
            'id': ss.id,
            'type': 'match',
            'type_label': 'Match Result',
            'event_name': ss.event.name,
            'event_category': ss.event.category or 'General',
            'category_lower': (ss.event.category or 'general').lower().replace(' ', '-'),
            'entry': f'{team_a} vs {team_b}',
            'entry_sub': ss.match.round_name if ss.match else '',
            'submitted_by': (submitter.get_full_name() or submitter.username) if submitter else 'Unknown',
            'submitted_role': 'Scorer',
            'submitted_initials': (submitter.username[:2].upper()) if submitter else 'UN',
            'submitted_at': ss.created_at.strftime('%b %d, %Y'),
            'submitted_time': ss.created_at.strftime('%I:%M %p'),
        })

    unlocked_scores = JudgeScore.objects.filter(is_locked=False, submitted_at__isnull=False).select_related(
        'judge', 'candidate', 'candidate__event'
    ).order_by('-submitted_at')[:10]
    seen_candidates = set()
    for js in unlocked_scores:
        cand = js.candidate
        if cand.id in seen_candidates:
            continue
        seen_candidates.add(cand.id)
        je = cand.event
        pending_results.append({
            'id': js.id,
            'type': 'judge',
            'type_label': 'Judge Score',
            'event_name': je.title,
            'event_category': je.category.name if je.category else 'General',
            'category_lower': (je.category.category_type if je.category else 'general'),
            'entry': f'Contestant #{cand.number}',
            'entry_sub': cand.name,
            'submitted_by': js.judge.get_full_name() or js.judge.username,
            'submitted_role': 'Judge',
            'submitted_initials': js.judge.username[:2].upper(),
            'submitted_at': js.submitted_at.strftime('%b %d, %Y') if js.submitted_at else '—',
            'submitted_time': js.submitted_at.strftime('%I:%M %p') if js.submitted_at else '',
        })
    pending_results.sort(key=lambda x: x.get('submitted_at', ''), reverse=True)
    pending_results = pending_results[:5]
    pending_count = ScoreSheet.objects.filter(status='pending').count() + \
                    JudgeScore.objects.filter(is_locked=False, submitted_at__isnull=False).values('candidate').distinct().count()

    # Upcoming assignments: future events not yet completed
    today = timezone.localdate()
    upcoming_events = Event.objects.filter(
        event_date__gte=today
    ).exclude(status='completed').order_by('event_date', 'event_time')[:5]
    upcoming_assignments = []
    for ev in upcoming_events:
        month = ev.event_date.strftime('%b').upper() if ev.event_date else ''
        day = ev.event_date.strftime('%d') if ev.event_date else ''
        time_str = ev.event_time.strftime('%I:%M %p') if ev.event_time else ''
        row_for_ev = next((r for r in event_rows if r['id'] == ev.id), None)
        status_label = row_for_ev['tab_status_label'] if row_for_ev else 'Upcoming'
        upcoming_assignments.append({
            'id': ev.id,
            'name': ev.name,
            'round': ev.tournament_type or ev.scoring_method or 'Finals',
            'month': month,
            'day': day,
            'time': time_str,
            'venue': ev.venue or '',
            'status_label': status_label,
        })

    # Recent activity
    recent_logs = LogEntry.objects.select_related('user').order_by('-action_time')[:5]
    activity_rows = []
    action_icons = {ADDITION: 'add', CHANGE: 'edit'}
    for log in recent_logs:
        delta = timezone.now() - log.action_time
        if delta.days >= 1:
            ago = f'{delta.days}d ago'
        elif delta.seconds >= 3600:
            ago = f'{delta.seconds // 3600}h ago'
        elif delta.seconds >= 60:
            ago = f'{delta.seconds // 60}m ago'
        else:
            ago = 'just now'
        activity_rows.append({
            'message': log.change_message or log.object_repr,
            'user': log.user.get_full_name() or log.user.username if log.user else 'System',
            'ago': ago,
            'time_str': log.action_time.strftime('%I:%M %p'),
            'icon': action_icons.get(log.action_flag, 'info'),
            'accent': {ADDITION: 'green', CHANGE: 'blue'}.get(log.action_flag, 'gray'),
        })

    current_event = Event.objects.filter(status='active').order_by('-event_date').first()
    if not current_event:
        current_event = Event.objects.order_by('-event_date').first()

    return render(request, 'tabulatordash/tabdashboard.html', {
        'display_name': _tabulator_display_name(request.user),
        'total_assigned': total_assigned,
        'pending_encode': pending_encode,
        'verified': verified,
        'in_progress': in_progress,
        'completed_count': completed_count,
        'pending_results': pending_results,
        'pending_count': pending_count,
        'upcoming_assignments': upcoming_assignments,
        'event_rows': event_rows[:5],
        'activity_rows': activity_rows,
        'current_event': current_event,
    })


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_approve_result(request, result_type, result_id):
    """Approve a pending scoresheet or judge score."""
    from events.models import ScoreSheet, JudgeScore
    from django.contrib.admin.models import LogEntry, CHANGE
    from django.contrib.contenttypes.models import ContentType

    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    try:
        if result_type == 'match':
            ss = ScoreSheet.objects.get(id=result_id)
            ss.status = 'confirmed'
            ss.save()
            obj_repr = str(ss)
            ct = ContentType.objects.get_for_model(ScoreSheet)
            obj_id = ss.id
        elif result_type == 'judge':
            js = JudgeScore.objects.get(id=result_id)
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
    """Reject a pending scoresheet or judge score."""
    from events.models import ScoreSheet, JudgeScore
    from django.contrib.admin.models import LogEntry, CHANGE
    from django.contrib.contenttypes.models import ContentType

    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    try:
        if result_type == 'match':
            ss = ScoreSheet.objects.get(id=result_id)
            ss.status = 'pending'
            ss.remarks = (ss.remarks or '') + '\n[Rejected by tabulator]'
            ss.save()
            obj_repr = str(ss)
            ct = ContentType.objects.get_for_model(ScoreSheet)
            obj_id = ss.id
        elif result_type == 'judge':
            js = JudgeScore.objects.get(id=result_id)
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


def _build_pending_result_rows():
    """Collect all pending match and judge results for the tabulator portal."""
    from datetime import datetime
    from decimal import Decimal
    from django.db.models import Sum
    from django.utils import timezone
    from django.contrib.auth.models import User
    from events.models import ScoreSheet, JudgeScore

    now = timezone.now()
    today = now.date()
    pending_rows = []

    pending_sheets = ScoreSheet.objects.filter(status='pending').select_related(
        'event', 'match', 'match__team_a', 'match__team_b', 'tabulator'
    ).order_by('-created_at')
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
            **due,
        })

    approved_judge_keys = _tabulator_approved_judge_keys()
    submitted_scores = JudgeScore.objects.filter(
        is_locked=True, submitted_at__isnull=False
    ).select_related(
        'candidate', 'candidate__event', 'candidate__event__category', 'criterion'
    ).order_by('-submitted_at')
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
        judge_user = User.objects.filter(id=js.judge_id).first()
        judge_name = (judge_user.get_full_name() or judge_user.username) if judge_user else f'Judge #{js.judge_id}'
        judge_initials = judge_user.username[:2].upper() if judge_user else 'JG'
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
    """Page listing all pending (unverified) results for the tabulator."""
    from django.core.paginator import Paginator

    pending_rows, stats = _build_pending_result_rows()

    tab = request.GET.get('tab', 'all')
    status_filter = request.GET.get('status', 'all')
    category_filter = request.GET.get('category', 'all')
    search = (request.GET.get('q') or '').strip().lower()

    filtered = pending_rows
    if tab == 'match':
        filtered = [r for r in filtered if r['type'] == 'match']
    elif tab == 'judge':
        filtered = [r for r in filtered if r['type'] == 'judge']
    elif tab == 'overdue':
        filtered = [r for r in filtered if r.get('is_overdue')]

    if status_filter == 'awaiting':
        filtered = [r for r in filtered if not r.get('is_overdue')]
    elif status_filter == 'overdue':
        filtered = [r for r in filtered if r.get('is_overdue')]

    if category_filter != 'all':
        filtered = [r for r in filtered if r.get('category_lower') == category_filter]

    if search:
        filtered = [
            r for r in filtered
            if search in r['event_name'].lower()
            or search in r['entry'].lower()
            or search in r['submitted_by'].lower()
            or search in (r.get('round_label') or '').lower()
        ]

    categories = sorted({r['category_lower'] for r in pending_rows})

    paginator = Paginator(filtered, 10)
    page_obj = paginator.get_page(request.GET.get('page'))

    review_json = [
        {key: val for key, val in row.items() if key != 'submitted_dt'}
        for row in page_obj.object_list
    ]

    return render(request, 'tabulatordash/pendingresult.html', {
        'display_name': _tabulator_display_name(request.user),
        'pending_rows': page_obj.object_list,
        'review_json': review_json,
        'page_obj': page_obj,
        'paginator': paginator,
        'filtered_count': len(filtered),
        'categories': categories,
        'active_tab': tab,
        'status_filter': status_filter,
        'category_filter': category_filter,
        'search_query': request.GET.get('q', ''),
        **stats,
    })


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
    """Page listing all approved/verified results."""
    from django.core.paginator import Paginator

    approved_rows, stats = _build_approved_result_rows(request)

    tab = request.GET.get('tab', 'all')
    status_filter = request.GET.get('status', 'all')
    category_filter = request.GET.get('category', 'all')
    search = (request.GET.get('q') or '').strip().lower()

    filtered = approved_rows
    if tab == 'match':
        filtered = [r for r in filtered if r['type'] == 'match']
    elif tab == 'judge':
        filtered = [r for r in filtered if r['type'] == 'judge']
    elif tab == 'completed':
        filtered = [r for r in filtered if r.get('event_completed')]

    if status_filter == 'approved':
        filtered = filtered  # all rows are approved
    elif status_filter == 'finalized':
        filtered = [r for r in filtered if r.get('status_label') == 'FINALIZED']

    if category_filter != 'all':
        filtered = [r for r in filtered if r.get('category_lower') == category_filter]

    if search:
        filtered = [
            r for r in filtered
            if search in r['event_name'].lower()
            or search in r['entry'].lower()
            or search in (r.get('round_label') or '').lower()
        ]

    categories = sorted({r['category_lower'] for r in approved_rows})

    paginator = Paginator(filtered, 10)
    page_obj = paginator.get_page(request.GET.get('page'))

    detail_json = [
        {key: val for key, val in row.items() if key != 'approved_dt'}
        for row in page_obj.object_list
    ]

    user_initials = request.user.username[:2].upper() if request.user.username else 'TA'

    return render(request, 'tabulatordash/approveresult.html', {
        'display_name': _tabulator_display_name(request.user),
        'approved_rows': page_obj.object_list,
        'detail_json': detail_json,
        'page_obj': page_obj,
        'paginator': paginator,
        'filtered_count': len(filtered),
        'categories': categories,
        'active_tab': tab,
        'status_filter': status_filter,
        'category_filter': category_filter,
        'search_query': request.GET.get('q', ''),
        'user_initials': user_initials,
        **stats,
    })


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_assigned_events(request):
    """Tabulator assigned events list."""
    from events.models import Event
    from django.core.paginator import Paginator

    status_filter = request.GET.get('status', 'all').strip().lower()
    cat_filter = request.GET.get('category', 'all').strip().lower()
    search_q = request.GET.get('q', '').strip().lower()

    event_rows = _get_tab_event_rows(request)

    # Enrich rows with extra event details for the new design
    events_by_name = {ev.name: ev for ev in Event.objects.all()}
    for row in event_rows:
        ev = events_by_name.get(row['title'])
        if ev:
            row['scoring_method'] = ev.scoring_method or ''
            row['tournament_type'] = ev.tournament_type or ''
            row['time_str'] = ev.event_time.strftime('%I:%M %p') if ev.event_time else ''
            row['venue'] = ev.venue or ''
            # Determine type label for display
            if ev.scoring_method == 'criteria':
                row['type_label'] = 'Criteria Based'
                row['type_sub'] = ev.tournament_type or ev.category or ''
            else:
                row['type_label'] = 'Match Result'
                row['type_sub'] = ev.tournament_type or ''
            # Scorer/judge info
            scorer_judges = list(ev.assigned_judges.all()[:1])
            if scorer_judges:
                j = scorer_judges[0]
                row['panel_name'] = j.get_full_name() or j.username
                row['panel_label'] = f'{ev.assigned_judges.count()} Judge{"s" if ev.assigned_judges.count() != 1 else ""}'
                row['panel_sub'] = 'Panel 1'
            else:
                row['panel_name'] = ''
                row['panel_label'] = f'{row["judge_count"]} Scorer' if row['judge_count'] == 1 else f'{row["judge_count"]} Scorers'
                row['panel_sub'] = row.get('panel_initials', [''])[0] if row.get('panel_initials') else ''

    if status_filter != 'all':
        event_rows = [r for r in event_rows if r['tab_status'] == status_filter]
    if cat_filter != 'all':
        event_rows = [r for r in event_rows if r['category'].lower() == cat_filter]
    if search_q:
        event_rows = [r for r in event_rows if search_q in r['title'].lower() or search_q in r['category'].lower()]

    total = len(event_rows)
    pending = sum(1 for r in event_rows if r['tab_status'] in ('pending', 'not_started'))
    in_progress = sum(1 for r in event_rows if r['tab_status'] == 'in_progress')
    completed = sum(1 for r in event_rows if r['tab_status'] == 'completed')
    upcoming = sum(1 for r in event_rows if r['tab_status'] == 'not_started')
    ongoing = in_progress + pending

    paginator = Paginator(event_rows, 10)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    all_rows = _get_tab_event_rows(request)
    categories = sorted({r['category'] for r in all_rows if r['category']})

    return render(request, 'tabulatordash/assigned.html', {
        'display_name': _tabulator_display_name(request.user),
        'total_assigned': total,
        'pending': pending,
        'upcoming': upcoming,
        'ongoing': ongoing,
        'in_progress': in_progress,
        'completed': completed,
        'event_rows': page_obj.object_list,
        'page_obj': page_obj,
        'total_count': total,
        'categories': categories,
        'selected_status': status_filter,
        'selected_category': cat_filter,
        'search_query': search_q,
    })


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_scoresheets(request):
    """Tabulator score sheets — bracket match sheets and judge mobile submissions."""
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

    rows, stats, filter_options = _build_tabulator_scoresheet_rows()

    tab = request.GET.get('tab', 'event').strip().lower()
    if tab not in ('event', 'submission'):
        tab = 'event'

    event_type_filter = request.GET.get('event_type', 'all').strip()
    event_filter = request.GET.get('event', 'all').strip()
    round_filter = request.GET.get('round', 'all').strip()
    status_filter = request.GET.get('status', 'all').strip().lower()
    search_q = (request.GET.get('q') or '').strip().lower()
    selected_sheet = (request.GET.get('sheet') or '').strip()

    date_from_raw = (request.GET.get('date_from') or '').strip()
    date_to_raw = (request.GET.get('date_to') or '').strip()
    date_from = date_to = None
    if date_from_raw:
        try:
            date_from = datetime.strptime(date_from_raw, '%Y-%m-%d').date()
        except ValueError:
            pass
    if date_to_raw:
        try:
            date_to = datetime.strptime(date_to_raw, '%Y-%m-%d').date()
        except ValueError:
            pass

    filtered = rows
    if event_type_filter != 'all':
        filtered = [r for r in filtered if r.get('category_lower') == event_type_filter]
    if event_filter != 'all':
        filtered = [r for r in filtered if r['event_name'] == event_filter]
    if round_filter != 'all':
        filtered = [r for r in filtered if r.get('round_label') == round_filter]
    if status_filter != 'all':
        filtered = [r for r in filtered if r.get('status_key') == status_filter]
    if date_from:
        filtered = [r for r in filtered if r.get('submitted_dt') and r['submitted_dt'].date() >= date_from]
    if date_to:
        filtered = [r for r in filtered if r.get('submitted_dt') and r['submitted_dt'].date() <= date_to]
    if search_q:
        filtered = [
            r for r in filtered
            if search_q in r['entry_title'].lower()
            or search_q in r['event_name'].lower()
            or search_q in r.get('submitted_by_name', '').lower()
            or search_q in r.get('type_label', '').lower()
        ]

    if tab == 'event':
        epoch = timezone.make_aware(datetime(1970, 1, 1))
        filtered.sort(key=lambda r: r.get('submitted_dt') or epoch, reverse=True)
        filtered.sort(key=lambda r: r['event_name'].lower())
    else:
        epoch = timezone.make_aware(datetime(1970, 1, 1))
        filtered.sort(key=lambda r: r.get('submitted_dt') or epoch, reverse=True)

    paginator = Paginator(filtered, 6)
    page_number = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    page_rows = page_obj.object_list
    active_row = None
    if selected_sheet:
        active_row = next((r for r in filtered if r['row_key'] == selected_sheet), None)
    if not active_row and page_rows:
        active_row = page_rows[0]

    detail_json = [
        {k: v for k, v in row.items() if k not in ('submitted_dt',)}
        for row in filtered
    ]

    user_initials = request.user.username[:2].upper() if request.user.username else 'TA'

    return render(request, 'tabulatordash/scoresheets.html', {
        'display_name': _tabulator_display_name(request.user),
        'sheet_rows': page_rows,
        'active_row': active_row,
        'detail_json': detail_json,
        'page_obj': page_obj,
        'filtered_count': len(filtered),
        'active_tab': tab,
        'event_types': filter_options['event_types'],
        'events': filter_options['events'],
        'rounds': filter_options['rounds'],
        'selected_event_type': event_type_filter,
        'selected_event': event_filter,
        'selected_round': round_filter,
        'selected_status': status_filter,
        'search_query': request.GET.get('q', ''),
        'date_from': date_from.isoformat() if date_from else '',
        'date_to': date_to.isoformat() if date_to else '',
        'selected_sheet': active_row['row_key'] if active_row else '',
        'user_initials': user_initials,
        **stats,
    })


def _tabulator_scoresheet_status(raw_status, event_status=None):
    """Map model status to mockup display status."""
    from events.models import Event
    if raw_status in ('pending', 'in_progress', 'pending_review'):
        return 'pending_review', 'PENDING REVIEW'
    if raw_status == 'confirmed':
        return 'pending_review', 'PENDING REVIEW'
    if raw_status in ('finalized', 'encoded', 'submitted'):
        if event_status == Event.STATUS_COMPLETED:
            return 'archived', 'ARCHIVED'
        return 'encoded', 'ENCODED'
    if raw_status == 'archived':
        return 'archived', 'ARCHIVED'
    return 'pending_review', 'PENDING REVIEW'


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


def _build_tabulator_scoresheet_rows():
    """Build unified score sheet rows from bracket ScoreSheet and JudgeScore data."""
    from datetime import datetime as dt
    from django.db.models import Max, Count, Q
    from events.models import Event, ScoreSheet, JudgeScore, JudgingEvent
    from django.contrib.contenttypes.models import ContentType
    from django.contrib.admin.models import LogEntry

    rows = []
    ss_ct = ContentType.objects.get_for_model(ScoreSheet)

    portal_map = {}
    for ev in Event.objects.select_related('judging_event'):
        if ev.judging_event_id:
            portal_map[ev.judging_event_id] = ev

    je_map = {je.id: je for je in JudgingEvent.objects.select_related('category')}

    judge_ids = JudgeScore.objects.values_list('judge_id', flat=True).distinct()

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
            view_url = f'/tabulator/encode/?sheet={s.id}'
            review_url = f'/tabulator/encode/?sheet={s.id}'
        elif s.status == 'confirmed':
            view_url = f'/tabulator/review/?sheet={s.id}'
            review_url = f'/tabulator/review/?sheet={s.id}'
        else:
            view_url = f'/tabulator/review/?sheet={s.id}'
            review_url = f'/tabulator/review/?sheet={s.id}'

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
        'encoded_count': sum(1 for r in rows if r['status_key'] == 'encoded'),
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
    """Tabulator OCR Upload page"""
    from events.models import Event
    events = list(Event.objects.all().order_by('-event_date').values_list('name', flat=True))
    if not events:
        events = ['National Science Decathlon 2024', 'Regional Athletics Meet 2024']

    return render(request, 'tabulatordash/ocr.html', {
        'display_name': _tabulator_display_name(request.user),
        'events': events,
    })


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
    """Tabulator Event Reports page — analytics and exports for assigned events."""
    ctx = _build_event_reports_context(request)
    ctx['display_name'] = _tabulator_display_name(request.user)
    ctx['user_initials'] = request.user.username[:2].upper() if request.user.username else 'TA'
    ctx['current_event'] = ctx['selected_event']
    return render(request, 'tabulatordash/tabreport.html', ctx)


# ── Tabulation sub-pages ────────────────────────────────────────

@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_tabulation(request):
    """Tabulation landing page — shows workflow overview and quick stats."""
    from events.models import ScoreSheet
    sheets = ScoreSheet.objects.all()
    encoded_count = sheets.filter(status__in=('confirmed', 'finalized')).count()
    pending_review = sheets.filter(status='confirmed').count()
    finalized_count = sheets.filter(status='finalized').count()
    return render(request, 'tabulatordash/tabulation.html', {
        'display_name': _tabulator_display_name(request.user),
        'encoded_count': encoded_count,
        'pending_review': pending_review,
        'finalized_count': finalized_count,
    })


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_encode_score(request):
    """Encode scores for a specific score sheet, or list pending sheets."""
    from events.models import ScoreSheet, Event, BracketTeam
    from django.utils import timezone

    sheet_id = request.GET.get('sheet', '').strip()
    sheet_obj = None
    sheet_ctx = None
    criteria = []
    teams = []
    history_logs = []

    # KPI counts (always shown)
    all_sheets = ScoreSheet.objects.all()
    total_sheets = all_sheets.count()
    encoded_count = all_sheets.filter(status__in=('confirmed', 'finalized')).count()
    pending_count = all_sheets.filter(status='pending').count()
    validated_count = all_sheets.filter(status='finalized').count()

    current_event = Event.objects.filter(status='active').order_by('-event_date').first()

    if sheet_id and sheet_id.isdigit():
        try:
            sheet_obj = ScoreSheet.objects.select_related('event', 'match', 'tabulator').get(id=int(sheet_id))
        except ScoreSheet.DoesNotExist:
            sheet_obj = None

    if sheet_obj:
        idx = ScoreSheet.objects.filter(created_at__lte=sheet_obj.created_at).count()
        ss_id = f'SS-{sheet_obj.created_at.year}-{str(idx).zfill(3)}'
        sheet_ctx = {
            'id': sheet_obj.id,
            'ss_id': ss_id,
            'event_name': sheet_obj.event.name,
            'category': sheet_obj.event.category,
            'judge_name': sheet_obj.judges_names or 'Unknown Judge',
            'date_received': sheet_obj.created_at.strftime('%b %d, %Y %I:%M %p'),
            'status': sheet_obj.status,
            'status_label': sheet_obj.get_status_display(),
        }
        teams_qs = BracketTeam.objects.filter(event=sheet_obj.event).select_related('department')
        for team in teams_qs:
            teams.append({
                'id': team.id,
                'name': team.name,
                'department': team.department.name if team.department else '',
                'total_score': '0',
            })
        from django.contrib.admin.models import LogEntry
        logs = LogEntry.objects.filter(object_id=str(sheet_obj.id)).select_related('user').order_by('-action_time')[:10]
        for log in logs:
            history_logs.append({
                'action': log.change_message or 'Updated',
                'user': log.user.username if log.user else 'System',
                'date': log.action_time.strftime('%b %d, %Y %I:%M %p'),
                'notes': log.object_repr,
            })

    if request.method == 'POST' and sheet_obj:
        action = request.POST.get('action', 'save')
        if action == 'mark_encoded':
            sheet_obj.status = 'confirmed'
        sheet_obj.tabulator = request.user
        sheet_obj.save()
        return redirect(f"{request.path}?sheet={sheet_obj.id}")

    pending_sheets = []
    if not sheet_obj:
        for i, s in enumerate(ScoreSheet.objects.select_related('event', 'match').filter(
                status__in=('pending', 'confirmed')).order_by('-created_at')):
            ss_id = f'SS-{s.created_at.year}-{str(i + 1).zfill(3)}'
            pending_sheets.append({
                'id': s.id, 'ss_id': ss_id,
                'event_name': s.event.name, 'category': s.event.category,
                'judge_name': s.judges_names or 'Unknown Judge',
                'date_received': s.created_at.strftime('%b %d, %Y %I:%M %p'),
                'status': s.status, 'status_label': s.get_status_display(),
            })

    return render(request, 'tabulatordash/encodedscore.html', {
        'display_name': _tabulator_display_name(request.user),
        'current_event': current_event,
        'sheet': sheet_ctx,
        'criteria': criteria,
        'teams': teams,
        'history_logs': history_logs,
        'pending_sheets': pending_sheets,
        'total_sheets': total_sheets,
        'encoded_count': encoded_count,
        'pending_count': pending_count,
        'validated_count': validated_count,
        'total_possible_pts': 100,
        'last_saved': None,
    })


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_save_scores(request, sheet_id):
    """POST endpoint to save encoded scores for a sheet."""
    from events.models import ScoreSheet
    if request.method != 'POST':
        return redirect('tabulator_encode_score')
    try:
        sheet = ScoreSheet.objects.get(id=sheet_id)
        action = request.POST.get('action', 'save')
        if action == 'mark_encoded':
            sheet.status = 'confirmed'
        sheet.tabulator = request.user
        sheet.save()
    except ScoreSheet.DoesNotExist:
        pass
    return redirect(f"/tabulator/encode/?sheet={sheet_id}")


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
    """Verify and lock a score sheet — sets status to finalized and logs the action."""
    from events.models import ScoreSheet
    from django.contrib.admin.models import LogEntry, CHANGE
    from django.contrib.contenttypes.models import ContentType
    if request.method == 'POST':
        try:
            sheet = ScoreSheet.objects.select_related('event', 'match').get(id=sheet_id)
            sheet.status = 'finalized'
            sheet.tabulator = request.user
            sheet.save()

            # Tabulator approval drives the bracket: sync match, advance winners,
            # award points, and crown the champion on the final.
            change_message = "Verified and locked score sheet."
            try:
                from events.bracket_progression import apply_scoresheet_to_bracket
                result = apply_scoresheet_to_bracket(sheet)
                if result.get('updated'):
                    change_message = (
                        "Verified & locked — championship result, leaderboard updated."
                        if result.get('is_championship')
                        else f"Verified & locked — bracket Match {result['match_number']} updated."
                    )
            except Exception:
                # Never let bracket sync block the verify/lock action.
                pass

            # Log the verify & lock action
            ct = ContentType.objects.get_for_model(ScoreSheet)
            LogEntry.objects.create(
                user=request.user,
                content_type_id=ct.id,
                object_id=str(sheet.id),
                object_repr=f"{sheet.event.name} Score Sheet #{sheet.id}",
                action_flag=CHANGE,
                change_message=change_message,
            )
        except ScoreSheet.DoesNotExist:
            pass
    return redirect(f'/tabulator/review/?sheet={sheet_id}')


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_request_changes(request, sheet_id):
    """Request changes — reverts sheet to pending and logs the action."""
    from events.models import ScoreSheet
    from django.contrib.admin.models import LogEntry, CHANGE
    from django.contrib.contenttypes.models import ContentType
    if request.method == 'POST':
        try:
            sheet = ScoreSheet.objects.select_related('event').get(id=sheet_id)
            sheet.status = 'pending'
            sheet.save()
            ct = ContentType.objects.get_for_model(ScoreSheet)
            LogEntry.objects.create(
                user=request.user,
                content_type_id=ct.id,
                object_id=str(sheet.id),
                object_repr=f"{sheet.event.name} Score Sheet #{sheet.id}",
                action_flag=CHANGE,
                change_message="Requested changes — score sheet reverted to pending."
            )
        except ScoreSheet.DoesNotExist:
            pass
    return redirect('/tabulator/review/')
    """AJAX/POST endpoint to save encoded scores for a sheet."""
    from events.models import ScoreSheet
    if request.method != 'POST':
        return redirect('tabulator_encode_score')
    try:
        sheet = ScoreSheet.objects.get(id=sheet_id)
        action = request.POST.get('action', 'save')
        if action == 'mark_encoded':
            sheet.status = 'confirmed'
            sheet.tabulator = request.user
            sheet.save()
        else:
            sheet.tabulator = request.user
            sheet.save()
    except ScoreSheet.DoesNotExist:
        pass
    return redirect(f"{'/tabulator/encode/'}?sheet={sheet_id}")


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_review(request):
    """Review & Verify — detail view for a sheet or list of encoded sheets."""
    from events.models import ScoreSheet, Event, BracketTeam, JudgingEvent, Criterion, JudgeScore, Candidate

    sheet_id = request.GET.get('sheet', '').strip()
    current_event = Event.objects.filter(status='active').order_by('-event_date').first()

    review_sheet = None
    review_teams = []
    review_criteria = []
    revision_history = []
    total_possible_pts = 0
    encoded_teams = 0

    if sheet_id and sheet_id.isdigit():
        try:
            s = ScoreSheet.objects.select_related('event', 'match', 'tabulator').get(id=int(sheet_id))
            idx = ScoreSheet.objects.filter(created_at__lte=s.created_at).count()
            review_sheet = {
                'id': s.id,
                'ss_id': f'SS-{s.created_at.year}-{str(idx).zfill(3)}',
                'event_name': s.event.name,
                'category': s.event.category,
                'judge_name': s.judges_names or 'Unknown Judge',
                'date_received': s.created_at.strftime('%b %d, %Y %I:%M %p'),
                'status': s.status,
                'status_label': s.get_status_display(),
            }

            # Try to get criteria from linked JudgingEvent
            judging_event = getattr(s.event, 'judging_event', None)
            if judging_event:
                criteria_qs = list(Criterion.objects.filter(event=judging_event).order_by('order'))
                for c in criteria_qs:
                    review_criteria.append({'id': c.id, 'name': c.name, 'max_score': int(c.max_score)})
                total_possible_pts = sum(c['max_score'] for c in review_criteria)

                # Build team rows from Candidates + JudgeScores
                candidates = list(Candidate.objects.filter(event=judging_event).order_by('number'))
                for cand in candidates:
                    scores_qs = list(JudgeScore.objects.filter(candidate=cand).select_related('criterion'))
                    # Average scores across all judges per criterion
                    crit_scores = {}
                    for sc in scores_qs:
                        cid = sc.criterion_id
                        if cid not in crit_scores:
                            crit_scores[cid] = []
                        crit_scores[cid].append(float(sc.score))
                    # Build per-criterion average values
                    score_values = []
                    for c in criteria_qs:
                        vals = crit_scores.get(c.id, [])
                        avg = round(sum(vals) / len(vals), 1) if vals else 0
                        score_values.append(avg)
                    total = round(sum(score_values), 1)
                    # Consistency: locked = verified, unlocked = revised
                    has_unlocked = any(sc.is_locked is False for sc in scores_qs)
                    consistency = 'revised' if has_unlocked else 'consistent'
                    review_teams.append({
                        'id': cand.id,
                        'name': f'#{cand.number} {cand.name}',
                        'score_values': score_values,
                        'total': total,
                        'consistency': consistency,
                    })
            else:
                # Fallback: use BracketTeams — show aggregate scores from ScoreSheet
                teams_qs = BracketTeam.objects.filter(event=s.event).select_related('department')
                for team in teams_qs:
                    review_teams.append({
                        'id': team.id,
                        'name': team.name,
                        'score_values': [],
                        'total': '—',
                        'consistency': 'consistent',
                    })
                total_possible_pts = 100

            encoded_teams = len(review_teams)

            # Revision history from LogEntry — filter by ScoreSheet content type + object_id
            from django.contrib.admin.models import LogEntry
            from django.contrib.contenttypes.models import ContentType
            try:
                ss_ct = ContentType.objects.get_for_model(ScoreSheet)
                history_qs = LogEntry.objects.filter(
                    content_type=ss_ct,
                    object_id=str(s.id)
                ).select_related('user').order_by('-action_time')[:10]
            except Exception:
                history_qs = []
            for log in history_qs:
                msg = log.change_message or 'Updated'
                if 'locked' in msg.lower() or 'verified' in msg.lower():
                    action_type = 'locked'
                elif 'request' in msg.lower() or 'pending' in msg.lower():
                    action_type = 'revised'
                else:
                    action_type = 'change'
                revision_history.append({
                    'date': log.action_time.strftime('%b %d, %Y %I:%M %p'),
                    'action_label': msg[:80],
                    'action_type': action_type,
                    'details': log.object_repr,
                    'by': log.user.username if log.user else 'System',
                })
        except ScoreSheet.DoesNotExist:
            pass

    # List view (no sheet selected)
    event_filter = request.GET.get('event', 'all').strip()
    search_q = request.GET.get('q', '').strip().lower()
    sheets_qs = ScoreSheet.objects.select_related('event', 'match', 'tabulator').filter(
        status__in=('confirmed', 'finalized')).order_by('-created_at')
    if event_filter != 'all':
        sheets_qs = sheets_qs.filter(event__name=event_filter)
    all_sheets = list(sheets_qs)
    if search_q:
        all_sheets = [s for s in all_sheets if search_q in s.event.name.lower()]

    total_encoded = len(all_sheets)
    pending_review = sum(1 for s in all_sheets if s.status == 'confirmed')
    discrepancies = 0
    verified_count = sum(1 for s in all_sheets if s.status == 'finalized')

    review_rows = []
    for i, s in enumerate(all_sheets):
        ss_id = f'SS-{s.created_at.year}-{str(i + 1).zfill(3)}'
        review_rows.append({
            'id': s.id, 'ss_id': ss_id,
            'event_name': s.event.name, 'category': s.event.category,
            'judge_name': s.judges_names or 'Unknown Judge',
            'encoded_by': s.tabulator.username if s.tabulator else 'Unknown',
            'encoded_at': s.updated_at.strftime('%b %d, %Y %I:%M %p'),
            'status': s.status, 'status_label': s.get_status_display(),
        })

    events = list(Event.objects.values_list('name', flat=True).order_by('name'))

    return render(request, 'tabulatordash/review.html', {
        'display_name': _tabulator_display_name(request.user),
        'current_event': current_event,
        'review_sheet': review_sheet,
        'review_teams': review_teams,
        'review_criteria': review_criteria,
        'revision_history': revision_history,
        'total_possible_pts': total_possible_pts or 100,
        'encoded_teams': encoded_teams,
        'total_encoded': total_encoded,
        'pending_review': pending_review,
        'discrepancies': discrepancies,
        'verified_count': verified_count,
        'review_rows': review_rows,
        'events': events,
        'selected_event': event_filter,
        'search_query': search_q,
    })


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_results(request):
    """Results — finalized rankings per event."""
    from events.models import Event, ScoreSheet, BracketTeam

    event_filter = request.GET.get('event', 'all').strip()
    export_fmt = request.GET.get('export', '').strip().lower()
    events = list(Event.objects.order_by('name'))
    current_event = Event.objects.filter(status='active').order_by('-event_date').first()

    sheets_qs = ScoreSheet.objects.select_related('event', 'match', 'winner').filter(status='finalized')
    if event_filter != 'all' and event_filter.isdigit():
        sheets_qs = sheets_qs.filter(event_id=int(event_filter))

    team_scores = {}
    for s in sheets_qs:
        if s.winner:
            key = (s.event_id, s.winner.id)
            team_scores.setdefault(key, {
                'team_name': s.winner.name,
                'event_name': s.event.name,
                'category': s.event.category,
                'department': s.winner.department.name if s.winner.department else '',
                'total_score': 0,
            })
            team_scores[key]['total_score'] += float(s.score_team_a or 0)

    sorted_teams = sorted(team_scores.values(), key=lambda x: x['total_score'], reverse=True)
    result_rows = []
    for rank, row in enumerate(sorted_teams, start=1):
        result_rows.append({
            'rank': rank,
            'team_name': row['team_name'],
            'event_name': row['event_name'],
            'category': row['category'],
            'department': row['department'],
            'total_score': f"{row['total_score']:.2f}",
        })

    # Event info for header card
    scores = [float(r['total_score']) for r in result_rows]
    event_info = {
        'name': sorted_teams[0]['event_name'] if sorted_teams else '—',
        'category': sorted_teams[0]['category'] if sorted_teams else '—',
        'date_range': current_event.schedule_label if current_event else '—',
        'ss_id': 'SS-2025-001',
        'judge': '—',
        'judge_count': 1,
        'criteria_count': 5,
        'total_pts': 100,
        'highest_score': f"{max(scores):.2f}" if scores else '0.00',
        'lowest_score': f"{min(scores):.2f}" if scores else '0.00',
        'avg_score': f"{sum(scores)/len(scores):.2f}" if scores else '0.00',
    }

    # Score distribution buckets
    buckets = {'90-100': 0, '80-89': 0, '70-79': 0, 'Below 70': 0}
    for s in scores:
        if s >= 90: buckets['90-100'] += 1
        elif s >= 80: buckets['80-89'] += 1
        elif s >= 70: buckets['70-79'] += 1
        else: buckets['Below 70'] += 1
    dist_data = {'labels': list(buckets.keys()), 'values': list(buckets.values())}

    # Audit logs
    from django.contrib.admin.models import LogEntry
    audit_logs = []
    for log in LogEntry.objects.select_related('user').order_by('-action_time')[:10]:
        audit_logs.append({
            'date': log.action_time.strftime('%b %d, %Y %I:%M %p'),
            'action': log.change_message or 'Updated',
            'user': log.user.username if log.user else 'System',
        })

    return render(request, 'tabulatordash/results.html', {
        'display_name': _tabulator_display_name(request.user),
        'current_event': current_event,
        'result_rows': result_rows,
        'events': events,
        'selected_event': event_filter,
        'event_info': event_info,
        'is_locked': bool(result_rows),
        'dist_data_json': json.dumps(dist_data),
        'audit_logs': audit_logs,
    })


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_activity_logs(request):
    """Activity logs for judges (mobile app) and scorers (tabulator score sheets)."""
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
    from events.models import JudgeActivityLog, ScoreSheet

    tab = request.GET.get('tab', 'judge').strip().lower()
    if tab not in ('judge', 'scorer'):
        tab = 'judge'

    search_q = (request.GET.get('q') or '').strip()
    selected_user = request.GET.get('user', 'all').strip()
    selected_action = request.GET.get('action', 'all').strip()
    selected_event = request.GET.get('event', 'all').strip()
    selected_round = request.GET.get('round', 'all').strip()
    export_format = (request.GET.get('export') or '').strip().lower()

    today = timezone.localdate()

    date_from_raw = (request.GET.get('date_from') or '').strip()
    date_to_raw = (request.GET.get('date_to') or '').strip()
    date_from = None
    date_to = None
    if date_from_raw:
        try:
            date_from = datetime.strptime(date_from_raw, '%Y-%m-%d').date()
        except ValueError:
            date_from = None
    if date_to_raw:
        try:
            date_to = datetime.strptime(date_to_raw, '%Y-%m-%d').date()
        except ValueError:
            date_to = None
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    week_start = today - timedelta(days=today.weekday())
    ss_ct = ContentType.objects.get_for_model(ScoreSheet)
    User = get_user_model()
    tabulator_ids = list(
        User.objects.filter(groups__name__iexact='Tabulator').values_list('id', flat=True).distinct()
    )

    judge_qs = JudgeActivityLog.objects.select_related('event', 'candidate').order_by('-timestamp')
    scorer_qs = LogEntry.objects.filter(
        Q(content_type=ss_ct)
        | Q(change_message__in=['Approved match result', 'Rejected match result'])
    ).filter(user_id__in=tabulator_ids).select_related('user').order_by('-action_time')

    if date_from:
        judge_qs = judge_qs.filter(timestamp__date__gte=date_from)
    if date_to:
        judge_qs = judge_qs.filter(timestamp__date__lte=date_to)
    if date_from:
        scorer_qs = scorer_qs.filter(action_time__date__gte=date_from)
    if date_to:
        scorer_qs = scorer_qs.filter(action_time__date__lte=date_to)

    if selected_user != 'all' and selected_user.isdigit():
        uid = int(selected_user)
        judge_qs = judge_qs.filter(judge_id=uid)
        scorer_qs = scorer_qs.filter(user_id=uid)

    if selected_action != 'all':
        judge_qs = judge_qs.filter(action=selected_action)
        scorer_qs = _filter_scorer_logs_by_action(scorer_qs, selected_action)

    if selected_event != 'all':
        judge_qs = judge_qs.filter(event__title=selected_event)
        scorer_qs = _filter_scorer_logs_by_event(scorer_qs, selected_event, ss_ct)

    if selected_round != 'all' and tab == 'scorer':
        scorer_qs = _filter_scorer_logs_by_round(scorer_qs, selected_round, ss_ct)

    if search_q:
        judge_qs = judge_qs.filter(
            Q(details__icontains=search_q)
            | Q(event__title__icontains=search_q)
            | Q(judge_id__in=User.objects.filter(
                Q(username__icontains=search_q) | Q(first_name__icontains=search_q) | Q(last_name__icontains=search_q)
            ).values_list('id', flat=True))
        )
        scorer_qs = scorer_qs.filter(
            Q(change_message__icontains=search_q)
            | Q(object_repr__icontains=search_q)
            | Q(user__username__icontains=search_q)
        )

    active_qs = judge_qs if tab == 'judge' else scorer_qs

    if export_format == 'csv':
        return _export_tabulator_activity_logs(active_qs, tab)

    total_logs = JudgeActivityLog.objects.count() + LogEntry.objects.filter(
        Q(content_type=ss_ct)
        | Q(change_message__in=['Approved match result', 'Rejected match result'])
    ).filter(user_id__in=tabulator_ids).count()
    today_logs = (
        JudgeActivityLog.objects.filter(timestamp__date=today).count()
        + LogEntry.objects.filter(content_type=ss_ct, action_time__date=today, user_id__in=tabulator_ids).count()
    )
    week_logs = (
        JudgeActivityLog.objects.filter(timestamp__date__gte=week_start).count()
        + LogEntry.objects.filter(
            content_type=ss_ct, action_time__date__gte=week_start, user_id__in=tabulator_ids
        ).count()
    )
    judge_user_ids = set(JudgeActivityLog.objects.values_list('judge_id', flat=True))
    scorer_user_ids = set(
        LogEntry.objects.filter(content_type=ss_ct, user_id__isnull=False).values_list('user_id', flat=True)
    )
    active_users = len(judge_user_ids | scorer_user_ids)

    judge_count = judge_qs.count()
    scorer_count = scorer_qs.count()
    filtered_count = active_qs.count()

    paginator = Paginator(active_qs, 10)
    page_number = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    if tab == 'judge':
        log_rows = _serialize_judge_activity_logs(page_obj.object_list)
        action_choices = JudgeActivityLog.ACTION_CHOICES
    else:
        sheet_cache = _scoresheet_cache_for_log_entries(page_obj.object_list)
        log_rows = _serialize_scorer_activity_logs(page_obj.object_list, sheet_cache)
        action_choices = _SCORER_ACTION_CHOICES

    log_users = _tabulator_activity_log_users(tab, ss_ct, tabulator_ids)
    events = _tabulator_activity_log_events(tab, ss_ct, tabulator_ids)
    rounds = _tabulator_activity_log_rounds(ss_ct, tabulator_ids)

    date_range_label = (
        f'{date_from.strftime("%b %d, %Y")} – {date_to.strftime("%b %d, %Y")}'
        if date_from and date_to else
        f'From {date_from.strftime("%b %d, %Y")}' if date_from else
        f'Until {date_to.strftime("%b %d, %Y")}' if date_to else
        'All dates'
    )
    week_label = f'{week_start.strftime("%b %d")} – {today.strftime("%b %d, %Y")}'
    user_initials = request.user.username[:2].upper() if request.user.username else 'TA'

    return render(request, 'tabulatordash/tabactivitylogs.html', {
        'display_name': _tabulator_display_name(request.user),
        'log_rows': log_rows,
        'page_obj': page_obj,
        'filtered_count': filtered_count,
        'total_logs': total_logs,
        'today_logs': today_logs,
        'week_logs': week_logs,
        'active_users': active_users,
        'today_label': today.strftime('%b %d, %Y'),
        'week_label': week_label,
        'judge_count': judge_count,
        'scorer_count': scorer_count,
        'active_tab': tab,
        'log_users': log_users,
        'action_choices': action_choices,
        'events': events,
        'rounds': rounds,
        'selected_user': selected_user,
        'selected_action': selected_action,
        'selected_event': selected_event,
        'selected_round': selected_round,
        'search_query': search_q,
        'date_from': date_from.isoformat() if date_from else '',
        'date_to': date_to.isoformat() if date_to else '',
        'date_range_label': date_range_label,
        'user_initials': user_initials,
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
        return f'{fallback_label} #{user_id}', 'U' + str(user_id)[-1], 'User'
    name = user.get_full_name() or user.username
    parts = name.split()
    if len(parts) >= 2:
        initials = (parts[0][0] + parts[1][0]).upper()
    else:
        initials = name[:2].upper() if name else 'U?'
    role = 'Judge' if fallback_label == 'Judge' else 'Scorer'
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


@login_required
@user_passes_test(lambda user: user.is_superuser, login_url='login')
def superadmin_dashboard(request):
    User = get_user_model()
    admins = User.objects.filter(is_staff=True).count()
    departments = Group.objects.count()
    total_events = LogEntry.objects.count()
    total_users = User.objects.count()
    users = User.objects.order_by('id')[:5]
    admin_users, admin_rows = get_admin_rows()
    active_admins = sum(1 for row in admin_rows if row['status'] == 'Active')
    deactivated_admins = max(admins - active_admins, 0)

    activity_alerts = []
    recent_logs = LogEntry.objects.select_related('user').order_by('-action_time')[:3]
    action_titles = {
        ADDITION: 'New Activity',
        CHANGE: 'Role Reassignment',
        DELETION: 'Security Update',
    }

    for index, entry in enumerate(recent_logs):
        delta = timezone.now() - entry.action_time
        if delta.days:
            activity_time = f'{delta.days}d ago'
        else:
            minutes = delta.seconds // 60
            activity_time = f'{minutes}m ago' if minutes else 'Just now'

        activity_alerts.append({
            'title': action_titles.get(entry.action_flag, 'System Update'),
            'time': activity_time,
            'message': entry.change_message or f'{entry.object_repr} was updated by {entry.user.username}.',
            'accent': ['green', 'blue', 'yellow'][index % 3],
        })

    return render(request, 'superadmin_dashboard.html', {
        'admins': admins,
        'departments': departments,
        'total_events': total_events,
        'notification_count': len(activity_alerts),
        'total_users': total_users,
        'users': users,
        'admin_rows': admin_rows,
        'activity_alerts': activity_alerts,
        'admin_count': admin_users.count(),
        'department_count': departments,
        'active_admins': active_admins,
        'deactivated_admins': deactivated_admins,
    })


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
    _, all_admin_rows = get_admin_rows()
    departments = Group.objects.exclude(name__in=['Admin', 'Tabulator', 'Judge', 'Judges', 'Viewers']).order_by('name')
    department_names = sorted({row['department'] for row in all_admin_rows})

    q = request.GET.get('q', '').strip()
    selected_department = request.GET.get('department', '').strip()
    selected_role = request.GET.get('role', '').strip()
    selected_status = request.GET.get('status', '').strip()
    sort = request.GET.get('sort', 'username').strip()
    direction = request.GET.get('dir', 'asc').strip().lower()
    page_number = request.GET.get('page', 1)

    filtered_rows = all_admin_rows
    if q:
        q_lower = q.lower()
        filtered_rows = [
            row for row in filtered_rows
            if q_lower in row['user'].username.lower()
            or q_lower in (row['user'].email or '').lower()
            or q_lower in row['department'].lower()
        ]

    if selected_department:
        filtered_rows = [row for row in filtered_rows if row['department'] == selected_department]

    if selected_role:
        filtered_rows = [row for row in filtered_rows if row['role'] == selected_role]

    if selected_status:
        filtered_rows = [row for row in filtered_rows if row['status'] == selected_status]

    reverse_sort = direction == 'desc'
    sortable_fields = {
        'username': lambda row: row['user'].username.lower(),
        'department': lambda row: row['department'].lower(),
        'role': lambda row: row['role'].lower(),
        'status': lambda row: row['status'].lower(),
        'last_login': lambda row: (
            row['user'].last_login is None,
            row['user'].last_login or timezone.now(),
        ),
    }
    if sort not in sortable_fields:
        sort = 'username'
    filtered_rows = sorted(filtered_rows, key=sortable_fields[sort], reverse=reverse_sort)

    paginator = Paginator(filtered_rows, 10)
    page_obj = paginator.get_page(page_number)
    start_index = page_obj.start_index() if paginator.count else 0
    end_index = page_obj.end_index() if paginator.count else 0

    base_params = {
        'q': q,
        'department': selected_department,
        'role': selected_role,
        'status': selected_status,
    }

    return render(request, 'admin.html', {
        'admin_rows': page_obj.object_list,
        'admin_count': len(filtered_rows),
        'total_admin_count': len(all_admin_rows),
        'department_count': departments.count(),
        'departments': departments,
        'department_names': department_names,
        'selected_department': selected_department,
        'selected_role': selected_role,
        'selected_status': selected_status,
        'search_query': q,
        'sort': sort,
        'dir': direction,
        'page_obj': page_obj,
        'start_index': start_index,
        'end_index': end_index,
        'base_params': base_params,
    })


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

        total = matches_qs.count()
        completed = matches_qs.filter(status=BracketMatch.STATUS_COMPLETED).count()
        ongoing = matches_qs.filter(status=BracketMatch.STATUS_ONGOING).count()
        bracket_meta = get_bracket_status_meta(total > 0, total, completed, ongoing)

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
