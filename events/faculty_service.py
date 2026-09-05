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
from .scoresheet_pdf import render_scoresheet_pdf


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


def _faculty_identity_keys(user) -> set[str]:
    """Usernames / display names used to match legacy text assignments."""
    keys = set()
    if not user:
        return keys
    username = (user.username or '').strip().lower()
    if username:
        keys.add(username)
    full = (user.get_full_name() or '').strip().lower()
    if full:
        keys.add(full)
    return keys


def _link_legacy_faculty_assignments(user):
    """
    Repair events that only stored faculty_in_charge text (no FK).
    Matches username or full name, case-insensitive.
    """
    if not user or not getattr(user, 'pk', None):
        return
    keys = _faculty_identity_keys(user)
    if not keys:
        return
    name_q = Q()
    for key in keys:
        name_q |= Q(faculty_in_charge__iexact=key)
    Event.objects.filter(faculty_account__isnull=True).filter(name_q).update(faculty_account_id=user.pk)


def resolve_faculty_user_from_text(text):
    """Resolve a faculty_in_charge display string to a Faculty/Tabulator user."""
    value = (text or '').strip()
    if not value:
        return None
    User = get_user_model()
    qs = User.objects.filter(is_active=True).filter(
        Q(groups__name__iexact='Tabulator') | Q(groups__name__iexact='Faculty')
    ).distinct()
    by_username = qs.filter(username__iexact=value).first()
    if by_username:
        return by_username
    # Match full name loosely
    for user in qs.filter(Q(first_name__icontains=value) | Q(last_name__icontains=value))[:20]:
        full = (user.get_full_name() or '').strip()
        if full.lower() == value.lower():
            return user
    return None


def faculty_event_qs(user):
    """
    Events where the logged-in user is Faculty In-Charge (FK) or an assigned tabulator.
    Also includes legacy rows that only stored the faculty username/name in faculty_in_charge.
    Does not filter by publication/status — assigned drafts and published events both appear.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return Event.objects.none()

    _link_legacy_faculty_assignments(user)

    q = Q(faculty_account=user) | Q(assigned_tabulators=user)
    keys = _faculty_identity_keys(user)
    if keys:
        legacy = Q(faculty_account__isnull=True)
        name_q = Q()
        for key in keys:
            name_q |= Q(faculty_in_charge__iexact=key)
        q |= legacy & name_q

    return (
        Event.objects.filter(q)
        .distinct()
        .select_related('faculty_account', 'chief_judge', 'judging_event')
        .prefetch_related('assigned_judges', 'assigned_tabulators')
    )


def faculty_can_access(user, event):
    if not event or not user or not user.is_authenticated:
        return False
    if not is_faculty_user(user) and not user.is_staff:
        return False
    if event.faculty_account_id == user.id:
        return True
    if event.assigned_tabulators.filter(id=user.id).exists():
        return True
    # Legacy text-only assignment
    text = (event.faculty_in_charge or '').strip().lower()
    if text and text in _faculty_identity_keys(user):
        if event.faculty_account_id is None:
            Event.objects.filter(pk=event.pk, faculty_account__isnull=True).update(
                faculty_account_id=user.id
            )
            event.faculty_account_id = user.id
        return True
    return False


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
                status = str(round_cfg.get('status') or 'configured').replace('_', ' ').title()
                rows.append({
                    'id': f'round-{idx}',
                    'game': f'Stage {round_cfg.get("stage_number") or (idx + 1)}',
                    'stage': round_cfg.get('name') or f'Stage {idx + 1}',
                    'matchup': (
                        f"Weight {round_cfg.get('weight', '—')}%"
                        + (
                            ' · Final'
                            if round_cfg.get('is_final')
                            else f" · Advance {round_cfg.get('qualifiers', '—')}"
                        )
                    ),
                    'date': event.event_date.isoformat() if event.event_date else '',
                    'time': '',
                    'venue': event.venue or '—',
                    'status': status,
                    'status_key': str(round_cfg.get('status') or 'configured'),
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
            scores = JudgeScore.objects.filter(candidate__event=judging, judge=judge)
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
    scores = JudgeScore.objects.filter(candidate__event=judging, is_locked=True).select_related('candidate', 'criterion', 'judge')
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
                # Absolute event weight: (score / max) × event_weight_%
                total = 0.0
                for cid, val in cmap.items():
                    w = weight_by_id.get(cid, 1.0)
                    mx = max_by_id.get(cid, 100) or 100
                    total += (val / mx) * w
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
    active_stage = None
    stages = [row for row in (event.rounds_config or []) if isinstance(row, dict)]
    if (event.event_format or '') in {'multiple_stage', 'multiple_rounds'} and stages:
        result_cfg = event.result_processing_config or {}
        active_idx = int(result_cfg.get('active_stage_index') or 0)
        if 0 <= active_idx < len(stages):
            active_stage = stages[active_idx]
    dept_by_id = {c.id: (c.department or '') for c in candidates}
    for idx, row in enumerate(rankings, start=1):
        row['rank'] = idx
        row['department'] = dept_by_id.get(row['candidate_id'], '') or '—'
        if active_stage and not active_stage.get('is_final'):
            quals = int(active_stage.get('qualifiers') or 0)
            method = (active_stage.get('qualification_method') or 'top_ranking').lower()
            if method == 'top_ranking' and quals:
                row['qualification_status'] = 'Qualified' if idx <= quals else 'Eliminated'
            elif method == 'minimum_score':
                minimum = float(active_stage.get('minimum_score') or 0)
                row['qualification_status'] = (
                    'Qualified' if row['final_score'] >= minimum else 'Eliminated'
                )
            else:
                row['qualification_status'] = 'Pending Manual Selection'
        else:
            row['qualification_status'] = 'Finalist'
    return rankings


def stage_panels(event):
    panels = []
    cfg_list = event.rounds_config or []
    result_cfg = event.result_processing_config or {}
    confirmed = result_cfg.get('confirmed_stages') or {}
    for idx, cfg in enumerate(cfg_list):
        if not isinstance(cfg, dict):
            continue
        if cfg.get('mode') == 'preliminary_final':
            panels.append({
                'index': 0,
                'name': 'Preliminary Round',
                'weight': cfg.get('prelim_percentage'),
                'qualifiers': cfg.get('finalists'),
                'rule': f"Advance Top {cfg.get('finalists', '—')}",
                'status': 'open',
                'status_label': 'Open',
                'qualification_method': 'top_ranking',
                'carry_previous_scores': False,
                'require_faculty_confirmation': True,
                'is_final': False,
                'confirmed': '0' in confirmed,
            })
            panels.append({
                'index': 1,
                'name': 'Final Round',
                'weight': cfg.get('final_percentage'),
                'qualifiers': None,
                'rule': 'Final Ranking · No Further Qualification',
                'status': 'locked' if '0' not in confirmed else 'open',
                'status_label': 'Locked' if '0' not in confirmed else 'Open',
                'qualification_method': None,
                'carry_previous_scores': bool(cfg.get('carry_prelim_scores')),
                'require_faculty_confirmation': False,
                'is_final': True,
                'confirmed': False,
            })
            continue

        is_final = bool(cfg.get('is_final')) or idx == len(cfg_list) - 1
        qualifiers = cfg.get('qualifiers')
        method = cfg.get('qualification_method') or ('top_ranking' if not is_final else None)
        status = str(cfg.get('status') or ('open' if idx == 0 else 'locked'))
        if str(idx) in confirmed and not is_final:
            status = 'completed'
        rule = 'Final Ranking · No Further Qualification' if is_final else (
            f"Advance Top {qualifiers}" if method == 'top_ranking'
            else ('Manual Faculty Selection' if method == 'manual_selection'
                  else f"Minimum Score {cfg.get('minimum_score', '—')}")
        )
        panels.append({
            'index': idx,
            'name': cfg.get('name') or f'Stage {idx + 1}',
            'weight': cfg.get('weight'),
            'qualifiers': qualifiers,
            'rule': rule,
            'status': status,
            'status_label': status.replace('_', ' ').title(),
            'qualification_method': method,
            'carry_previous_scores': bool(cfg.get('carry_previous_scores')),
            'require_faculty_confirmation': bool(cfg.get('require_faculty_confirmation')),
            'is_final': is_final,
            'confirmed': str(idx) in confirmed,
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
        try:
            from .audit_service import record_audit
            record_audit(
                user=user,
                action='Confirmed Match Result',
                description=f'Confirmed match {match.match_number} for "{event.name}".',
                module='Results',
                event_name=event.name,
            )
        except Exception:
            pass
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
    qs = JudgeScore.objects.filter(candidate__event=judging)
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
    try:
        from .audit_service import record_audit
        record_audit(
            user=user,
            action='Returned Result for Correction',
            description=f'Returned scores for correction on "{event.name}".',
            module='Results',
            event_name=event.name,
        )
    except Exception:
        pass


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
    try:
        from .audit_service import record_audit
        record_audit(
            user=user,
            action='Approved Results',
            description=f'Approved results for "{event.name}".',
            module='Results',
            event_name=event.name,
        )
    except Exception:
        pass


@transaction.atomic
def publish_results(event, user):
    judging = event.judging_event
    if judging:
        JudgeScore.objects.filter(candidate__event=judging).update(is_locked=True)
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
    try:
        from .audit_service import record_audit
        record_audit(
            user=user,
            action='Published Official Results',
            description=f'Published official results for "{event.name}".',
            module='Results',
            event_name=event.name,
        )
        record_audit(
            user=user,
            action='Modified Championship Points',
            description=f'Championship points / leaderboard updated after publishing "{event.name}".',
            module='Championship',
            event_name=event.name,
        )
    except Exception:
        pass


@transaction.atomic
def confirm_stage_advancement(event, user, stage_index, qualifier_ids):
    """Confirm qualifiers for a stage and unlock the next stage when required."""
    stage_index = int(stage_index)
    stages = list(event.rounds_config or [])
    if stage_index < 0 or stage_index >= len(stages):
        raise FacultyServiceError('Stage index is invalid.')
    stage = stages[stage_index]
    if not isinstance(stage, dict):
        raise FacultyServiceError('Stage configuration is invalid.')
    if stage.get('is_final') or stage_index == len(stages) - 1:
        raise FacultyServiceError('The final stage does not generate another qualification list.')

    cfg = dict(event.result_processing_config or {})
    qualified_map = dict(cfg.get('qualified_participant_ids') or {})
    current_ids = qualified_map.get(str(stage_index)) or event.participant_ids or []
    eligible_ids = []
    for value in current_ids:
        try:
            eligible_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    eligible_ids = list(dict.fromkeys(eligible_ids))
    method = (stage.get('qualification_method') or 'top_ranking').lower()
    try:
        configured_expected = int(stage.get('qualifiers') or 0)
    except (TypeError, ValueError) as exc:
        raise FacultyServiceError('Stage qualifier count is invalid.') from exc
    if method in {'advance_all', 'no_elimination'}:
        expected = len(eligible_ids)
    else:
        expected = min(configured_expected, len(eligible_ids)) if eligible_ids else configured_expected

    cleaned_ids = []
    for value in qualifier_ids or []:
        try:
            cleaned_ids.append(int(value))
        except (TypeError, ValueError) as exc:
            raise FacultyServiceError('Qualifier selection is invalid.') from exc
    if len(cleaned_ids) != len(set(cleaned_ids)):
        raise FacultyServiceError('Duplicate qualifier selections are not allowed.')
    if eligible_ids:
        invalid_ids = [value for value in cleaned_ids if value not in eligible_ids]
        if invalid_ids:
            raise FacultyServiceError('Qualifier selection includes candidates outside this stage.')
    if method != 'manual_selection' and expected and len(cleaned_ids) != expected:
        raise FacultyServiceError(f'Select exactly {expected} qualifiers for this stage.')
    if method == 'manual_selection' and expected and len(cleaned_ids) > expected:
        raise FacultyServiceError(f'Manual selection cannot exceed {expected} qualifiers.')
    if not cleaned_ids:
        raise FacultyServiceError('Select at least one qualifier.')

    confirmed = dict(cfg.get('confirmed_stages') or {})
    confirmed[str(stage_index)] = {
        'qualifier_ids': cleaned_ids,
        'confirmed_at': timezone.now().isoformat(),
        'confirmed_by': user.id,
    }
    qualified_map[str(stage_index + 1)] = cleaned_ids
    cfg['confirmed_stages'] = confirmed
    cfg['qualified_participant_ids'] = qualified_map
    cfg['active_stage_index'] = stage_index + 1

    # Update stage statuses in rounds_config.
    stage['status'] = 'completed'
    stages[stage_index] = stage
    if stage_index + 1 < len(stages) and isinstance(stages[stage_index + 1], dict):
        stages[stage_index + 1]['status'] = 'open'
    event.rounds_config = stages
    event.result_processing_config = cfg
    event.save(update_fields=['rounds_config', 'result_processing_config', 'updated_at'])

    LogEntry.objects.create(
        user=user,
        content_type=ContentType.objects.get_for_model(Event),
        object_id=str(event.pk),
        object_repr=event.name,
        action_flag=CHANGE,
        change_message=f'Faculty confirmed stage {stage_index + 1} advancement.',
    )
    try:
        from .audit_service import record_audit
        record_audit(
            user=user,
            action='Confirmed Stage Qualifiers',
            description=(
                f'Confirmed {len(cleaned_ids)} qualifier(s) for stage '
                f'"{stage.get("name", stage_index + 1)}" in "{event.name}".'
            ),
            module='Results',
            event_name=event.name,
        )
    except Exception:
        pass
    return {
        'stage_index': stage_index,
        'next_stage_index': stage_index + 1,
        'qualifier_ids': cleaned_ids,
    }


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


def leaderboard_by_game():
    """
    Per-game (event) department standings so Faculty can see who won each category/game.
    Groups BracketTeam points by event; includes event category for context.
    """
    from .models import Department

    by_event = defaultdict(lambda: defaultdict(int))
    event_meta = {}
    for team in BracketTeam.objects.select_related('department', 'event').filter(points__gt=0):
        if not team.department_id or not team.event_id:
            continue
        by_event[team.event_id][team.department_id] += int(team.points or 0)
        if team.event_id not in event_meta:
            event_meta[team.event_id] = team.event

    all_dept_ids = {dept_id for bucket in by_event.values() for dept_id in bucket}
    depts = {d.id: d for d in Department.objects.filter(id__in=all_dept_ids)}

    games = []
    for event_id, dept_pts in by_event.items():
        event = event_meta.get(event_id)
        if not event:
            continue
        rows = []
        for dept_id, pts in dept_pts.items():
            dept = depts.get(dept_id)
            rows.append({
                'department': dept.name if dept else '—',
                'points': pts,
            })
        rows.sort(key=lambda r: (-r['points'], r['department']))
        for idx, row in enumerate(rows, start=1):
            row['rank'] = idx
            row['is_winner'] = idx == 1
        winner = rows[0] if rows else None
        classification = ''
        if event.event_classification:
            try:
                classification = event.get_event_classification_display()
            except Exception:
                classification = event.event_classification
        games.append({
            'event_id': event.id,
            'game': event.name,
            'category': (event.category or 'Uncategorized').strip() or 'Uncategorized',
            'classification': classification or '—',
            'winner': winner['department'] if winner else '—',
            'winner_points': winner['points'] if winner else 0,
            'rows': rows,
        })

    games.sort(key=lambda g: (g['category'].lower(), g['game'].lower()))
    return games


def leaderboard_category_winners(games=None):
    """Compact champion list: one winner row per game, grouped for display."""
    games = games if games is not None else leaderboard_by_game()
    return [
        {
            'category': g['category'],
            'game': g['game'],
            'classification': g['classification'],
            'winner': g['winner'],
            'points': g['winner_points'],
        }
        for g in games
        if g.get('winner') and g['winner'] != '—'
    ]


def template_event_type(event) -> str:
    method = (event.scoring_method or '').lower()
    if method == 'criteria' or (event.judging_criteria_config or event.rounds_config):
        if method != 'match':
            return 'criteria'
    if method == 'match' or event.bracket_matches.exists():
        return 'match'
    if event.judging_criteria_config or event.event_format:
        return 'criteria'
    return 'match'


def resolve_template_for_event(event):
    from .models import ScoresheetTemplate
    from .scoresheet_pdf import pack_template_layout

    assigned = getattr(event, 'scoresheet_template', None)
    if assigned is not None and assigned.status == ScoresheetTemplate.STATUS_ACTIVE:
        return assigned

    etype = template_event_type(event)
    qs = ScoresheetTemplate.objects.filter(
        status=ScoresheetTemplate.STATUS_ACTIVE,
        event_type=etype,
    )
    category = (event.category or '').strip()
    if category:
        matched = qs.filter(category__iexact=category).order_by('-updated_at').first()
        if matched:
            return matched
    blank = qs.filter(category='').order_by('-updated_at').first()
    if blank:
        return blank
    return qs.order_by('-updated_at').first()


def parse_item_key(item_key: str):
    key = (item_key or '').strip()
    if key.startswith('match-'):
        return 'match', int(key.split('-', 1)[1])
    if key.startswith('stage-'):
        return 'stage', int(key.split('-', 1)[1])
    if key.startswith('round-'):
        return 'stage', int(key.split('-', 1)[1])
    if key.startswith('prelim-') or key.startswith('final-'):
        return 'legacy_stage', key
    if key == 'event':
        return 'event', None
    raise FacultyServiceError('Invalid scoresheet item.')


def scoresheet_payload_for(event, item_kind, item_ref):
    """Build payload with event info filled and score/signature fields blank."""
    faculty_name = ''
    if event.faculty_account_id:
        faculty_name = event.faculty_account.get_full_name().strip() or event.faculty_account.username
    else:
        faculty_name = event.faculty_in_charge or ''

    classification = ''
    if event.event_classification:
        try:
            classification = event.get_event_classification_display()
        except Exception:
            classification = event.event_classification

    tournament_format = ''
    if event.tournament_type:
        labels = {
            'single_elimination': 'Single Elimination',
            'double_elimination': 'Double Elimination',
            'round_robin': 'Round Robin',
        }
        tournament_format = labels.get(event.tournament_type, event.tournament_type.replace('_', ' ').title())

    payload = {
        'EventName': event.name or '',
        'Classification': classification,
        'Division': event.division or '',
        'TournamentFormat': tournament_format,
        'Category': event.category or '',
        'Venue': event.venue or '',
        'Date': event.event_date.strftime('%b %d, %Y') if event.event_date else '',
        'Time': event.event_time.strftime('%I:%M %p') if event.event_time else '',
        'PlayingArea': '',
        'FacultyInCharge': faculty_name,
        'GameNumber': '',
        'Round': '',
        'TeamA': '',
        'TeamB': '',
        'ScoreA': '',
        'ScoreB': '',
        'Winner': '',
        'StageName': '',
        'Contestant': '',
        'ContestantNumber': '',
        'Department': '',
        'CriteriaWeight': '',
        'MaximumScore': '',
        'JudgeScore': '',
        'TotalScore': '',
        'Remarks': '',
    }

    if item_kind == 'match':
        match = item_ref
        payload.update({
            'GameNumber': str(match.match_number),
            'Round': match.round_name or '',
            'Date': match.match_date.strftime('%b %d, %Y') if match.match_date else payload['Date'],
            'Time': match.match_time.strftime('%I:%M %p') if match.match_time else payload['Time'],
            'Venue': match.venue or event.venue or '',
            'TeamA': match.team_a.name if match.team_a else 'TBD',
            'TeamB': match.team_b.name if match.team_b else 'TBD',
            # Scores/winner left blank for paper recording
            'ScoreA': '',
            'ScoreB': '',
            'Winner': '',
        })
    elif item_kind in {'stage', 'legacy_stage', 'event'}:
        stage_name = 'Competition'
        if item_kind == 'stage' and isinstance(item_ref, dict):
            stage_name = item_ref.get('name') or f"Stage {item_ref.get('stage_number') or ''}"
            weight = item_ref.get('weight')
            if weight is not None and weight != '':
                payload['CriteriaWeight'] = f'{weight}%'
        elif item_kind == 'legacy_stage':
            stage_name = 'Preliminary Round' if str(item_ref).startswith('prelim') else 'Final Round'
        payload['StageName'] = stage_name
        # Populate criteria meta from event config when available
        criteria = event.judging_criteria_config or []
        if isinstance(criteria, list) and criteria:
            max_scores = []
            weights = []
            for c in criteria:
                if not isinstance(c, dict):
                    continue
                if c.get('max_score') not in (None, ''):
                    max_scores.append(str(c.get('max_score')))
                if c.get('weight') not in (None, '') or c.get('weight_percent') not in (None, ''):
                    weights.append(str(c.get('weight') or c.get('weight_percent')))
            if max_scores:
                payload['MaximumScore'] = ', '.join(max_scores[:6])
            if weights and not payload['CriteriaWeight']:
                payload['CriteriaWeight'] = ', '.join(f'{w}%' for w in weights[:6])

    return payload


def resolve_scoresheet_item(event, item_key: str):
    kind, ref = parse_item_key(item_key)
    if kind == 'match':
        match = BracketMatch.objects.select_related('team_a', 'team_b', 'winner').filter(
            pk=ref, event=event
        ).first()
        if not match:
            raise FacultyServiceError('Match not found.')
        label = f'Game {match.match_number}'
        if match.round_name:
            label = f'{label} · {match.round_name}'
        return kind, match, label
    if kind == 'stage':
        rounds = event.rounds_config or []
        if ref < 0 or ref >= len(rounds) or not isinstance(rounds[ref], dict):
            raise FacultyServiceError('Stage not found.')
        cfg = rounds[ref]
        label = cfg.get('name') or f'Stage {ref + 1}'
        return kind, cfg, label
    if kind == 'legacy_stage':
        label = 'Preliminary Round' if str(ref).startswith('prelim') else 'Final Round'
        return kind, ref, label
    return 'event', None, 'Event Scoresheet'


def build_scoresheet_pdf(event, item_key: str, *, blank_unresolved: bool = True) -> tuple[bytes, object, str, dict]:
    from .scoresheet_pdf import pack_template_layout

    kind, item_ref, item_label = resolve_scoresheet_item(event, item_key)
    template = resolve_template_for_event(event)
    etype = template.event_type if template else template_event_type(event)
    layout = template.layout if template else pack_template_layout({}, etype)
    orientation = template.orientation if template else 'portrait'
    paper_size = template.paper_size if template else 'a4'
    payload = scoresheet_payload_for(event, kind, item_ref)
    pdf = render_scoresheet_pdf(
        layout,
        orientation=orientation,
        paper_size=paper_size,
        payload=payload,
        event_type=etype,
        blank_unresolved=blank_unresolved,
    )
    return pdf, template, item_label, payload


def record_generated_scoresheet(event, template, item_label, payload, user, pdf_bytes):
    from django.core.files.base import ContentFile
    from .models import GeneratedScoresheet

    row = GeneratedScoresheet(
        template=template,
        event=event,
        event_label=event.name,
        item_label=item_label,
        generated_by=user,
        payload=payload,
    )
    filename = f"scoresheet_{event.id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    row.file.save(filename, ContentFile(pdf_bytes), save=False)
    row.save()
    return row


def scoresheet_rows_for_user(user, *, q='', event_type='', status='', event_id=None):
    events = faculty_event_qs(user).select_related('faculty_account', 'scoresheet_template').order_by('name')
    if event_id:
        events = events.filter(pk=event_id)
    if q:
        events = events.filter(Q(name__icontains=q) | Q(venue__icontains=q) | Q(category__icontains=q))

    rows = []
    for event in events:
        etype = template_event_type(event)
        if event_type == 'match' and etype != 'match':
            continue
        if event_type == 'criteria' and etype != 'criteria':
            continue
        type_label = 'Match-Based' if etype == 'match' else 'Criteria-Based'
        template = resolve_template_for_event(event)
        sched = schedule_rows(event)
        if not sched:
            sched = [{
                'id': 'event',
                'game': 'Scoresheet',
                'stage': 'Event',
                'date': event.event_date.isoformat() if event.event_date else '',
                'time': event.event_time.strftime('%I:%M %p') if event.event_time else '',
                'venue': event.venue or '—',
                'status': event.get_status_display() if hasattr(event, 'get_status_display') else '—',
                'status_key': event.status or '',
            }]
        for s in sched:
            sid = s.get('id')
            if isinstance(sid, int):
                item_key = f'match-{sid}'
                match_or_stage = s.get('game') or f'Game {sid}'
                if s.get('stage') and s.get('stage') != '—':
                    match_or_stage = f"{match_or_stage} · {s['stage']}"
            else:
                sid_str = str(sid)
                if sid_str.startswith('round-'):
                    item_key = f"stage-{sid_str.split('-', 1)[1]}"
                elif sid_str in {'event'}:
                    item_key = 'event'
                else:
                    item_key = sid_str
                match_or_stage = s.get('stage') or s.get('game') or 'Stage'
            schedule_bits = ' '.join(x for x in [s.get('date') or '', s.get('time') or ''] if x).strip() or '—'
            status_label = s.get('status') or '—'
            status_key = (s.get('status_key') or '').lower()
            if status and status not in status_key and status.lower() not in status_label.lower():
                continue
            rows.append({
                'event_id': event.id,
                'event_name': event.name,
                'event_type': type_label,
                'event_type_key': etype,
                'match_or_stage': match_or_stage,
                'schedule': schedule_bits,
                'venue': s.get('venue') or event.venue or '—',
                'status': status_label,
                'status_key': status_key,
                'item_key': item_key,
                'template_name': template.name if template else 'Default layout',
                'template_id': template.id if template else None,
            })
    return rows


def generate_match_scoresheet_pdf(event, match):
    """Backward-compatible shim used by legacy faculty match PDF route."""
    pdf, _template, _label, _payload = build_scoresheet_pdf(event, f'match-{match.id}')
    return pdf


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
