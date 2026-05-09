from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth.models import Group
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.http import HttpResponse, JsonResponse
from django.contrib.contenttypes.models import ContentType
import json
import csv
from datetime import timedelta


ROLE_CHOICES = {
    'super-admin': 'Super Admin',
    'admin': 'Admin',
    'tabulator': 'Tabulator',
    'viewers': 'Viewers',
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
            (name for name in groups if name not in {'Admin', 'Tabulator', 'Viewers'}),
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

        context['success'] = f'Logged in as {ROLE_CHOICES[role]}.'

    return render(request, 'login.html', context)


@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_dashboard(request):
    status_filter = request.GET.get('status', 'all').strip().lower()
    category_filter = request.GET.get('category', 'all').strip().lower()
    search_query = request.GET.get('q', '').strip()

    logs = LogEntry.objects.select_related('user').order_by('-action_time')[:40]
    status_map = {
        ADDITION: 'active',
        CHANGE: 'pending',
        DELETION: 'cancelled',
    }
    category_map = {
        ADDITION: 'Academic',
        CHANGE: 'Esports',
        DELETION: 'Social',
    }

    event_rows = []
    for entry in logs:
        event_name = entry.object_repr or 'System Event'
        status = status_map.get(entry.action_flag, 'active')
        category = category_map.get(entry.action_flag, 'General')

        event_rows.append({
            'name': event_name[:42],
            'description': (entry.change_message or 'Activity update recorded.')[:62],
            'date_label': timezone.localtime(entry.action_time).strftime('%b %d, %Y'),
            'time_label': timezone.localtime(entry.action_time).strftime('%I:%M %p'),
            'category': category,
            'status': status,
            'status_label': status.title(),
        })

    if search_query:
        query_lower = search_query.lower()
        event_rows = [
            row for row in event_rows
            if query_lower in row['name'].lower()
            or query_lower in row['description'].lower()
            or query_lower in row['category'].lower()
        ]

    if category_filter != 'all':
        event_rows = [row for row in event_rows if row['category'].lower() == category_filter]

    if status_filter != 'all':
        event_rows = [row for row in event_rows if row['status'] == status_filter]

    categories = sorted({row['category'] for row in event_rows}) if event_rows else ['Academic', 'Esports', 'Social']
    total_events = len(event_rows)
    ongoing_games = len([row for row in event_rows if row['status'] == 'active'])
    completed_events = len([row for row in event_rows if row['status'] in {'active', 'pending'}])

    return render(request, 'admindash/admindashboard.html', {
        'ongoing_games': ongoing_games,
        'total_events': total_events,
        'completed_events': completed_events,
        'event_rows': event_rows[:12],
        'selected_status': status_filter,
        'selected_category': category_filter,
        'search_query': search_query,
        'categories': categories,
    })


@login_required
@user_passes_test(lambda user: user.is_staff, login_url='login')
def admin_manage_events(request):
    status_filter = request.GET.get('status', 'all').strip().lower()
    category_filter = request.GET.get('category', 'all').strip().lower()
    schedule_filter = request.GET.get('schedule', '').strip().lower()
    search_query = request.GET.get('q', '').strip()

    logs = LogEntry.objects.select_related('user').order_by('-action_time')[:80]
    status_map = {
        ADDITION: 'active',
        CHANGE: 'upcoming',
        DELETION: 'completed',
    }
    category_map = {
        ADDITION: 'Sports',
        CHANGE: 'Esports',
        DELETION: 'Pageant',
    }
    venue_map = {
        ADDITION: 'Covered Court',
        CHANGE: 'Smart Room',
        DELETION: 'Main Auditorium',
    }

    event_rows = []
    for entry in logs:
        event_name = entry.object_repr or 'Event'
        category = category_map.get(entry.action_flag, 'General')
        status = status_map.get(entry.action_flag, 'active')
        venue = venue_map.get(entry.action_flag, 'Main Hall')
        event_rows.append({
            'id': entry.id,
            'name': event_name[:42],
            'category': category,
            'schedule': timezone.localtime(entry.action_time),
            'schedule_label': timezone.localtime(entry.action_time).strftime('%B %d, %Y'),
            'venue': venue,
            'status': status,
        })

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

    if schedule_filter:
        event_rows = [row for row in event_rows if schedule_filter in row['schedule_label'].lower()]

    active_count = sum(1 for row in event_rows if row['status'] == 'active')
    upcoming_count = sum(1 for row in event_rows if row['status'] == 'upcoming')
    completed_count = sum(1 for row in event_rows if row['status'] == 'completed')
    category_names = sorted({row['category'] for row in event_rows}) or ['Sports', 'Esports', 'Pageant']

    return render(request, 'admindash/event.html', {
        'event_rows': event_rows[:30],
        'active_count': active_count,
        'upcoming_count': upcoming_count,
        'completed_count': completed_count,
        'category_names': category_names,
        'selected_category': category_filter,
        'selected_status': status_filter,
        'search_query': search_query,
        'schedule_filter': schedule_filter,
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
    departments = Group.objects.exclude(name__in=['Admin', 'Tabulator', 'Viewers']).order_by('name')
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
        Group.objects.exclude(name__in=['Admin', 'Tabulator', 'Viewers']).order_by('name').values_list('name', flat=True)
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
