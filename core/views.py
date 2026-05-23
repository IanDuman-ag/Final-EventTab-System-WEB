from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth.models import Group
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render, get_object_or_404
from django.utils import timezone
from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.contrib.contenttypes.models import ContentType
import json
import csv
from datetime import datetime, timedelta

from events.models import (
    Event, Department,
    Portion, CandidateNumber, Contestant, JudgeAssignment,
    ScoringCriterion, Chairperson, BracketMatch,
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

ASSIGNMENT_ROLE_LABELS = {'Tabulator', 'Judge'}
ASSIGNMENT_GROUP_ALIASES = {
    'tabulator': 'Tabulator',
    'judge': 'Judge',
    'judges': 'Judge',
}
RESERVED_DEPARTMENT_NAMES = {'admin', 'tabulator', 'judge', 'judges', 'viewers'}


def normalize_event_availability_status(status):
    status = (status or Event.STATUS_ACTIVE).strip().lower()
    if status in {Event.STATUS_INACTIVE, 'deactivate', 'deactivated'}:
        return Event.STATUS_INACTIVE
    return Event.STATUS_ACTIVE


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
        reserved_groups = {'Admin', 'Tabulator', 'Judge', 'Judges', 'Viewers'}
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
                'status': 'active'
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


@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_create_department(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)
    
    try:
        # Support both JSON and Form data
        if request.content_type == 'application/json':
            data = json.loads(request.body)
        else:
            data = request.POST

        name = data.get('name', '').strip()
        code = data.get('code', '').strip()
        unit_number = data.get('unit_number', '').strip()
        delegation_color = data.get('color', '#022068')
        head = data.get('head', '').strip()
        status = data.get('status', 'active')
        remarks = data.get('remarks', '').strip()

        if not name or not code:
            return JsonResponse({'success': False, 'message': 'Name and Code are required.'}, status=400)

        # Create matching Group for role system
        Group.objects.get_or_create(name=name)
        
        dept, created = Department.objects.update_or_create(
            code=code,
            defaults={
                'name': name,
                'unit_number': unit_number,
                'delegation_color': delegation_color,
                'head': head,
                'status': status,
                'remarks': remarks
            }
        )
        
        return JsonResponse({'success': True, 'message': f'Department {name} created.', 'id': dept.id})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@login_required
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


@login_required
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
        return JsonResponse({'success': True, 'message': f'Department {name} updated.'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_delete_department(request, dept_id):
    """Delete a Department."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    dept = get_object_or_404(Department, id=dept_id)
    name = dept.name
    try:
        Event.objects.filter(department__iexact=name).update(department='')
        dept.delete()
        return JsonResponse({'success': True, 'message': f'Department {name} deleted.'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@never_cache
@ensure_csrf_cookie
def login_view(request):
    context = {'roles': ROLE_CHOICES}

    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        role = request.POST.get('role', '')

        context.update({
            'email': email,
            'selected_role': role,
        })

        if role not in ROLE_CHOICES:
            context['error'] = 'Select a valid role.'
            return render(request, 'login.html', context)

        user = authenticate_email(request, email, password)
        if user is None:
            context['error'] = 'Invalid email or password.'
            return render(request, 'login.html', context)

        if not user_has_role(user, role):
            context['error'] = 'Your account does not match the selected role.'
            return render(request, 'login.html', context)

        login(request, user)
        request.session['login_role'] = role
        if role == 'super-admin':
            return redirect('superadmin_dashboard')
        if role == 'admin':
            return redirect('admin_dashboard')
        if role == 'tabulator':
            return redirect('tabulator_dashboard')

        context['success'] = f'Logged in as {ROLE_CHOICES[role]}.'

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
        start_date_str = request.POST.get('start_date', '').strip()
        end_date_str = request.POST.get('end_date', '').strip()
        included_metrics = request.POST.getlist('metrics')

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
                stream = generate_excel_report(event_name, dept_name, start_date, end_date, included_metrics)
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
                stream = generate_pdf_report(event_name, dept_name, start_date, end_date, included_metrics)
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
    total_students = sum(row['students'] for row in all_rows)
    return render(request, 'admindash/department.html', {
        'department_rows': department_rows,
        'total_department_count': len(all_rows),
        'visible_department_count': len(department_rows),
        'search_query': search_query,
        'with_dean_count': with_dean_count,
        'total_students_count': total_students,
        'new_department_count': 0,
    })


def _split_full_name(full_name):
    name_parts = (full_name or '').strip().split()
    if not name_parts:
        return '', ''
    if len(name_parts) == 1:
        return name_parts[0], ''
    return name_parts[0], ' '.join(name_parts[1:])


def _normalize_assignment_role(role):
    role_label = (role or '').strip().title()
    if role_label not in ASSIGNMENT_ROLE_LABELS:
        return None
    return role_label


def _apply_assignment_role(user, role, is_active):
    role_label = _normalize_assignment_role(role)
    if role_label is None:
        raise ValueError('Select either Tabulator or Judge as the account role.')

    assignment_groups = Group.objects.filter(
        Q(name__iexact='Tabulator')
        | Q(name__iexact='Judge')
        | Q(name__iexact='Judges')
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
        'role': (payload.get('role') or '').strip(),
        'is_active': bool(payload.get('is_active', True)),
    }


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def create_assignment_account(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=400)

    payload = _assignment_payload(request)
    if not payload['username'] or not payload['email'] or not payload['password']:
        return JsonResponse({'success': False, 'message': 'Username, email, and password are required.'}, status=400)

    role_label = _normalize_assignment_role(payload['role'])
    if role_label is None:
        return JsonResponse({'success': False, 'message': 'Select either Tabulator or Judge as the account role.'}, status=400)

    User = get_user_model()
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
    if not payload['username'] or not payload['email']:
        return JsonResponse({'success': False, 'message': 'Username and email are required.'}, status=400)

    role_label = _normalize_assignment_role(payload['role'])
    if role_label is None:
        return JsonResponse({'success': False, 'message': 'Select either Tabulator or Judge as the account role.'}, status=400)

    User = get_user_model()
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

    today = timezone.localdate()
    event_rows = []
    for ev in event_qs:
        status_meta = get_event_status_meta(ev, today)
        if status_filter != 'all' and status_meta['status'] != status_filter:
            continue

        event_rows.append({
            'id':               ev.id,
            'name':             ev.name,
            'category':         ev.category,
            'division':         ev.division,
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
            'mobile_published': ev.judging_event is not None,
            'assigned_judge_ids': list(ev.assigned_judges.values_list('id', flat=True)),
            'assigned_judges_display': ', '.join(
                u.get_full_name() or u.username
                for u in ev.assigned_judges.all()
            ),
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
    category_names  = sorted({ev.category for ev in all_events if ev.category}) or ['Sports', 'Esports', 'Pageant']

    create_category_options = sorted(set(category_names) | {'Academic', 'Sports', 'Esports', 'Socio-cultural', 'Pageant'})
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
    all_events = list(Event.objects.all())
    today = timezone.localdate()
    event_rows = []
    for ev in all_events[:50]:
        status_meta = get_event_status_meta(ev, today)
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

    # Sync with BracketMatch
    match = scoresheet.match
    match.score_a = f"{scoresheet.score_team_a:.1f}"
    match.score_b = f"{scoresheet.score_team_b:.1f}"
    match.winner = scoresheet.winner

    # Identify loser
    if match.winner == match.team_a:
        match.loser = match.team_b
    else:
        match.loser = match.team_a

    match.status = BracketMatch.STATUS_COMPLETED
    match.save()

    # Advance winner to the next round if applicable
    if match.next_match_winner:
        next_m = match.next_match_winner
        if next_m.team_a != match.winner and next_m.team_b != match.winner:
            if next_m.team_a is None:
                next_m.team_a = match.winner
            elif next_m.team_b is None:
                next_m.team_b = match.winner
            next_m.save()

    # Advance loser to loser bracket if double elimination (if next_match_loser exists)
    if match.next_match_loser:
        next_l = match.next_match_loser
        if next_l.team_a != match.loser and next_l.team_b != match.loser:
            if next_l.team_a is None:
                next_l.team_a = match.loser
            elif next_l.team_b is None:
                next_l.team_b = match.loser
            next_l.save()

    return JsonResponse({
        'success': True,
        'message': f'Scoresheet for Match {match.match_number} approved successfully. Bracket updated.'
    })


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


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_dashboard(request):
    """Tabulator home: assigned events and verification queue (sample data until event models exist)."""
    total_assigned = 24
    completed_count = 18
    pending_verify = 6
    completion_rate = round(100 * completed_count / total_assigned) if total_assigned else 0
    return render(request, 'tabulatordash/tabdashboard.html', {
        'display_name': _tabulator_display_name(request.user),
        'pending_critical': 4,
        'total_assigned': total_assigned,
        'completed_count': completed_count,
        'pending_verify_fmt': f'{pending_verify:02d}',
        'completion_rate': completion_rate,
        'next_up_items': [
            {'code': 'ITB 201', 'tag': 'Academic', 'tag_variant': 'academic', 'title': 'Quiz Bee', 'status_line': ''},
            {'code': 'MSC 02', 'tag': 'Esports', 'tag_variant': 'esports', 'title': 'Mobile Legends (Women)', 'status_line': ''},
            {'code': 'CC 1', 'tag': 'Sports', 'tag_variant': 'sports', 'title': '3v3 Basketball (Men)', 'status_line': 'Pending Release'},
        ],
    })


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_assigned_events(request):
    """Tabulator assigned events list (sample data until event models exist)."""
    return render(request, 'tabulatordash/assigned.html', {
        'display_name': _tabulator_display_name(request.user),
        'assigned_stats': [
            {'label': 'Total Events', 'value': '24', 'variant': 'blue', 'icon': 'clipboard'},
            {'label': 'Pending', 'value': '12', 'variant': 'amber', 'icon': 'pending'},
            {'label': 'Verified', 'value': '08', 'variant': 'green', 'icon': 'verified'},
            {'label': 'Disputes', 'value': '04', 'variant': 'red', 'icon': 'dispute'},
        ],
        'assigned_rows': [
            {
                'title': 'Chess',
                'code': 'CW-2024-001',
                'category': 'Sports',
                'panel_initials': ['JD', 'MK', 'LP'],
                'panel_meta': '+2',
                'status': 'awaiting',
                'status_label': 'Awaiting Scores',
                'action_label': 'TABULATE',
                'action_kind': 'link',
            },
            {
                'title': 'Modern Dance Competition',
                'code': 'AM-2024-042',
                'category': 'Socio-cultural',
                'panel_initials': ['AR', 'BN'],
                'panel_meta': '3/3',
                'status': 'ready',
                'status_label': 'Ready for Verification',
                'action_label': 'TABULATE',
                'action_kind': 'primary',
            },
            {
                'title': 'Singing Competition',
                'code': 'WH-2024-118',
                'category': 'Socio-cultural',
                'panel_initials': ['KC'],
                'panel_meta': '1/4',
                'status': 'idle',
                'status_label': 'Not Started',
                'action_label': 'TABULATE',
                'action_kind': 'link',
            },
            {
                'title': 'Essay Writing',
                'code': 'BS-2024-009',
                'category': 'Academic',
                'panel_initials': ['LM', 'PQ', 'RS'],
                'panel_meta': '',
                'status': 'done',
                'status_label': 'Verified',
                'action_label': 'VIEW REPORT',
                'action_kind': 'link',
            },
        ],
        'assigned_showing': 4,
        'assigned_total': 24,
        'assigned_page': 1,
        'assigned_page_count': 3,
        'assigned_page_numbers': [1, 2, 3],
    })


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_scoresheets(request):
    """Tabulator scoresheet review: verify judge scores, deductions, and totals (sample data)."""
    return render(request, 'tabulatordash/scoresheets.html', {
        'display_name': _tabulator_display_name(request.user),
        'sheet_total_events': 42,
        'sheet_category_count': 4,
        'sheet_options': [
            {'id': 'rq-d1', 'label': 'Regional Qualifiers — Day 1'},
            {'id': 'rq-d2', 'label': 'Regional Qualifiers — Day 2'},
            {'id': 'finals', 'label': 'Championship Finals'},
        ],
        'sheet_selected_id': 'rq-d1',
        'sheet_rows': [
            {
                'title': 'Table Tennis',
                'category': 'Sports',
                'accent': 'amber',
                'j1': '88.0', 'j2': '90.0', 'j3': '89.0',
                'raw_avg': '89.00',
                'deduction': '0.0',
                'deduction_alert': False,
                'final': '89.00',
            },
            {
                'title': 'Quiz Bee',
                'category': 'Academic',
                'accent': 'blue',
                'j1': '91.0', 'j2': '93.5', 'j3': '92.0',
                'raw_avg': '92.17',
                'deduction': '0.5',
                'deduction_alert': True,
                'final': '91.67',
            },
            {
                'title': 'Spelling Bee',
                'category': 'Academic',
                'accent': 'slate',
                'j1': '95.0', 'j2': '94.0', 'j3': '96.0',
                'raw_avg': '95.00',
                'deduction': '0.0',
                'deduction_alert': False,
                'final': '95.00',
            },
            {
                'title': 'Modern Dance',
                'category': 'Socio-cultural',
                'accent': 'rose',
                'j1': '87.5', 'j2': '88.0', 'j3': '87.0',
                'raw_avg': '87.50',
                'deduction': '1.0',
                'deduction_alert': True,
                'final': '86.50',
            },
        ],
        'last_computed': 'Today at 14:32:05',
    })


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


@login_required(login_url='login')
@user_passes_test(lambda u: user_has_role(u, 'tabulator'), login_url='login')
def tabulator_reports(request):
    """Tabulator Reports and Metrics page"""
    from events.models import Event
    # Gathers mock/live statistics for tabulator
    assigned_count = 5
    completed_count = 3
    accuracy_rate = 98.4

    assigned_events = []
    events_qs = Event.objects.all().order_by('-event_date')
    for event in events_qs:
        assigned_events.append({
            'name': event.name,
            'category': event.category,
            'date': event.event_date.strftime('%b %d, %Y'),
            'status': event.get_status_display(),
            'completion': '100%' if event.status == 'completed' else '60%',
        })

    # Default fallback data if empty
    if not assigned_events:
        assigned_events = [
            {'name': 'National Science Decathlon 2024', 'category': 'Academic', 'date': 'Oct 24, 2024', 'status': 'Active', 'completion': '80%'},
            {'name': 'Regional Athletics Meet 2024', 'category': 'Sports', 'date': 'Nov 12, 2024', 'status': 'Upcoming', 'completion': '0%'},
            {'name': 'Spelling Bee Finals', 'category': 'Academic', 'date': 'May 15, 2026', 'status': 'Completed', 'completion': '100%'},
        ]

    return render(request, 'tabulatordash/tabreport.html', {
        'display_name': _tabulator_display_name(request.user),
        'assigned_count': assigned_count,
        'completed_count': completed_count,
        'accuracy_rate': accuracy_rate,
        'assigned_events': assigned_events,
    })


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
        Group.objects.exclude(name__in=['Admin', 'Tabulator', 'Judge', 'Judges', 'Viewers']).order_by('name').values_list('name', flat=True)
    )
    
    # Dynamic Event Loading
    events = list(Event.objects.all().order_by('-event_date').values_list('name', flat=True))
    if not events:
        events = ['National Science Decathlon 2024']

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

    title_map = {
        ADDITION: 'New Account Created',
        CHANGE: 'Privilege / Data Update',
        DELETION: 'Security / Record Removed',
    }
    class_map = {
        ADDITION: 'info',
        CHANGE: 'warning',
        DELETION: 'danger',
    }
    pill_map = {
        ADDITION: 'info',
        CHANGE: 'warning',
        DELETION: 'critical',
    }
    badge_map = {
        ADDITION: 'User Action',
        CHANGE: 'Policy Update',
        DELETION: 'Security',
    }

    timeline_rows = []
    grouped = {}
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)

    paginator = Paginator(list(logs_qs[:250]), 20)
    page_obj = paginator.get_page(page_number)
    start_index = page_obj.start_index() if paginator.count else 0
    end_index = page_obj.end_index() if paginator.count else 0

    for entry in page_obj.object_list:
        entry_date = timezone.localtime(entry.action_time).date()
        if entry_date == today:
            day_label = 'Today'
        elif entry_date == yesterday:
            day_label = 'Yesterday'
        else:
            day_label = entry_date.strftime('%b %d, %Y')

        local_action_time = timezone.localtime(entry.action_time)
        delta = now_local - local_action_time
        if delta.days:
            relative_time = f'{delta.days}d ago'
        else:
            mins = delta.seconds // 60
            relative_time = f'{mins}m ago' if mins else 'Just now'

        grouped.setdefault(day_label, []).append({
            'time': local_action_time.strftime('%I:%M').lstrip('0') or '0:00',
            'period': timezone.localtime(entry.action_time).strftime('%p'),
            'exact_time': local_action_time.strftime('%b %d, %Y %I:%M %p'),
            'relative_time': relative_time,
            'title': title_map.get(entry.action_flag, 'System Update'),
            'message': entry.change_message or f'{entry.object_repr} was updated.',
            'actor': entry.user.username if entry.user else 'System',
            'row_class': class_map.get(entry.action_flag, 'neutral'),
            'badge': badge_map.get(entry.action_flag, 'User Action'),
            'status': pill_map.get(entry.action_flag, 'active'),
        })

    for label, entries in grouped.items():
        timeline_rows.append({'label': label, 'entries': entries})

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
        from events.bracket_generator import generate_single_elimination, generate_double_elimination, generate_round_robin
        
        data = json.loads(request.body)
        event_data = data.get('event')
        event_id = event_data.get('event_id') if isinstance(event_data, dict) else event_data
        format_type = event_data.get('tournament_format') if isinstance(event_data, dict) else data.get('tournament_format')
        teams_data = data.get('teams', [])
        
        if not event_id or not format_type or not teams_data:
            return JsonResponse({'success': False, 'message': 'Event, format, and teams are required.'}, status=400)
            
        event = get_object_or_404(Event, id=event_id)
        
        # Check if matches already exist for this event
        if BracketMatch.objects.filter(event=event).exists():
             BracketMatch.objects.filter(event=event).delete()
             BracketTeam.objects.filter(event=event).delete()
        
        # Create Teams
        bracket_teams = []
        team_id_to_obj = {}
        for index, team_data in enumerate(teams_data):
            dept_id = team_data.get('department')
            department = Department.objects.get(id=dept_id) if dept_id else None
            
            name = team_data.get('team_name') or team_data.get('name') or f'Team {index+1}'
            seed = team_data.get('seed_number', index + 1)
            
            team = BracketTeam.objects.create(
                event=event,
                name=name,
                department=department,
                members=team_data.get('members', ''),
                seed=seed
            )
            bracket_teams.append(team)
            team_id_to_obj[index] = team

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
        else:
            # Generate matches based on format (legacy auto-mode)
            if format_type == 'Single Elimination':
                generate_single_elimination(event, bracket_teams)
            elif format_type == 'Double Elimination':
                generate_double_elimination(event, bracket_teams)
            elif format_type == 'Round Robin':
                generate_round_robin(event, bracket_teams)
            else:
                return JsonResponse({'success': False, 'message': 'Unsupported tournament format.'}, status=400)

        sync_event_to_mobile(event)
        return JsonResponse({'success': True, 'message': 'Bracket generated and synced to the judge mobile app.'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)

@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_view_bracket(request, event_id):
    if request.method != 'GET':
        return JsonResponse({'success': False, 'message': 'Invalid method'}, status=400)
    try:
        from events.models import Event, BracketMatch
        event = get_object_or_404(Event, id=event_id)
        matches = BracketMatch.objects.filter(event=event).select_related('team_a', 'team_b', 'winner')
        
        matches_data = []
        for m in matches:
            matches_data.append({
                'id': m.id,
                'match_number': m.match_number,
                'round_name': m.round_name,
                'team_a': m.team_a.name if m.team_a else 'TBD',
                'team_b': m.team_b.name if m.team_b else 'TBD',
                'team_a_id': m.team_a.id if m.team_a else None,
                'team_b_id': m.team_b.id if m.team_b else None,
                'winner': m.winner.name if m.winner else None,
                'score_a': m.score_a,
                'score_b': m.score_b,
                'status': m.status,
                'next_match_winner_id': m.next_match_winner.id if m.next_match_winner else None
            })
            
        return JsonResponse({'success': True, 'matches': matches_data, 'format': 'Unknown (Requires Event Format Tracking)'})
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
        teams = BracketTeam.objects.filter(event=event).order_by('seed')
        teams_data = []
        for t in teams:
            teams_data.append({
                'name': t.name,
                'department_id': t.department.id if t.department else '',
                'seed': t.seed,
                'members': t.members
            })
        return JsonResponse({'success': True, 'teams': teams_data})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)
