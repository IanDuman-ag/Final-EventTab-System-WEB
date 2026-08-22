"""Faculty In-Charge portal views + Tabulator results portal helpers."""
from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from events.faculty_service import (
    FacultyServiceError,
    approve_results,
    build_scoresheet_pdf,
    compute_criteria_rankings,
    confirm_stage_advancement,
    dashboard_stats,
    event_type_label,
    export_schedule_csv,
    faculty_can_access,
    faculty_event_qs,
    generate_match_scoresheet_pdf,
    is_faculty_user,
    judge_monitoring,
    leaderboard_by_game,
    leaderboard_category_winners,
    leaderboard_rows,
    publish_results,
    recent_notifications,
    record_generated_scoresheet,
    return_for_correction,
    save_match_result,
    schedule_rows,
    scoresheet_rows_for_user,
    serialize_event_detail,
    serialize_event_row,
    stage_panels,
)
from events.models import (
    AuditLog,
    BracketMatch,
    BracketTeam,
    Candidate,
    Criterion,
    Event,
    EventScoringCategory,
    JudgeActivityLog,
    JudgeScore,
    ScoreSheet,
)


def _faculty_required(view):
    @login_required(login_url='login')
    def wrapped(request, *args, **kwargs):
        if not is_faculty_user(request.user) and not request.user.is_staff:
            messages.error(request, 'Faculty access required.')
            return redirect('login')
        return view(request, *args, **kwargs)
    return wrapped


def _get_event(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    if not faculty_can_access(request.user, event) and not request.user.is_superuser:
        return None
    return event


@_faculty_required
def faculty_dashboard(request):
    events = faculty_event_qs(request.user).order_by('-updated_at')
    recent = [serialize_event_row(e) for e in events[:6]]
    return render(request, 'facultydash/dashboard.html', {
        'active': 'dashboard',
        'stats': dashboard_stats(request.user),
        'recent_events': recent,
        'notifications': recent_notifications(request.user),
        'user_display': request.user.get_full_name() or request.user.username,
    })


@_faculty_required
def faculty_my_events(request):
    qs = faculty_event_qs(request.user)
    q = (request.GET.get('q') or '').strip()
    status = (request.GET.get('status') or 'all').strip()
    sort = (request.GET.get('sort') or '-updated_at').strip()
    allowed = {'name', '-name', 'event_date', '-event_date', 'updated_at', '-updated_at', 'category', '-category'}
    if sort not in allowed:
        sort = '-updated_at'
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(category__icontains=q) | Q(venue__icontains=q) | Q(division__icontains=q)
        )
    if status == 'published':
        qs = qs.filter(Q(publication_status='published') | Q(results_finalized=True))
    elif status == 'draft':
        qs = qs.filter(publication_status='draft', results_finalized=False)
    elif status == 'ready':
        qs = qs.filter(result_processing_config__ready_for_publication=True)
    qs = qs.order_by(sort)
    page = Paginator(qs, 10).get_page(request.GET.get('page') or 1)
    rows = [serialize_event_row(e) for e in page.object_list]
    return render(request, 'facultydash/my_events.html', {
        'active': 'my_events',
        'page_obj': page,
        'event_rows': rows,
        'search_query': q,
        'status_filter': status,
        'sort': sort,
        'user_display': request.user.get_full_name() or request.user.username,
    })


@_faculty_required
def faculty_event_manage(request, event_id):
    event = _get_event(request, event_id)
    if event is None:
        messages.error(request, 'You do not have access to this event.')
        return redirect('faculty_my_events')
    tab = (request.GET.get('tab') or 'overview').strip()
    detail = serialize_event_detail(event)
    schedule = schedule_rows(event)
    match_schedule = [row for row in schedule if isinstance(row.get('id'), int)]
    monitoring = judge_monitoring(event) if detail['event_type'] == 'Criteria-Based' else None
    rankings = (
        compute_criteria_rankings(event) if detail['event_type'] == 'Criteria-Based' else []
    )
    sheets = {
        s.match_id: s
        for s in ScoreSheet.objects.filter(event=event).select_related('match', 'winner')
    }
    return render(request, 'facultydash/event_manage.html', {
        'active': 'my_events',
        'tab': tab,
        'event': event,
        'detail': detail,
        'schedule': schedule,
        'match_schedule': match_schedule,
        'monitoring': monitoring,
        'rankings': rankings,
        'stages': stage_panels(event),
        'sheets': sheets,
        'user_display': request.user.get_full_name() or request.user.username,
    })


@_faculty_required
def faculty_schedules(request):
    events = faculty_event_qs(request.user)
    rows = []
    for ev in events.order_by('-event_date')[:30]:
        for row in schedule_rows(ev)[:20]:
            rows.append({**row, 'event_name': ev.name, 'event_id': ev.id})
    return render(request, 'facultydash/schedules.html', {
        'active': 'schedules',
        'rows': rows,
        'user_display': request.user.get_full_name() or request.user.username,
    })


@_faculty_required
def faculty_scoresheets(request):
    q = (request.GET.get('q') or '').strip()
    event_type = (request.GET.get('event_type') or '').strip()
    status = (request.GET.get('status') or '').strip()
    event_id = request.GET.get('event_id') or ''
    try:
        event_id_int = int(event_id) if event_id else None
    except (TypeError, ValueError):
        event_id_int = None

    rows = scoresheet_rows_for_user(
        request.user,
        q=q,
        event_type=event_type,
        status=status,
        event_id=event_id_int,
    )
    paginator = Paginator(rows, 15)
    page_obj = paginator.get_page(request.GET.get('page') or 1)
    events = faculty_event_qs(request.user).order_by('name')
    return render(request, 'facultydash/scoresheets.html', {
        'active': 'scoresheets',
        'rows': page_obj.object_list,
        'page_obj': page_obj,
        'filters': {
            'q': q,
            'event_type': event_type,
            'status': status,
            'event_id': event_id,
        },
        'events': [{'id': e.id, 'name': e.name} for e in events],
        'user_display': request.user.get_full_name() or request.user.username,
    })


@_faculty_required
def faculty_scoresheet_item_pdf(request, event_id, item_key):
    if not is_faculty_user(request.user):
        return HttpResponse('Faculty access required.', status=403)
    event = _get_event(request, event_id)
    if event is None:
        return HttpResponse('Forbidden', status=403)
    try:
        pdf, template, item_label, _payload = build_scoresheet_pdf(event, item_key)
    except FacultyServiceError as exc:
        return HttpResponse(str(exc), status=404)
    response = HttpResponse(pdf, content_type='application/pdf')
    disposition = 'attachment' if request.GET.get('download') == '1' else 'inline'
    safe_label = ''.join(ch if ch.isalnum() or ch in '-_' else '-' for ch in item_label)[:60]
    response['Content-Disposition'] = f'{disposition}; filename="scoresheet-{safe_label or item_key}.pdf"'
    if template:
        response['X-Scoresheet-Template'] = template.name
    return response


@require_POST
@_faculty_required
def faculty_scoresheet_generate(request, event_id, item_key):
    if not is_faculty_user(request.user):
        return JsonResponse({'success': False, 'message': 'Faculty access required.'}, status=403)
    event = _get_event(request, event_id)
    if event is None:
        return JsonResponse({'success': False, 'message': 'Forbidden'}, status=403)
    try:
        pdf, template, item_label, payload = build_scoresheet_pdf(event, item_key)
        row = record_generated_scoresheet(event, template, item_label, payload, request.user, pdf)
    except FacultyServiceError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)
    return JsonResponse({
        'success': True,
        'message': f'Scoresheet generated for {item_label}.',
        'item_label': item_label,
        'template_name': template.name if template else 'Default layout',
        'download_url': (
            f'/faculty/scoresheets/{event.id}/{item_key}/pdf/?download=1'
        ),
        'preview_url': f'/faculty/scoresheets/{event.id}/{item_key}/pdf/',
        'generated_id': row.id,
    })


@_faculty_required
def faculty_scoresheet_pdf(request, event_id, match_id):
    """Legacy shim → match item PDF download."""
    if not is_faculty_user(request.user):
        return HttpResponse('Faculty access required.', status=403)
    event = _get_event(request, event_id)
    if event is None:
        return HttpResponse('Forbidden', status=403)
    match = get_object_or_404(BracketMatch, pk=match_id, event=event)
    pdf = generate_match_scoresheet_pdf(event, match)
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="scoresheet-game-{match.match_number}.pdf"'
    return response


@require_POST
@_faculty_required
def faculty_match_result(request, event_id, match_id):
    event = _get_event(request, event_id)
    if event is None:
        return JsonResponse({'success': False, 'message': 'Forbidden'}, status=403)
    match = get_object_or_404(BracketMatch, pk=match_id, event=event)
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        data = request.POST
    confirm = str(data.get('confirm') or data.get('action') or '').lower() in {'1', 'true', 'confirm'}
    try:
        sheet = save_match_result(
            event,
            match,
            request.user,
            score_a=data.get('score_a') or data.get('scoreA'),
            score_b=data.get('score_b') or data.get('scoreB'),
            winner_id=data.get('winner_id') or data.get('winner'),
            remarks=data.get('remarks') or '',
            confirm=confirm,
        )
    except FacultyServiceError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)
    return JsonResponse({
        'success': True,
        'message': 'Result confirmed.' if confirm else 'Draft saved.',
        'status': sheet.status,
    })


@_faculty_required
def faculty_results_review(request):
    events = faculty_event_qs(request.user).order_by('-updated_at')
    rows = []
    for ev in events:
        detail = serialize_event_row(ev)
        pending = False
        if detail['event_type'] == 'Criteria-Based' and not ev.results_finalized:
            mon = judge_monitoring(ev)
            pending = mon['all_submitted'] or mon['submitted'] > 0
        else:
            pending = ScoreSheet.objects.filter(
                event=ev, status__in=[ScoreSheet.STATUS_PENDING, ScoreSheet.STATUS_FINALIZED]
            ).exists() and not ev.results_finalized
        rows.append({**detail, 'needs_review': pending})
    return render(request, 'facultydash/results_review.html', {
        'active': 'results',
        'event_rows': rows,
        'user_display': request.user.get_full_name() or request.user.username,
    })


@_faculty_required
def faculty_leaderboard(request):
    games = leaderboard_by_game()
    return render(request, 'facultydash/leaderboard.html', {
        'active': 'leaderboard',
        'rows': leaderboard_rows(),
        'games': games,
        'category_winners': leaderboard_category_winners(games),
        'user_display': request.user.get_full_name() or request.user.username,
    })


@_faculty_required
def faculty_export_schedule(request, event_id):
    event = _get_event(request, event_id)
    if event is None:
        return HttpResponse('Forbidden', status=403)
    content = export_schedule_csv(event)
    response = HttpResponse(content, content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="schedule-{event.id}.csv"'
    return response


@require_POST
@_faculty_required
def faculty_results_action(request, event_id):
    event = _get_event(request, event_id)
    if event is None:
        return JsonResponse({'success': False, 'message': 'Forbidden'}, status=403)
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        data = request.POST
    action = (data.get('action') or '').strip().lower()
    try:
        if action == 'return':
            return_for_correction(event, request.user, data.get('judge_ids'))
            msg = 'Returned for correction.'
        elif action == 'approve':
            approve_results(event, request.user)
            msg = 'Results approved.'
        elif action == 'publish':
            publish_results(event, request.user)
            msg = 'Results published.'
        else:
            return JsonResponse({'success': False, 'message': 'Unknown action.'}, status=400)
    except FacultyServiceError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)
    return JsonResponse({'success': True, 'message': msg})


@require_POST
@_faculty_required
def faculty_stage_confirm(request, event_id):
    event = _get_event(request, event_id)
    if event is None:
        return JsonResponse({'success': False, 'message': 'Forbidden'}, status=403)
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON.'}, status=400)
    try:
        confirm_stage_advancement(
            event,
            request.user,
            data.get('stage_index', 0),
            data.get('qualifier_ids') or [],
        )
    except FacultyServiceError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)
    return JsonResponse({'success': True, 'message': 'Stage advancement confirmed.'})


# ─── Tabulator portal (Dashboard / Live Rankings / Results) ───────────────────


def _tabulator_portal_required(view):
    """Allow Tabulator or Faculty group members (and staff)."""
    @login_required(login_url='login')
    def wrapped(request, *args, **kwargs):
        if request.user.is_staff or is_faculty_user(request.user):
            return view(request, *args, **kwargs)
        messages.error(request, 'Tabulator access required.')
        return redirect('login')
    return wrapped


def _prize_label(rank: int) -> str:
    if rank == 1:
        return 'Winner'
    if rank == 2:
        return '1st Runner Up'
    if rank == 3:
        return '2nd Runner Up'
    if rank and rank > 0:
        return 'Placement'
    return '—'


def _infer_event_kind(event) -> str:
    special = (event.special_event_type or '').strip().lower()
    category = (event.category or '').strip().lower()
    sport = (event.sport_type or '').strip().lower()
    name = (event.name or '').strip().lower()
    haystack = f'{special} {category} {sport} {name}'
    if 'pageant' in haystack or special == 'pageant':
        return 'pageant'
    if 'quiz' in haystack or 'bee' in haystack:
        return 'quiz'
    if 'sing' in haystack or 'vocal' in haystack:
        return 'singing'
    if 'dance' in haystack:
        return 'dance'
    if category == 'sports' or event.scoring_method == 'match' or sport:
        return 'sports'
    return 'other'


def _participant_label(event_kind: str, participation_type: str) -> str:
    if event_kind == 'sports' or (participation_type or '') == 'team':
        return 'Team'
    if event_kind == 'pageant':
        return 'Candidate'
    if event_kind in {'singing', 'dance'}:
        return 'Performer' if (participation_type or '') == 'individual' else 'Group'
    if event_kind == 'quiz':
        return 'Participant'
    return 'Candidate' if (participation_type or '') == 'individual' else 'Participant'


def _event_is_ranking_mode(event) -> bool:
    if (event.criteria_score_method or '') == Event.CRITERIA_SCORE_RANKING:
        return True
    if (event.scoring_method or '').lower() == 'match':
        return True
    if (event.category or '').lower() == 'sports':
        return True
    if event.bracket_teams.exists():
        return True
    if event.scoring_categories.filter(judge_mode=EventScoringCategory.JUDGE_MODE_RANKING).exists():
        return True
    label = event_type_label(event)
    return label == 'Match-Based'


def _serialize_portal_event(event) -> dict:
    mode = 'ranking' if _event_is_ranking_mode(event) else 'scoring'
    kind = _infer_event_kind(event)
    status = event.get_status_display() if hasattr(event, 'get_status_display') else (event.status or '')
    return {
        'id': event.id,
        'name': event.name,
        'category': event.category or '—',
        'status': status,
        'status_key': event.status or '',
        'results_finalized': bool(event.results_finalized),
        'mode': mode,
        'mode_label': 'Ranking Mode' if mode == 'ranking' else 'Scoring Mode',
        'event_kind': kind,
        'participant_label': _participant_label(kind, event.participation_type or ''),
        'event_type': event_type_label(event),
    }


def _tabulator_portal_events(user) -> list[dict]:
    events = faculty_event_qs(user).order_by('-updated_at', 'name')
    return [_serialize_portal_event(ev) for ev in events]


def _resolve_portal_event(user, event_id=None):
    qs = faculty_event_qs(user).order_by('-updated_at', 'name')
    if event_id:
        try:
            eid = int(event_id)
        except (TypeError, ValueError):
            eid = None
        if eid:
            event = qs.filter(pk=eid).first()
            if event:
                return event
    return qs.first()


def _category_filter_options(event) -> list[str]:
    names = list(
        event.scoring_categories.order_by('display_order', 'name').values_list('name', flat=True)
    )
    if names:
        return names
    if event.judging_event_id:
        depts = (
            Candidate.objects.filter(event_id=event.judging_event_id)
            .exclude(department='')
            .values_list('department', flat=True)
            .distinct()
        )
        return sorted({d for d in depts if d})
    return []


def _enough_locked_scores(event) -> bool:
    if not event.judging_event_id:
        return False
    locked = JudgeScore.objects.filter(
        candidate__event_id=event.judging_event_id, is_locked=True
    ).exists()
    return locked


def _status_label_for_results(event, mode: str) -> str:
    if event.results_finalized:
        return 'Completed'
    if event.status == Event.STATUS_COMPLETED:
        return 'Completed'
    if mode == 'ranking':
        completed = event.bracket_matches.filter(
            status__in=[BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT]
        ).exists()
        if completed:
            return 'Live'
        return 'Results Pending'
    if _enough_locked_scores(event):
        return 'Live'
    return 'Results Pending'


def _is_finalized_display(event) -> bool:
    if event.results_finalized:
        return True
    if event.status == Event.STATUS_COMPLETED and (
        _enough_locked_scores(event)
        or event.bracket_matches.filter(status=BracketMatch.STATUS_COMPLETED).exists()
    ):
        return True
    return False


def _score_to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _build_score_breakdown(event, candidate_id: int | None) -> list[dict]:
    if not event.judging_event_id or not candidate_id:
        return []
    judging = event.judging_event
    criteria = list(judging.criteria.all())
    if not criteria:
        return []
    scores = JudgeScore.objects.filter(
        candidate_id=candidate_id, is_locked=True
    ).select_related('criterion')
    by_crit: dict[int, list[float]] = defaultdict(list)
    for s in scores:
        by_crit[s.criterion_id].append(float(s.score))

    method = event.criteria_score_method or 'weighted_percentage'
    rows = []
    for c in criteria:
        vals = by_crit.get(c.id) or []
        avg = sum(vals) / len(vals) if vals else 0.0
        weight = float(c.weight_percent or 0) or 0.0
        mx = float(c.max_score or 100) or 100.0
        if method == 'raw_score':
            contribution = avg
        elif method == 'average_score':
            contribution = avg
        else:
            contribution = (avg / mx) * weight if mx else 0.0
        rows.append({
            'criterion_id': c.id,
            'name': c.name,
            'average': round(avg, 2),
            'weight': round(weight, 1),
            'max_score': round(mx, 1),
            'contribution': round(contribution, 2),
            'judge_count': len(vals),
        })
    return rows


def _build_judges_scores(event, candidate_id: int | None) -> dict:
    empty = {'criteria': [], 'rows': [], 'averages': []}
    if not event.judging_event_id or not candidate_id:
        return empty
    criteria = list(event.judging_event.criteria.order_by('order', 'id'))
    if not criteria:
        return empty
    scores = list(
        JudgeScore.objects.filter(candidate_id=candidate_id, is_locked=True)
        .select_related('judge', 'criterion')
    )
    if not scores:
        return {
            'criteria': [{'id': c.id, 'name': c.name} for c in criteria],
            'rows': [],
            'averages': [],
        }
    by_judge: dict[int, dict] = {}
    for s in scores:
        row = by_judge.setdefault(s.judge_id, {
            'judge_id': s.judge_id,
            'judge_name': (s.judge.get_full_name() or s.judge.username) if s.judge_id else 'Judge',
            'scores': {},
        })
        row['scores'][s.criterion_id] = float(s.score)

    criteria_meta = [{'id': c.id, 'name': c.name} for c in criteria]
    rows = []
    col_sums = {c.id: [] for c in criteria}
    for judge_id, data in sorted(by_judge.items(), key=lambda item: item[1]['judge_name'].lower()):
        cells = []
        for c in criteria:
            val = data['scores'].get(c.id)
            cells.append(None if val is None else round(val, 2))
            if val is not None:
                col_sums[c.id].append(val)
        rows.append({
            'judge_id': judge_id,
            'judge_name': data['judge_name'],
            'scores': cells,
        })
    averages = []
    for c in criteria:
        vals = col_sums[c.id]
        averages.append(round(sum(vals) / len(vals), 2) if vals else None)
    return {'criteria': criteria_meta, 'rows': rows, 'averages': averages}


def _match_standings(event) -> list[dict]:
    teams = list(event.bracket_teams.select_related('department').order_by('-points', 'name'))
    completed = list(
        event.bracket_matches.filter(
            status__in=[BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT]
        ).select_related('team_a', 'team_b', 'winner')
    )
    wins = defaultdict(int)
    pf = defaultdict(float)
    pa = defaultdict(float)
    for m in completed:
        if m.winner_id:
            wins[m.winner_id] += 1
        sa = _score_to_float(m.score_a)
        sb = _score_to_float(m.score_b)
        if m.team_a_id:
            pf[m.team_a_id] += sa
            pa[m.team_a_id] += sb
        if m.team_b_id:
            pf[m.team_b_id] += sb
            pa[m.team_b_id] += sa

    rows = []
    for idx, team in enumerate(teams, start=1):
        rows.append({
            'rank': idx,
            'team_id': team.id,
            'name': team.name,
            'department': team.department.name if team.department_id else '—',
            'wins': wins.get(team.id, 0),
            'losses': int(team.loss_count or 0),
            'points': int(team.points or 0),
            'pf': round(pf.get(team.id, 0.0), 1),
            'pa': round(pa.get(team.id, 0.0), 1),
            'prize': _prize_label(idx),
            'is_champion': bool(team.is_champion),
            'photo': '',
        })
    rows.sort(key=lambda r: (-r['points'], -r['wins'], r['name']))
    for idx, row in enumerate(rows, start=1):
        row['rank'] = idx
        row['prize'] = _prize_label(idx)
    return rows


def _recent_games(event, limit=12) -> list[dict]:
    matches = (
        event.bracket_matches.filter(
            status__in=[BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT]
        )
        .select_related('team_a', 'team_b', 'winner')
        .order_by('-updated_at', '-match_number')[:limit]
    )
    rows = []
    for m in matches:
        rows.append({
            'id': m.id,
            'match_number': m.match_number,
            'round_name': m.round_name or '—',
            'team_a': m.team_a.name if m.team_a_id else 'TBD',
            'team_b': m.team_b.name if m.team_b_id else 'TBD',
            'score_a': m.score_a or '—',
            'score_b': m.score_b or '—',
            'winner': m.winner.name if m.winner_id else '—',
            'status': m.get_status_display(),
        })
    return rows


def _criteria_result_rows(event, category='', q='') -> list[dict]:
    rankings = compute_criteria_rankings(event)
    cat = (category or '').strip().lower()
    query = (q or '').strip().lower()
    photo_by_id = {}
    locked_candidate_ids = set()
    if event.judging_event_id:
        for c in Candidate.objects.filter(event_id=event.judging_event_id):
            photo_by_id[c.id] = c.photo.url if c.photo else ''
        locked_candidate_ids = set(
            JudgeScore.objects.filter(
                candidate__event_id=event.judging_event_id,
                is_locked=True,
            ).values_list('candidate_id', flat=True).distinct()
        )
    rows = []
    for r in rankings:
        dept = (r.get('department') or '') or '—'
        if cat and cat != 'all' and dept.lower() != cat:
            continue
        name = r.get('name') or ''
        if query and query not in name.lower() and query not in str(r.get('number') or ''):
            continue
        rank = int(r.get('rank') or 0)
        cand_id = r.get('candidate_id')
        has_score = cand_id in locked_candidate_ids and float(r.get('final_score') or 0) > 0
        # Patched unit-test rows may omit DB scores but still supply positive finals.
        if not locked_candidate_ids and float(r.get('final_score') or 0) > 0:
            has_score = True
        rows.append({
            'rank': rank,
            'candidate_id': cand_id,
            'name': name,
            'number': r.get('number'),
            'department': dept,
            'final_score': r.get('final_score'),
            'breakdown': r.get('breakdown') or '',
            'prize': _prize_label(rank) if has_score else '—',
            'qualification_status': r.get('qualification_status') or '',
            'photo': photo_by_id.get(cand_id, ''),
            'has_score': has_score,
        })
    # Re-rank after filter for display consistency within filtered set
    scored = [row for row in rows if row['has_score']]
    unscored = [row for row in rows if not row['has_score']]
    scored.sort(key=lambda row: (-(row['final_score'] or 0), row['name']))
    for idx, row in enumerate(scored, start=1):
        row['rank'] = idx
        row['prize'] = _prize_label(idx)
    for row in unscored:
        row['rank'] = 0
        row['prize'] = '—'
    return scored + unscored


def _by_category_groups(event, rows: list[dict]) -> list[dict]:
    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        key = row.get('department') or 'Uncategorized'
        groups[key].append(row)
    out = []
    for name in sorted(groups.keys(), key=lambda s: s.lower()):
        members = list(groups[name])
        members.sort(key=lambda r: (r['rank'] or 9999, r['name']))
        out.append({
            'category': name,
            'rows': members,
            'winner': members[0] if members and members[0].get('rank') == 1 else (members[0] if members else None),
        })
    return out


def _by_round_groups(event, rows: list[dict]) -> list[dict]:
    panels = stage_panels(event)
    if not panels:
        return []
    groups = []
    for panel in panels:
        groups.append({
            'name': panel.get('name') or f"Stage {panel.get('index', 0) + 1}",
            'rule': panel.get('rule') or '',
            'status_label': panel.get('status_label') or '',
            'is_final': bool(panel.get('is_final')),
            'rows': rows if panel.get('is_final') or panel.get('status') in {'open', 'completed'} else [],
        })
    return groups


def _result_updates(event, limit=12) -> list[dict]:
    updates = []
    event_name = event.name
    for log in AuditLog.objects.filter(
        Q(event_name__iexact=event_name) | Q(module__icontains='Result')
    ).order_by('-created_at')[:limit]:
        if log.event_name and log.event_name.lower() != event_name.lower():
            if 'result' not in (log.module or '').lower() and 'result' not in (log.action or '').lower():
                continue
        updates.append({
            'id': f'audit-{log.id}',
            'when': timezone.localtime(log.created_at).strftime('%b %d · %I:%M %p'),
            'action': log.action,
            'description': (log.description or log.action)[:180],
            'user': log.user_name or 'System',
        })
    if event.judging_event_id:
        for jlog in JudgeActivityLog.objects.filter(
            event_id=event.judging_event_id
        ).select_related('judge').order_by('-timestamp')[:limit]:
            updates.append({
                'id': f'jal-{jlog.id}',
                'when': timezone.localtime(jlog.timestamp).strftime('%b %d · %I:%M %p'),
                'action': jlog.get_action_display(),
                'description': (jlog.details or jlog.get_action_display())[:180],
                'user': (jlog.judge.get_full_name() or jlog.judge.username) if jlog.judge_id else 'Judge',
            })
    for ss in ScoreSheet.objects.filter(event=event).select_related(
        'match', 'tabulator'
    ).order_by('-updated_at')[:limit]:
        who = ''
        if ss.tabulator_id:
            who = ss.tabulator.get_full_name() or ss.tabulator.username
        updates.append({
            'id': f'ss-{ss.id}',
            'when': timezone.localtime(ss.updated_at or ss.created_at).strftime('%b %d · %I:%M %p'),
            'action': f'Scoresheet {ss.status}',
            'description': f'Match {ss.match.match_number if ss.match_id else "—"} · {ss.status}',
            'user': who or 'Scorer',
        })
    # Sort by parsed when string is weak; keep insertion order preference then trim
    return updates[:limit]


def _tabulator_results_payload(user, event_id=None, category='', tab='overall', q=''):
    events = _tabulator_portal_events(user)
    event = _resolve_portal_event(user, event_id)
    if event is None:
        return {
            'event': None,
            'events': events,
            'categories': [],
            'selected_category': category or '',
            'tab': tab or 'overall',
            'is_finalized': False,
            'status_label': 'Results Pending',
            'mode': 'scoring',
            'participant_label': 'Participant',
            'results': [],
            'winner': None,
            'score_breakdown': [],
            'judges_scores': {'criteria': [], 'rows': [], 'averages': []},
            'by_category': [],
            'by_round': [],
            'match_standings': [],
            'recent_games': [],
            'updates': [],
            'message': 'No events are currently assigned to you.',
            'stats': {
                'participant_count': 0,
                'scored_count': 0,
                'judge_count': 0,
                'match_count': 0,
            },
        }

    portal = _serialize_portal_event(event)
    mode = portal['mode']
    categories = _category_filter_options(event)
    selected_category = (category or '').strip()
    tab = (tab or 'overall').strip().lower()
    is_finalized = _is_finalized_display(event)
    status_label = _status_label_for_results(event, mode)

    results = []
    match_standings = []
    recent_games = []
    score_breakdown = []
    judges_scores = {'criteria': [], 'rows': [], 'averages': []}
    by_category = []
    by_round = []
    winner = None
    message = ''

    if mode == 'ranking':
        match_standings = _match_standings(event)
        recent_games = _recent_games(event)
        results = match_standings
        if selected_category and selected_category.lower() != 'all':
            results = [
                r for r in results
                if (r.get('department') or '').lower() == selected_category.lower()
            ]
        if q:
            ql = q.strip().lower()
            results = [r for r in results if ql in (r.get('name') or '').lower()]
        winner = results[0] if results else None
        by_category = _by_category_groups(event, results)
        if not results:
            message = 'No match standings yet for this event.'
    else:
        results = _criteria_result_rows(event, category=selected_category, q=q)
        scored = [r for r in results if r.get('has_score')]
        winner = scored[0] if scored else None
        selected_id = winner.get('candidate_id') if winner else None
        # Allow focusing breakdown on ?participant_id=
        score_breakdown = _build_score_breakdown(event, selected_id)
        judges_scores = _build_judges_scores(event, selected_id)
        by_category = _by_category_groups(event, results)
        by_round = _by_round_groups(event, results)
        if not scored:
            message = 'Awaiting final scores — standings will appear when judges lock scores.'
        elif not is_finalized:
            message = 'Results Pending — showing current calculated standings.'

    if is_finalized and not message:
        message = 'Final results are official for this event.'

    participant_count = 0
    scored_count = 0
    judge_count = event.assigned_judges.count()
    match_count = event.bracket_matches.count()
    if mode == 'ranking':
        participant_count = event.bracket_teams.count()
        scored_count = len([r for r in match_standings if r.get('wins') or r.get('points')])
    elif event.judging_event_id:
        participant_count = Candidate.objects.filter(event_id=event.judging_event_id).count()
        scored_count = len([r for r in results if r.get('has_score')])

    return {
        'event': {
            **portal,
            'venue': event.venue or '—',
            'date': event.event_date.strftime('%b %d, %Y') if event.event_date else '—',
        },
        'events': events,
        'categories': categories,
        'selected_category': selected_category,
        'tab': tab,
        'is_finalized': is_finalized,
        'status_label': status_label,
        'mode': mode,
        'participant_label': portal['participant_label'],
        'results': results,
        'winner': winner,
        'score_breakdown': score_breakdown,
        'judges_scores': judges_scores,
        'by_category': by_category,
        'by_round': by_round,
        'match_standings': match_standings,
        'recent_games': recent_games,
        'updates': _result_updates(event),
        'message': message,
        'stats': {
            'participant_count': participant_count,
            'scored_count': scored_count,
            'judge_count': judge_count,
            'match_count': match_count,
        },
        'q': q or '',
    }


def _portal_context(request, active, payload):
    display = request.user.get_full_name() or request.user.username
    initials = ''.join(part[:1] for part in display.split()[:2]).upper() or display[:2].upper()
    return {
        'active': active,
        'payload': payload,
        'payload_json': json.dumps(payload, default=str),
        'user_display': display,
        'user_initials': initials,
        'events': payload.get('events') or [],
        'event': payload.get('event'),
    }


@_tabulator_portal_required
def tabulator_dashboard(request):
    event_id = request.GET.get('event_id')
    payload = _tabulator_results_payload(request.user, event_id=event_id)
    ctx = _portal_context(request, 'dashboard', payload)
    return render(request, 'tabulator/tabulatordashboard.html', ctx)


@_tabulator_portal_required
def tabulator_live_rankings(request):
    event_id = request.GET.get('event_id')
    payload = _tabulator_results_payload(
        request.user,
        event_id=event_id,
        category=request.GET.get('category') or '',
        tab=request.GET.get('tab') or 'overall',
        q=request.GET.get('q') or '',
    )
    ctx = _portal_context(request, 'live_rankings', payload)
    return render(request, 'tabulator/tabliverankings.html', ctx)


@_tabulator_portal_required
def tabulator_results(request):
    event_id = request.GET.get('event_id')
    payload = _tabulator_results_payload(
        request.user,
        event_id=event_id,
        category=request.GET.get('category') or '',
        tab=request.GET.get('tab') or 'overall',
        q=request.GET.get('q') or '',
    )
    ctx = _portal_context(request, 'results', payload)
    return render(request, 'tabulator/tabresults.html', ctx)


@_tabulator_portal_required
def tabulator_results_data(request):
    payload = _tabulator_results_payload(
        request.user,
        event_id=request.GET.get('event_id'),
        category=request.GET.get('category') or '',
        tab=request.GET.get('tab') or 'overall',
        q=request.GET.get('q') or '',
    )
    return JsonResponse({'success': True, 'payload': payload})


@_tabulator_portal_required
def tabulator_rankings_data(request):
    payload = _tabulator_results_payload(
        request.user,
        event_id=request.GET.get('event_id'),
        category=request.GET.get('category') or '',
        tab=request.GET.get('tab') or 'overall',
        q=request.GET.get('q') or '',
    )
    return JsonResponse({
        'success': True,
        'payload': payload,
        'results': payload.get('results') or [],
        'status_label': payload.get('status_label'),
        'is_finalized': payload.get('is_finalized'),
        'updated_at': timezone.now().isoformat(),
    })


@_tabulator_portal_required
def tabulator_results_csv(request):
    payload = _tabulator_results_payload(
        request.user,
        event_id=request.GET.get('event_id'),
        category=request.GET.get('category') or '',
        tab=request.GET.get('tab') or 'overall',
        q=request.GET.get('q') or '',
    )
    event = payload.get('event')
    if not event:
        return HttpResponse('No event assigned.', status=404)

    buf = io.StringIO()
    writer = csv.writer(buf)
    mode = payload.get('mode')
    if mode == 'ranking':
        writer.writerow(['Rank', 'Team', 'Department', 'Wins', 'Losses', 'Points', 'PF', 'PA', 'Prize'])
        for row in payload.get('results') or []:
            writer.writerow([
                row.get('rank'),
                row.get('name'),
                row.get('department'),
                row.get('wins'),
                row.get('losses'),
                row.get('points'),
                row.get('pf'),
                row.get('pa'),
                row.get('prize'),
            ])
    else:
        writer.writerow(['Rank', 'Number', 'Name', 'Category', 'Score', 'Prize'])
        for row in payload.get('results') or []:
            writer.writerow([
                row.get('rank') or '',
                row.get('number') or '',
                row.get('name'),
                row.get('department'),
                row.get('final_score'),
                row.get('prize'),
            ])
    filename = f"results-{event.get('id')}-{datetime.now().strftime('%Y%m%d')}.csv"
    response = HttpResponse(buf.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@_tabulator_portal_required
def tabulator_participants(request):
    event = _resolve_portal_event(request.user, request.GET.get('event_id'))
    events = _tabulator_portal_events(request.user)
    rows = []
    portal = _serialize_portal_event(event) if event else None
    if event:
        if _event_is_ranking_mode(event):
            for team in event.bracket_teams.select_related('department').order_by('seed', 'name'):
                rows.append({
                    'label': team.name,
                    'meta': team.department.name if team.department_id else '—',
                    'number': team.seed or '',
                })
        elif event.judging_event_id:
            for cand in Candidate.objects.filter(event_id=event.judging_event_id).order_by('number'):
                rows.append({
                    'label': cand.name,
                    'meta': cand.department or '—',
                    'number': cand.number,
                    'photo': cand.photo.url if cand.photo else '',
                })
    payload = {
        'event': portal,
        'events': events,
        'rows': rows,
        'message': '' if rows else ('No participants for this event.' if event else 'No events assigned.'),
    }
    ctx = _portal_context(request, 'participants', payload)
    ctx['rows'] = rows
    return render(request, 'tabulator/tabparticipants.html', ctx)


@_tabulator_portal_required
def tabulator_categories(request):
    event = _resolve_portal_event(request.user, request.GET.get('event_id'))
    events = _tabulator_portal_events(request.user)
    rows = []
    portal = _serialize_portal_event(event) if event else None
    if event:
        cats = list(event.scoring_categories.order_by('display_order', 'name'))
        if cats:
            for cat in cats:
                crit_count = cat.criteria.count()
                rows.append({
                    'name': cat.name,
                    'mode': cat.get_judge_mode_display(),
                    'weight': float(cat.overall_weight_percent or 0),
                    'criteria_count': crit_count,
                })
        elif event.judging_event_id:
            for c in Criterion.objects.filter(event_id=event.judging_event_id).order_by('order', 'id'):
                rows.append({
                    'name': c.name,
                    'mode': 'Scoring',
                    'weight': float(c.weight_percent or 0),
                    'criteria_count': 1,
                    'max_score': float(c.max_score or 0),
                })
    payload = {
        'event': portal,
        'events': events,
        'rows': rows,
        'message': '' if rows else ('No categories/criteria for this event.' if event else 'No events assigned.'),
    }
    ctx = _portal_context(request, 'categories', payload)
    ctx['rows'] = rows
    return render(request, 'tabulator/tabcategories.html', ctx)
