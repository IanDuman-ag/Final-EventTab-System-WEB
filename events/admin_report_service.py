"""Administrator Generate Reports — catalog, preview, and export helpers."""
from __future__ import annotations

import csv
import io
from datetime import datetime

from django.contrib.auth import get_user_model
from django.db.models import Q, Sum
from django.utils import timezone
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .faculty_service import event_type_label
from .models import (
    AdminReportHistory,
    AuditLog,
    BracketMatch,
    BracketTeam,
    Department,
    Event,
    GeneratedScoresheet,
    JudgeScore,
    RegistryCandidate,
)
from .superadmin_service import championship_standings

User = get_user_model()

REPORT_CATALOG = {
    'events': {
        'label': 'Event Reports',
        'types': [
            ('event_summary', 'Event Summary'),
            ('match_based_events', 'Match-Based Events'),
            ('criteria_based_events', 'Criteria-Based Events'),
            ('event_schedule', 'Event Schedule'),
            ('event_participation', 'Event Participation'),
        ],
    },
    'results': {
        'label': 'Results Reports',
        'types': [
            ('match_results', 'Match Results'),
            ('criteria_results', 'Criteria-Based Results'),
            ('championship_rankings', 'Championship Rankings'),
            ('department_rankings', 'Department Rankings'),
            ('special_awards', 'Special Awards'),
        ],
    },
    'participation': {
        'label': 'Participation Reports',
        'types': [
            ('participating_teams', 'Participating Teams'),
            ('individual_participants', 'Individual Participants'),
            ('contestants', 'Contestants'),
            ('departments_participation', 'Departments'),
        ],
    },
    'scoresheets': {
        'label': 'Scoresheet Reports',
        'types': [
            ('generated_scoresheets', 'Generated Scoresheets'),
            ('printed_scoresheets', 'Printed Scoresheets'),
            ('match_scoresheets', 'Match Scoresheets'),
            ('criteria_scoresheets', 'Criteria-Based Scoresheets'),
        ],
    },
    'users': {
        'label': 'User Reports',
        'types': [
            ('administrators', 'Administrators'),
            ('faculty_in_charge', 'Faculty In-Charge'),
            ('judges', 'Judges'),
            ('user_activity', 'User Activity Summary'),
            ('audit_log', 'Audit Log Report'),
        ],
    },
}

TYPE_LABELS = {
    key: label
    for cat in REPORT_CATALOG.values()
    for key, label in cat['types']
}


def category_for_type(report_type: str) -> str:
    for key, cat in REPORT_CATALOG.items():
        if any(t[0] == report_type for t in cat['types']):
            return cat['label']
    return 'Reports'


def _parse_date(value):
    value = (value or '').strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


def filter_events(filters: dict):
    qs = Event.objects.select_related('faculty_account').prefetch_related(
        'assigned_judges', 'bracket_matches', 'bracket_teams'
    ).order_by('-event_date', 'name')

    event_id = (filters.get('event_id') or '').strip()
    if event_id.isdigit():
        qs = qs.filter(pk=int(event_id))

    event_type = (filters.get('event_type') or 'all').strip().lower()
    if event_type == 'match':
        qs = qs.filter(Q(scoring_method='match') | Q(category__iexact='sports'))
    elif event_type == 'criteria':
        qs = qs.filter(Q(scoring_method='criteria') | ~Q(judging_criteria_config={}))

    category = (filters.get('category') or 'all').strip()
    if category and category != 'all':
        qs = qs.filter(category__iexact=category)

    classification = (filters.get('classification') or 'all').strip().lower()
    if classification in ('major', 'minor'):
        qs = qs.filter(event_classification=classification)

    department = (filters.get('department') or 'all').strip()
    if department and department != 'all':
        qs = qs.filter(Q(department__iexact=department) | Q(bracket_teams__department__name__iexact=department)).distinct()

    faculty_id = (filters.get('faculty_id') or 'all').strip()
    if faculty_id.isdigit():
        qs = qs.filter(faculty_account_id=int(faculty_id))

    judge_id = (filters.get('judge_id') or 'all').strip()
    if judge_id.isdigit():
        qs = qs.filter(assigned_judges__id=int(judge_id)).distinct()

    status = (filters.get('status') or 'all').strip().lower()
    if status and status != 'all':
        qs = qs.filter(status=status)

    d_from = _parse_date(filters.get('date_from'))
    d_to = _parse_date(filters.get('date_to'))
    if d_from:
        qs = qs.filter(event_date__gte=d_from)
    if d_to:
        qs = qs.filter(Q(end_date__lte=d_to) | Q(end_date__isnull=True, event_date__lte=d_to))

    return qs


def build_report_preview(report_type: str, filters: dict | None = None) -> dict:
    filters = filters or {}
    report_type = (report_type or 'event_summary').strip()
    events = list(filter_events(filters))
    title = TYPE_LABELS.get(report_type, report_type.replace('_', ' ').title())
    headers: list[str] = []
    rows: list[list] = []
    highlight = {}

    if report_type in ('event_summary', 'match_based_events', 'criteria_based_events', 'event_participation'):
        if report_type == 'match_based_events':
            events = [e for e in events if 'match' in event_type_label(e).lower()]
        elif report_type == 'criteria_based_events':
            events = [e for e in events if 'criteria' in event_type_label(e).lower()]
        headers = ['Event Name', 'Category', 'Event Type', 'Classification', 'Venue', 'Date', 'Status']
        for ev in events:
            rows.append([
                ev.name,
                ev.category or '—',
                event_type_label(ev),
                ev.get_event_classification_display() or '—',
                ev.venue or '—',
                ev.event_date.strftime('%b %d, %Y') if ev.event_date else '—',
                ev.get_status_display(),
            ])
        if events:
            first = events[0]
            highlight = {
                'title': first.name,
                'event_type': event_type_label(first),
                'date': first.event_date.strftime('%B %d, %Y') if first.event_date else '—',
                'venue': first.venue or '—',
                'champion': _event_champion(first),
                'runner_up': _event_runner_up(first),
            }

    elif report_type == 'event_schedule':
        headers = ['Event', 'Game / Stage', 'Matchup', 'Date', 'Time', 'Venue', 'Status']
        for ev in events:
            for m in ev.bracket_matches.all().order_by('match_number')[:80]:
                rows.append([
                    ev.name,
                    f'Game {m.match_number}' + (f' · {m.round_name}' if m.round_name else ''),
                    f'{(m.team_a.name if m.team_a else "TBD")} vs {(m.team_b.name if m.team_b else "TBD")}',
                    m.match_date.strftime('%b %d, %Y') if m.match_date else '—',
                    m.match_time.strftime('%I:%M %p') if m.match_time else '—',
                    m.venue or ev.venue or '—',
                    m.get_status_display(),
                ])

    elif report_type == 'match_results':
        headers = ['Match Number', 'Event', 'Teams', 'Scores', 'Winner', 'Date']
        matches = BracketMatch.objects.filter(event__in=events).select_related(
            'event', 'team_a', 'team_b', 'winner'
        ).order_by('event_id', 'match_number')
        for m in matches:
            rows.append([
                str(m.match_number),
                m.event.name if m.event_id else '—',
                f'{(m.team_a.name if m.team_a else "TBD")} vs {(m.team_b.name if m.team_b else "TBD")}',
                f'{m.score_a or "—"} - {m.score_b or "—"}',
                m.winner.name if m.winner_id else '—',
                m.match_date.strftime('%b %d, %Y') if m.match_date else '—',
            ])
        if events:
            first = events[0]
            highlight = {
                'title': first.name,
                'event_type': event_type_label(first),
                'date': first.event_date.strftime('%B %d, %Y') if first.event_date else '—',
                'venue': first.venue or '—',
                'champion': _event_champion(first),
                'runner_up': _event_runner_up(first),
            }

    elif report_type == 'criteria_results':
        headers = ['Contestant', 'Department', 'Final Rank', 'Final Score', 'Awards', 'Event']
        for ev in events:
            if not ev.judging_event_id:
                continue
            judging = ev.judging_event
            # Aggregate locked scores by candidate
            totals = {}
            for s in JudgeScore.objects.filter(event=judging, is_locked=True).select_related('candidate'):
                cid = s.candidate_id
                if cid not in totals:
                    totals[cid] = {'name': s.candidate.name, 'dept': getattr(s.candidate, 'department', '') or '—', 'score': 0.0}
                totals[cid]['score'] += float(s.score or 0)
            ranked = sorted(totals.values(), key=lambda x: -x['score'])
            for idx, item in enumerate(ranked, start=1):
                award = 'Champion' if idx == 1 else ('1st Runner-Up' if idx == 2 else ('2nd Runner-Up' if idx == 3 else '—'))
                rows.append([item['name'], item['dept'], str(idx), f"{item['score']:.2f}", award, ev.name])

    elif report_type in ('championship_rankings', 'department_rankings'):
        headers = ['Rank', 'Department', 'Total Championship Points', 'Major Points', 'Minor Points']
        for r in championship_standings():
            rows.append([
                str(r['rank']), r['department'], str(r['total_points']),
                str(r['major_points']), str(r['minor_points']),
            ])

    elif report_type == 'special_awards':
        headers = ['Event', 'Award', 'Recipient', 'Department']
        for ev in events:
            champ = _event_champion(ev)
            if champ and champ != '—':
                rows.append([ev.name, 'Champion', champ, '—'])
            runner = _event_runner_up(ev)
            if runner and runner != '—':
                rows.append([ev.name, 'Runner-Up', runner, '—'])

    elif report_type == 'participating_teams':
        headers = ['Team', 'Department', 'Event', 'Points']
        for t in BracketTeam.objects.filter(event__in=events).select_related('department', 'event'):
            rows.append([
                t.name,
                t.department.name if t.department_id else '—',
                t.event.name if t.event_id else '—',
                str(t.points or 0),
            ])

    elif report_type in ('individual_participants', 'contestants'):
        headers = ['Contestant / Participant', 'Department', 'Event']
        for ev in events:
            ids = ev.participant_ids or []
            if ids:
                for c in RegistryCandidate.objects.filter(id__in=ids):
                    rows.append([f'#{c.number} {c.name}', getattr(c, 'department', '') or '—', ev.name])
            elif ev.judging_event_id:
                for c in ev.judging_event.candidates.all()[:100]:
                    rows.append([c.name, getattr(c, 'department', '') or '—', ev.name])

    elif report_type == 'departments_participation':
        headers = ['Department', 'Events Involved', 'Teams', 'Points']
        for dept in Department.objects.filter(status=Department.STATUS_ACTIVE):
            team_count = BracketTeam.objects.filter(department=dept, event__in=events).count() if events else BracketTeam.objects.filter(department=dept).count()
            pts = BracketTeam.objects.filter(department=dept).aggregate(s=Sum('points'))['s'] or 0
            ev_count = Event.objects.filter(bracket_teams__department=dept).distinct().count()
            rows.append([dept.name, str(ev_count), str(team_count), str(pts)])

    elif report_type in ('generated_scoresheets', 'printed_scoresheets', 'match_scoresheets', 'criteria_scoresheets'):
        headers = ['Scoresheet', 'Event', 'Created', 'Status']
        sheets = GeneratedScoresheet.objects.select_related('event', 'template').order_by('-created_at')[:200]
        for s in sheets:
            rows.append([
                s.item_label or s.event_label or f'Scoresheet #{s.id}',
                s.event.name if s.event_id else (s.event_label or '—'),
                timezone.localtime(s.created_at).strftime('%b %d, %Y %I:%M %p') if s.created_at else '—',
                'Generated',
            ])

    elif report_type == 'administrators':
        headers = ['Name', 'Username', 'Email', 'Status', 'Last Login']
        for u in User.objects.filter(is_staff=True).order_by('username'):
            rows.append([
                u.get_full_name() or u.username,
                u.username,
                u.email or '—',
                'Active' if u.is_active else 'Inactive',
                timezone.localtime(u.last_login).strftime('%b %d, %Y') if u.last_login else 'Never',
            ])

    elif report_type == 'faculty_in_charge':
        headers = ['Name', 'Username', 'Email', 'Assigned Events', 'Status']
        faculty = User.objects.filter(
            Q(groups__name__iexact='Tabulator') | Q(groups__name__iexact='Faculty')
        ).distinct()
        for u in faculty:
            assigned = Event.objects.filter(Q(faculty_account=u) | Q(assigned_tabulators=u)).distinct().count()
            rows.append([
                u.get_full_name() or u.username,
                u.username,
                u.email or '—',
                str(assigned),
                'Active' if u.is_active else 'Inactive',
            ])

    elif report_type == 'judges':
        headers = ['Name', 'Username', 'Email', 'Assigned Events', 'Status']
        judges = User.objects.filter(
            Q(groups__name__iexact='Judge') | Q(groups__name__iexact='Judges') | Q(groups__name__iexact='Scorer')
        ).distinct()
        for u in judges:
            assigned = Event.objects.filter(assigned_judges=u).count()
            rows.append([
                u.get_full_name() or u.username,
                u.username,
                u.email or '—',
                str(assigned),
                'Active' if u.is_active else 'Inactive',
            ])

    elif report_type == 'user_activity':
        headers = ['User', 'Role', 'Action', 'Module', 'Date']
        for log in AuditLog.objects.select_related('user').order_by('-created_at')[:200]:
            rows.append([
                log.user_name,
                log.user_role,
                log.action,
                log.module,
                timezone.localtime(log.created_at).strftime('%b %d, %Y %I:%M %p'),
            ])

    elif report_type == 'audit_log':
        headers = ['User', 'Action', 'Date', 'Module', 'Description']
        for log in AuditLog.objects.order_by('-created_at')[:300]:
            rows.append([
                log.user_name,
                log.action,
                timezone.localtime(log.created_at).strftime('%b %d, %Y %I:%M %p'),
                log.module,
                (log.description or '')[:120],
            ])

    else:
        headers = ['Event Name', 'Category', 'Type', 'Date', 'Status']
        for ev in events:
            rows.append([
                ev.name, ev.category or '—', event_type_label(ev),
                ev.event_date.strftime('%b %d, %Y') if ev.event_date else '—',
                ev.get_status_display(),
            ])

    winners = sum(1 for r in rows if any(str(c).lower() in ('champion', 'winner') for c in r))
    stats = {
        'events': len(events),
        'rows': len(rows),
        'participants': _count_participants(events),
        'winners': winners or BracketTeam.objects.filter(event__in=events, is_champion=True).count(),
    }

    return {
        'report_type': report_type,
        'title': title,
        'category': category_for_type(report_type),
        'headers': headers,
        'rows': rows,
        'stats': stats,
        'highlight': highlight,
        'generated_at': timezone.localtime().strftime('%B %d, %Y · %I:%M %p'),
        'filters': filters,
    }


def _count_participants(events):
    total = 0
    for ev in events:
        if ev.participant_ids:
            total += len(ev.participant_ids)
        else:
            total += ev.bracket_teams.count() or int(ev.max_participants or 0) or int(ev.num_teams or 0)
    return total


def _event_champion(event):
    champ = BracketTeam.objects.filter(event=event, is_champion=True).select_related('department').first()
    if champ:
        return champ.department.name if champ.department_id else champ.name
    top = BracketTeam.objects.filter(event=event).select_related('department').order_by('-points', 'seed').first()
    if top:
        return top.department.name if top.department_id else top.name
    return '—'


def _event_runner_up(event):
    teams = list(BracketTeam.objects.filter(event=event).select_related('department').order_by('-points', 'seed')[:2])
    if len(teams) < 2:
        return '—'
    t = teams[1]
    return t.department.name if t.department_id else t.name


def export_report_pdf(preview: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter), leftMargin=24, rightMargin=24, topMargin=28, bottomMargin=24)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"EventTab — {preview.get('title', 'Report')}", styles['Title']),
        Paragraph(f"Category: {preview.get('category', '')} · Generated {preview.get('generated_at', '')}", styles['Normal']),
        Spacer(1, 10),
    ]
    hl = preview.get('highlight') or {}
    if hl.get('title'):
        story.append(Paragraph(f"<b>{hl.get('title')}</b>", styles['Heading2']))
        story.append(Paragraph(
            f"Type: {hl.get('event_type', '—')} · Champion: {hl.get('champion', '—')} · "
            f"Runner-Up: {hl.get('runner_up', '—')} · Date: {hl.get('date', '—')} · Venue: {hl.get('venue', '—')}",
            styles['Normal'],
        ))
        story.append(Spacer(1, 8))

    stats = preview.get('stats') or {}
    story.append(Paragraph(
        f"Events: {stats.get('events', 0)} · Participants: {stats.get('participants', 0)} · "
        f"Rows: {stats.get('rows', 0)} · Winners: {stats.get('winners', 0)}",
        styles['Normal'],
    ))
    story.append(Spacer(1, 10))

    headers = preview.get('headers') or []
    data = [headers]
    for row in (preview.get('rows') or [])[:250]:
        data.append([str(c)[:40] for c in row])
    if len(data) == 1:
        data.append(['—'] * max(len(headers), 1))
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#021943')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#cfdbe8')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f6f9fc')]),
    ]))
    story.append(table)
    doc.build(story)
    return buf.getvalue()


def export_report_excel(preview: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = (preview.get('title') or 'Report')[:31]
    headers = preview.get('headers') or []
    ws.append(headers)
    for row in preview.get('rows') or []:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_report_csv(preview: dict) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(preview.get('headers') or [])
    for row in preview.get('rows') or []:
        writer.writerow(row)
    return buf.getvalue()


def save_report_history(*, user, preview: dict, export_format: str, file_content: bytes | None = None):
    from django.core.files.base import ContentFile

    name = f"{preview.get('title', 'Report')} — {timezone.localtime().strftime('%b %d, %Y %H:%M')}"
    hist = AdminReportHistory(
        name=name[:200],
        category=preview.get('category') or '',
        report_type=preview.get('report_type') or '',
        export_format=export_format,
        filters=preview.get('filters') or {},
        preview={
            'title': preview.get('title'),
            'stats': preview.get('stats'),
            'headers': preview.get('headers'),
            'row_count': len(preview.get('rows') or []),
            'highlight': preview.get('highlight') or {},
        },
        generated_by=user,
    )
    hist.save()
    if file_content:
        ext = {'pdf': 'pdf', 'excel': 'xlsx', 'xlsx': 'xlsx', 'csv': 'csv'}.get(export_format, 'bin')
        hist.file.save(f'report-{hist.id}.{ext}', ContentFile(file_content), save=True)
    return hist


def history_rows(limit=30):
    rows = []
    for h in AdminReportHistory.objects.select_related('generated_by').order_by('-created_at')[:limit]:
        rows.append({
            'id': h.id,
            'name': h.name,
            'generated_by': (h.generated_by.get_full_name() or h.generated_by.username) if h.generated_by_id else '—',
            'generated_date': timezone.localtime(h.created_at).strftime('%b %d, %Y %I:%M %p'),
            'export_format': (h.export_format or '—').upper(),
            'has_file': bool(h.file),
            'report_type': h.report_type,
            'category': h.category,
        })
    return rows


def filter_context():
    events = list(Event.objects.order_by('-event_date').values('id', 'name'))
    departments = list(Department.objects.filter(status=Department.STATUS_ACTIVE).order_by('name').values_list('name', flat=True))
    categories = sorted({c for c in Event.objects.exclude(category='').values_list('category', flat=True) if c})
    faculty = list(
        User.objects.filter(Q(groups__name__iexact='Tabulator') | Q(groups__name__iexact='Faculty'))
        .distinct().order_by('username')
        .values('id', 'username', 'first_name', 'last_name')
    )
    judges = list(
        User.objects.filter(
            Q(groups__name__iexact='Judge') | Q(groups__name__iexact='Judges') | Q(groups__name__iexact='Scorer')
        ).distinct().order_by('username').values('id', 'username', 'first_name', 'last_name')
    )
    return {
        'events': events,
        'departments': departments,
        'categories': categories,
        'faculty_users': faculty,
        'judge_users': judges,
        'catalog': REPORT_CATALOG,
        'type_labels': TYPE_LABELS,
    }
