"""Superadmin portal views."""
from __future__ import annotations

from django.views.decorators.cache import never_cache

import json
import csv
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from events.faculty_service import serialize_event_detail, schedule_rows, judge_monitoring
from events.models import Department, Event, SystemSettings
from events.superadmin_service import (
    SuperadminServiceError,
    championship_standings,
    chart_department_points,
    chart_events_by_category,
    chart_events_by_month,
    chart_user_activity,
    create_portal_user,
    dashboard_cards,
    dashboard_distributions,
    delete_portal_user,
    event_progress,
    export_leaderboard_csv,
    get_system_settings,
    is_superadmin_user,
    monitoring_status,
    recent_activities,
    reset_user_password,
    serialize_monitoring_row,
    set_user_active,
    superadmin_user_rows,
    update_portal_user,
)


def _superadmin_required(view):
    @login_required(login_url='login')
    def wrapped(request, *args, **kwargs):
        if not is_superadmin_user(request.user):
            messages.error(request, 'Superadmin access required.')
            return redirect('login')
        return view(request, *args, **kwargs)
    return wrapped


def _parse_json(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return {}


def _display_name(user):
    return user.get_full_name().strip() or user.username


@_superadmin_required
def superadmin_dashboard(request):
    cards = dashboard_cards()
    distributions = dashboard_distributions()
    cards['published_results'] = next(row['value'] for row in distributions['publication'] if row['label'] == 'Published')
    activities = recent_activities(10)
    return render(request, 'superadmindash/dashboard.html', {
        'active': 'dashboard',
        'user_display': _display_name(request.user),
        'cards': cards,
        'chart_category': chart_events_by_category(),
        'chart_month': chart_events_by_month(),
        'chart_points': chart_department_points(),
        'chart_activity': chart_user_activity(),
        'chart_status': distributions['status'],
        'chart_roles': distributions['roles'],
        'chart_publication': distributions['publication'],
        'activities': activities,
        'notification_count': len(activities),
        # legacy keys for any leftover template refs
        **cards,
    })


@_superadmin_required
def superadmin_users(request):
    role = request.GET.get('role', 'all')
    status = request.GET.get('status', 'all')
    department = request.GET.get('department', 'all')
    q = request.GET.get('q', '')
    sort = request.GET.get('sort', 'name')
    direction = request.GET.get('dir', 'asc')

    rows = superadmin_user_rows(
        role=role,
        q=q,
        status='' if status == 'all' else status,
        department='' if department == 'all' else department,
        sort=sort,
        direction=direction,
    )
    paginator = Paginator(rows, 10)
    page_obj = paginator.get_page(request.GET.get('page'))
    departments = list(Department.objects.filter(status='active').order_by('name').values_list('name', flat=True))
    all_rows = superadmin_user_rows()
    summary = {
        'total': len(all_rows),
        'admins': sum(row['role'] in ('Admin', 'Super Admin') for row in all_rows),
        'judges': sum(row['role'] == 'Judge' for row in all_rows),
        'active': sum(row['is_active'] for row in all_rows),
    }
    params = request.GET.copy()
    params.pop('page', None)
    sort_headers = []
    for key, label in (('name', 'Name'), ('email', 'Email'), ('role', 'Role'),
                       ('status', 'Status'), ('last_login', 'Last Login')):
        header_params = params.copy()
        header_params['sort'] = key
        header_params['dir'] = 'desc' if sort == key and direction == 'asc' else 'asc'
        sort_headers.append({'label': label, 'key': key, 'url': '?' + header_params.urlencode(),
                             'aria_sort': ('descending' if direction == 'desc' else 'ascending') if sort == key else 'none'})
    return render(request, 'superadmindash/users.html', {
        'active': 'users',
        'user_display': _display_name(request.user),
        'rows': page_obj.object_list,
        'page_obj': page_obj,
        'paginator': paginator,
        'total_count': len(rows),
        'departments': departments,
        'selected_role': role,
        'selected_status': status,
        'selected_department': department,
        'search_query': q,
        'sort': sort,
        'direction': direction,
        'summary': summary,
        'sort_headers': sort_headers,
        'pagination_query': params.urlencode(),
        'page_numbers': paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
    })


@_superadmin_required
@never_cache
def superadmin_user_detail(request, user_id):
    rows = superadmin_user_rows()
    row = next((r for r in rows if r['id'] == user_id), None)
    if not row:
        return JsonResponse({'success': False, 'message': 'User not found.'}, status=404)
    return JsonResponse({
        'success': True,
        'user': {
            'id': row['id'],
            'name': row['name'],
            'username': row['username'],
            'email': row['email'],
            'role': row['role'],
            'department': row['department'],
            'status': row['status'],
            'last_login': row['last_login'],
        },
    })


@require_POST
@_superadmin_required
def superadmin_user_create(request):
    payload = _parse_json(request)
    try:
        user = create_portal_user(
            actor=request.user,
            role=payload.get('role') or 'Admin',
            username=payload.get('username'),
            email=payload.get('email') or '',
            password=payload.get('password') or '',
            department=payload.get('department') or '',
            first_name=payload.get('first_name') or '',
            last_name=payload.get('last_name') or '',
            is_active=bool(payload.get('is_active', True)),
        )
        return JsonResponse({'success': True, 'message': f'Created {user.username}.', 'id': user.id})
    except SuperadminServiceError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)


@require_POST
@_superadmin_required
def superadmin_user_update(request, user_id):
    payload = _parse_json(request)
    try:
        update_portal_user(
            actor=request.user,
            user_id=user_id,
            role=payload.get('role'),
            username=payload.get('username'),
            email=payload.get('email'),
            password=payload.get('password') or None,
            department=payload.get('department'),
            first_name=payload.get('first_name'),
            last_name=payload.get('last_name'),
            is_active=payload.get('is_active'),
        )
        return JsonResponse({'success': True, 'message': 'User updated.'})
    except SuperadminServiceError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)


@require_POST
@_superadmin_required
@never_cache
def superadmin_user_reset_password(request, user_id):
    payload = _parse_json(request)
    password = payload.get('password')
    if not isinstance(password, str) or not password:
        return JsonResponse({'success': False, 'message': 'Enter a new password.'}, status=400)
    if password != payload.get('password_confirm'):
        return JsonResponse({'success': False, 'message': 'Passwords do not match.'}, status=400)
    try:
        password = reset_user_password(
            actor=request.user,
            user_id=user_id,
            password=payload.get('password') or None,
        )
        return JsonResponse({'success': True, 'message': 'Password reset successfully.'})
    except SuperadminServiceError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)


@require_POST
@_superadmin_required
def superadmin_user_set_active(request, user_id):
    payload = _parse_json(request)
    active = bool(payload.get('active', True))
    try:
        set_user_active(actor=request.user, user_id=user_id, active=active)
        return JsonResponse({'success': True, 'message': 'Activated.' if active else 'Deactivated.'})
    except SuperadminServiceError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)


@require_POST
@_superadmin_required
def superadmin_user_delete(request, user_id):
    try:
        delete_portal_user(actor=request.user, user_id=user_id)
        return JsonResponse({'success': True, 'message': 'User deleted.'})
    except SuperadminServiceError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)


@_superadmin_required
def superadmin_event_monitoring(request):
    q = (request.GET.get('q') or '').strip().lower()
    type_filter = (request.GET.get('type') or 'all').lower()
    status_filter = (request.GET.get('status') or 'all').lower()
    classification = request.GET.get('classification', 'all')
    period = request.GET.get('period', 'all')
    today = timezone.localdate()
    month_start = today.replace(day=1)
    previous_start = (month_start - timedelta(days=1)).replace(day=1)

    events = (
        Event.objects.select_related('faculty_account')
        .prefetch_related('bracket_matches', 'assigned_judges')
        .order_by('-event_date', 'name')
    )
    rows = []
    for event in events:
        row = serialize_monitoring_row(event)
        progress = event_progress(event)
        judge_pct = round(100 * progress['judges_submitted'] / progress['judges_total']) if progress['judges_total'] else 0
        row.update(date=event.event_date, classification_key=event.event_classification,
                   progress=100 if row['status_key'] == 'completed' else min(100, max(0, progress['results_pct'], judge_pct)))
        rows.append(row)
    predicates = (
        ('total', 'Total Events', 'calendar', lambda row: True),
        ('ongoing', 'Ongoing Events', 'play', lambda row: row['status_key'] == 'ongoing'),
        ('completed', 'Completed Events', 'check', lambda row: row['status_key'] == 'completed'),
        ('criteria', 'Criteria-Based Events', 'list', lambda row: 'criteria' in row['event_type'].lower()),
    )
    summary = []
    for key, label, icon, predicate in predicates:
        current = sum(predicate(row) for row in rows if row['date'] and month_start <= row['date'] <= today)
        previous = sum(predicate(row) for row in rows if row['date'] and previous_start <= row['date'] < month_start)
        summary.append({'key': key, 'label': label, 'icon': icon,
                        'count': sum(predicate(row) for row in rows), 'delta': current - previous})
    if classification in dict(Event.CLASSIFICATION_CHOICES):
        rows = [row for row in rows if row['classification_key'] == classification]
    if period == 'month':
        rows = [row for row in rows if row['date'] and row['date'].year == today.year and row['date'].month == today.month]
    elif period == 'year':
        rows = [row for row in rows if row['date'] and row['date'].year == today.year]
    if type_filter == 'match':
        rows = [r for r in rows if 'match' in r['event_type'].lower()]
    elif type_filter == 'criteria':
        rows = [r for r in rows if 'criteria' in r['event_type'].lower()]
    if status_filter != 'all':
        rows = [r for r in rows if r['status_key'] == status_filter]
    if q:
        rows = [
            r for r in rows
            if q in r['name'].lower()
            or q in r['category'].lower()
            or q in r['faculty'].lower()
            or q in r['admin'].lower()
        ]
    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="event-monitoring.csv"'
        writer = csv.writer(response)
        writer.writerow(['Event Name', 'Event Type', 'Category', 'Classification', 'Assigned Admin', 'Assigned Faculty', 'Status', 'Event Date', 'Progress (%)'])
        for row in rows:
            cells = [row[key] for key in ('name', 'event_type', 'category', 'classification', 'admin', 'faculty', 'status', 'event_date', 'progress')]
            writer.writerow(["'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else value for value in cells])
        return response
    params = request.GET.copy()
    params.pop('page', None)
    params.pop('export', None)
    export_params = params.copy()
    export_params['export'] = 'csv'
    paginator = Paginator(rows, 12)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'superadmindash/event_monitoring.html', {
        'active': 'events',
        'user_display': _display_name(request.user),
        'rows': page_obj.object_list,
        'page_obj': page_obj,
        'search_query': request.GET.get('q', ''),
        'selected_type': type_filter,
        'selected_status': status_filter,
        'selected_classification': classification,
        'selected_period': period,
        'classifications': Event.CLASSIFICATION_CHOICES,
        'summary': summary,
        'pagination_query': params.urlencode(),
        'export_query': export_params.urlencode(),
        'total_count': len(rows),
    })


@_superadmin_required
def superadmin_event_detail(request, event_id):
    event = get_object_or_404(
        Event.objects.select_related('faculty_account', 'chief_judge')
        .prefetch_related('assigned_judges', 'assigned_tabulators', 'bracket_matches'),
        pk=event_id,
    )
    status_key, status_label = monitoring_status(event)
    progress = event_progress(event)
    detail = serialize_event_detail(event)
    schedule = schedule_rows(event)
    judges = []
    try:
        judges = judge_monitoring(event).get('rows', [])
    except Exception:
        pass
    return render(request, 'superadmindash/event_monitor_detail.html', {
        'active': 'events',
        'user_display': _display_name(request.user),
        'event': event,
        'detail': detail,
        'status_key': status_key,
        'status_label': status_label,
        'progress': progress,
        'schedule': schedule,
        'judges': judges,
        'row': serialize_monitoring_row(event),
    })


@_superadmin_required
def superadmin_leaderboard(request):
    rows = championship_standings()
    return render(request, 'superadmindash/leaderboard.html', {
        'active': 'leaderboard',
        'user_display': _display_name(request.user),
        'rows': rows,
    })


@_superadmin_required
def superadmin_leaderboard_export(request):
    fmt = (request.GET.get('format') or 'csv').lower()
    rows = championship_standings()
    if fmt == 'csv':
        content = export_leaderboard_csv(rows)
        response = HttpResponse(content, content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="championship-leaderboard.csv"'
        return response
    # Simple Excel via openpyxl
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = 'Leaderboard'
    ws.append(['Rank', 'Department', 'Total Points', 'Major Points', 'Minor Points'])
    for r in rows:
        ws.append([r['rank'], r['department'], r['total_points'], r['major_points'], r['minor_points']])
    import io
    buf = io.BytesIO()
    wb.save(buf)
    response = HttpResponse(
        buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="championship-leaderboard.xlsx"'
    return response


@_superadmin_required
def superadmin_settings(request):
    from django.conf import settings as dj_settings
    settings_obj = get_system_settings()
    return render(request, 'superadmindash/settings.html', {
        'active': 'settings',
        'user_display': _display_name(request.user),
        'settings': settings_obj,
        'section': request.GET.get('section', 'general'),
        'allow_restore': bool(getattr(dj_settings, 'SUPERADMIN_ALLOW_RESTORE', False)),
    })


@require_POST
@_superadmin_required
def superadmin_settings_save(request):
    from django.urls import reverse
    section = request.POST.get('section', 'general')
    obj = SystemSettings.load()
    if section == 'general':
        obj.school_name = request.POST.get('school_name', obj.school_name)
        obj.academic_year = request.POST.get('academic_year', obj.academic_year)
        obj.intramurals_name = request.POST.get('intramurals_name', obj.intramurals_name)
        if request.FILES.get('school_logo'):
            obj.school_logo = request.FILES['school_logo']
    elif section == 'users':
        obj.password_min_length = int(request.POST.get('password_min_length') or obj.password_min_length)
        obj.password_require_uppercase = request.POST.get('password_require_uppercase') == 'on'
        obj.password_require_number = request.POST.get('password_require_number') == 'on'
        obj.password_require_symbol = request.POST.get('password_require_symbol') == 'on'
        obj.session_timeout_minutes = int(request.POST.get('session_timeout_minutes') or obj.session_timeout_minutes)
        obj.max_login_attempts = int(request.POST.get('max_login_attempts') or obj.max_login_attempts)
        obj.lockout_duration_minutes = int(request.POST.get('lockout_duration_minutes') or obj.lockout_duration_minutes)
    elif section == 'notifications':
        obj.email_notifications = request.POST.get('email_notifications') == 'on'
        obj.mobile_notifications = request.POST.get('mobile_notifications') == 'on'
        obj.system_notifications = request.POST.get('system_notifications') == 'on'
    obj.updated_by = request.user
    obj.save()
    try:
        from events.audit_service import record_audit
        record_audit(
            user=request.user,
            action='Changed System Settings',
            description=f'Updated system settings section "{section}".',
            module='System Settings',
            request=request,
        )
    except Exception:
        pass
    messages.success(request, 'Settings saved.')
    return redirect(f"{reverse('superadmin_settings')}?section={section}")


@_superadmin_required
def superadmin_profile(request):
    return render(request, 'superadmindash/profile.html', {
        'active': 'profile',
        'user_display': _display_name(request.user),
        'profile_user': request.user,
        'password_form': PasswordChangeForm(request.user),
    })


@require_POST
@_superadmin_required
def superadmin_profile_update(request):
    user = request.user
    user.first_name = request.POST.get('first_name', user.first_name).strip()
    user.last_name = request.POST.get('last_name', user.last_name).strip()
    email = request.POST.get('email', user.email).strip()
    from django.contrib.auth import get_user_model
    User = get_user_model()
    if email and User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
        messages.error(request, 'Email already in use.')
        return redirect('superadmin_profile')
    user.email = email
    user.save()
    messages.success(request, 'Profile updated.')
    return redirect('superadmin_profile')


@require_POST
@_superadmin_required
def superadmin_change_password(request):
    form = PasswordChangeForm(request.user, request.POST)
    if form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, 'Password changed.')
    else:
        messages.error(request, 'Could not change password. Check the form.')
    return redirect('superadmin_profile')


@_superadmin_required
@never_cache
def superadmin_activity_logs(request):
    """Audit Logs — read-only unified activity monitor."""
    from events.audit_service import (
        ACTIONS,
        MODULES,
        build_audit_rows,
        export_audit_csv,
        export_audit_excel,
        export_audit_pdf,
        filter_options,
        get_audit_detail,
        record_audit,
    )

    q = request.GET.get('q', '')
    selected_user = request.GET.get('user', 'all')
    selected_role = request.GET.get('role', 'all')
    selected_module = request.GET.get('module', 'all')
    selected_action = request.GET.get('action', 'all')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    selected_result = request.GET.get('result', 'all')
    insight_period = request.GET.get('insights', '7')
    export_format = (request.GET.get('export') or '').lower()
    detail_id = request.GET.get('detail', '')

    if detail_id and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        detail = get_audit_detail(detail_id)
        if not detail:
            return JsonResponse({'success': False, 'message': 'Log not found.'}, status=404)
        payload = {k: v for k, v in detail.items() if k != 'created_at'}
        payload['created_at'] = detail['created_at'].isoformat()
        return JsonResponse({'success': True, 'log': payload})

    rows = build_audit_rows(
        q=q,
        user_id=selected_user,
        role=selected_role,
        module=selected_module,
        action=selected_action,
        date_from=date_from,
        date_to=date_to,
    )
    if selected_result in ('success', 'failed'):
        rows = [row for row in rows if row['status_key'] == selected_result]

    if export_format in ('csv', 'excel', 'xlsx', 'pdf'):
        record_audit(
            user=request.user,
            action='Exported Audit Logs',
            description=f'Exported {len(rows)} audit log rows as {export_format.upper()}.',
            module='Audit Logs',
            request=request,
        )
        stamp = timezone.now().strftime('%Y%m%d')
        if export_format == 'pdf':
            content = export_audit_pdf(rows)
            response = HttpResponse(content, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="audit-logs-{stamp}.pdf"'
            return response
        if export_format in ('excel', 'xlsx'):
            content = export_audit_excel(rows)
            response = HttpResponse(
                content,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            )
            response['Content-Disposition'] = f'attachment; filename="audit-logs-{stamp}.xlsx"'
            return response
        content = export_audit_csv(rows)
        response = HttpResponse(content, content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="audit-logs-{stamp}.csv"'
        return response

    opts = filter_options(rows if len(rows) < 1500 else None)
    paginator = Paginator(rows, 8)
    page_obj = paginator.get_page(request.GET.get('page'))
    today = timezone.localdate()
    today_count = sum(1 for r in rows if timezone.localtime(r['created_at']).date() == today)
    failed_count = sum(1 for r in rows if r.get('status_key') == 'failed')
    latest = rows[0]['datetime_display'] if rows else 'N/A'

    def change(current, previous):
        if not previous:
            return '—' if not current else 'New'
        return f'{(current - previous) / previous:+.0%}'

    dates = [(row, timezone.localtime(row['created_at']).date()) for row in rows]
    yesterday = today - timedelta(days=1)
    recent = sum(today - timedelta(days=6) <= date <= today for _, date in dates)
    previous = sum(today - timedelta(days=13) <= date < today - timedelta(days=6) for _, date in dates)
    yesterday_count = sum(date == yesterday for _, date in dates)
    failed_today = sum(date == today and row['status_key'] == 'failed' for row, date in dates)
    failed_yesterday = sum(date == yesterday and row['status_key'] == 'failed' for row, date in dates)
    if insight_period not in ('7', '30', 'all'):
        insight_period = '7'
    insight_rows = [
        row for row, date in dates
        if insight_period == 'all' or today - timedelta(days=int(insight_period) - 1) <= date <= today
    ]
    insight_failed = sum(row['status_key'] == 'failed' for row in insight_rows)
    insight_success = len(insight_rows) - insight_failed
    modules = {}
    for row in insight_rows:
        modules[row['module']] = modules.get(row['module'], 0) + 1
    top_modules = sorted(modules.items(), key=lambda item: (-item[1], item[0]))[:5]
    params = request.GET.copy()
    params.pop('page', None)
    params.pop('export', None)
    params.pop('detail', None)
    quick_filters = []
    for label, key, value in [('Success', 'result', 'success'), ('Failed', 'result', 'failed'),
                               ('Login', 'action', 'Logged In'), ('Logout', 'action', 'Logged Out')]:
        quick_params = params.copy()
        active = quick_params.get(key) == value
        quick_params[key] = 'all' if active else value
        quick_filters.append({'label': label, 'key': label.lower(), 'active': active, 'query': quick_params.urlencode()})

    return render(request, 'superadmindash/activitylogs.html', {
        'active': 'audit_logs',
        'user_display': _display_name(request.user),
        'rows': page_obj.object_list,
        'page_obj': page_obj,
        'filtered_count': len(rows),
        'today_count': today_count,
        'failed_count': failed_count,
        'latest_activity': latest,
        'weekly_change': change(recent, previous),
        'today_change': change(today_count, yesterday_count),
        'failed_change': change(failed_today, failed_yesterday),
        'selected_result': selected_result,
        'insight_period': insight_period,
        'insight_count': len(insight_rows),
        'insight_success': insight_success,
        'insight_failed': insight_failed,
        'success_percent': round(100 * insight_success / len(insight_rows)) if insight_rows else 0,
        'failed_percent': round(100 * insight_failed / len(insight_rows)) if insight_rows else 0,
        'top_modules': [
            {'name': name, 'count': count, 'percent': round(100 * count / top_modules[0][1])}
            for name, count in top_modules
        ],
        'quick_filters': quick_filters,
        'filter_query': params.urlencode(),
        'insight_fields': [(key, value) for key, value in params.items() if key != 'insights'],
        'search_query': q,
        'selected_user': selected_user,
        'selected_role': selected_role,
        'selected_module': selected_module,
        'selected_action': selected_action,
        'date_from': date_from,
        'date_to': date_to,
        'filter_users': opts['users'],
        'filter_roles': opts['roles'],
        'filter_modules': opts['modules'] or MODULES,
        'filter_actions': opts['actions'] or ACTIONS,
        'all_modules': MODULES,
        'all_actions': ACTIONS,
    })
