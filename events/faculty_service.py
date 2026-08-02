"""Faculty In-Charge: scoped event access, results review, and publish helpers."""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from .bracket_progression import apply_scoresheet_to_bracket, recompute_points
from .models import (
    BracketMatch,
    BracketTeam,
    Event,
    JudgeScore,
    RegistryCandidate,
    ScoreSheet,
    Team,
)
from .scoresheet_pdf import default_sample_payload, render_scoresheet_pdf


class FacultyServiceError(ValueError):
    pass


def models_q_for_faculty():
    return (
        Q(is_staff=True)
        | Q(groups__name__iexact='Faculty')
        | Q(groups__name__iexact='Scorer')
        | Q(groups__name__iexact='Tabulator')
    )


def is_faculty_user(user):
    if not user or not user.is_authenticated:
        return False
    return user.groups.filter(
        Q(name__iexact='Tabulator') | Q(name__iexact='Faculty')
    ).exists()


def faculty_event_qs(user):
    return (
        Event.objects.filter(Q(faculty_account=user) | Q(assigned_tabulators=user))
        .distinct()
        .select_related('faculty_account', 'chief_judge', 'judging_event')
        .prefetch_related('assigned_judges', 'assigned_tabulators')
    )


def faculty_can_access(user, event):
    if not event or not is_faculty_user(user):
        return False
    if event.faculty_account_id == user.id:
        return True
    return event.assigned_tabulators.filter(id=user.id).exists()


def event_type_label(event):
    method = (event.scoring_method or '').lower()
    if method == 'criteria':
        return 'Criteria-Based'
    if method == 'match' or (event.category or '').lower() == 'sports':
        return 'Match-Based'
    if event.judging_criteria_config:
        return 'Criteria-Based'
    return 'Match-Based' if event.bracket_matches.exists() else 'Event'


def serialize_event_row(event):
    start = event.event_date
    end = event.end_date or event.event_date
    schedule = ''
    if start:
        schedule = start.strftime('%b %d, %Y')
        if end and end != start:
            schedule += f' – {end.strftime("%b %d, %Y")}'
    status = event.get_publication_status_display() if event.publication_status else event.get_status_display()
    if event.results_finalized:
        status = 'Published Results'
    elif (event.result_processing_config or {}).get('ready_for_publication'):
        status = 'Ready for Publication'
    return {
        'id': event.id,
        'name': event.name,
        'event_type': event_type_label(event),
        'category': event.category or '—',
        'venue': event.venue or '—',
        'schedule': schedule or '—',
        'status': status,
        'publication_status': event.publication_status,
        'scoring_method': event.scoring_method or '',
        'division': event.division or '—',
        'classification': event.get_event_classification_display() or '—',
    }


def serialize_event_detail(event):
    row = serialize_event_row(event)
    judges = [
        (j.get_full_name() or j.username)
        for j in event.assigned_judges.all()
    ]
    if event.chief_judge_id:
        chief = event.chief_judge.get_full_name() or event.chief_judge.username
        if chief not in judges:
            judges.insert(0, f'{chief} (Chief)')
    participants = []
    if event.scoring_method == 'criteria' or event.participation_type == 'individual':
        ids = event.participant_ids or []
        for c in RegistryCandidate.objects.filter(id__in=ids):
            participants.append(f'#{c.number} {c.name}')
        if not participants and event.judging_event_id:
            for c in event.judging_event.candidates.all()[:50]:
                participants.append(c.name)
    else:
        ids = event.participant_ids or []
        if ids:
            for t in Team.objects.filter(id__in=ids):
                participants.append(t.name)
        else:
            for t in BracketTeam.objects.filter(event=event).order_by('seed', 'name')[:50]:
                participants.append(t.name)
    structure = 'Single Performance'
    if event.scoring_method == 'match':
        structure = (event.tournament_type or 'single_elim').replace('_', ' ').title()
    elif event.event_format:
        structure = event.get_event_format_display() or event.event_format
    row.update({
        'start_date': event.event_date.isoformat() if event.event_date else '',
        'end_date': (event.end_date or event.event_date).isoformat() if event.event_date else '',
        'judges': judges,
        'participants': participants,
        'structure': structure,
        'rounds_config': event.rounds_config or [],
        'results_finalized': event.results_finalized,
        'ready_for_publication': bool((event.result_processing_config or {}).get('ready_for_publication')),
        'apply_championship_points': event.apply_championship_points,
        'championship_points_config': event.championship_points_config or [],
        'faculty_name': (
            event.faculty_account.get_full_name().strip() or event.faculty_account.username
            if event.faculty_account_id else (event.faculty_in_charge or '—')
        ),
    })
    return row


def schedule_rows(event):
    rows = []
    matches = (
        BracketMatch.objects.filter(event=event)
        .select_related('team_a', 'team_b', 'winner')
        .order_by('match_number', 'match_date', 'match_time')
    )
    for m in matches:
        rows.append({
            'id': m.id,
            'game': f'Game {m.match_number}',
            'stage': m.round_name or '—',
            'matchup': f'{(m.team_a.name if m.team_a else "TBD")} vs {(m.team_b.name if m.team_b else "TBD")}',
            'date': m.match_date.isoformat() if m.match_date else '',
            'time': m.match_time.strftime('%I:%M %p') if m.match_time else '',
            'venue': m.venue or event.venue or '—',
            'status': m.get_status_display(),
            'status_key': m.status,
            'score_a': m.score_a or '',
            'score_b': m.score_b or '',
            'team_a': m.team_a.name if m.team_a else 'TBD',
            'team_b': m.team_b.name if m.team_b else 'TBD',
            'team_a_id': m.team_a_id,
            'team_b_id': m.team_b_id,
            'winner_id': m.winner_id,
        })
    if not rows and (event.rounds_config or []):
        for idx, round_cfg in enumerate(event.rounds_config):
            if isinstance(round_cfg, dict) and round_cfg.get('mode') == 'preliminary_final':
                rows.append({
                    'id': f'prelim-{idx}',
                    'game': 'Stage',
                    'stage': 'Preliminary Round',
                    'matchup': f"{round_cfg.get('prelim_participants', '—')} participants",
                    'date': event.event_date.isoformat() if event.event_date else '',
                    'time': '',
                    'venue': event.venue or '—',
                    'status': 'Configured',
                    'status_key': 'configured',
                })
                rows.append({
                    'id': f'final-{idx}',
                    'game': 'Stage',
                    'stage': 'Final Round',
                    'matchup': f"{round_cfg.get('finalists', '—')} finalists",
                    'date': event.event_date.isoformat() if event.event_date else '',
                    'time': '',
                    'venue': event.venue or '—',
                    'status': 'Configured',
                    'status_key': 'configured',
                })
            elif isinstance(round_cfg, dict):
                rows.append({
                    'id': f'round-{idx}',
                    'game': f'Round {idx + 1}',
                    'stage': round_cfg.get('name') or f'Round {idx + 1}',
                    'matchup': f"Weight {round_cfg.get('weight', '—')}%",
                    'date': event.event_date.isoformat() if event.event_date else '',
                    'time': '',
                    'venue': event.venue or '—',
                    'status': 'Configured',
                    'status_key': 'configured',
                })
    return rows


def export_schedule_csv(event):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['Game', 'Stage', 'Match / Participant', 'Date', 'Time', 'Venue', 'Status'])
    for row in schedule_rows(event):
        writer.writerow([
            row['game'], row['stage'], row['matchup'], row['date'], row['time'], row['venue'], row['status'],
        ])
    return buffer.getvalue()


def dashboard_stats(user):
    events = faculty_event_qs(user)
    today = timezone.localdate()
    todays = BracketMatch.objects.filter(event__in=events, match_date=today).count()
    pending = ScoreSheet.objects.filter(
        event__in=events, status__in=[ScoreSheet.STATUS_PENDING, ScoreSheet.STATUS_CONFIRMED]
    ).count()
    # Criteria pending: events with judges not all submitted and not finalized
    criteria_pending = 0
    for ev in events.filter(scoring_method='criteria', results_finalized=False)[:30]:
        mon = judge_monitoring(ev)
        if mon['total'] and mon['submitted'] < mon['total']:
            criteria_pending += 1
    published = events.filter(
        Q(results_finalized=True) | Q(publication_status=Event.PUBLICATION_PUBLISHED)
    ).count()
    return {
        'assigned_events': events.count(),
        'todays_schedule': todays,
        'pending_reviews': pending + criteria_pending,
        'published_events': published,
    }


def recent_notifications(user, limit=8):
    events = faculty_event_qs(user)
    event_ids = list(events.values_list('id', flat=True)[:100])
    notes = []
    ct = ContentType.objects.get_for_model(Event)
    for entry in LogEntry.objects.filter(content_type=ct, object_id__in=[str(i) for i in event_ids]).order_by('-action_time')[:limit]:
        msg = (entry.change_message or '').lower()
        kind = 'Schedule Updates'
        if 'assign' in msg or 'created' in msg:
            kind = 'New Assigned Event'
        elif 'judge' in msg or 'submit' in msg:
            kind = 'Judge Submission Completed'
        elif 'publish' in msg or 'final' in msg:
            kind = 'Results Published'
        notes.append({
            'kind': kind,
            'message': entry.change_message or entry.object_repr,
            'when': timezone.localtime(entry.action_time).strftime('%b %d, %Y %I:%M %p'),
        })
    if not notes:
        for ev in events.order_by('-updated_at')[:limit]:
            notes.append({
                'kind': 'New Assigned Event',
                'message': f'Assigned to {ev.name}',
                'when': timezone.localtime(ev.updated_at).strftime('%b %d, %Y %I:%M %p') if ev.updated_at else '—',
            })
    return notes


def judge_monitoring(event):
    judges = list(event.assigned_judges.all())
    if event.chief_judge_id and event.chief_judge not in judges:
        judges.append(event.chief_judge)
    judging = event.judging_event
    rows = []
    submitted = 0
    for judge in judges:
        status = 'Pending'
        submitted_at = None
        if judging:
            scores = JudgeScore.objects.filter(event=judging, judge=judge)
            if scores.filter(is_locked=True).exists():
                status = 'Submitted'
                submitted += 1
                latest = scores.filter(submitted_at__isnull=False).order_by('-submitted_at').first()
                submitted_at = latest.submitted_at if latest else None
            elif scores.exists():
                status = 'Draft'
        rows.append({
            'id': judge.id,
            'name': judge.get_full_name() or judge.username,
            'status': status,
            'submitted_at': timezone.localtime(submitted_at).strftime('%b %d, %Y %I:%M %p') if submitted_at else '—',
        })
    total = len(rows)
    return {
        'rows': rows,
        'submitted': submitted,
        'remaining': max(0, total - submitted),
        'total': total,
        'all_submitted': total > 0 and submitted >= total,
    }


def compute_criteria_rankings(event):
    """Weighted totals from JudgeScore; applies criterion weights when available."""
    judging = event.judging_event
    if not judging:
        return []
    criteria = list(judging.criteria.all())
    weight_by_id = {c.id: float(c.weight_percent or 0) or 1.0 for c in criteria}
    max_by_id = {c.id: float(c.max_score or 100) for c in criteria}
    candidates = list(judging.candidates.all())
    scores = JudgeScore.objects.filter(event=judging, is_locked=True).select_related('candidate', 'criterion', 'judge')
    # candidate -> judge -> criterion -> score
    by_cand = defaultdict(lambda: defaultdict(dict))
    for s in scores:
        by_cand[s.candidate_id][s.judge_id][s.criterion_id] = float(s.score)

    method = event.criteria_score_method or 'weighted_percentage'
    rankings = []
    for cand in candidates:
        judge_totals = []
        for judge_id, cmap in by_cand.get(cand.id, {}).items():
            if method == 'raw_score':
                total = sum(cmap.values())
            elif method == 'average_score':
                total = sum(cmap.values()) / max(1, len(cmap))
            else:
                # weighted percentage of max
                total = 0.0
                weight_sum = 0.0
                for cid, val in cmap.items():
                    w = weight_by_id.get(cid, 1.0)
                    mx = max_by_id.get(cid, 100) or 100
                    total += (val / mx) * w
                    weight_sum += w
                if weight_sum and abs(weight_sum - 100) > 0.01:
                    total = total * (100.0 / weight_sum) if weight_sum else total
            judge_totals.append(total)
        if not judge_totals:
            final = 0.0
        elif method == 'average_score':
            final = sum(judge_totals) / len(judge_totals)
        else:
            final = sum(judge_totals) / len(judge_totals)
        # Deductions
        deductions = 0.0
        if event.deductions_enabled:
            for d in event.deductions_config or []:
                try:
                    val = float(d.get('value') or 0)
                except (TypeError, ValueError):
                    val = 0
                if (d.get('deduction_type') or 'fixed') == 'percentage':
                    deductions += final * (val / 100.0)
                else:
                    deductions += val
        final = max(0.0, final - deductions)
        rankings.append({
            'candidate_id': cand.id,
            'name': cand.name,
            'number': cand.number,
            'final_score': round(final, 2),
            'breakdown': f'{len(judge_totals)} judge(s)',
            'comments': [],
            'penalties': deductions,
        })
    rankings.sort(key=lambda r: (-r['final_score'], r['name']))
    # Tie-break: stable order already; apply simple chief-judge preference if configured
    for idx, row in enumerate(rankings, start=1):
        row['rank'] = idx
    return rankings


def stage_panels(event):
    panels = []
    for idx, cfg in enumerate(event.rounds_config or []):
        if not isinstance(cfg, dict):
            continue
        if cfg.get('mode') == 'preliminary_final':
            panels.append({
                'index': idx,
                'name': 'Preliminary Round',
                'weight': cfg.get('prelim_percentage'),
                'qualifiers': cfg.get('finalists'),
                'rule': f"Advance Top {cfg.get('finalists', '—')}",
            })
            panels.append({
                'index': idx,
                'name': 'Final Round',
                'weight': cfg.get('final_percentage'),
                'qualifiers': None,
                'rule': 'Final Ranking',
            })
        else:
            qualifiers = cfg.get('qualifiers')
            panels.append({
                'index': idx,
                'name': cfg.get('name') or f'Stage {idx + 1}',
                'weight': cfg.get('weight'),
                'qualifiers': qualifiers,
                'rule': f'Advance Top {qualifiers}' if qualifiers else 'Final Ranking',
            })
    return panels


def _patch_result_config(event, **kwargs):
    cfg = dict(event.result_processing_config or {})
    cfg.update(kwargs)
    event.result_processing_config = cfg
    event.save(update_fields=['result_processing_config', 'updated_at'])


@transaction.atomic
def save_match_result(event, match, user, *, score_a, score_b, winner_id, remarks, confirm=False):
    if match.event_id != event.id:
        raise FacultyServiceError('Match does not belong to this event.')
    try:
        sa = Decimal(str(score_a))
        sb = Decimal(str(score_b))
    except Exception as exc:
        raise FacultyServiceError('Scores must be numeric.') from exc
    winner = None
    if winner_id:
        if int(winner_id) not in {match.team_a_id, match.team_b_id}:
            raise FacultyServiceError('Winner must be Team A or Team B.')
        winner = BracketTeam.objects.filter(pk=int(winner_id), event=event).first()
    elif sa != sb:
        winner = match.team_a if sa > sb else match.team_b

    sheet, _ = ScoreSheet.objects.get_or_create(
        event=event,
        match=match,
        defaults={'tabulator': user},
    )
    sheet.tabulator = user
    sheet.score_team_a = sa
    sheet.score_team_b = sb
    sheet.winner = winner
    sheet.remarks = (remarks or '').strip()
    sheet.status = ScoreSheet.STATUS_FINALIZED if confirm else ScoreSheet.STATUS_PENDING
    sheet.save()

    if confirm:
        apply_scoresheet_to_bracket(sheet)
        recompute_points(event)
        remaining = BracketMatch.objects.filter(event=event).exclude(
            status=BracketMatch.STATUS_COMPLETED
        ).count()
        if remaining == 0:
            event.status = Event.STATUS_COMPLETED
            event.save(update_fields=['status', 'updated_at'])
            _apply_championship_points(event)
        _patch_result_config(event, ready_for_publication=True)
        LogEntry.objects.create(
            user=user,
            content_type=ContentType.objects.get_for_model(Event),
            object_id=str(event.pk),
            object_repr=event.name,
            action_flag=CHANGE,
            change_message=f'Faculty confirmed match {match.match_number} result.',
        )
    return sheet


def _apply_championship_points(event):
    if not event.apply_championship_points:
        return
    config = event.championship_points_config or []
    if not config:
        return
    # Rank bracket teams by points
    teams = list(BracketTeam.objects.filter(event=event).select_related('department').order_by('-points', 'seed'))
    for idx, team in enumerate(teams):
        if idx >= len(config):
            break
        pts = int(config[idx].get('points') or 0)
        # Store on team points as championship award overlay
        team.points = (team.points or 0) + pts
        team.save(update_fields=['points'])


@transaction.atomic
def return_for_correction(event, user, judge_ids=None):
    judging = event.judging_event
    if not judging:
        raise FacultyServiceError('No judging event linked.')
    qs = JudgeScore.objects.filter(event=judging)
    if judge_ids:
        qs = qs.filter(judge_id__in=judge_ids)
    qs.update(is_locked=False)
    _patch_result_config(event, ready_for_publication=False, approved=False)
    _notify_judges(event, judge_ids)
    LogEntry.objects.create(
        user=user,
        content_type=ContentType.objects.get_for_model(Event),
        object_id=str(event.pk),
        object_repr=event.name,
        action_flag=CHANGE,
        change_message='Faculty returned results for correction.',
    )


@transaction.atomic
def approve_results(event, user):
    _patch_result_config(event, approved=True, ready_for_publication=True)
    LogEntry.objects.create(
        user=user,
        content_type=ContentType.objects.get_for_model(Event),
        object_id=str(event.pk),
        object_repr=event.name,
        action_flag=CHANGE,
        change_message='Faculty approved results. Ready for publication.',
    )


@transaction.atomic
def publish_results(event, user):
    judging = event.judging_event
    if judging:
        JudgeScore.objects.filter(event=judging).update(is_locked=True)
    event.results_finalized = True
    event.publication_status = Event.PUBLICATION_PUBLISHED
    if event.status != Event.STATUS_COMPLETED:
        event.status = Event.STATUS_COMPLETED
    cfg = dict(event.result_processing_config or {})
    cfg['ready_for_publication'] = False
    cfg['published_at'] = timezone.now().isoformat()
    cfg['published_by'] = user.id
    event.result_processing_config = cfg
    event.save()
    _apply_championship_points(event)
    try:
        from .mobile_sync import sync_event_to_mobile
        sync_event_to_mobile(event)
    except Exception:
        pass
    LogEntry.objects.create(
        user=user,
        content_type=ContentType.objects.get_for_model(Event),
        object_id=str(event.pk),
        object_repr=event.name,
        action_flag=CHANGE,
        change_message='Faculty published official results.',
    )


@transaction.atomic
def confirm_stage_advancement(event, user, stage_index, qualifier_ids):
    cfg = dict(event.result_processing_config or {})
    stages = dict(cfg.get('confirmed_stages') or {})
    stages[str(stage_index)] = {
        'qualifier_ids': list(qualifier_ids),
        'confirmed_at': timezone.now().isoformat(),
        'confirmed_by': user.id,
    }
    cfg['confirmed_stages'] = stages
    event.result_processing_config = cfg
    event.save(update_fields=['result_processing_config', 'updated_at'])
    LogEntry.objects.create(
        user=user,
        content_type=ContentType.objects.get_for_model(Event),
        object_id=str(event.pk),
        object_repr=event.name,
        action_flag=CHANGE,
        change_message=f'Faculty confirmed stage {stage_index} advancement.',
    )


def leaderboard_rows():
    """Overall championship standings by department from bracket team points."""
    from .models import Department
    dept_points = defaultdict(int)
    for team in BracketTeam.objects.select_related('department').filter(points__gt=0):
        if team.department_id:
            dept_points[team.department_id] += int(team.points or 0)
    depts = {d.id: d for d in Department.objects.filter(id__in=dept_points.keys())}
    rows = []
    for dept_id, pts in dept_points.items():
        dept = depts.get(dept_id)
        rows.append({'department': dept.name if dept else '—', 'points': pts})
    rows.sort(key=lambda r: (-r['points'], r['department']))
    for idx, row in enumerate(rows, start=1):
        row['rank'] = idx
    return rows


def generate_match_scoresheet_pdf(event, match):
    payload = {
        **default_sample_payload(),
        'EventName': event.name,
        'GameNumber': str(match.match_number),
        'Date': match.match_date.strftime('%b %d, %Y') if match.match_date else '',
        'Venue': match.venue or event.venue or '',
        'TeamA': match.team_a.name if match.team_a else 'TBD',
        'TeamB': match.team_b.name if match.team_b else 'TBD',
        'ScoreA': match.score_a or '',
        'ScoreB': match.score_b or '',
        'Winner': match.winner.name if match.winner else '',
    }
    from .scoresheet_pdf import default_match_layout
    return render_scoresheet_pdf(default_match_layout(), payload=payload)


def _notify_judges(event, judge_ids=None):
    from django.conf import settings
    backend = (getattr(settings, 'EMAIL_BACKEND', '') or '').lower()
    if 'console' in backend or 'locmem' in backend or 'dummy' in backend:
        return
    if not getattr(settings, 'EMAIL_HOST_USER', '') or not getattr(settings, 'EMAIL_HOST_PASSWORD', ''):
        return
    User = get_user_model()
    qs = event.assigned_judges.all()
    if judge_ids:
        qs = qs.filter(id__in=judge_ids)
    for user in qs:
        email = (user.email or '').strip()
        if not email:
            continue
        try:
            send_mail(
                f'Results returned for correction: {event.name}',
                f'Hello {user.get_full_name() or user.username},\n\n'
                f'Faculty returned scores for "{event.name}" for correction. '
                f'Please edit and resubmit in the Judge app.\n',
                settings.DEFAULT_FROM_EMAIL,
                [email],
                fail_silently=True,
            )
        except Exception:
            continue
