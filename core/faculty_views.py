"""Faculty In-Charge portal views."""
from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from events.faculty_service import (
    FacultyServiceError,
    approve_results,
    build_scoresheet_pdf,
    compute_criteria_rankings,
    confirm_stage_advancement,
    dashboard_stats,
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
from events.models import BracketMatch, Event, ScoreSheet


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
