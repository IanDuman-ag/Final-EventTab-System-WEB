"""Read-only Flutter projections. Never synchronize legacy mobile IDs on GET."""
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .criteria_faculty_service import criteria_rounds, round_is_scoreable, round_participant_ids
from .faculty_service import FacultyServiceError
from .judging_views import IsJudgeUser
from .models import CriteriaScoreSubmission, Event, EventScoringCriterion, JudgeScore, JudgingEvent
from .public_service import image_url
from .mobile_scoring_compatibility import legacy_score_is_submitted, read_mobile_mapping, round_mapping_issues


def assigned_events(user):
    # Portal ownership is authoritative; legacy assignments apply only to unlinked events.
    return JudgingEvent.objects.filter(
        Q(portal_event__assigned_judges=user, portal_event__scoring_method='criteria')
        | Q(portal_event__isnull=True, assigned_judges=user),
    ).exclude(portal_event__status=Event.STATUS_INACTIVE).select_related('category', 'portal_event').distinct()


def portal_for(event):
    return getattr(event, 'portal_event', None)


def date_display(value):
    if isinstance(value, datetime) and timezone.is_aware(value):
        value = timezone.localtime(value, timezone=ZoneInfo('Asia/Manila'))
    return value.strftime('%b %d, %Y') if value else ''


def entry_photo_urls(entry, legacy_photo=''):
    """Use explicit registry links; never guess a participant's photo by name."""
    if entry.entry_type == 'individual' and entry.source_individual_id:
        photos = [image_url(entry.source_individual.image)]
    elif entry.entry_type == 'team' and entry.source_team_id:
        photos = [image_url(entry.source_team.image)]
    elif entry.entry_type == 'pair':
        photos = [image_url(link.individual.image) for link in entry.member_links.all()]
    else:
        photos = []
    return [url for url in photos if url] or ([legacy_photo] if legacy_photo else [])


def round_projection(event):
    portal = portal_for(event)
    if portal is None:
        return [{'id': 'main', 'name': 'Main Round', 'status': 'open' if event.status == 'active' else 'locked',
                 'can_score': event.status == 'active'}], {}, {}
    rounds = criteria_rounds(portal)
    for index, row in enumerate(rounds):
        try:
            row['can_score'] = portal.status == Event.STATUS_ACTIVE and round_is_scoreable(portal, index)
        except (TypeError, ValueError):
            row['can_score'] = False
        row['status'] = row.get('status', 'open' if row['can_score'] else 'locked')
    mapping = read_mobile_mapping(portal, event)
    return rounds, mapping.get('criteria') or {}, mapping.get('candidates') or {}


def criterion_payload(criterion, round_id, definition=None):
    return {'id': criterion.pk, 'name': criterion.name, 'description': criterion.description,
            'max_score': float(criterion.max_score), 'min_score': float(definition.min_score) if definition else 0,
            'weight_percent': float(definition.category.overall_weight_percent * definition.weight_percent / 100)
                              if definition else float(criterion.weight_percent),
            'order': criterion.order, 'round_id': round_id, 'decimal_places': 2,
            'comment_enabled': False, 'comment_required': False,
            'subcriteria': [{'id': child.pk, 'name': child.name, 'max_score': float(child.max_score),
                             'display_order': child.display_order} for child in criterion.subcriteria.all()]}


def score_total(values, criteria, method):
    maximum = sum(row['max_score'] for row in criteria.values())
    if method == 'raw_score':
        return sum(values.values()), maximum
    if method == 'average_score':
        count = len(criteria)
        return (sum(values.values()) / count if count else 0), (maximum / count if count else 0)
    return sum(value * criteria[key]['weight_percent'] / criteria[key]['max_score']
               for key, value in values.items() if criteria[key]['max_score']), 100


def submission_state(sheet, required):
    statuses = {row.status for row in sheet}
    complete = bool(required) and {row.criterion_id for row in sheet} == required
    if statuses & {'REJECTED', 'REOPENED'}:
        return 'returned'
    if complete and statuses <= {'SUBMITTED', 'VERIFIED', 'APPROVED', 'NEEDS_REVIEW'}:
        return 'submitted'
    return 'draft'


def detail_payload(user, event, requested_round=None):
    portal = portal_for(event)
    rounds, mapping, candidates = round_projection(event)
    try:
        active_index = int((portal.result_processing_config or {}).get('active_stage_index') or 0) if portal else 0
    except (TypeError, ValueError):
        active_index = 0
    active_index = active_index if 0 <= active_index < len(rounds) else 0
    selected = next((row for row in rounds if row['id'] == requested_round), None) if requested_round else rounds[active_index]
    if selected is None:
        raise ValidationError({'round_id': 'Unknown round for this assignment.'})
    rid = selected['id']
    mobile = {row.pk: row for row in event.criteria.prefetch_related('subcriteria')}
    criteria, all_criteria = [], []
    ready = True
    configuration_issues = []
    normalized = bool(portal and portal.scoring_categories.exists())
    if normalized:
        for category in portal.scoring_categories.filter(purpose='official').prefetch_related('criteria__subcriteria'):
            for definition in category.criteria.all():
                stored = mobile.get(mapping.get(f'{category.assigned_round_id}:{definition.pk}'))
                # Definitions remain visible even when legacy mobile IDs have
                # never been provisioned. A null ID is never sent for scoring.
                payload = {'id': stored.pk if stored else None, 'normalized_criterion_id': definition.pk,
                    'name': definition.name, 'description': definition.judging_description,
                    'max_score': float(definition.max_score or 0), 'min_score': float(definition.min_score),
                    'weight_percent': float(category.overall_weight_percent * definition.weight_percent / 100),
                    'order': stored.order if stored else len(all_criteria) + 1,
                    'category_order': definition.display_order, 'round_id': category.assigned_round_id,
                    'segment_name': category.name, 'decimal_places': 2,
                    'comment_enabled': False, 'comment_required': False,
                    'subcriteria': [{'id': None, 'normalized_subcriterion_id': child.pk, 'name': child.name,
                                     'max_score': float(child.max_score), 'display_order': child.display_order}
                                    for child in definition.subcriteria.all()]}
                if stored:
                    children = {child.display_order: child for child in stored.subcriteria.all()}
                    for child in payload['subcriteria']:
                        legacy = children.get(child['display_order'])
                        if legacy and legacy.name == child['name'] and float(legacy.max_score) == child['max_score']:
                            child['id'] = legacy.pk
                all_criteria.append(payload)
                if category.assigned_round_id == rid:
                    criteria.append(payload)
        configuration_issues = round_mapping_issues(portal, event, rid,
            {'criteria': mapping, 'candidates': candidates})
        ready = not configuration_issues
    elif len(rounds) == 1:
        criteria = [criterion_payload(row, rid) for row in mobile.values()]
        all_criteria = list(criteria)
    else:
        ready = False
        configuration_issues.append('Round-specific official scoring categories must be configured for multi-round judging.')
    eligible = None
    if normalized:
        try:
            eligible_entries = round_participant_ids(portal, rounds.index(selected))
        except FacultyServiceError:
            eligible_entries = []
            ready = False
            configuration_issues.append('Faculty round participant eligibility contains duplicate or unknown entries.')
        eligible = set(eligible_entries)
        mobile_ids = [candidates.get(str(entry)) for entry in eligible]
        participants_ready = (len(set(mobile_ids)) == len(mobile_ids)
                 and set(portal.criteria_entries.filter(pk__in=eligible).values_list('pk', flat=True)) == eligible
                 and all(list(candidates.values()).count(value) == 1 for value in mobile_ids)
                 and all(value in {row.pk for row in event.candidates.all()} for value in mobile_ids))
        if not participants_ready:
            configuration_issues.append('Eligible participants have missing, ambiguous or invalid mobile candidate mappings.')
        ready = ready and participants_ready
    elif len(rounds) > 1:
        eligible = set()
    criterion_ids = {row['id'] for row in criteria if row['id'] is not None}
    own_scores = list(JudgeScore.objects.filter(judge=user, candidate__event=event,
                                               criterion_id__in=criterion_ids))
    normalized_sheets = defaultdict(list)
    required = set()
    if normalized:
        required = set(portal.scoring_categories.filter(assigned_round_id=rid, purpose='official')
                       .values_list('criteria__pk', flat=True)) - {None}
        for submission in CriteriaScoreSubmission.objects.filter(
            event=portal, judge=user, assigned_round_id=rid, is_active=True,
            category__event=portal, category__purpose='official', category__assigned_round_id=rid,
            criterion__category_id=F('category_id'), criterion_id__in=required,
        ).exclude(status=CriteriaScoreSubmission.STATUS_SUPERSEDED):
            normalized_sheets[submission.participant_id].append(submission)
    participants = []
    legacy_photos = {row.pk: image_url(row.photo) for row in event.candidates.all()}
    source = (portal.criteria_entries.filter(pk__in=portal.participant_ids)
              .select_related('department', 'source_individual', 'source_team')
              .prefetch_related('member_links__individual')) if normalized else event.candidates.all()
    for candidate in source:
        entry_id = candidate.pk if normalized else None
        mobile_id = candidates.get(str(entry_id)) if normalized else candidate.pk
        qualified = eligible is None or candidate.pk in eligible
        scores = [score for score in own_scores if score.candidate_id == mobile_id]
        complete = bool(criterion_ids) and {score.criterion_id for score in scores if legacy_score_is_submitted(score)} == criterion_ids
        state = 'not_qualified' if not qualified else 'submitted' if complete else 'draft' if scores else 'pending'
        if qualified and normalized_sheets[candidate.pk]:
            state = submission_state(normalized_sheets[candidate.pk], required)
            complete = state == 'submitted'
        photos = entry_photo_urls(candidate, legacy_photos.get(mobile_id, '')) if normalized else ([image_url(candidate.photo)] if candidate.photo else [])
        participants.append({'id': mobile_id, 'entry_id': entry_id,
                             'number': candidate.display_order + 1 if normalized else candidate.number,
                             'name': candidate.display_name if normalized else candidate.name,
                             'department': (candidate.department.name if candidate.department else '') if normalized else candidate.department,
                             'photo': photos[0] if photos else '', 'photos': photos,
                             'is_qualified': qualified, 'scoring_status': state,
                             'scoring_status_label': state.replace('_', ' ').title(), 'review_note': '',
                             'draft_step': 0, 'action_label': 'View Submission' if complete else 'Score'})
    total = sum(row['is_qualified'] for row in participants)
    scored = sum(row['is_qualified'] and row['scoring_status'] == 'submitted' for row in participants)
    source_status = portal.status if portal else event.status
    status = {'active': 'ongoing'}.get(source_status, source_status)
    when = portal.event_date if portal else event.date
    clock = portal.event_time if portal else event.time
    reasons = list(configuration_issues)
    if not user.is_active or not (portal.assigned_judges if portal else event.assigned_judges).filter(pk=user.pk).exists():
        reasons.append('You are not an active assigned judge for this event.')
    if (portal.status if portal else event.status) != 'active':
        reasons.append('The event is not active for judging.')
    if not selected['can_score'] and (portal is None or portal.status == Event.STATUS_ACTIVE):
        index = rounds.index(selected)
        config = (portal.result_processing_config or {}) if portal else {}
        confirmations = config.get('confirmed_stages') or {}
        if portal and portal.results_finalized:
            reasons.append('Official results are finalized; this event is read-only.')
        elif str(index) in confirmations:
            reasons.append('Faculty has confirmed this round; it is read-only.')
        elif portal and index != active_index:
            reasons.append('This is not the Faculty-selected active round.')
        elif selected.get('status') not in {'open', 'active'}:
            reasons.append(f'Round "{selected["name"]}" is {selected.get("status")}; only Faculty can open the round.')
        elif index > 0 and not confirmations.get(str(index - 1), {}).get('approved_at'):
            reasons.append('The previous round is awaiting Faculty approval before this round can open.')
        else:
            reasons.append('Faculty round configuration is invalid or its approval requirements are not satisfied.')
    if not criteria:
        reasons.append('No scoring criteria are available for this round.')
    if not total:
        reasons.append('No eligible participants are configured for this round.')
    return {'id': event.pk, 'judging_event_id': event.pk, 'portal_event_id': portal.pk if portal else None,
            'title': portal.name if portal else event.title, 'subtitle': portal.division if portal else '',
            'category': event.category.name, 'category_label': event.category.name.upper(),
            'category_icon': event.category.icon, 'assignment_type': 'CRITERIA BASED',
            'status': status, 'date': when.isoformat(), 'date_display': date_display(when),
            'time_display': clock.strftime('%I:%M %p') if clock else '',
            'venue': portal.venue if portal else event.venue, 'image_url': image_url(portal.image) if portal else '',
            'description': portal.mechanics if portal else event.description,
            'instructions': portal.rules_guidelines if portal else event.description,
            'faculty_in_charge': portal.faculty_in_charge if portal else '',
            'participant_count': total,
            'participant_label': {'team': 'Teams', 'pair': 'Pairs', 'group': 'Groups'}.get(portal.participation_type, 'Participants') if portal else 'Participants',
            'participation_type': portal.participation_type if portal else 'individual',
            'criteria_count': len(all_criteria), 'criteria_names': [row['name'] for row in all_criteria],
            'total_points': 100, 'criteria': criteria, 'all_criteria': all_criteria, 'participants': participants,
            'stages': rounds, 'current_stage': selected, 'current_round': selected['name'], 'selected_round_id': rid,
            'can_score': not reasons,
            'scoring_unavailable_reasons': reasons, 'scoring_unavailable_reason': ' '.join(reasons),
            'configuration_required': not ready,
            'scoring_method': portal.criteria_score_method if portal else 'weighted_percentage',
            'round_max_score': score_total({}, {index: row for index, row in enumerate(criteria)},
                                           portal.criteria_score_method if portal else '')[1],
            'result_scope': 'round', 'contestants_total': total,
            'contestants_scored': scored, 'contestants_remaining': total - scored,
            'participants_total': total, 'participants_scored': scored,
            'scoring_progress': round(scored / total * 100) if total else 0,
            'round_scoring_complete': total > 0 and scored == total,
            'action_label': 'Open Event', 'event_status_label': status.title(),
            'assignment': {'role': 'JUDGE', 'role_detail': 'Criteria-Based Judge',
                           'judge_id': f'JDG-{user.pk:04d}', 'assigned_at': None, 'assigned_at_display': ''}}


@api_view(['GET'])
@permission_classes([IsJudgeUser])
def assignments(request):
    rows = [detail_payload(request.user, event) for event in assigned_events(request.user)]
    counts = {key: sum(row['status'] == key for row in rows) for key in ('upcoming', 'ongoing', 'completed')}
    selected = request.query_params.get('status')
    if selected in counts:
        rows = [row for row in rows if row['status'] == selected]
    return Response({'assignments': rows, 'counts': counts,
                     'today_display': date_display(timezone.localdate(timezone=ZoneInfo('Asia/Manila')))})


@api_view(['GET'])
@permission_classes([IsJudgeUser])
def assignment_detail(request, judging_event_id):
    event = get_object_or_404(assigned_events(request.user), pk=judging_event_id)
    return Response(detail_payload(request.user, event, request.query_params.get('round_id')))


@api_view(['GET'])
@permission_classes([IsJudgeUser])
def portal_assignment_detail(request, portal_event_id):
    # An explicit portal route prevents numeric ID collisions with JudgingEvent.
    event = get_object_or_404(assigned_events(request.user), portal_event__pk=portal_event_id)
    return Response(detail_payload(request.user, event, request.query_params.get('round_id')))


def history_payload(user):
    entries = []
    for event in assigned_events(user):
        rounds, _, _ = round_projection(event)
        for stage in rounds:
            detail = detail_payload(user, event, stage['id'])
            criteria = {row['id']: row for row in detail['criteria'] if row['id'] is not None}
            grouped = defaultdict(list)
            for score in JudgeScore.objects.filter(judge=user, candidate__event=event, criterion_id__in=criteria):
                grouped[score.candidate_id].append(score)
            for candidate in detail['participants']:
                scores = grouped[candidate['id']]
                if not scores:
                    continue
                stamp = max((row.submitted_at for row in scores if row.submitted_at), default=None)
                state = candidate['scoring_status']
                if state == 'not_qualified':
                    state = 'submitted' if all(legacy_score_is_submitted(row) for row in scores) else 'draft'
                total, maximum = score_total({row.criterion_id: float(row.score) for row in scores},
                                             criteria, detail['scoring_method'])
                entries.append({'id': f'{event.pk}-{candidate["id"]}-{stage["id"]}',
                                'judging_event_id': event.pk, 'candidate_id': candidate['id'],
                                'candidate_name': candidate['name'], 'candidate_number': candidate['number'],
                                'subject_name': candidate['name'], 'subject_type': 'Participant',
                                'department': candidate['department'], 'photo': candidate['photo'],
                                'title': detail['title'], 'category_label': detail['category_label'],
                                'category_icon': detail['category_icon'], 'round_id': stage['id'],
                                'current_round': stage['name'], 'result_scope': 'round', 'is_official': False,
                                'date_display': detail['date_display'], 'time_display': detail['time_display'],
                                'venue': detail['venue'], 'criteria_count': len(scores), 'score': round(total, 2),
                                'max_score': maximum, 'status': state, 'scoring_status': state,
                                'status_label': state.title(), 'review_status': 'pending',
                                'verification_id': scores[0].verification_id,
                                'submitted_at': stamp.isoformat() if stamp else None,
                                'submitted_at_display': date_display(stamp),
                                'scoring_method': detail['scoring_method'],
                                'receipt_criteria': list(criteria.values()),
                                'receipt_scores': [{'criterion': row.criterion_id, 'score': float(row.score),
                                    'submitted_at': row.submitted_at.isoformat() if row.submitted_at else None,
                                    'verification_id': row.verification_id} for row in scores]})
        portal = portal_for(event)
        if portal and portal.scoring_categories.exists():
            # Normalized submissions are authoritative when present; do not double
            # count their bridged legacy cells or include superseded submissions.
            _, _, candidate_mapping = round_projection(event)
            participants = {row.pk: row for row in portal.criteria_entries.select_related('department')}
            stages = {row['id']: row for row in rounds}
            grouped = defaultdict(list)
            submissions = CriteriaScoreSubmission.objects.filter(
                event=portal, judge=user, is_active=True, category__event=portal,
                category__purpose='official',
            ).exclude(status=CriteriaScoreSubmission.STATUS_SUPERSEDED).select_related('criterion', 'category')
            for submission in submissions:
                if (submission.assigned_round_id in stages and submission.participant_id in participants
                        and submission.criterion.category_id == submission.category_id
                        and submission.category.assigned_round_id == submission.assigned_round_id):
                    grouped[(submission.assigned_round_id, submission.participant_id)].append(submission)
            for (rid, participant_id), sheet in grouped.items():
                participant = participants[participant_id]
                mobile_id = candidate_mapping.get(str(participant_id))
                entries = [row for row in entries if not (row['judging_event_id'] == event.pk
                           and row['round_id'] == rid and row['candidate_id'] == mobile_id)]
                status_values = {row.status for row in sheet}
                required = set(portal.scoring_categories.filter(assigned_round_id=rid, purpose='official')
                               .values_list('criteria__pk', flat=True)) - {None}
                state = submission_state(sheet, required)
                definitions = {}
                # Use the full round denominator even for partial draft sheets.
                for index, definition in enumerate(EventScoringCriterion.objects.filter(category__event=portal,
                        category__assigned_round_id=rid, category__purpose='official').select_related('category')):
                    definitions[definition.pk] = {'id': definition.pk, 'name': definition.name, 'order': index,
                        'max_score': float(definition.max_score or 0),
                        'weight_percent': float(definition.category.overall_weight_percent * definition.weight_percent / 100)}
                total, maximum = score_total({row.criterion_id: float(row.raw_score) for row in sheet},
                                             definitions, portal.criteria_score_method)
                stamp = max((row.submitted_at for row in sheet if row.submitted_at), default=None)
                entries.append({'id': f'{event.pk}-entry-{participant_id}-{rid}', 'judging_event_id': event.pk,
                    'entry_id': participant_id,
                    'candidate_id': mobile_id, 'candidate_name': participant.display_name,
                    'candidate_number': participant.display_order + 1, 'subject_name': participant.display_name,
                    'subject_type': 'Participant', 'department': participant.department.name if participant.department else '',
                    'photo': '', 'title': portal.name, 'category_label': event.category.name.upper(),
                    'category_icon': event.category.icon, 'round_id': rid, 'current_round': stages[rid]['name'],
                    'result_scope': 'round', 'is_official': False, 'date_display': date_display(portal.event_date),
                    'time_display': portal.event_time.strftime('%I:%M %p') if portal.event_time else '',
                    'venue': portal.venue, 'criteria_count': len(sheet), 'score': round(total, 2), 'max_score': maximum,
                    'status': state, 'scoring_status': state, 'status_label': state.title(),
                    'review_status': 'approved' if status_values == {'APPROVED'} else 'rejected' if 'REJECTED' in status_values else 'pending',
                    'verification_id': sheet[0].submission_key, 'submitted_at': stamp.isoformat() if stamp else None,
                    'submitted_at_display': date_display(stamp or max(row.updated_at for row in sheet)),
                    'updated_at': max(row.updated_at for row in sheet).isoformat(),
                    'scoring_method': portal.criteria_score_method,
                    'receipt_criteria': list(definitions.values()),
                    'receipt_scores': [{'criterion': row.criterion_id, 'score': float(row.raw_score),
                        'submitted_at': row.submitted_at.isoformat() if row.submitted_at else None,
                        'verification_id': row.submission_key} for row in sheet]})
    return sorted(entries, key=lambda row: row['submitted_at'] or row.get('updated_at') or '', reverse=True)


@api_view(['GET'])
@permission_classes([IsJudgeUser])
def score_history(request):
    rows = history_payload(request.user)
    def record_date(row):
        raw = row['submitted_at'] or row.get('updated_at')
        stamp = parse_datetime(raw) if raw else None
        if stamp and timezone.is_aware(stamp):
            return timezone.localdate(stamp, timezone=ZoneInfo('Asia/Manila'))
        return stamp.date() if stamp else None

    for param, lower in (('date_from', True), ('date_to', False)):
        raw = request.query_params.get(param)
        if raw:
            try:
                value = parse_date(raw)
            except ValueError:
                value = None
            if value is None:
                raise ValidationError({param: 'Use YYYY-MM-DD.'})
            rows = [row for row in rows if record_date(row) is not None and
                    ((record_date(row) >= value) if lower else (record_date(row) <= value))]
    counts = {key: sum(row['status'] == key for row in rows) for key in ('draft', 'submitted', 'returned')}
    counts['all'] = len(rows)
    selected = request.query_params.get('status')
    if selected in counts and selected != 'all':
        rows = [row for row in rows if row['status'] == selected]
    return Response({'entries': rows, 'counts': counts})


@api_view(['GET'])
@permission_classes([IsJudgeUser])
def profile(request):
    user = request.user
    rows = history_payload(user)
    events = list(assigned_events(user))
    today = timezone.localdate(timezone=ZoneInfo('Asia/Manila'))
    return Response({'display_name': user.get_full_name() or user.username, 'username': user.username,
                     'email': user.email, 'judge_id': f'JDG-{user.pk:04d}', 'role': 'JUDGE',
                     'role_detail': 'Criteria-Based Judge', 'status': 'ACTIVE' if user.is_active else 'INACTIVE',
                     'member_since': user.date_joined.date().isoformat(),
                     'member_since_display': date_display(user.date_joined),
                     'stats': {'assignments': len(events), 'completed': sum(row['status'] == 'submitted' for row in rows),
                               'pending': sum(row['status'] == 'draft' for row in rows),
                               'events_this_month': sum(event.date.year == today.year and event.date.month == today.month for event in events)}})


@api_view(['GET'])
@permission_classes([IsJudgeUser])
def dashboard(request):
    rows = [detail_payload(request.user, event) for event in assigned_events(request.user)]
    history = history_payload(request.user)
    return Response({'greeting_name': request.user.first_name or request.user.username,
                     'assigned_events': rows, 'todays_assignments': [], 'upcoming_events': [],
                     'notification_count': 0, 'last_submission': next((row for row in history if row['status'] == 'submitted'), None),
                     'stats': {'assigned': len(rows), 'pending': sum(row['contestants_remaining'] for row in rows),
                               'submitted': sum(row['status'] == 'submitted' for row in history),
                               'draft': sum(row['status'] == 'draft' for row in history)}})


@api_view(['GET'])
@permission_classes([IsJudgeUser])
def notifications(request):
    rows = [{'id': f'assignment-{event.pk}', 'icon': 'event_available', 'icon_color': '#0D7A62',
             'title': 'Event Assigned', 'body': f'You are assigned to judge {event.title}.',
             'time': event.date.isoformat(), 'is_unread': False}
            for event in assigned_events(request.user)]
    return Response(rows)
