"""Unified audit logging for Superadmin transparency / accountability."""
from __future__ import annotations

import csv
import io
from datetime import datetime

from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import AuditLog, JudgeActivityLog
from .superadmin_service import classify_user_role

User = get_user_model()

MODULES = [
    'Authentication',
    'User Management',
    'Match-Based Event',
    'Criteria-Based Event',
    'Schedule',
    'Scoresheet',
    'Results',
    'Championship',
    'Reports',
    'System Settings',
    'Audit Logs',
    'System',
]

ACTIONS = [
    'Logged In',
    'Logged Out',
    'Created User',
    'Edited User',
    'Activated User',
    'Deactivated User',
    'Reset Password',
    'Deleted User',
    'Created Match-Based Event',
    'Created Criteria-Based Event',
    'Updated Event',
    'Deleted Event',
    'Assigned Faculty',
    'Assigned Judges',
    'Updated Schedule',
    'Modified Championship Points',
    'Published Event',
    'Reopened Event',
    'Generated Reports',
    'Generated Scoresheet',
    'Downloaded Scoresheet',
    'Printed Scoresheet',
    'Confirmed Match Result',
    'Returned Result for Correction',
    'Approved Results',
    'Published Official Results',
    'Opened Assigned Event',
    'Started Scoring Contestant',
    'Saved Draft Score',
    'Edited Draft Score',
    'Submitted Final Score',
    'Corrected Returned Score',
    'Resubmitted Score',
    'Changed System Settings',
    'Exported Audit Logs',
]


def client_ip(request):
    if not request:
        return None
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def parse_user_agent(ua: str):
    ua = ua or ''
    device = 'Desktop'
    lower = ua.lower()
    if 'android' in lower:
        device = 'Android Mobile'
    elif 'iphone' in lower or 'ipad' in lower:
        device = 'iOS Mobile'
    elif 'mobile' in lower:
        device = 'Mobile'
    elif 'tablet' in lower:
        device = 'Tablet'

    browser = 'Unknown'
    if 'edg/' in lower:
        browser = 'Edge'
    elif 'chrome/' in lower and 'edg/' not in lower:
        browser = 'Chrome'
    elif 'firefox/' in lower:
        browser = 'Firefox'
    elif 'safari/' in lower and 'chrome/' not in lower:
        browser = 'Safari'
    elif 'msie' in lower or 'trident/' in lower:
        browser = 'Internet Explorer'
    return device, browser


def record_audit(
    *,
    user=None,
    action: str,
    description: str = '',
    module: str = 'System',
    event_name: str = '',
    contestant: str = '',
    request=None,
    status: str = AuditLog.STATUS_SUCCESS,
    metadata: dict | None = None,
    ip_address=None,
    device: str = '',
    browser: str = '',
):
    """Append-only audit record. Never update existing rows."""
    ip = ip_address or (client_ip(request) if request is not None else None)
    if request is not None and not (device and browser):
        device, browser = parse_user_agent(request.META.get('HTTP_USER_AGENT', ''))
    elif not device and not browser:
        device, browser = '', ''

    role = ''
    name = 'System'
    if user is not None and getattr(user, 'is_authenticated', False):
        role = classify_user_role(user)
        name = user.get_full_name().strip() or user.username
    elif user is not None:
        role = classify_user_role(user) if getattr(user, 'pk', None) else ''
        name = getattr(user, 'username', '') or 'System'

    # Bypass AuditLog.delete protection by using objects.create only
    return AuditLog.objects.create(
        user=user if getattr(user, 'pk', None) else None,
        user_name=name,
        user_role=role or 'System',
        module=module or 'System',
        action=action,
        description=description or '',
        event_name=event_name or '',
        contestant=contestant or '',
        ip_address=ip,
        device=device,
        browser=browser,
        status=status if status in (AuditLog.STATUS_SUCCESS, AuditLog.STATUS_FAILED) else AuditLog.STATUS_SUCCESS,
        metadata=metadata or {},
    )


def _role_label_from_user(user):
    if not user:
        return 'System'
    return classify_user_role(user)


def _normalize_from_audit(log: AuditLog) -> dict:
    local = timezone.localtime(log.created_at)
    return {
        'id': f'a-{log.id}',
        'source': 'audit',
        'pk': log.id,
        'created_at': log.created_at,
        'date_display': local.strftime('%B %d, %Y'),
        'time_display': local.strftime('%I:%M %p').lstrip('0'),
        'datetime_display': local.strftime('%B %d, %Y · %I:%M %p').replace(' 0', ' '),
        'user_name': log.user_name or (log.user.get_full_name() if log.user_id else 'System') or 'System',
        'user_role': log.user_role or 'System',
        'module': log.module or 'System',
        'action': log.action,
        'description': log.description,
        'event_name': log.event_name,
        'contestant': log.contestant,
        'ip_address': log.ip_address or '—',
        'device': log.device or '—',
        'browser': log.browser or '—',
        'status': log.get_status_display() if hasattr(log, 'get_status_display') else (log.status or 'Success').title(),
        'status_key': log.status or 'success',
    }


def _classify_logentry_action(entry: LogEntry):
    msg = (entry.change_message or '').strip()
    obj = entry.object_repr or ''
    msg_l = msg.lower()
    obj_l = obj.lower()
    model = (entry.content_type.model if entry.content_type_id else '') or ''

    module = 'System'
    action = 'Record Updated'
    if entry.action_flag == ADDITION:
        action = 'Record Created'
    elif entry.action_flag == DELETION:
        action = 'Record Deleted'

    if 'login' in msg_l:
        return 'Authentication', 'Logged In', msg or 'User logged in.'
    if 'logout' in msg_l:
        return 'Authentication', 'Logged Out', msg or 'User logged out.'
    if 'password' in msg_l and 'reset' in msg_l:
        return 'User Management', 'Reset Password', msg
    if 'password' in msg_l:
        return 'User Management', 'Reset Password', msg
    if 'deactivated' in msg_l:
        return 'User Management', 'Deactivated User', msg
    if 'activated' in msg_l:
        return 'User Management', 'Activated User', msg
    if 'created' in msg_l and ('admin' in msg_l or 'account' in msg_l or 'user' in msg_l):
        return 'User Management', 'Created User', msg
    if 'updated' in msg_l and ('account' in msg_l or 'user' in msg_l or 'admin' in msg_l):
        return 'User Management', 'Edited User', msg
    if 'deleted' in msg_l and ('account' in msg_l or 'user' in msg_l):
        return 'User Management', 'Deleted User', msg
    if 'faculty' in msg_l and 'assign' in msg_l:
        return 'Match-Based Event' if 'match' in msg_l else 'Criteria-Based Event', 'Assigned Faculty', msg
    if 'judge' in msg_l and 'assign' in msg_l:
        return 'Criteria-Based Event', 'Assigned Judges', msg
    if 'criteria' in msg_l and ('created' in msg_l or entry.action_flag == ADDITION):
        return 'Criteria-Based Event', 'Created Criteria-Based Event', msg or f'Created "{obj}"'
    if 'match' in msg_l and ('created' in msg_l or entry.action_flag == ADDITION):
        return 'Match-Based Event', 'Created Match-Based Event', msg or f'Created "{obj}"'
    if model == 'event' and entry.action_flag == ADDITION:
        return 'System', 'Created Event', msg or f'Created "{obj}"'
    if model == 'event' and entry.action_flag == DELETION:
        return 'System', 'Deleted Event', msg or f'Deleted "{obj}"'
    if model == 'event':
        if 'publish' in msg_l and 'result' in msg_l:
            return 'Results', 'Published Official Results', msg
        if 'publish' in msg_l:
            return 'System', 'Published Event', msg
        return 'System', 'Updated Event', msg or f'Updated "{obj}"'
    if 'publish' in msg_l and 'result' in msg_l:
        return 'Results', 'Published Official Results', msg
    if 'return' in msg_l and 'correction' in msg_l:
        return 'Results', 'Returned Result for Correction', msg
    if 'approv' in msg_l and 'result' in msg_l:
        return 'Results', 'Approved Results', msg
    if 'confirm' in msg_l and 'match' in msg_l:
        return 'Results', 'Confirmed Match Result', msg
    if 'scoresheet' in msg_l or 'score sheet' in msg_l:
        if 'download' in msg_l:
            return 'Scoresheet', 'Downloaded Scoresheet', msg
        if 'print' in msg_l:
            return 'Scoresheet', 'Printed Scoresheet', msg
        return 'Scoresheet', 'Generated Scoresheet', msg
    if 'championship' in msg_l or 'leaderboard' in msg_l:
        return 'Championship', 'Modified Championship Points', msg
    if 'report' in msg_l and 'generated' in msg_l:
        return 'Reports', 'Generated Reports', msg
    if 'setting' in msg_l:
        return 'System Settings', 'Changed System Settings', msg
    if 'audit' in msg_l and 'export' in msg_l:
        return 'Audit Logs', 'Exported Audit Logs', msg
    if 'schedule' in msg_l:
        return 'Schedule', 'Updated Schedule', msg

    if entry.action_flag == ADDITION:
        action = 'Record Created'
    elif entry.action_flag == DELETION:
        action = 'Record Deleted'
    else:
        action = 'Record Updated'
    return module, action, msg or f'{obj} was modified.'


def _normalize_from_logentry(entry: LogEntry) -> dict:
    module, action, description = _classify_logentry_action(entry)
    local = timezone.localtime(entry.action_time)
    user = entry.user
    status_key = 'failed' if entry.action_flag == DELETION and 'fail' in (entry.change_message or '').lower() else 'success'
    event_name = ''
    if entry.content_type and entry.content_type.model == 'event':
        event_name = entry.object_repr or ''
    return {
        'id': f'l-{entry.id}',
        'source': 'logentry',
        'pk': entry.id,
        'created_at': entry.action_time,
        'date_display': local.strftime('%B %d, %Y'),
        'time_display': local.strftime('%I:%M %p').lstrip('0'),
        'datetime_display': local.strftime('%B %d, %Y · %I:%M %p').replace(' 0', ' '),
        'user_name': (user.get_full_name().strip() or user.username) if user else 'System',
        'user_role': _role_label_from_user(user),
        'module': module,
        'action': action,
        'description': description,
        'event_name': event_name,
        'contestant': '',
        'ip_address': '—',
        'device': '—',
        'browser': '—',
        'status': 'Success' if status_key == 'success' else 'Failed',
        'status_key': status_key,
    }


_JUDGE_ACTION_MAP = {
    JudgeActivityLog.ACTION_LOGIN: ('Authentication', 'Logged In'),
    JudgeActivityLog.ACTION_LOGOUT: ('Authentication', 'Logged Out'),
    JudgeActivityLog.ACTION_SUBMIT_SCORE: ('Criteria-Based Event', 'Submitted Final Score'),
    JudgeActivityLog.ACTION_EDIT_SCORE: ('Criteria-Based Event', 'Edited Draft Score'),
    JudgeActivityLog.ACTION_VIEW_EVENT: ('Criteria-Based Event', 'Opened Assigned Event'),
    JudgeActivityLog.ACTION_VIEW_SCORES: ('Criteria-Based Event', 'Opened Assigned Event'),
}


def _normalize_from_judge(log: JudgeActivityLog) -> dict:
    module, action = _JUDGE_ACTION_MAP.get(log.action, ('Criteria-Based Event', log.get_action_display()))
    details = (log.details or '').lower()
    if 'draft' in details and 'save' in details:
        action = 'Saved Draft Score'
    elif 'resubmit' in details or 'return' in details or 'correction' in details:
        action = 'Resubmitted Score'
    local = timezone.localtime(log.timestamp)
    event_name = ''
    if log.event_id:
        event_name = getattr(log.event, 'name', '') or str(log.event)
    contestant = ''
    if log.candidate_id:
        contestant = getattr(log.candidate, 'name', '') or str(log.candidate)
    return {
        'id': f'j-{log.id}',
        'source': 'judge',
        'pk': log.id,
        'created_at': log.timestamp,
        'date_display': local.strftime('%B %d, %Y'),
        'time_display': local.strftime('%I:%M %p').lstrip('0'),
        'datetime_display': local.strftime('%B %d, %Y · %I:%M %p').replace(' 0', ' '),
        'user_name': log.judge.get_full_name().strip() or log.judge.username,
        'user_role': 'Judge',
        'module': module,
        'action': action,
        'description': log.details or f'{action}' + (f' — {event_name}' if event_name else ''),
        'event_name': event_name,
        'contestant': contestant,
        'ip_address': log.ip_address or '—',
        'device': '—',
        'browser': '—',
        'status': 'Success',
        'status_key': 'success',
    }


def build_audit_rows(
    *,
    q='',
    user_id='all',
    role='all',
    module='all',
    action='all',
    date_from='',
    date_to='',
    limit=2000,
):
    rows = []

    for log in AuditLog.objects.select_related('user').order_by('-created_at')[:limit]:
        rows.append(_normalize_from_audit(log))

    # Legacy sources (avoid duplicates by time+user+action when AuditLog already covers)
    seen = {(r['user_name'], r['action'], r['created_at'].replace(microsecond=0)) for r in rows}

    for entry in LogEntry.objects.select_related('user', 'content_type').order_by('-action_time')[:limit]:
        row = _normalize_from_logentry(entry)
        key = (row['user_name'], row['action'], row['created_at'].replace(microsecond=0))
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)

    for jlog in JudgeActivityLog.objects.select_related('judge', 'event', 'candidate').order_by('-timestamp')[:limit]:
        row = _normalize_from_judge(jlog)
        key = (row['user_name'], row['action'], row['created_at'].replace(microsecond=0))
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)

    rows.sort(key=lambda r: r['created_at'], reverse=True)

    query = (q or '').strip().lower()
    if query:
        rows = [
            r for r in rows
            if query in r['user_name'].lower()
            or query in r['user_role'].lower()
            or query in r['module'].lower()
            or query in r['action'].lower()
            or query in (r['description'] or '').lower()
            or query in (r['event_name'] or '').lower()
        ]

    if user_id and user_id != 'all':
        uid = str(user_id).strip()
        if uid.isdigit():
            u = User.objects.filter(pk=int(uid)).first()
            if u:
                names = {
                    u.username.lower(),
                    (u.get_full_name() or '').strip().lower(),
                }
                names.discard('')
                rows = [r for r in rows if r['user_name'].lower() in names]
            else:
                rows = []
        else:
            rows = [r for r in rows if r['user_name'].lower() == uid.lower()]

    if role and role != 'all':
        role_l = role.lower()
        rows = [r for r in rows if role_l in r['user_role'].lower()]

    if module and module != 'all':
        rows = [r for r in rows if r['module'].lower() == module.lower()]

    if action and action != 'all':
        rows = [r for r in rows if r['action'].lower() == action.lower()]

    def _parse_date(value):
        value = (value or '').strip()
        if not value:
            return None
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            return None

    d_from = _parse_date(date_from)
    d_to = _parse_date(date_to)
    if d_from:
        rows = [r for r in rows if timezone.localtime(r['created_at']).date() >= d_from]
    if d_to:
        rows = [r for r in rows if timezone.localtime(r['created_at']).date() <= d_to]

    return rows


def filter_options(rows=None):
    if rows is None:
        rows = build_audit_rows(limit=1500)
    users = sorted({r['user_name'] for r in rows if r['user_name']})
    roles = sorted({r['user_role'] for r in rows if r['user_role']})
    modules = sorted({r['module'] for r in rows if r['module']})
    actions = sorted({r['action'] for r in rows if r['action']})
    return {
        'users': users,
        'roles': roles or ['Admin', 'Faculty In-Charge', 'Judge', 'Super Admin'],
        'modules': modules or MODULES,
        'actions': actions or ACTIONS,
    }


def export_audit_csv(rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        'Date & Time', 'User Name', 'User Role', 'Module', 'Action',
        'Description', 'Event', 'IP Address', 'Device', 'Browser', 'Status',
    ])
    for r in rows:
        writer.writerow([
            r['datetime_display'], r['user_name'], r['user_role'], r['module'], r['action'],
            r['description'], r['event_name'], r['ip_address'], r['device'], r['browser'], r['status'],
        ])
    return buf.getvalue()


def export_audit_excel(rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = 'Audit Logs'
    headers = [
        'Date & Time', 'User Name', 'User Role', 'Module', 'Action',
        'Description', 'Event', 'IP Address', 'Device', 'Browser', 'Status',
    ]
    ws.append(headers)
    for r in rows:
        ws.append([
            r['datetime_display'], r['user_name'], r['user_role'], r['module'], r['action'],
            r['description'], r['event_name'], r['ip_address'], r['device'], r['browser'], r['status'],
        ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_audit_pdf(rows):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter), leftMargin=24, rightMargin=24, topMargin=28, bottomMargin=24)
    styles = getSampleStyleSheet()
    story = [
        Paragraph('EventTab Audit Logs', styles['Title']),
        Paragraph(f'Exported {timezone.localtime().strftime("%B %d, %Y %I:%M %p")}', styles['Normal']),
        Spacer(1, 12),
    ]
    data = [['Date/Time', 'User', 'Role', 'Module', 'Action', 'Status']]
    for r in rows[:200]:
        data.append([
            r['datetime_display'][:28],
            r['user_name'][:18],
            r['user_role'][:16],
            r['module'][:18],
            r['action'][:24],
            r['status'],
        ])
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#06233f')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#cfdbe8')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f6f9fc')]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(table)
    doc.build(story)
    return buf.getvalue()


def get_audit_detail(row_id: str):
    rows = build_audit_rows(limit=3000)
    for r in rows:
        if r['id'] == row_id:
            return r
    return None
