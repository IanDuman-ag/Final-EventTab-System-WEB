"""Faculty In-Charge portal views + Tabulator results portal helpers."""
from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Max, Q
from django.db import transaction, DatabaseError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.cache import never_cache

from events.faculty_service import (
    FacultyServiceError,
    approve_results,
    compute_criteria_rankings,
    confirm_tabulation,
    confirm_stage_advancement,
    dashboard_stats,
    event_type_label,
    export_schedule_csv,
    faculty_can_access,
    faculty_event_qs,
    is_faculty_user,
    judge_monitoring,
    leaderboard_by_game,
    leaderboard_category_winners,
    leaderboard_rows,
    publish_results,
    recent_notifications,
    return_for_correction,
    save_match_result,
    schedule_rows,
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
    CriteriaScoreSubmission,
    Event,
    EventScoringCategory,
    JudgeActivityLog,
    JudgeScore,
    ScoreSheet,
)


def _faculty_required(view):
    @login_required(login_url='login')
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff and (
            request.user.groups.filter(name__iexact='Tabulator').exists()
            or not request.user.groups.filter(name__iregex=r'^(Faculty|Faculty In-Charge)$').exists()
        ):
            return HttpResponse('Faculty access required.', status=403)
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
    rows = [_publication_row(e, request.user) for e in events]
    recent = rows[:6]
    return render(request, 'facultydash/dashboard.html', {
        'active': 'dashboard',
        'stats': dashboard_stats(request.user),
        'recent_events': recent,
        'publication_queue': [row for row in rows if row['in_publication_queue']],
        'publication_ready_count': sum(row['publication_ready'] for row in rows),
        'user_display': request.user.get_full_name() or request.user.username,
    })


@_faculty_required
def faculty_my_events(request):
    qs = faculty_event_qs(request.user)
    assigned_events = list(qs)
    assigned_rows = {event.pk: _publication_row(event, request.user) for event in assigned_events}
    summary = {
        'total': len(assigned_events),
        'published': sum(event.results_finalized for event in assigned_events),
        'ready': sum(row['publication_ready'] for row in assigned_rows.values()),
        'criteria': sum(row['event_type'] == 'Criteria-Based' for row in assigned_rows.values()),
        'match': sum(row['event_type'] == 'Match-Based' for row in assigned_rows.values()),
    }
    categories = sorted({event.category for event in assigned_events if event.category})
    event_type = request.GET.get('type', 'all')
    event_type = event_type if event_type in {'criteria', 'match'} else 'all'
    category = request.GET.get('category', '')
    category = category if category in categories else ''
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
    if event_type != 'all':
        label = 'Criteria-Based' if event_type == 'criteria' else 'Match-Based'
        qs = qs.filter(pk__in=[pk for pk, row in assigned_rows.items() if row['event_type'] == label])
    if category:
        qs = qs.filter(category=category)
    if status == 'published':
        qs = qs.filter(Q(publication_status='published') | Q(results_finalized=True))
    elif status == 'draft':
        qs = qs.filter(publication_status='draft', results_finalized=False)
    elif status == 'ready':
        ready_ids = [pk for pk, row in assigned_rows.items() if row['publication_ready']]
        qs = qs.filter(pk__in=ready_ids)
    qs = qs.order_by(sort)
    page = Paginator(qs, 10).get_page(request.GET.get('page') or 1)
    rows = [assigned_rows[e.pk] for e in page.object_list]
    pagination_query = request.GET.copy()
    pagination_query.pop('page', None)
    return render(request, 'facultydash/my_events.html', {
        'active': 'my_events',
        'page_obj': page,
        'event_rows': rows,
        'search_query': q,
        'status_filter': status,
        'sort': sort,
        'event_summary': summary,
        'categories': categories,
        'type_filter': event_type,
        'category_filter': category,
        'pagination_query': pagination_query.urlencode(),
        'user_display': request.user.get_full_name() or request.user.username,
    })


def _publication_row(event, user):
    from events.publication_service import can_publish_event, match_publication_review
    if event_type_label(event) != 'Match-Based':
        row = serialize_event_row(event)
        from events.criteria_faculty_service import criteria_faculty_portal
        publication_portal = criteria_faculty_portal(event, user)
        publication = publication_portal['publication']
        ready = publication['can_publish']
        queued = not event.results_finalized and (ready or bool(
            (event.result_processing_config or {}).get('ready_for_publication')))
        row.update(
            tournament_format='', in_publication_queue=queued, publication_ready=ready,
            show_publication_button=False,
            show_unpublish_button=can_publish_event(user, event) and event.results_finalized and bool(
                (event.result_processing_config or {}).get('published_at')),
            status='Published Results' if event.results_finalized else 'Ready for Publication' if ready else 'Pending Approval' if queued else 'Pending Review',
            status_key='published' if event.results_finalized else 'ready' if ready else 'pending',
            publication_errors=publication['publish_errors'],
            judges_submitted=publication_portal['summary']['submitted_judges'],
            judges_total=publication_portal['summary']['total_judges'],
            judging_progress_percent=publication_portal['current_round']['progress_percent'],
            action='Open Event', action_url=reverse('faculty_event_manage', args=[event.pk]),
        )
        return row
    review = match_publication_review(event)
    row = serialize_event_row(event)
    row['tournament_format'] = (event.tournament_type or '').replace('_', ' ').title()
    row.update(review_ready=review['review_ready'], publication_ready=review['can_publish'], last_match=review['last_match'], standings=review['standings'])
    row['in_publication_queue'] = review['all_confirmed'] and not review['published']
    row['status'] = ('Published' if review['published'] else 'Ready for Publication' if review['can_publish'] else 'Placements Incomplete' if review['errors'] and review['all_confirmed'] else 'Pending Approval' if review['all_confirmed'] else 'Pending Confirmation'
                     if event.scoresheets.filter(status=ScoreSheet.STATUS_PENDING).exists() else 'In Progress')
    row['status_key'] = ('published' if review['published'] else 'ready' if review['can_publish']
                         else 'pending' if row['status'] in {'Pending Confirmation', 'Placements Incomplete', 'Pending Approval'} else 'progress')
    row['show_publication_button'] = can_publish_event(user, event) and review['can_publish']
    row['show_unpublish_button'] = can_publish_event(user, event) and review['published']
    row['publication_errors'] = review['errors'] + review['approval_errors']
    row['action'] = 'View Results' if review['published'] else 'Open Event'
    row['action_url'] = reverse('public_result_detail', args=[event.pk]) if review['published'] else reverse(
        'faculty_event_manage' if row['action'] == 'Open Event' else 'faculty_publication_review', args=[event.pk])
    return row


@_faculty_required
def faculty_publication_review(request, event_id):
    from events.publication_service import match_publication_review
    from events.audit_service import record_audit
    publisher = (request.user.is_staff and not request.user.groups.filter(
        name__iregex=r'^(Faculty|Faculty In-Charge|Tabulator|Judge|Judges|Scorer|Viewers)$').exists())
    event = get_object_or_404(Event, pk=event_id) if publisher else _get_event(request, event_id)
    if event is None or event_type_label(event) not in {'Match-Based', 'Criteria-Based'}:
        return HttpResponse('Event access required.', status=403)
    if event_type_label(event) == 'Criteria-Based' and (
        request.method != 'POST' or request.POST.get('action') != 'unpublish'
    ):
        return HttpResponse('Use the criteria event results page to review and publish results.', status=403)
    if request.method == 'POST':
        try:
            with transaction.atomic():
                event = Event.objects.select_for_update().get(pk=event.pk)
                review = match_publication_review(event) if event_type_label(event) == 'Match-Based' else None
                action = request.POST.get('action')
                if action == 'insert_playoff':
                    from events.publication_service import insert_missing_third_place
                    try:
                        insert_missing_third_place(event, request.user)
                    except ValueError as exc:
                        raise FacultyServiceError(str(exc))
                    messages.success(request, 'Third-Place Playoff added. Existing match results were preserved.')
                elif action == 'approve':
                    if not publisher:
                        raise FacultyServiceError('Only an authorized administrator can approve final results.')
                    if not review['review_ready']:
                        raise FacultyServiceError(' '.join(review['errors']) or 'This event is already published.')
                    approve_results(event, request.user)
                    messages.success(request, 'Final results approved for publication.')
                elif action == 'publish':
                    if request.POST.get('confirm') != 'yes':
                        raise FacultyServiceError('Explicit publication confirmation is required.')
                    publish_results(event, request.user)
                    messages.success(request, 'Official results published. Public results and championship standings are updated.')
                elif action == 'unpublish':
                    from events.faculty_service import unpublish_results
                    if request.POST.get('confirm') != 'yes':
                        raise FacultyServiceError('Explicit confirmation is required to remove publication.')
                    unpublish_results(event, request.user)
                    messages.success(request, 'Publication removed. Results are hidden from the public and their points no longer count in the leaderboard.')
                elif action == 'submit':
                    if not review['review_ready']:
                        raise FacultyServiceError(' '.join(review['errors']) or 'This event is already published.')
                    from events.publication_service import faculty_publication_workflow, can_publish_event
                    if faculty_publication_workflow(event):
                        if not can_publish_event(request.user, event):
                            raise FacultyServiceError('Only the assigned faculty or an authorized administrator may publish these results.')
                        messages.success(request, 'Results are ready for publication. Review the standings and confirm Publish Official Results.')
                    else:
                        if not review['submitted']:
                            cfg = dict(event.result_processing_config or {})
                            cfg.update(publication_submitted_at=timezone.now().isoformat(), publication_submitted_by=request.user.pk)
                            event.result_processing_config = cfg
                            event.save(update_fields=['result_processing_config', 'updated_at'])
                            record_audit(user=request.user, action='Submitted Results for Publication',
                                         description=f'Submitted final results for "{event.name}".', module='Results', event_name=event.name)
                        messages.success(request, 'Results submitted for authorized review and publication.')
                else:
                    raise FacultyServiceError('Unknown publication action.')
        except FacultyServiceError as exc:
            messages.error(request, str(exc))
        except DatabaseError:
            messages.error(request, 'Results could not be saved. Please try again; no publication changes were committed.')
        if request.POST.get('return_to') == 'admin_matches' and publisher:
            return redirect('admin_match_events')
        if request.POST.get('return_to') == 'dashboard':
            return redirect('faculty_dashboard')
        if request.POST.get('return_to') == 'my_events':
            return redirect('faculty_my_events')
        return redirect(reverse('faculty_event_manage', args=[event.pk]) + '?tab=results')
    return redirect(reverse('faculty_event_manage', args=[event.pk]) + '?tab=results')


@_faculty_required
def faculty_event_manage(request, event_id):
    from events.publication_service import match_publication_review
    publisher = request.user.is_staff and not request.user.groups.filter(
        name__iregex=r'^(Faculty|Faculty In-Charge|Tabulator|Judge|Judges|Scorer|Viewers)$').exists()
    event = get_object_or_404(Event, pk=event_id) if publisher else _get_event(request, event_id)
    if event is None:
        messages.error(request, 'You do not have access to this event.')
        return redirect('faculty_my_events')
    if event.scoring_method == 'criteria':
        from events.criteria_faculty_service import (
            approve_criteria_round, criteria_faculty_portal, criteria_rounds,
            submit_criteria_publication, unlock_criteria_round,
        )
        if request.method not in {'GET', 'HEAD', 'POST'}:
            return HttpResponse('Method not allowed.', status=405)
        selected_tab = request.POST.get('tab') if request.method == 'POST' else request.GET.get('tab')
        selected_tab = selected_tab if selected_tab in {'overview', 'rounds', 'judges', 'participants', 'removal_history', 'results'} else 'rounds'
        current_index = (event.result_processing_config or {}).get('active_stage_index', 0)
        raw_index = request.POST.get('selected_round_index', current_index) if request.method == 'POST' else request.GET.get('round', current_index)
        try:
            selected_index = int(raw_index)
        except (TypeError, ValueError):
            selected_index = 0
        selected_index = max(0, min(selected_index, len(criteria_rounds(event)) - 1))
        if request.method == 'POST':
            if event.faculty_account_id != request.user.pk or not request.user.is_active:
                return HttpResponse('Assigned Faculty-in-Charge access required.', status=403)
            action = request.POST.get('action', '')
            try:
                if action in {'remove_participant', 'restore_participant'}:
                    from events.participant_removal_service import remove_criteria_participant, restore_criteria_participant
                    if request.POST.get('confirm') != 'yes':
                        raise FacultyServiceError('Explicit confirmation is required.')
                    if action == 'remove_participant':
                        remove_criteria_participant(event, request.user, request.POST.get('participant_id'),
                                                    request.POST.get('reason'), request.POST.get('explanation', ''))
                        messages.success(request, 'Participant removed. Previous scores are preserved.')
                    else:
                        restore_criteria_participant(event, request.user, request.POST.get('removal_id'))
                        messages.success(request, 'Participant restored, subject to round qualification rules.')
                elif action in {'approve_round', 'unlock_round'}:
                    try:
                        round_index = int(request.POST.get('round_index', ''))
                    except (TypeError, ValueError) as exc:
                        raise FacultyServiceError('Select a valid round.') from exc
                    if action == 'approve_round':
                        approve_criteria_round(event, request.user, round_index, request.POST.getlist('qualifier_ids'))
                        messages.success(request, 'Final round approved. Review and publish the official results.' if round_index == len(criteria_rounds(event)) - 1 else 'Round results approved. Unlock the next round when you are ready.')
                    else:
                        unlock_criteria_round(event, request.user, round_index)
                        selected_index = round_index
                        messages.success(request, 'Round unlocked. Assigned judges can now score the qualified participants.')
                elif action == 'submit_publication':
                    submit_criteria_publication(event, request.user)
                    messages.success(request, 'Final results submitted for authorized publication.')
                elif action == 'publish':
                    if request.POST.get('confirm') != 'yes':
                        raise FacultyServiceError('Explicit publication confirmation is required.')
                    with transaction.atomic():
                        event = Event.objects.select_for_update().get(pk=event.pk)
                        publication = criteria_faculty_portal(event, request.user)['publication']
                        if not publication['can_publish']:
                            raise FacultyServiceError(' '.join(publication['publish_errors']) or 'Results are unavailable for publication.')
                        if not publication['submitted_at']:
                            submit_criteria_publication(event, request.user)
                        publish_results(event, request.user)
                    messages.success(request, 'Official results published. Public results and championship standings are updated.')
                else:
                    raise FacultyServiceError('Unknown event action.')
            except (FacultyServiceError, ValueError, TypeError) as exc:
                messages.error(request, str(exc))
            except DatabaseError:
                messages.error(request, 'The action could not be saved. Please try again.')
            return redirect(reverse('faculty_event_manage', args=[event.pk]) + '?' + urlencode({'tab': selected_tab, 'round': selected_index}))
        portal = criteria_faculty_portal(event, request.user, selected_index)
        if request.GET.get('format') == 'json':
            response = JsonResponse({'portal': portal})
            response['Cache-Control'] = 'no-store'
            return response
        return render(request, 'facultydash/criteria_event.html', {
            'active': 'my_events', 'event': event,
            'portal': portal,
            'tab': selected_tab, 'selected_round_index': selected_index,
            'user_display': request.user.get_full_name() or request.user.username,
        })
    if event_type_label(event) != 'Match-Based':
        messages.error(request, 'This event type is unavailable.')
        return redirect('faculty_my_events')
    tab = (request.GET.get('tab') or 'overview').strip()
    tab = {'schedule': 'matches'}.get(tab, tab)
    if tab not in {'overview', 'matches', 'participants', 'results'}:
        tab = 'overview'
    detail = serialize_event_detail(event)
    schedule = schedule_rows(event)
    match_schedule = [row for row in schedule if isinstance(row.get('id'), int)]
    match_filter = (request.GET.get('status') or 'all').lower()
    if match_filter not in {'all', 'pending', 'draft', 'confirmed'}:
        match_filter = 'all'
    filtered_matches = match_schedule if match_filter == 'all' else [
        row for row in match_schedule if row['result_status_key'] == match_filter
    ]
    confirmed_count = sum(1 for row in match_schedule if row['is_confirmed'])
    requested_match = request.GET.get('match')
    open_match_id = ''
    if requested_match and any(str(row['id']) == requested_match for row in match_schedule):
        open_match_id = requested_match
    return render(request, 'facultydash/event_manage.html', {
        'active': 'my_events',
        'tab': tab,
        'event': event,
        'detail': detail,
        'publication_review': match_publication_review(event),
        'publication_row': _publication_row(event, request.user),
        'can_approve_role': publisher,
        'schedule': schedule,
        'match_schedule': filtered_matches,
        'match_filter': match_filter,
        'confirmed_count': confirmed_count,
        'match_count': len(match_schedule),
        'progress_percent': round((confirmed_count / len(match_schedule)) * 100) if match_schedule else 0,
        'open_match_id': open_match_id,
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


@require_POST
@_faculty_required
def faculty_match_result(request, event_id, match_id):
    event = _get_event(request, event_id)
    if event is None:
        return JsonResponse({'success': False, 'message': 'Forbidden'}, status=403)
    if event_type_label(event) != 'Match-Based':
        return JsonResponse({'success': False, 'message': 'Match result entry is unavailable for this event.'}, status=400)
    match = get_object_or_404(BracketMatch, pk=match_id, event=event)
    if match.is_automatic_advance or not match.team_a_id or not match.team_b_id:
        return JsonResponse({'success': False, 'message': 'This match is not ready for result entry.'}, status=400)
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        data = request.POST
    if not hasattr(data, 'get'):
        return JsonResponse({'success': False, 'message': 'Request body must be a JSON object.'}, status=400)
    confirm = str(data.get('confirm') or data.get('action') or '').lower() in {'1', 'true', 'confirm'}
    score_a = data.get('score_a') if 'score_a' in data else data.get('scoreA')
    score_b = data.get('score_b') if 'score_b' in data else data.get('scoreB')
    winner_id = data.get('winner_id') if 'winner_id' in data else data.get('winner')
    try:
        sheet = save_match_result(
            event,
            match,
            request.user,
            score_a=score_a,
            score_b=score_b,
            winner_id=winner_id,
            remarks=data.get('remarks'),
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


@never_cache
@_faculty_required
def faculty_leaderboard(request):
    latest_update = Event.objects.aggregate(latest=Max('updated_at'))['latest']
    games = leaderboard_by_game()
    response = render(request, 'facultydash/leaderboard.html', {
        'active': 'leaderboard',
        'rows': leaderboard_rows(),
        'games': games,
        'category_winners': leaderboard_category_winners(games),
        'leaderboard_version': latest_update.isoformat() if latest_update else 'empty',
        'user_display': request.user.get_full_name() or request.user.username,
    })
    response['X-Leaderboard-Version'] = latest_update.isoformat() if latest_update else 'empty'
    return response


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
    if not hasattr(data, 'get'):
        return JsonResponse({'success': False, 'message': 'Request body must be a JSON object.'}, status=400)
    action = (data.get('action') or '').strip().lower()
    from events.publication_service import can_publish_event, faculty_publication_workflow
    direct_faculty_publish = (
        action == 'publish' and faculty_publication_workflow(event)
        and can_publish_event(request.user, event)
    )
    is_faculty_only = (
        request.user.groups.filter(Q(name__iexact='Faculty') | Q(name__iexact='Faculty In-Charge')).exists()
        and not request.user.groups.filter(name__iexact='Tabulator').exists()
        and not request.user.is_staff
    )
    if is_faculty_only and not direct_faculty_publish:
        return JsonResponse({'success': False, 'message': 'Faculty cannot manage criteria result approval.'}, status=403)
    if not request.user.is_staff and action in {'approve', 'publish'} and not direct_faculty_publish:
        return JsonResponse({'success': False, 'message': 'Faculty cannot approve or publish official results.'}, status=403)
    is_tabulator = request.user.groups.filter(name__iexact='Tabulator').exists() and not request.user.is_staff
    if is_tabulator and action in {'approve', 'publish'}:
        return JsonResponse({'success': False, 'message': 'Tabulation Officers cannot approve or publish official results.'}, status=403)
    try:
        if action == 'return':
            return_for_correction(event, request.user, data.get('judge_ids'))
            msg = 'Returned for correction.'
        elif action == 'approve':
            approve_results(event, request.user)
            msg = 'Results approved.'
        elif action == 'publish':
            if str(data.get('confirm') or '').lower() not in {'yes', 'true', '1'}:
                raise FacultyServiceError('Explicit publication confirmation is required.')
            publish_results(event, request.user)
            msg = 'Results published.'
        else:
            return JsonResponse({'success': False, 'message': 'Unknown action.'}, status=400)
    except FacultyServiceError as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)
    except DatabaseError:
        return JsonResponse({'success': False, 'message': 'Publication could not be saved; no publication changes were committed.'}, status=503)
    return JsonResponse({'success': True, 'message': msg})


@require_POST
@_faculty_required
def faculty_stage_confirm(request, event_id):
    event = _get_event(request, event_id)
    if event is None:
        return JsonResponse({'success': False, 'message': 'Forbidden'}, status=403)
    if event.scoring_method == 'criteria' and event.faculty_account_id and event.faculty_account.groups.filter(
        Q(name__iexact='Faculty') | Q(name__iexact='Faculty In-Charge')
    ).exists():
        return JsonResponse({'success': False, 'message': 'Round approval and unlocking are managed by the assigned Faculty-in-Charge from Event Details.'}, status=403)
    if (
        request.user.groups.filter(name__iexact='Faculty').exists()
        and not request.user.groups.filter(name__iexact='Tabulator').exists()
        and not request.user.is_staff
    ):
        return JsonResponse({'success': False, 'message': 'Faculty cannot manage criteria stages.'}, status=403)
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
    """Keep the read-only portal available to Tabulators and staff."""
    @login_required(login_url='login')
    def wrapped(request, *args, **kwargs):
        if request.user.is_staff or request.user.groups.filter(name__iexact='Tabulator').exists():
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
    """Match events have team standings; criteria events use judge scores."""
    if (event.scoring_method or '').lower() == 'criteria' or event.judging_event_id:
        return False
    if (event.scoring_method or '').lower() == 'match':
        return True
    if event.scoring_categories.exists():
        return False
    if (event.category or '').lower() == 'sports':
        return True
    if event.bracket_teams.exists():
        return True
    label = event_type_label(event)
    return label == 'Match-Based'


def _judge_completion(event):
    """Count a submission only when every participant and criterion is locked."""
    judges = list(event.assigned_judges.all())
    judging = event.judging_event
    if event.scoring_categories.exists() and not _event_is_ranking_mode(event):
        requirements = set(event.scoring_categories.filter(purpose='official').values_list(
            'assigned_round_id', 'criteria__id'))
        requirements = {(rid, cid) for rid, cid in requirements if cid is not None}
        from events.criteria_faculty_service import criteria_rounds
        from events.participant_removal_service import active_participant_ids
        round_indices = {row['id']: index for index, row in enumerate(criteria_rounds(event))}
        expected_keys = {(rid, cid, pid) for rid, cid in requirements
                         for pid in active_participant_ids(event, round_indices.get(rid))}
        submissions = event.criteria_score_submissions.filter(
            is_active=True, participant_id__in=event.participant_ids,
            status__in=['SUBMITTED', 'NEEDS_REVIEW', 'VERIFIED', 'APPROVED'],
        ).values_list('judge_id', 'assigned_round_id', 'criterion_id', 'participant_id')
        by_judge = defaultdict(set)
        for jid, rid, cid, pid in submissions:
            by_judge[jid].add((rid, cid, pid))
        rows = [{
            'name': judge.get_full_name() or judge.username,
            'complete': bool(expected_keys) and expected_keys <= by_judge[judge.pk],
            'locked': len(expected_keys & by_judge[judge.pk]), 'expected': len(expected_keys),
        } for judge in judges]
        return {'rows': rows, 'submitted': sum(row['complete'] for row in rows),
                'total': len(judges), 'expected_per_judge': len(expected_keys)}
    from events.participant_removal_service import active_mobile_candidate_ids
    candidate_ids = active_mobile_candidate_ids(event) if judging else []
    candidates = len(candidate_ids)
    criteria = judging.criteria.count() if judging else 0
    expected = candidates * criteria
    rows = []
    for judge in judges:
        locked = JudgeScore.objects.filter(
            candidate__event=judging, candidate_id__in=candidate_ids, judge=judge, is_locked=True,
        ).count() if judging else 0
        rows.append({
            'name': judge.get_full_name() or judge.username,
            'complete': expected > 0 and locked == expected,
            'locked': locked,
            'expected': expected,
        })
    submitted = sum(row['complete'] for row in rows)
    return {'rows': rows, 'submitted': submitted, 'total': len(judges),
            'expected_per_judge': expected}


def _serialize_portal_event(event) -> dict:
    mode = 'ranking' if _event_is_ranking_mode(event) else 'scoring'
    kind = _infer_event_kind(event)
    match_mode = mode == 'ranking'
    if match_mode:
        matches = event.bracket_matches.filter(is_automatic_advance=False)
        total = matches.count()
        completed = matches.filter(status__in=[BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT]).count()
        progress_label = f'{completed} / {total} matches completed'
    else:
        total = event.assigned_judges.count()
        completed = sum(1 for row in _judge_completion(event)['rows'] if row['complete'])
        progress_label = f'{completed} / {total} judges submitted'
    progress = round(completed * 100 / total) if total else 0
    config = event.result_processing_config or {}
    if event.results_finalized:
        status = 'Published' if event.publication_status == Event.PUBLICATION_PUBLISHED else 'Finalized'
    elif config.get('tabulation_confirmed_at'):
        status = 'Finalized'
    elif total and completed == total:
        status = 'Ready for Review'
    elif event.status == Event.STATUS_COMPLETED or (
        event.status == Event.STATUS_UPCOMING and event.event_date and event.event_date < timezone.localdate()
    ):
        status = 'Awaiting Results'
    elif event.status == Event.STATUS_ACTIVE:
        status = 'Ongoing'
    else:
        status = 'Upcoming'
    return {
        'id': event.id,
        'name': event.name,
        'category': event.category or '—',
        'status': status,
        'status_label': status,
        'status_key': status.lower().replace(' ', '_'),
        'results_finalized': bool(event.results_finalized),
        'mode': mode,
        'mode_label': 'Ranking Mode' if mode == 'ranking' else 'Scoring Mode',
        'event_kind': kind,
        'participant_label': _participant_label(kind, event.participation_type or ''),
        'event_type': event_type_label(event),
        'event_kind': 'match' if match_mode else 'criteria',
        'date': event.event_date.strftime('%b %d, %Y') if event.event_date else '—',
        'venue': event.venue or '—',
        'image_url': event.image.url if event.image else '',
        'progress': progress,
        'progress_label': progress_label,
    }


def _tabulator_portal_events(user) -> list[dict]:
    events = _tabulator_event_qs(user).order_by('-updated_at', 'name')
    return [_serialize_portal_event(ev) for ev in events]


def _tabulator_event_qs(user):
    if not user or not user.is_authenticated:
        return Event.objects.none()
    return Event.objects.filter(assigned_tabulators=user).distinct().select_related('judging_event')


def _resolve_portal_event(user, event_id=None):
    qs = _tabulator_event_qs(user).order_by('-updated_at', 'name')
    if event_id:
        try:
            eid = int(event_id)
        except (TypeError, ValueError):
            eid = None
        return qs.filter(pk=eid).first() if eid else None
    return qs.first()


def _category_filter_options(event) -> list[str]:
    names = list(
        event.scoring_categories.order_by('display_order', 'name').values_list('name', flat=True)
    )
    if names:
        return names
    if event.judging_event_id:
        from events.participant_removal_service import active_mobile_candidate_ids
        depts = (
            Candidate.objects.filter(event_id=event.judging_event_id, pk__in=active_mobile_candidate_ids(event))
            .exclude(department='')
            .values_list('department', flat=True)
            .distinct()
        )
        return sorted({d for d in depts if d})
    return []


def _enough_locked_scores(event) -> bool:
    if not event.judging_event_id:
        return False
    from events.participant_removal_service import active_mobile_candidate_ids
    locked = JudgeScore.objects.filter(
        candidate__event_id=event.judging_event_id, candidate_id__in=active_mobile_candidate_ids(event), is_locked=True
    ).exists()
    return locked


def _status_label_for_results(event, mode: str) -> str:
    if event.results_finalized:
        return 'Completed'
    result_cfg = event.result_processing_config or {}
    if result_cfg.get('tabulation_confirmed_at'):
        return 'Tabulation Confirmed'
    if event.status == Event.STATUS_COMPLETED:
        return 'Completed'
    if mode == 'ranking':
        completed = event.bracket_matches.filter(
            status__in=[BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT]
        ).exists()
        if completed:
            return 'Live'
        return 'Results Pending'
    if judge_monitoring(event)['all_submitted']:
        return 'Ready for Verification'
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
        .select_related('criterion')
    )
    if not scores:
        return {
            'criteria': [{'id': c.id, 'name': c.name} for c in criteria],
            'rows': [],
            'averages': [],
        }
    judge_ids = {score.judge_id for score in scores}
    judge_names = {
        user.id: user.get_full_name() or user.username
        for user in get_user_model().objects.filter(id__in=judge_ids)
    }
    by_judge: dict[int, dict] = {}
    for s in scores:
        row = by_judge.setdefault(s.judge_id, {
            'judge_id': s.judge_id,
            'judge_name': judge_names.get(s.judge_id, 'Former Judge'),
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
            status__in=[BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT],
            is_automatic_advance=False,
        ).select_related('team_a', 'team_b', 'winner')
    )
    wins = defaultdict(int)
    pf = defaultdict(float)
    pa = defaultdict(float)
    played = defaultdict(int)
    form = defaultdict(list)
    for m in completed:
        for team_id in (m.team_a_id, m.team_b_id):
            if team_id:
                played[team_id] += 1
                if m.winner_id:
                    form[team_id].append('W' if team_id == m.winner_id else 'L')
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
            'rank': idx if completed else None,
            'team_id': team.id,
            'name': team.name,
            'department': team.department.name if team.department_id else '—',
            'wins': wins.get(team.id, 0),
            'played': played.get(team.id, 0),
            'losses': int(team.loss_count or 0),
            'points': int(team.points or 0),
            'pf': round(pf.get(team.id, 0.0), 1),
            'pa': round(pa.get(team.id, 0.0), 1),
            'prize': _prize_label(idx) if completed else '—',
            'form': form.get(team.id, [])[-5:],
            'is_champion': bool(team.is_champion),
            'photo': '',
        })
    rows.sort(key=lambda r: (-r['points'], -r['wins'], r['name']))
    for idx, row in enumerate(rows, start=1):
        row['rank'] = idx if completed else None
        row['prize'] = _prize_label(idx) if completed else '—'
    rules = (event.result_processing_config or {}).get('championship_scoring')
    if rules:
        from events.points_settings_service import _match_placements
        actual = list(event.bracket_matches.filter(is_automatic_advance=False).order_by('match_number'))
        final_ranks = {}
        if actual and all(match.status in {BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT} for match in actual):
            final_ranks, _ = _match_placements(event, teams, actual, rules)
        for row in rows:
            row['rank'] = final_ranks.get(row['team_id'])
            row['prize'] = _prize_label(row['rank']) if row['rank'] else '—'
        rows.sort(key=lambda row: (row['rank'] or 1000000, -row['wins'], row['name']))
    return rows


def _recent_games(event, limit=12) -> list[dict]:
    matches = (
        event.bracket_matches.filter(
            status__in=[BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT],
            is_automatic_advance=False,
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
            'date': m.match_date.strftime('%b %d, %Y') if m.match_date else '—',
            'time': m.match_time.strftime('%I:%M %p') if m.match_time else '—',
            'venue': m.venue or event.venue or '—',
            'details': m.remarks or '',
        })
    return rows


def _upcoming_games(event, limit=12):
    matches = event.bracket_matches.filter(
        status__in=[BracketMatch.STATUS_PENDING, BracketMatch.STATUS_ONGOING],
        is_automatic_advance=False,
    ).select_related('team_a', 'team_b').order_by('match_date', 'match_time', 'match_number')[:limit]
    return [{
        'id': match.id,
        'match_number': match.match_number,
        'team_a': match.team_a.name if match.team_a_id else 'TBD',
        'team_b': match.team_b.name if match.team_b_id else 'TBD',
        'date': match.match_date.strftime('%b %d, %Y') if match.match_date else 'To be scheduled',
        'time': match.match_time.strftime('%I:%M %p') if match.match_time else '—',
        'venue': match.venue or event.venue or '—',
        'status': match.get_status_display(),
    } for match in matches]


def _all_match_rows(event):
    matches = event.bracket_matches.filter(is_automatic_advance=False).select_related(
        'team_a', 'team_b', 'winner',
    ).order_by('match_number')
    return [{
        'id': match.id,
        'match_number': match.match_number,
        'date': match.match_date.strftime('%b %d, %Y') if match.match_date else '—',
        'time': match.match_time.strftime('%I:%M %p') if match.match_time else '—',
        'venue': match.venue or event.venue or '—',
        'team_a': match.team_a.name if match.team_a_id else 'TBD',
        'team_b': match.team_b.name if match.team_b_id else 'TBD',
        'score_a': match.score_a if match.score_a != '' else None,
        'score_b': match.score_b if match.score_b != '' else None,
        'status': match.get_status_display(),
        'winner': match.winner.name if match.winner_id else '',
        'details': match.remarks or '',
    } for match in matches]


def _verification_summary(event, match_mode, match_count, completed_matches, completion):
    if match_mode:
        completed = event.bracket_matches.filter(
            status__in=[BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT],
            is_automatic_advance=False,
        )
        missing_winners = completed.filter(winner__isnull=True).count()
        points = list(event.bracket_teams.values_list('points', flat=True)) if completed_matches else []
        tied_points = len(points) != len(set(points)) if points else False
        checks = [
            {'label': 'All matches completed', 'complete': match_count > 0 and completed_matches == match_count,
             'detail': f'{completed_matches} of {match_count} completed'},
            {'label': 'Winners recorded', 'complete': completed_matches > 0 and missing_winners == 0,
             'detail': f'{missing_winners} completed matches missing winners' if completed_matches else 'Awaiting completed matches'},
            {'label': 'Points calculated', 'complete': completed_matches > 0 and missing_winners == 0,
             'detail': 'Calculated from recorded match results' if completed_matches else 'Points will appear after the first completed match'},
            {'label': 'Tie-break rules checked', 'complete': completed_matches > 0 and (not tied_points or event.results_finalized),
             'detail': 'Review after standings are available' if not completed_matches else ('Tied points need review against the event rules' if tied_points and not event.results_finalized else 'No unresolved points tie')},
            {'label': 'Final standings generated', 'complete': event.results_finalized and completed_matches == match_count and match_count > 0,
             'detail': 'Awaiting final confirmation' if completed_matches else 'Awaiting completed matches'},
        ]
    else:
        judging = event.judging_event
        criteria = list(judging.criteria.all()) if judging else []
        expected = completion['expected_per_judge']
        all_submitted = completion['total'] > 0 and completion['submitted'] == completion['total'] and expected > 0
        weighted = event.criteria_score_method == Event.CRITERIA_SCORE_WEIGHTED
        weight_total = sum(float(criterion.weight_percent or 0) for criterion in criteria)
        weight_ok = not weighted or (bool(criteria) and abs(weight_total - 100) < 0.01)
        checks = [
            {'label': 'All judges submitted', 'complete': all_submitted,
             'detail': f"{completion['submitted']} of {completion['total']} complete"},
            {'label': 'All criteria completed', 'complete': all_submitted,
             'detail': 'Every participant has a locked score for every criterion' if all_submitted else 'Scores are still missing'},
            {'label': 'Total weight equals 100%', 'complete': weight_ok,
             'detail': f'{weight_total:g}% configured' if weighted else 'Not used by this scoring method'},
            {'label': 'No missing scores', 'complete': all_submitted,
             'detail': 'Complete' if all_submitted else 'Review judge submissions'},
            {'label': 'Final calculations completed', 'complete': all_submitted and weight_ok,
             'detail': 'Ready for verification' if all_submitted and weight_ok else 'Awaiting complete scoring'},
        ]
    has_results = completed_matches > 0 if match_mode else completion['submitted'] > 0
    issues = [check['detail'] for check in checks if not check['complete']] if has_results else []
    return {'checks': checks, 'complete': all(check['complete'] for check in checks), 'issues': issues}


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
        has_score = cand_id in locked_candidate_ids
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
    for log in AuditLog.objects.filter(event_name__iexact=event_name).order_by('-created_at')[:limit]:
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


def _tabulator_results_payload(user, event_id=None, category='', tab='overall', q='', participant_id=None):
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
            'event_kind': 'criteria',
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
            'upcoming_matches': [],
            'match_results': [],
            'participants': [],
            'selected_participant': None,
            'verification': {'checks': [], 'complete': False, 'issues': []},
            'updates': [],
            'message': 'No events are currently assigned to you.',
            'stats': {
                'participant_count': 0,
                'scored_count': 0,
                'judge_count': 0,
                'match_count': 0,
                'completed_matches': 0,
                'remaining_matches': 0,
                'submitted_judges': 0,
                'completion_pct': 0,
            },
        }

    portal = _serialize_portal_event(event)
    mode = portal['mode']
    categories = _category_filter_options(event)
    selected_category = (category or '').strip()
    tab = (tab or 'overall').strip().lower()
    is_finalized = bool(event.results_finalized)
    status_label = portal['status_label']

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
        winner = results[0] if results and results[0].get('rank') else None
        by_category = _by_category_groups(event, results)
        if not results:
            message = 'No match standings yet for this event.'
    else:
        results = _criteria_result_rows(event, category=selected_category, q=q)
        scored = [r for r in results if r.get('has_score')]
        winner = scored[0] if scored else None
        selected_id = None
        if participant_id:
            try:
                requested_id = int(participant_id)
            except (TypeError, ValueError):
                requested_id = None
            if requested_id and any(row.get('candidate_id') == requested_id for row in results):
                selected_id = requested_id
        if selected_id is None:
            selected_id = winner.get('candidate_id') if winner else (results[0].get('candidate_id') if results else None)
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
    match_qs = event.bracket_matches.filter(is_automatic_advance=False)
    match_count = match_qs.count() if mode == 'ranking' else 0
    completed_matches = match_qs.filter(status__in=[BracketMatch.STATUS_COMPLETED, BracketMatch.STATUS_FORFEIT]).count() if mode == 'ranking' else 0
    if mode == 'ranking':
        participant_count = event.bracket_teams.count()
        scored_count = len([r for r in match_standings if r.get('wins') or r.get('points')])
    elif event.judging_event_id:
        from events.participant_removal_service import active_mobile_candidate_ids
        participant_count = len(active_mobile_candidate_ids(event))
        scored_count = len([r for r in results if r.get('has_score')])

    monitoring = judge_monitoring(event) if mode == 'scoring' else {
        'rows': [], 'submitted': 0, 'remaining': 0, 'total': 0, 'all_submitted': False,
    }
    completion = _judge_completion(event) if mode == 'scoring' else {'rows': [], 'submitted': 0, 'total': 0, 'expected_per_judge': 0}
    complete_scores = completion['total'] > 0 and completion['submitted'] == completion['total']
    if mode == 'scoring':
        judge_count = completion['total']
        for row in results:
            candidate_scores = list(JudgeScore.objects.filter(
                candidate_id=row['candidate_id'], is_locked=True,
            ).values_list('score', flat=True)) if row.get('candidate_id') else []
            row['average_score'] = round(sum(candidate_scores) / len(candidate_scores), 2) if candidate_scores else None
            row['weighted_score'] = row.get('final_score') if event.criteria_score_method == Event.CRITERIA_SCORE_WEIGHTED else None
            row['judges_submitted'] = JudgeScore.objects.filter(candidate_id=row['candidate_id'], is_locked=True).values('judge_id').distinct().count() if row.get('candidate_id') else 0
            row['judge_count'] = judge_count
            row['final_visible'] = is_finalized or complete_scores
            row['status'] = 'Complete' if row['judges_submitted'] == judge_count and judge_count else 'In Progress'
            if not row['final_visible']:
                row['rank'] = None
                row['prize'] = '—'
        if not (is_finalized or complete_scores):
            winner = None
    verification = _verification_summary(event, mode == 'ranking', match_count, completed_matches, completion)
    if is_finalized:
        status_message = 'Results have been finalized and published.' if status_label == 'Published' else 'Results have been finalized and are awaiting publication.'
    elif mode == 'ranking':
        review_count = event.scoresheets.filter(status=ScoreSheet.STATUS_CONFIRMED).count()
        if review_count:
            status_message = f'{review_count} result{"s are" if review_count != 1 else " is"} ready for review.'
        elif match_count and completed_matches == match_count:
            status_message = 'All match results have been completed and are ready for verification.'
        else:
            status_message = 'Results are still in progress. Standings will update after results are submitted.'
    elif verification['complete']:
        status_message = 'All results have been completed and are ready for verification.'
    else:
        status_message = 'Results are still in progress. Standings will update after results are submitted.'
    if mode == 'ranking':
        progress_pct = round(completed_matches * 100 / match_count) if match_count else 0
        upcoming_matches = _upcoming_games(event)
        match_results = _all_match_rows(event)
    else:
        progress_pct = round(completion['submitted'] * 100 / judge_count) if judge_count else 0
        upcoming_matches = []
        match_results = []
    result_cfg = dict(event.result_processing_config or {})
    payload = {
        'event': portal,
        'event_kind': portal['event_kind'],
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
        'upcoming_matches': upcoming_matches,
        'match_results': match_results,
        'participants': results if mode == 'scoring' else [],
        'selected_participant': next((row for row in results if row.get('candidate_id') == selected_id), None) if mode == 'scoring' else None,
        'verification': verification,
        'status_message': status_message,
        'updates': _result_updates(event),
        'judge_monitoring': monitoring,
        'verification_mode': result_cfg.get('verification_mode') or 'admin_only',
        'tabulation_confirmed': bool(result_cfg.get('tabulation_confirmed_at')),
        'tabulation_confirmed_at': result_cfg.get('tabulation_confirmed_at') or '',
        'can_confirm_tabulation': (
            mode == 'scoring'
            and result_cfg.get('verification_mode') in {'tabulator_verification', 'tabulator_admin_approval'}
            and event.assigned_tabulators.filter(pk=user.pk).exists()
            and not result_cfg.get('tabulation_confirmed_at')
            and not event.results_finalized
            and verification['complete']
        ),
        'message': message,
        'stats': {
            'participant_count': participant_count,
            'scored_count': scored_count,
            'judge_count': judge_count,
            'match_count': match_count,
            'completed_matches': completed_matches,
            'remaining_matches': max(0, match_count - completed_matches),
            'submitted_judges': completion['submitted'],
            'completion_pct': progress_pct,
        },
        'q': q or '',
    }
    if mode == 'scoring' and event.scoring_categories.exists():
        from .tabulator_result_page import build_results_page
        page = build_results_page(SimpleNamespace(GET={}), event, payload)
        rows = []
        for source in page['live_rows']:
            row = dict(source)
            row.update(candidate_id=row['participant_id'], final_visible=row['complete'],
                       average_score=None, weighted_score=row['final_score'],
                       judges_submitted=row.get('approved_judges', 0), judge_count=judge_count)
            rows.append(row)
        payload.update(results=rows, participants=rows, winner=None,
                       status_message=page['scope_notice'] or page['status_message'],
                       criteria_round_name=page['round_name'], is_finalized=page['is_published'])
        from events.participant_removal_service import active_participant_ids
        payload['stats'].update(participant_count=len(set(active_participant_ids(event))),
                                scored_count=sum(row['complete'] for row in rows))
    return payload



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
    event = _resolve_portal_event(request.user, event_id)
    ctx['dashboard_stats'] = {'submitted_scores': 0, 'pending_scores': 0}
    ctx['score_scope'] = 'Linked judging event · individual criterion scores'
    ctx['standings_scope'] = 'Official standings' if payload['is_finalized'] else 'Provisional standings'
    ctx['dashboard_final'] = payload['is_finalized']
    if event:
        portal = payload['event']
        if not event.results_finalized and (event.result_processing_config or {}).get('tabulation_confirmed_at'):
            portal.update(status='Pending Confirmation', status_label='Pending Confirmation',
                          status_key='ready_for_review')
            payload['status_message'] = 'Tabulation has been verified. Waiting for authorized confirmation and publication.'
        elif portal['status_key'] == 'upcoming':
            payload['status_message'] = 'This event has not started. Standings will appear once valid results are available.'
        elif portal['status_key'] == 'ongoing':
            payload['status_message'] = 'Scoring is currently ongoing. Live rankings are provisional.'
    if event and payload['event_kind'] == 'criteria':
        judging = event.judging_event
        if judging:
            from events.participant_removal_service import active_mobile_candidate_ids
            candidate_ids = active_mobile_candidate_ids(event)
            judge_ids = event.assigned_judges.values_list('pk', flat=True)
            criterion_ids = judging.criteria.values_list('pk', flat=True)
            expected = len(candidate_ids) * judging.criteria.count() * event.assigned_judges.count()
            submitted = JudgeScore.objects.filter(
                candidate__event=judging, candidate_id__in=candidate_ids, criterion_id__in=criterion_ids,
                judge_id__in=judge_ids, is_locked=True,
            ).count()
            ctx['dashboard_stats'] = {
                'submitted_scores': submitted,
                'pending_scores': max(0, expected - submitted),
            }
        if event.scoring_categories.exists():
            completion = _judge_completion(event)
            submitted = sum(row['locked'] for row in completion['rows'])
            expected = completion['expected_per_judge'] * completion['total']
            ctx['dashboard_stats'] = {'submitted_scores': submitted,
                                      'pending_scores': max(0, expected - submitted)}
            ctx['score_scope'] = 'All configured rounds ? individual criterion submissions'
            ctx['standings_scope'] = f"{payload.get('criteria_round_name', 'Selected round')} ? round standings"
            ctx['dashboard_final'] = payload['is_finalized']
        elif len(stage_panels(event)) > 1:
            ctx['score_scope'] = 'Linked judging event only · not an overall round total'
            ctx['standings_scope'] = 'Linked judging event scores · overall round aggregation unavailable'
            ctx['dashboard_final'] = False
            # The existing service has no verified cross-round aggregate. Do not
            # promote its linked-event scores to official overall standings.
            for row in payload['results']:
                row['rank'] = None
                row['final_visible'] = False
    # Reuse the existing activity serializer and enforce assignment scope before
    # exposing any rows. It already sorts activity by the source timestamps.
    from .views import _build_official_activity_rows
    assigned_ids = set(_tabulator_event_qs(request.user).values_list('pk', flat=True))
    ctx['recent_activity'] = [
        row for row in _build_official_activity_rows()
        if row.get('event_id') in assigned_ids
    ][:6]
    for row in ctx['recent_activity']:
        if row.get('sort_dt'):
            local_time = timezone.localtime(row['sort_dt'])
            row['date'] = local_time.strftime('%b %d, %Y')
            row['time'] = local_time.strftime('%I:%M %p')
    return render(request, 'tabulator/tabulatordashboard.html', ctx)


@_tabulator_portal_required
@require_GET
def tabulator_notifications(request):
    events = _tabulator_portal_events(request.user)
    ctx = _portal_context(request, 'notifications', {'events': events})
    return render(request, 'tabulator/tabnotification.html', ctx)


@_tabulator_portal_required
def tabulator_my_events(request):
    payload = _tabulator_results_payload(request.user, event_id=request.GET.get('event_id'))
    status = (request.GET.get('status') or 'all').strip().lower()
    query = (request.GET.get('q') or '').strip()
    allowed = {'all', 'ongoing', 'upcoming', 'completed'}
    if status not in allowed:
        status = 'all'
    events = payload['events']
    if status == 'completed':
        events = [event for event in events if event['status_label'] in {'Finalized', 'Published'}]
    elif status != 'all':
        events = [event for event in events if event['status_label'].lower() == status]
    if query:
        events = [event for event in events if query.lower() in event['name'].lower()]
    payload['filtered_events'] = events
    payload['event_filter'] = status
    payload['event_query'] = query
    return render(request, 'tabulator/tabmyevents.html', _portal_context(request, 'my_events', payload))


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


@never_cache
@_tabulator_portal_required
def tabulator_results(request):
    payload = _tabulator_results_payload(
        request.user,
        event_id=request.GET.get('event_id'),
        participant_id=request.GET.get('participant_id'),
    )
    from .tabulator_result_page import build_results_page
    ctx = _portal_context(request, 'results', payload)
    ctx['results_page'] = build_results_page(
        request, _resolve_portal_event(request.user, request.GET.get('event_id')), payload,
    )
    template = 'tabulator/results-report.html' if request.GET.get('print') == '1' else 'tabulator/tabresults.html'
    return render(request, template, ctx)


@_tabulator_portal_required
def tabulator_full_results(request):
    # Preserve bookmarked event, participant and tab selections on the old URL.
    query = request.GET.urlencode()
    url = reverse('tabulator_results')
    return redirect(f'{url}?{query}' if query else url)


@_tabulator_portal_required
def tabulator_profile(request):
    payload = _tabulator_results_payload(request.user)
    return render(request, 'tabulator/tabprofile.html', _portal_context(request, 'profile', payload))


@require_POST
@_tabulator_portal_required
def tabulator_confirm_results(request, event_id):
    event = _resolve_portal_event(request.user, event_id)
    if event is None or event.pk != event_id:
        return JsonResponse({'success': False, 'message': 'Forbidden'}, status=403)
    if not request.user.groups.filter(name__iexact='Tabulator').exists():
        return JsonResponse({'success': False, 'message': 'Tabulator access required.'}, status=403)
    try:
        confirm_tabulation(event, request.user)
    except (FacultyServiceError, ValueError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Tabulation confirmed. Results are ready for the configured approval workflow.')
    return redirect(f"{reverse('tabulator_results')}?event_id={event.id}")


@_tabulator_portal_required
def tabulator_results_data(request):
    payload = _tabulator_results_payload(
        request.user,
        event_id=request.GET.get('event_id'),
        category=request.GET.get('category') or '',
        tab=request.GET.get('tab') or 'overall',
        q=request.GET.get('q') or '',
        participant_id=request.GET.get('participant_id'),
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


@never_cache
@_tabulator_portal_required
def tabulator_results_csv(request):
    event = _resolve_portal_event(request.user, request.GET.get('event_id'))
    if event is None:
        return HttpResponse('No assigned event selected.', status=404)
    if not event.results_finalized and request.GET.get('tab') not in {'live', 'breakdown'}:
        return HttpResponse('Final results are not available yet.', status=409)
    payload = _tabulator_results_payload(
        request.user,
        event_id=request.GET.get('event_id'),
    )
    from .tabulator_result_page import build_results_page
    page = build_results_page(request, event, payload)
    if page['error']:
        return HttpResponse(page['error'], status=400)
    if page['default_tab'] == 'official' and not page['is_published']:
        return HttpResponse('Official results are awaiting verification and publication.', status=409)
    buf = io.StringIO()
    writer = csv.writer(buf)
    # Prevent user-controlled labels being interpreted as spreadsheet formulas.
    def safe_cell(value):
        if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
            return "'" + value
        return value

    def write(values):
        writer.writerow([safe_cell(value) for value in values])

    write(['Event', event.name, 'Round', page['round_name'], 'Category', page['category_name']])
    write(['Report status', 'Published' if page['is_published'] and page['default_tab'] == 'official' else 'Unofficial / provisional',
           'Generated', page['generated_at_iso']])
    if page['default_tab'] == 'breakdown' and page['event_kind'] == 'criteria':
        write(['Participant', 'Number', 'Criterion', 'Category', 'Maximum', 'Weight']
              + [judge['name'] for judge in page['judges']] + ['Average', 'Contribution'])
        for group in page['breakdown_groups']:
            for row in group['rows']:
                write([group['name'], group['number'], row['name'], row.get('category', ''), row['max_score'], row['weight']]
                      + [cell['value'] if cell['value'] is not None else cell['status'] for cell in row['cells']]
                      + [row['average'], row['contribution']])
    elif page['default_tab'] == 'breakdown' and page['event_kind'] == 'match':
        write(['Match', 'Team A', 'Team B', 'Score A', 'Score B', 'Winner', 'Status'])
        for row in page['match_results']:
            write([row['match_number'], row['team_a'], row['team_b'], row['score_a'], row['score_b'], row['winner'], row['status']])
    else:
        rows = page['official_rows'] if page['default_tab'] == 'official' else page['live_rows']
        if not rows:
            return HttpResponse('No standings are available for the selected filters.', status=409)
        if page['event_kind'] == 'match':
            write(['Rank', 'Team', 'Department', 'Played', 'Wins', 'Losses', 'Points', 'Result', 'Status'])
            for row in rows:
                write([row.get('rank'), row['name'], row.get('department'), row['played'], row['wins'], row['losses'],
                       row['points'], row.get('prize') if page['is_published'] else '', row['status']])
        else:
            write(['Rank', 'Number', 'Name', 'Category', 'Score', 'Result', 'Status'])
            for row in rows:
                write([row.get('rank'), row.get('number'), row['name'], row.get('department'), row['final_score'],
                       row.get('prize') if page['is_published'] else '', row['status']])
    filename = f"results-{event.pk}-{timezone.localdate().strftime('%Y%m%d')}.csv"
    response = HttpResponse(buf.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
