from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction, DatabaseError
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from events.audit_service import record_audit
from events.models import AuditLog, Event, SystemSettings
from events.schedule_navigation import ScheduleNavigationForm
from events.points_settings_service import (
    ScoringRulesError, default_rules, event_snapshot, placement_schedule,
    reconcile_championship_points, scoring_rules, validate_scoring_rules,
    invalidate_scoring_publication,
    reopen_match_for_correction, apply_global_rules_to_existing_events,
)
import json
from copy import deepcopy


def can_manage_scoring(user):
    groups = {name.lower() for name in user.groups.values_list('name', flat=True)}
    return user.is_superuser or 'admin' in groups or (
        user.is_staff and not groups.intersection({'faculty', 'faculty in-charge', 'tabulator', 'judge', 'judges', 'scorer', 'viewers'})
    )


def _draft_config(data, previous):
    draft = deepcopy(previous)
    draft['method'] = data.get('method', previous['method'])
    for key in ('major', 'minor'):
        draft['win_points'][key] = data.get(f'{key}_win', previous['win_points'][key])
    try:
        rows = json.loads(data.get('placements', '[]'))
        if isinstance(rows, list) and len(rows) <= 100 and all(isinstance(row, dict) for row in rows):
            draft['placements'] = rows
    except (ValueError, TypeError):
        pass
    for field in ('approval_required', 'automatic_calculation'):
        draft[field] = data.get(field) == 'on'
    draft['tie_policy'] = data.get('tie_policy', previous['tie_policy'])
    return draft


def _resolved_placements(data, event, rules):
    teams = list(event.bracket_teams.all())
    entries = [(team.pk, data.get(f'placement_{team.pk}', '').strip()) for team in teams]
    if not any(value for _, value in entries):
        return {}
    actual = event.bracket_matches.filter(is_automatic_advance=False)
    if not actual.exists() or actual.exclude(status__in=['completed', 'forfeit']).exists():
        raise ScoringRulesError('Confirm all actual matches before recording final placements.')
    if any(not value or not value.isascii() or not value.isdecimal() or len(value) > 3
           or not 1 <= int(value) <= len(teams) for _, value in entries):
        raise ScoringRulesError('Provide a valid final position for every team.', {'final_placements': 'All teams need a final position.'})
    positions = [int(value) for _, value in entries]
    if len(set(positions)) != len(positions) and rules['tie_policy'] != 'shared':
        raise ScoringRulesError('Shared positions require the Shared placement points tie policy.')
    return {str(team_id): int(value) for team_id, value in entries}


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def admin_settings(request):
    if not can_manage_scoring(request.user):
        return HttpResponseForbidden('Only authorized administrators may manage championship scoring rules.')
    event = None
    if request.GET.get('event'):
        try:
            event_id = int(request.GET['event'])
        except (ValueError, TypeError):
            return HttpResponseBadRequest('Invalid event selection.')
        event = get_object_or_404(Event, pk=event_id, scoring_method__in=['match', 'criteria'])
    previous = (event.result_processing_config or {}).get('championship_scoring') if event else None
    config = deepcopy(previous or scoring_rules())
    error = ''
    field_errors = {}
    if request.method == 'POST' and request.POST.get('operation') == 'schedule_navigation':
        if event:
            return HttpResponseBadRequest('Competition day settings are system-wide, not event overrides.')
        form = ScheduleNavigationForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    obj = SystemSettings.load()
                    obj = SystemSettings.objects.select_for_update().get(pk=obj.pk)
                    previous_schedule = {'start_date': str(obj.intramurals_start_date or ''),
                                         'excluded_dates': obj.non_competition_dates}
                    obj.intramurals_start_date = form.cleaned_data['intramurals_start_date']
                    obj.non_competition_dates = form.cleaned_data['non_competition_dates']
                    obj.updated_by = request.user
                    obj.save(update_fields=['intramurals_start_date', 'non_competition_dates',
                                            'updated_by', 'updated_at'])
                    record_audit(user=request.user, action='Updated Competition Day Navigation',
                        module='Schedules', request=request,
                        description='Updated the official intramurals calendar. Competition results are unchanged.',
                        metadata={'previous': previous_schedule,
                                  'start_date': str(obj.intramurals_start_date or ''),
                                  'excluded_dates': obj.non_competition_dates})
            except DatabaseError:
                return _render_settings(request, event, config,
                    'Unable to save schedule settings. Please try again.', {}, form)
            messages.success(request, 'Competition day settings saved. Scoring and results are unchanged.')
            return redirect(reverse('admin_settings') + '#schedule-navigation')
        return _render_settings(request, event, config, 'Please correct the schedule settings.', {}, form)
    if request.method == 'POST':
        config = _draft_config(request.POST, config)
        if event and request.POST.get('operation') == 'reopen':
            try:
                reason = request.POST.get('reason', '').strip()
                if not reason:
                    raise ScoringRulesError('Enter the audit reason for reopening this match.')
                if len(reason) > 2000:
                    raise ScoringRulesError('Keep the audit reason within 2,000 characters.')
                match_id = int(request.POST.get('correction_match', ''))
                reopen_match_for_correction(event, match_id, request.user, reason)
            except (ScoringRulesError, ValueError) as exc:
                error = str(exc) or 'Choose a confirmed match.'
            else:
                messages.success(request, 'Match reopened. Updated results need confirmation, approval and republication.')
                return redirect(reverse('admin_settings') + f'?event={event.pk}')
        if error:
            return _render_settings(request, event, config, error, {'correction_match': error})
        try:
            validated = validate_scoring_rules(request.POST, previous or scoring_rules())
            if event:
                if event.event_classification not in {'major', 'minor'}:
                    raise ScoringRulesError('Set this event to Major or Minor in the event editor before overriding its rules.')
                if event.scoring_method == 'criteria' and validated['method'] != 'placement':
                    raise ScoringRulesError('Criteria-based events use final placement awards; judging calculations remain unchanged.')
                if request.POST.get('operation') == 'recalculate' and not previous:
                    raise ScoringRulesError('Save an explicit event override before recalculating legacy scoring.')
                reason = request.POST.get('reason', '').strip()
                if not reason:
                    raise ScoringRulesError('An override or recalculation requires an audit reason.', {'reason': 'Enter the reason.'})
                if len(reason) > 2000:
                    raise ScoringRulesError('Keep the audit reason within 2,000 characters.', {'reason': 'Use no more than 2,000 characters.'})
                placements = _resolved_placements(request.POST, event, validated)
        except ScoringRulesError as exc:
            error = str(exc)
            field_errors = exc.fields
        else:
            try:
                with transaction.atomic():
                    if event:
                        event = Event.objects.select_for_update().get(pk=event.pk)
                        if request.POST.get('operation') == 'recalculate':
                            reconcile_championship_points(event, force=True)
                            action = 'Recalculated Championship Points'
                        else:
                            cfg = dict(event.result_processing_config or {})
                            cfg['championship_scoring'] = event_snapshot(event, validated)
                            cfg['championship_placements'] = placements
                            cfg.pop('match_win_points', None)
                            event.result_processing_config = cfg
                            event.championship_points_config = placement_schedule(validated, event.event_classification)
                            if event.scoring_method == 'match':
                                event.apply_championship_points = False
                            event.save()
                            invalidate_scoring_publication(event, request.user, reason)
                            reconcile_championship_points(event, force=True)
                            action = 'Overrode Event Championship Rules'
                        record_audit(user=request.user, action=action, module='Championship', request=request,
                                     event_name=event.name, description=reason,
                                     metadata={'event_id': event.pk, 'previous': previous or {},
                                               'updated': event.result_processing_config['championship_scoring']})
                    else:
                        obj = SystemSettings.load()
                        obj = SystemSettings.objects.select_for_update().get(pk=obj.pk)
                        before = scoring_rules()
                        obj.championship_points_config = validated
                        obj.updated_by = request.user
                        obj.save(update_fields=['championship_points_config', 'updated_by', 'updated_at'])
                        updated_events = apply_global_rules_to_existing_events(validated, request.user)
                        record_audit(user=request.user, action='Changed Championship Scoring Rules', module='Championship',
                                     request=request, description='Updated global championship rules and existing Major and Minor events.',
                                     metadata={'previous': before, 'updated': validated, 'updated_events': updated_events})
            except (DatabaseError, ScoringRulesError):
                return _render_settings(request, event, config,
                    'Unable to save championship settings. No configuration changes were applied. Please try again.', {})
            messages.success(request, 'Event scoring updated; authorized approval and republication are required.' if event
                             else f'Championship scoring rules saved. Updated {updated_events} existing Major and Minor events. Changed events require renewed approval and publication.')
            return redirect(reverse('admin_settings') + (f'?event={event.pk}' if event else ''))
    return _render_settings(request, event, config, error, field_errors)


def _render_settings(request, event, config, error, field_errors, schedule_form=None):
    if schedule_form is None:
        settings_row = SystemSettings.objects.filter(pk=1).first()
        schedule_form = ScheduleNavigationForm(initial={
            'intramurals_start_date': settings_row.intramurals_start_date if settings_row else None,
            'non_competition_dates': '\n'.join(settings_row.non_competition_dates or []) if settings_row else '',
        })
    return render(request, 'admindash/settings.html', {
        'schedule_form': schedule_form,
        'config': config, 'default_config': default_rules(), 'placements': config['placements'],
        'saved_config': ((event.result_processing_config or {}).get('championship_scoring') or scoring_rules()) if event else scoring_rules(),
        'error': error, 'field_errors': field_errors,
        'audit_rows': AuditLog.objects.filter(module='Championship').order_by('-created_at')[:10],
        'override_events': Event.objects.filter(scoring_method__in=['match', 'criteria']).order_by('name'),
        'override_event': event, 'placement_teams': event.bracket_teams.all() if event else [],
        'official_placements': (event.result_processing_config or {}).get('championship_placements', {}) if event else {},
        'override_url': reverse('admin_settings') + f'?event={event.pk}' if event else '',
        'override_reason': request.POST.get('reason', '') if request.method == 'POST' else '',
        'correction_selector': request.POST.get('correction_match', ''),
        'entered_placements': {str(team.pk): request.POST.get(f'placement_{team.pk}', '')
                               for team in event.bracket_teams.all()} if event and request.method == 'POST' else None,
        'correction_matches': event.bracket_matches.filter(is_automatic_advance=False, status__in=['completed', 'forfeit']) if event else [],
    }, status=400 if error else 200)
