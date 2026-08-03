"""Superadmin: system stats, user rows, event monitoring, standings helpers."""
from __future__ import annotations

import csv
import io
import secrets
import string
from collections import Counter, defaultdict
from datetime import timedelta

from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone

from .faculty_service import event_type_label, judge_monitoring
from .models import BracketMatch, BracketTeam, Department, Event, SystemSettings

User = get_user_model()

ROLE_ADMIN = 'Admin'
ROLE_FACULTY = 'Faculty In-Charge'
ROLE_JUDGE = 'Judge'
ROLE_SUPER = 'Super Admin'

RESERVED_GROUPS = {
    'admin', 'tabulator', 'faculty', 'judge', 'judges', 'scorer', 'viewers', 'super admin',
}


class SuperadminServiceError(ValueError):
    pass


def is_superadmin_user(user):
    return bool(user and user.is_authenticated and user.is_superuser)


def _relative_time(dt):
    if not dt:
        return 'Never'
    delta = timezone.now() - dt
    if delta.days:
        return f'{delta.days}d ago'
    hours = delta.seconds // 3600
    if hours:
        return f'{hours}h ago'
    minutes = delta.seconds // 60
    return f'{minutes}m ago' if minutes else 'Just now'


def resolve_user_department(user):
    if user.is_superuser:
        return 'System'
    dept_names = {d.name.lower(): d.name for d in Department.objects.all()}
    for name in user.groups.values_list('name', flat=True):
        key = (name or '').lower()
        if key in dept_names:
            return dept_names[key]
    for name in user.groups.values_list('name', flat=True):
        if (name or '').lower() not in RESERVED_GROUPS:
            return name
    return '—'


def classify_user_role(user):
    if user.is_superuser:
        return ROLE_SUPER
    if user.is_staff:
        return ROLE_ADMIN
    groups = {g.lower() for g in user.groups.values_list('name', flat=True)}
    if 'tabulator' in groups or 'faculty' in groups:
        return ROLE_FACULTY
    if groups & {'judge', 'judges', 'scorer'}:
        return ROLE_JUDGE
    return 'User'


def dashboard_cards():
    today = timezone.localdate()
    total_users = User.objects.count()
    total_events = Event.objects.count()
    active_events = Event.objects.filter(
        Q(status=Event.STATUS_ACTIVE)
        | Q(event_date__lte=today, end_date__gte=today)
        | Q(event_date=today)
    ).exclude(status=Event.STATUS_COMPLETED).exclude(status=Event.STATUS_INACTIVE).count()
    completed_events = Event.objects.filter(status=Event.STATUS_COMPLETED).count()
    departments = Department.objects.filter(status=Department.STATUS_ACTIVE).count()
    judges = (
        User.objects.filter(
            Q(groups__name__iexact='Judge')
            | Q(groups__name__iexact='Judges')
            | Q(groups__name__iexact='Scorer')
        )
        .exclude(is_superuser=True)
        .distinct()
        .count()
    )
    faculty = (
        User.objects.filter(
            Q(groups__name__iexact='Tabulator') | Q(groups__name__iexact='Faculty')
        )
        .exclude(is_superuser=True)
        .distinct()
        .count()
    )
    published = Event.objects.filter(
        Q(results_finalized=True) | Q(publication_status=Event.PUBLICATION_PUBLISHED)
    ).count()
    return {
        'total_users': total_users,
        'total_events': total_events,
        'active_events': active_events,
        'completed_events': completed_events,
        'total_departments': departments,
        'total_judges': judges,
        'total_faculty': faculty,
        'published_results': published,
    }


def chart_events_by_category():
    rows = (
        Event.objects.exclude(category='')
        .values('category')
        .annotate(count=Count('id'))
        .order_by('-count')[:8]
    )
    return [{'label': r['category'] or 'Other', 'value': r['count']} for r in rows]


def chart_events_by_month(months=12):
    start = timezone.now() - timedelta(days=30 * months)
    qs = (
        Event.objects.filter(event_date__gte=start.date())
        .annotate(month=TruncMonth('event_date'))
        .values('month')
        .annotate(count=Count('id'))
        .order_by('month')
    )
    return [
        {
            'label': r['month'].strftime('%b %Y') if r['month'] else '—',
            'value': r['count'],
        }
        for r in qs
        if r['month']
    ]


def chart_department_points(limit=8):
    rows = championship_standings()[:limit]
    return [{'label': r['department'], 'value': r['total_points']} for r in rows]


def chart_user_activity(days=30):
    start = timezone.now() - timedelta(days=days)
    counter = Counter()
    for ts in LogEntry.objects.filter(action_time__gte=start).values_list('action_time', flat=True):
        counter[ts.date().isoformat()] += 1
    return [{'label': day, 'value': counter[day]} for day in sorted(counter.keys())]


def classify_log_entry(entry):
    msg = (entry.change_message or '').lower()
    repr_ = (entry.object_repr or '').lower()
    title = 'System Update'
    if 'login' in msg:
        title = 'User Logged In'
    elif 'password' in msg:
        title = 'Password Reset'
    elif entry.action_flag == ADDITION and 'admin' in msg:
        title = 'New Admin Created'
    elif 'faculty' in msg and ('assign' in msg or 'created' in msg):
        title = 'New Faculty Assigned'
    elif 'publish' in msg and 'result' in msg:
        title = 'Results Published'
    elif 'publish' in msg:
        title = 'Event Published'
    elif 'leaderboard' in msg or 'championship' in msg:
        title = 'Leaderboard Updated'
    elif entry.action_flag == DELETION:
        title = 'Record Deleted'
    elif entry.action_flag == CHANGE:
        title = 'Record Updated'
    elif entry.action_flag == ADDITION:
        title = 'Record Created'
    return {
        'title': title,
        'message': entry.change_message or f'{entry.object_repr} updated',
        'user': entry.user.get_full_name() or entry.user.username if entry.user_id else 'System',
        'role': classify_user_role(entry.user) if entry.user_id else 'System',
        'module': entry.content_type.model if entry.content_type_id else 'system',
        'time': _relative_time(entry.action_time),
        'action_time': entry.action_time,
        'action_flag': entry.action_flag,
        'object_repr': entry.object_repr,
    }


def recent_activities(limit=10):
    entries = LogEntry.objects.select_related('user', 'content_type').order_by('-action_time')[:limit]
    return [classify_log_entry(e) for e in entries]


def superadmin_user_rows(*, role='all', q='', status='', department='', sort='name', direction='asc'):
    seen = set()
    rows = []

    staff_qs = User.objects.filter(is_staff=True).prefetch_related('groups').order_by('id')
    for user in staff_qs:
        if user.id in seen:
            continue
        seen.add(user.id)
        role_label = classify_user_role(user)
        rows.append(_user_row(user, role_label))

    assign_qs = (
        User.objects.filter(
            Q(groups__name__iexact='Tabulator')
            | Q(groups__name__iexact='Faculty')
            | Q(groups__name__iexact='Judge')
            | Q(groups__name__iexact='Judges')
            | Q(groups__name__iexact='Scorer')
        )
        .exclude(is_superuser=True)
        .prefetch_related('groups')
        .distinct()
        .order_by('id')
    )
    for user in assign_qs:
        if user.id in seen:
            continue
        seen.add(user.id)
        role_label = classify_user_role(user)
        if role_label not in (ROLE_FACULTY, ROLE_JUDGE):
            continue
        rows.append(_user_row(user, role_label))

    role_key = (role or 'all').strip().lower()
    if role_key in ('admin', 'admins'):
        rows = [r for r in rows if r['role'] in (ROLE_ADMIN, ROLE_SUPER)]
    elif role_key in ('faculty', 'faculty in-charge', 'tabulator'):
        rows = [r for r in rows if r['role'] == ROLE_FACULTY]
    elif role_key in ('judge', 'judges'):
        rows = [r for r in rows if r['role'] == ROLE_JUDGE]

    status_key = (status or '').strip().lower()
    if status_key in ('active', 'inactive', 'deactivated'):
        want_active = status_key == 'active'
        rows = [r for r in rows if r['is_active'] == want_active]

    dept = (department or '').strip().lower()
    if dept and dept != 'all':
        rows = [r for r in rows if dept in (r['department'] or '').lower()]

    query = (q or '').strip().lower()
    if query:
        rows = [
            r for r in rows
            if query in r['name'].lower()
            or query in r['username'].lower()
            or query in (r['email'] or '').lower()
            or query in r['role'].lower()
            or query in (r['department'] or '').lower()
        ]

    reverse = (direction or 'asc').lower() == 'desc'
    sort_key = (sort or 'name').lower()
    key_map = {
        'name': lambda r: r['name'].lower(),
        'email': lambda r: (r['email'] or '').lower(),
        'role': lambda r: r['role'].lower(),
        'department': lambda r: (r['department'] or '').lower(),
        'status': lambda r: r['status'].lower(),
        'last_login': lambda r: r['last_login_dt'] or timezone.now().replace(year=1970),
    }
    rows.sort(key=key_map.get(sort_key, key_map['name']), reverse=reverse)
    return rows


def _user_row(user, role_label):
    name = user.get_full_name().strip() or user.username
    return {
        'id': user.id,
        'user': user,
        'name': name,
        'username': user.username,
        'email': user.email or '',
        'role': role_label,
        'department': resolve_user_department(user),
        'status': 'Active' if user.is_active else 'Inactive',
        'is_active': user.is_active,
        'last_login': _relative_time(user.last_login),
        'last_login_dt': user.last_login,
        'can_delete': not user.is_superuser,
    }


def create_portal_user(*, actor, role, username, email, password, department='', first_name='', last_name='', is_active=True):
    role = (role or ROLE_ADMIN).strip()
    username = (username or '').strip()
    email = (email or '').strip()
    if not username or not password:
        raise SuperadminServiceError('Username and password are required.')
    if User.objects.filter(username__iexact=username).exists():
        raise SuperadminServiceError('Username already exists.')
    if email and User.objects.filter(email__iexact=email).exists():
        raise SuperadminServiceError('Email already exists.')

    user = User.objects.create_user(
        username=username,
        email=email,
        password=password,
        first_name=(first_name or '').strip(),
        last_name=(last_name or '').strip(),
    )
    user.is_active = bool(is_active)
    _apply_role(user, role, department)
    user.save()
    _log_user_action(actor, user, ADDITION, f'Created {role} account.')
    try:
        from .audit_service import record_audit
        record_audit(
            user=actor,
            action='Created User',
            description=f'Created {role} account "{username}".',
            module='User Management',
        )
    except Exception:
        pass
    return user


def update_portal_user(*, actor, user_id, role=None, username=None, email=None, password=None,
                       department=None, first_name=None, last_name=None, is_active=None):
    user = User.objects.filter(pk=user_id).first()
    if not user:
        raise SuperadminServiceError('User not found.')
    if user.is_superuser and actor.id != user.id:
        raise SuperadminServiceError('Cannot edit another Superadmin account.')

    if username is not None:
        username = username.strip()
        if not username:
            raise SuperadminServiceError('Username is required.')
        if User.objects.filter(username__iexact=username).exclude(pk=user.pk).exists():
            raise SuperadminServiceError('Username already exists.')
        user.username = username
    if email is not None:
        email = email.strip()
        if email and User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
            raise SuperadminServiceError('Email already exists.')
        user.email = email
    if first_name is not None:
        user.first_name = first_name.strip()
    if last_name is not None:
        user.last_name = last_name.strip()
    if password:
        user.set_password(password)
    if is_active is not None and not (user.is_superuser and actor.id == user.id and not is_active):
        user.is_active = bool(is_active)
    if role is not None:
        _apply_role(user, role, department if department is not None else resolve_user_department(user))
    elif department is not None and not user.is_superuser:
        _set_department_group(user, department)
    user.save()
    _log_user_action(actor, user, CHANGE, f'Updated {classify_user_role(user)} account.')
    try:
        from .audit_service import record_audit
        record_audit(
            user=actor,
            action='Edited User',
            description=f'Updated account "{user.username}".',
            module='User Management',
        )
    except Exception:
        pass
    return user


def set_user_active(*, actor, user_id, active):
    user = User.objects.filter(pk=user_id).first()
    if not user:
        raise SuperadminServiceError('User not found.')
    if user.id == actor.id:
        raise SuperadminServiceError('Cannot change your own active status.')
    if user.is_superuser:
        raise SuperadminServiceError('Cannot deactivate a Superadmin account.')
    user.is_active = bool(active)
    user.save(update_fields=['is_active'])
    _log_user_action(actor, user, CHANGE, 'Activated user.' if active else 'Deactivated user.')
    try:
        from .audit_service import record_audit
        record_audit(
            user=actor,
            action='Activated User' if active else 'Deactivated User',
            description=f'{"Activated" if active else "Deactivated"} "{user.username}".',
            module='User Management',
        )
    except Exception:
        pass
    return user


def delete_portal_user(*, actor, user_id):
    user = User.objects.filter(pk=user_id).first()
    if not user:
        raise SuperadminServiceError('User not found.')
    if user.id == actor.id or user.is_superuser:
        raise SuperadminServiceError('Cannot delete this account.')
    label = str(user)
    uid = user.id
    user.delete()
    LogEntry.objects.create(
        user=actor,
        content_type_id=ContentType.objects.get_for_model(User).id,
        object_id=str(uid),
        object_repr=label,
        action_flag=DELETION,
        change_message='Deleted user account.',
    )


def reset_user_password(*, actor, user_id, password=None):
    user = User.objects.filter(pk=user_id).first()
    if not user:
        raise SuperadminServiceError('User not found.')
    if not password:
        alphabet = string.ascii_letters + string.digits
        password = ''.join(secrets.choice(alphabet) for _ in range(10))
    user.set_password(password)
    user.save(update_fields=['password'])
    _log_user_action(actor, user, CHANGE, 'Password reset.')
    try:
        from .audit_service import record_audit
        record_audit(
            user=actor,
            action='Reset Password',
            description=f'Reset password for "{user.username}".',
            module='User Management',
        )
    except Exception:
        pass
    return password


def _apply_role(user, role, department=''):
    role_norm = (role or ROLE_ADMIN).strip().lower()
    user.groups.clear()
    dept = (department or '').strip()
    if dept in ('—', 'System', 'Unassigned'):
        dept = ''

    if role_norm in ('super admin', 'superadmin'):
        user.is_staff = True
        user.is_superuser = True
        return

    user.is_superuser = False
    if role_norm in ('admin', 'administrator'):
        user.is_staff = True
        admin_group, _ = Group.objects.get_or_create(name='Admin')
        user.groups.add(admin_group)
        if dept:
            g, _ = Group.objects.get_or_create(name=dept)
            user.groups.add(g)
        return

    user.is_staff = False
    if role_norm in ('faculty', 'faculty in-charge', 'tabulator'):
        g, _ = Group.objects.get_or_create(name='Tabulator')
        user.groups.add(g)
    elif role_norm in ('judge', 'judges'):
        g, _ = Group.objects.get_or_create(name='Judge')
        user.groups.add(g)
    elif role_norm == 'scorer':
        g, _ = Group.objects.get_or_create(name='Scorer')
        user.groups.add(g)
    if dept:
        g, _ = Group.objects.get_or_create(name=dept)
        user.groups.add(g)


def _set_department_group(user, department):
    dept = (department or '').strip()
    keep = []
    for g in user.groups.all():
        if (g.name or '').lower() in RESERVED_GROUPS:
            keep.append(g)
    user.groups.set(keep)
    if dept and dept not in ('—', 'System'):
        g, _ = Group.objects.get_or_create(name=dept)
        user.groups.add(g)


def _log_user_action(actor, user, flag, message):
    LogEntry.objects.create(
        user=actor,
        content_type_id=ContentType.objects.get_for_model(User).id,
        object_id=str(user.id),
        object_repr=str(user),
        action_flag=flag,
        change_message=message,
    )


def monitoring_status(event):
    """Derived Superadmin status: Draft/Published/Ongoing/Waiting for Review/Completed."""
    cfg = event.result_processing_config or {}
    today = timezone.localdate()
    matches = list(event.bracket_matches.all()) if hasattr(event, '_prefetched_objects_cache') and 'bracket_matches' in getattr(event, '_prefetched_objects_cache', {}) else list(event.bracket_matches.all())
    all_done = bool(matches) and all(m.status in (BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT) for m in matches)

    if event.results_finalized or event.status == Event.STATUS_COMPLETED or all_done:
        return 'completed', 'Completed'
    if cfg.get('ready_for_publication'):
        return 'waiting_review', 'Waiting for Review'
    method = (event.scoring_method or '').lower()
    if method == 'criteria' or event.judging_criteria_config:
        try:
            mon = judge_monitoring(event)
            if mon.get('all_submitted') and not event.results_finalized:
                return 'waiting_review', 'Waiting for Review'
        except Exception:
            pass
    end = event.end_date or event.event_date
    if event.status == Event.STATUS_ACTIVE or (event.event_date and event.event_date <= today <= end):
        return 'ongoing', 'Ongoing'
    if any(m.status == BracketMatch.STATUS_ONGOING for m in matches):
        return 'ongoing', 'Ongoing'
    if event.publication_status == Event.PUBLICATION_PUBLISHED and event.status != Event.STATUS_UPCOMING:
        return 'published', 'Published'
    if event.publication_status == Event.PUBLICATION_DRAFT or event.status == Event.STATUS_UPCOMING:
        # published listing with upcoming date still shows Published if publication_status says so
        if event.publication_status == Event.PUBLICATION_PUBLISHED:
            return 'published', 'Published'
        return 'draft', 'Draft'
    return 'published', 'Published'


def event_progress(event):
    matches = list(event.bracket_matches.all())
    total_m = len(matches) or 0
    done_m = sum(1 for m in matches if m.status in (BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT))
    schedule_pct = int(round((done_m / total_m) * 100)) if total_m else 0

    judges_submitted = 0
    judges_total = event.assigned_judges.count()
    try:
        mon = judge_monitoring(event)
        judges_submitted = mon.get('submitted', 0)
        judges_total = mon.get('total', judges_total) or judges_total
    except Exception:
        pass

    results_pct = 100 if event.results_finalized else (70 if (event.result_processing_config or {}).get('ready_for_publication') else schedule_pct)
    publication_pct = 100 if (
        event.results_finalized or event.publication_status == Event.PUBLICATION_PUBLISHED
    ) else 0

    return {
        'schedule_pct': schedule_pct,
        'matches_completed': done_m,
        'matches_total': total_m,
        'judges_submitted': judges_submitted,
        'judges_total': judges_total,
        'results_pct': results_pct,
        'publication_pct': publication_pct,
    }


def serialize_monitoring_row(event):
    key, label = monitoring_status(event)
    admin_name = '—'
    # Best-effort: use LogEntry ADDITION for event creator
    created = (
        LogEntry.objects.filter(
            content_type=ContentType.objects.get_for_model(Event),
            object_id=str(event.pk),
            action_flag=ADDITION,
        )
        .select_related('user')
        .order_by('action_time')
        .first()
    )
    if created and created.user_id:
        admin_name = created.user.get_full_name() or created.user.username
    faculty = '—'
    if event.faculty_account_id:
        faculty = event.faculty_account.get_full_name() or event.faculty_account.username
    elif event.faculty_in_charge:
        faculty = event.faculty_in_charge
    return {
        'id': event.id,
        'name': event.name,
        'event_type': event_type_label(event),
        'category': event.category or '—',
        'classification': event.get_event_classification_display() if event.event_classification else '—',
        'admin': admin_name,
        'faculty': faculty,
        'status_key': key,
        'status': label,
        'event_date': event.event_date.strftime('%b %d, %Y') if event.event_date else '—',
        'venue': event.venue or '—',
    }


def championship_standings():
    """Department standings with major/minor split from BracketTeam points."""
    totals = defaultdict(lambda: {'total': 0, 'major': 0, 'minor': 0})
    teams = BracketTeam.objects.select_related('department', 'event').filter(points__gt=0)
    for team in teams:
        if not team.department_id:
            continue
        pts = int(team.points or 0)
        totals[team.department_id]['total'] += pts
        cls = (team.event.event_classification if team.event_id else '') or ''
        if cls == Event.CLASSIFICATION_MAJOR:
            totals[team.department_id]['major'] += pts
        elif cls == Event.CLASSIFICATION_MINOR:
            totals[team.department_id]['minor'] += pts
        else:
            totals[team.department_id]['major'] += pts

    depts = {d.id: d for d in Department.objects.filter(id__in=totals.keys())}
    rows = []
    for dept_id, bucket in totals.items():
        dept = depts.get(dept_id)
        rows.append({
            'department': dept.name if dept else '—',
            'department_code': dept.code if dept else '',
            'total_points': bucket['total'],
            'major_points': bucket['major'],
            'minor_points': bucket['minor'],
        })
    rows.sort(key=lambda r: (-r['total_points'], r['department']))
    for idx, row in enumerate(rows, start=1):
        row['rank'] = idx
    return rows


def export_leaderboard_csv(rows=None):
    rows = rows if rows is not None else championship_standings()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['Rank', 'Department', 'Total Points', 'Major Points', 'Minor Points'])
    for r in rows:
        writer.writerow([r['rank'], r['department'], r['total_points'], r['major_points'], r['minor_points']])
    return buf.getvalue()


def get_system_settings():
    return SystemSettings.load()
