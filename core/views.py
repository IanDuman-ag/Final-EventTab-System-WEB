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

from events.models import Event, Department


ROLE_CHOICES = {
    'super-admin': 'Super Admin',
    'admin': 'Admin',
    'tabulator': 'Tabulator',
    'judge': 'Judge',
    'viewers': 'Viewers',
}

ASSIGNMENT_ROLE_LABELS = {'Tabulator', 'Judge'}
ASSIGNMENT_GROUP_ALIASES = {
    'tabulator': 'Tabulator',
    'judge': 'Judge',
    'judges': 'Judge',
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

    return user.groups.filter(name__iexact=ROLE_CHOICES[role]).exists()


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
    all_events = Event.objects.all()
    ctx['ongoing_count'] = all_events.filter(status=Event.STATUS_ACTIVE).count()
    ctx['total_events_count'] = all_events.count()
    ctx['completed_events_count'] = all_events.filter(status=Event.STATUS_COMPLETED).count()
    total = ctx['total_events_count'] or 1
    ctx['completed_pct'] = int(ctx['completed_events_count'] / total * 100)
    # Serialize events for the calendar (rendered as JSON in template)
    events_json = []
    for ev in all_events:
        events_json.append({
            'id': ev.id,
            'title': ev.name,
            'date': ev.event_date.strftime('%Y-%m-%d') if ev.event_date else '',
            'time': ev.event_time.strftime('%H:%M') if ev.event_time else '',
            'category': ev.category,
            'venue': ev.venue,
            'status': ev.status,
        })
    ctx['events_json'] = json.dumps(events_json)
    return render(request, 'admindash/admindashboard.html', ctx)


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_manage_account(request):
    return render(request, 'admindash/manageacc.html', get_assignment_account_context(request))


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


def _event_progress_meta(status_key):
    """Map internal status to lifecycle labels (SYSTEM_FLOW event statuses)."""
    mapping = {
        'upcoming': {
            'progress_label': 'Setup',
            'progress_detail': 'Categories and schedule configured; not yet judging.',
            'progress_pct': 35,
            'stage_current': 2,
        },
        'active': {
            'progress_label': 'Active (Judging)',
            'progress_detail': 'Judges may submit scores; monitor submissions.',
            'progress_pct': 65,
            'stage_current': 4,
        },
        'completed': {
            'progress_label': 'Completed',
            'progress_detail': 'Event closed; results may be published or archived.',
            'progress_pct': 100,
            'stage_current': 6,
        },
    }
    return mapping.get(status_key, mapping['active'])


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

        Event.objects.create(
            name              = event_name[:200],
            category          = category,
            division          = request.POST.get('division', '').strip(),
            department        = request.POST.get('department', '').strip(),
            event_date        = event_date,
            event_time        = event_time_val,
            venue             = venue,
            status            = Event.STATUS_UPCOMING,
            max_participants  = _int_or_none(request.POST.get('max_participants', '')),
            num_teams         = _int_or_none(request.POST.get('num_teams', '')),
            faculty_in_charge = request.POST.get('faculty_in_charge', '').strip(),
            student_in_charge = request.POST.get('student_in_charge', '').strip(),
            mechanics         = request.POST.get('mechanics', '').strip(),
            scoring_criteria  = request.POST.get('scoring_criteria', '').strip(),
            created_by        = request.user,
        )
        messages.success(request, f'Event "{event_name}" created successfully.')
        return redirect('admin_manage_events')

    status_filter   = request.GET.get('status', 'all').strip().lower()
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
    if status_filter != 'all':
        event_qs = event_qs.filter(status=status_filter)
    if schedule_filter:
        event_qs = event_qs.filter(event_date__icontains=schedule_filter)

    event_rows = []
    for ev in event_qs[:30]:
        event_rows.append({
            'id':               ev.id,
            'name':             ev.name,
            'category':         ev.category,
            'division':         ev.division,
            'department':       ev.department,
            'schedule_label':   ev.schedule_label,
            'event_date':       ev.event_date.strftime('%Y-%m-%d') if ev.event_date else '',
            'event_time':       ev.event_time_str,
            'venue':            ev.venue,
            'status':           ev.status,
            'max_participants': str(ev.max_participants) if ev.max_participants else '',
            'num_teams':        str(ev.num_teams) if ev.num_teams else '',
            'faculty_in_charge': ev.faculty_in_charge,
            'student_in_charge': ev.student_in_charge,
            'mechanics':        ev.mechanics,
            'scoring_criteria': ev.scoring_criteria,
        })

    all_events = Event.objects.all()
    active_count    = all_events.filter(status=Event.STATUS_ACTIVE).count()
    upcoming_count  = all_events.filter(status=Event.STATUS_UPCOMING).count()
    completed_count = all_events.filter(status=Event.STATUS_COMPLETED).count()
    total_events_count = all_events.count()
    category_names  = sorted(all_events.values_list('category', flat=True).distinct()) or ['Sports', 'Esports', 'Pageant']

    department_names = list(Group.objects.order_by('name').values_list('name', flat=True))
    create_category_options = sorted(set(category_names) | {'Academic', 'Sports', 'Esports', 'Socio-cultural', 'Pageant'})
    create_division_options = ['College', 'Senior High School', 'Junior High School', 'Elementary']

    return render(request, 'admindash/event.html', {
        'event_rows':             event_rows,
        'active_count':           active_count,
        'upcoming_count':         upcoming_count,
        'completed_count':        completed_count,
        'total_events_count':     total_events_count,
        'category_names':         category_names,
        'department_names':       department_names,
        'create_category_options': create_category_options,
        'create_division_options': create_division_options,
        'selected_category':      category_filter,
        'selected_status':        status_filter,
        'search_query':           search_query,
        'schedule_filter':        schedule_filter,
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
    ev.department        = request.POST.get('department', '').strip()
    ev.event_date        = event_date
    ev.event_time        = event_time_val
    ev.venue             = venue
    ev.max_participants  = _int_or_none(request.POST.get('max_participants', ''))
    ev.num_teams         = _int_or_none(request.POST.get('num_teams', ''))
    ev.faculty_in_charge = request.POST.get('faculty_in_charge', '').strip()
    ev.student_in_charge = request.POST.get('student_in_charge', '').strip()
    ev.mechanics         = request.POST.get('mechanics', '').strip()
    ev.scoring_criteria  = request.POST.get('scoring_criteria', '').strip()
    ev.save()

    messages.success(request, f'Event "{event_name}" updated successfully.')
    return redirect('admin_manage_events')


@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_delete_event(request, event_id):
    """Delete an Event from the database."""
    if request.method != 'POST':
        return redirect('admin_manage_events')

    ev = get_object_or_404(Event, id=event_id)
    name = ev.name
    ev.delete()
    messages.success(request, f'Event "{name}" deleted successfully.')
    return redirect('admin_manage_events')

@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_event_progress(request):
    """Monitor event lifecycle progress (aligned with SYSTEM_FLOW.md event statuses)."""
    status_filter = request.GET.get('status', 'all').strip().lower()
    category_filter = request.GET.get('category', 'all').strip().lower()
    search_query = request.GET.get('q', '').strip()

    all_rows = list(Event.objects.all()[:80])
    # Convert ORM objects to dicts for template compatibility
    all_rows = [{'id': e.id, 'name': e.name, 'category': e.category, 'venue': e.venue, 'schedule_label': e.schedule_label, 'status': e.status} for e in Event.objects.all()[:80]]
    event_rows = list(all_rows)

    if search_query:
        q_lower = search_query.lower()
        event_rows = [
            row for row in event_rows
            if q_lower in row['name'].lower()
            or q_lower in row['category'].lower()
            or q_lower in row['venue'].lower()
        ]

    if category_filter != 'all':
        event_rows = [row for row in event_rows if row['category'].lower() == category_filter]

    if status_filter != 'all':
        event_rows = [row for row in event_rows if row['status'] == status_filter]

    active_count = sum(1 for row in all_rows if row['status'] == 'active')
    upcoming_count = sum(1 for row in all_rows if row['status'] == 'upcoming')
    completed_count = sum(1 for row in all_rows if row['status'] == 'completed')
    category_names = sorted({row['category'] for row in all_rows}) or ['Sports', 'Esports', 'Pageant']

    progress_rows = []
    for row in event_rows[:40]:
        meta = _event_progress_meta(row['status'])
        progress_rows.append({**row, **meta})

    progress_stage_labels = ['Draft', 'Setup', 'Active', 'Tabulation', 'Completed', 'Archived']

    return render(request, 'admindash/result.html', {
        'progress_rows': progress_rows,
        'active_count': active_count,
        'upcoming_count': upcoming_count,
        'completed_count': completed_count,
        'total_tracked': len(all_rows),
        'category_names': category_names,
        'selected_category': category_filter,
        'selected_status': status_filter,
        'search_query': search_query,
        'progress_stage_labels': progress_stage_labels,
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
    search_query = request.GET.get('q', '').strip()
    score_rows = get_tabulator_activity_score_rows()
    if search_query:
        q_lower = search_query.lower()
        score_rows = [
            row for row in score_rows
            if q_lower in row['tabulator'].lower()
            or q_lower in row['item'].lower()
            or q_lower in row['detail'].lower()
        ]
    return render(request, 'admindash/scoresheet.html', {
        'score_rows': score_rows,
        'search_query': search_query,
        'submission_count': len(score_rows),
    })


@login_required(login_url='login')
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_ocr(request):
    """Admin OCR: extract text from uploaded judge score card images (browser-side)."""
    return render(request, 'admindash/ocr.html', {})


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
    activity_log_count = LogEntry.objects.count()
    if activity_log_count >= 1000:
        activity_log_count_label = f'{activity_log_count // 1000}k+'
    else:
        activity_log_count_label = str(activity_log_count)

    department_names = list(
        Group.objects.exclude(name__in=['Admin', 'Tabulator', 'Judge', 'Judges', 'Viewers']).order_by('name').values_list('name', flat=True)
    )
    total_events_count = 1
    critical_logs_count = LogEntry.objects.filter(action_flag=DELETION).count()
    reports_generated_count = LogEntry.objects.filter(change_message__icontains='report').count()
    latest_report_log = LogEntry.objects.filter(change_message__icontains='report').order_by('-action_time').first()
    last_export_label = latest_report_log.action_time.strftime('%b %d, %Y') if latest_report_log else 'Never'

    recent_report_rows = []
    recent_logs = LogEntry.objects.select_related('user').order_by('-action_time')[:5]
    for log in recent_logs:
        recent_report_rows.append({
            'name': log.object_repr or 'System Report',
            'created_by': log.user.username if log.user else 'System',
            'created_at': log.action_time.strftime('%b %d, %Y %I:%M %p'),
            'format': 'PDF',
            'status': 'Ready',
        })

    return render(request, 'reports.html', {
        'activity_log_count': activity_log_count,
        'activity_log_count_label': activity_log_count_label,
        'events': ['National Science Decathlon 2024'],
        'department_names': department_names,
        'total_events_count': total_events_count,
        'critical_logs_count': critical_logs_count,
        'reports_generated_count': reports_generated_count,
        'last_export_label': last_export_label,
        'recent_report_rows': recent_report_rows,
    })


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

