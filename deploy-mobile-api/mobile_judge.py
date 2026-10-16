"""Read-only Flutter projections. Never synchronize legacy mobile IDs on GET."""
from collections import defaultdict
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .criteria_faculty_service import criteria_rounds, round_is_scoreable, round_participant_ids
from .faculty_service import FacultyServiceError
from .judging_views import IsJudgeUser
from .models import CriteriaScoreSubmission, Event, JudgeScore, JudgingEvent
from .public_service import image_url


def assigned_events(user):
    # Portal ownership is authoritative; legacy assignments apply only to unlinked events.
    return JudgingEvent.objects.filter(
        Q(portal_event__assigned_judges=user, portal_event__scoring_method='criteria')
        | Q(portal_event__isnull=True, assigned_judges=user),
    ).exclude(portal_event__status=Event.STATUS_INACTIVE).select_related('category', 'portal_event').distinct()


def portal_for(event):
    return getattr(event, 'portal_event', None)


def date_display(value):
    return value.strftime('%b %d, %Y') if value else ''


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
    mapping = (portal.result_processing_config or {}).get('mobile_round_mapping') or {}
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
    selected = next((row for row in rounds if row['id'] == requested_round), None) if requested_round else next(
        (row for row in rounds if row['can_score']), rounds[0])
    if selected is None:
        raise ValidationError({'round_id': 'Unknown round for this assignment.'})
    rid = selected['id']
    mobile = {row.pk: row for row in event.criteria.prefetch_related('subcriteria')}
    criteria, all_criteria = [], []
    ready = True
    if portal and portal.scoring_categories.exists():
        for category in portal.scoring_categories.filter(purpose='official').prefetch_related('criteria'):
            for definition in category.criteria.all():
                stored = mobile.get(mapping.get(f'{category.assigned_round_id}:{definition.pk}'))
                if stored:
                    payload = criterion_payload(stored, category.assigned_round_id, definition)
                    all_criteria.append(payload)
                    if category.assigned_round_id == rid:
                        criteria.append(payload)
                        expected_weight = category.overall_weight_percent * definition.weight_percent / 100
                        if stored.max_score != definition.max_score or abs(stored.weight_percent - expected_weight) > Decimal('0.05'):
                            ready = False
                elif category.assigned_round_id == rid:
                    ready = False
                if category.assigned_round_id == rid and category.judge_mode != 'scoring':
                    ready = False
    elif len(rounds) == 1:
        criteria = [criterion_payload(row, rid) for row in mobile.values()]
        all_criteria = list(criteria)
    else:
        ready = False
    eligible = None
    if portal and portal.scoring_categories.exists():
        try:
            eligible_entries = round_participant_ids(portal, rounds.index(selected))
        except FacultyServiceError:
            eligible_entries = []
            ready = False
        eligible = {candidates.get(str(entry)) for entry in eligible_entries}
        ready = ready and None not in eligible
    elif len(rounds) > 1:
        eligible = set()
    criterion_ids = {row['id'] for row in criteria}
    own_scores = list(JudgeScore.objects.filter(judge=user, candidate__event=event,
                                               criterion_id__in=criterion_ids))
    normalized_sheets = defaultdict(list)
    required = set()
    if portal and portal.scoring_categories.exists():
        required = set(portal.scoring_categories.filter(assigned_round_id=rid, purpose='official')
                       .values_list('criteria__pk', flat=True)) - {None}
        for submission in CriteriaScoreSubmission.objects.filter(
            event=portal, judge=user, assigned_round_id=rid, is_active=True,
            category__purpose='official', criterion_id__in=required,
        ).exclude(status=CriteriaScoreSubmission.STATUS_SUPERSEDED):
            mapped_id = candidates.get(str(submission.participant_id))
            if mapped_id is not None:
                normalized_sheets[mapped_id].append(submission)
    participants = []
    for candidate in event.candidates.all():
        qualified = eligible is None or candidate.pk in eligible
        scores = [score for score in own_scores if score.candidate_id == candidate.pk]
        complete = bool(criterion_ids) and {score.criterion_id for score in scores if score.is_locked} == criterion_ids
        state = 'not_qualified' if not qualified else 'submitted' if complete else 'draft' if scores else 'pending'
        if qualified and normalized_sheets[candidate.pk]:
            state = submission_state(normalized_sheets[candidate.pk], required)
            complete = state == 'submitted'
        participants.append({'id': candidate.pk, 'number': candidate.number, 'name': candidate.name,
                             'department': candidate.department, 'photo': image_url(candidate.photo),
                             'is_qualified': qualified, 'scoring_status': state,
                             'scoring_status_label': state.replace('_', ' ').title(), 'review_note': '',
                             'draft_step': 0, 'action_label': 'View Submission' if complete else 'Score'})
    total = sum(row['is_qualified'] for row in participants)
    scored = sum(row['is_qualified'] and row['scoring_status'] == 'submitted' for row in participants)
    status = {'active': 'ongoing'}.get(portal.status if portal else event.status, event.status)
    when = portal.event_date if portal else event.date
    clock = portal.event_time if portal else event.time
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
            'participant_count': total, 'participant_label': 'Participants',
            'participation_type': portal.participation_type if portal else 'individual',
            'criteria_count': len(all_criteria), 'criteria_names': [row['name'] for row in all_criteria],
            'total_points': 100, 'criteria': criteria, 'all_criteria': all_criteria, 'participants': participants,
            'stages': rounds, 'current_stage': selected, 'current_round': selected['name'], 'selected_round_id': rid,
            'can_score': bool(selected['can_score'] and ready and criteria and total
                              and event.assigned_judges.filter(pk=user.pk).exists()),
            'configuration_required': not ready,
            'scoring_method': portal.criteria_score_method if portal else 'weighted_percentage',
            'round_max_score': score_total({}, {row['id']: row for row in criteria},
                                           portal.criteria_score_method if portal else '')[1],
            'result_scope': 'round', 'contestants_total': total,
            'contestants_scored': scored, 'contestants_remaining': total - scored,
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


def history_payload(user):
    entries = []
    for event in assigned_events(user):
        rounds, _, _ = round_projection(event)
        for stage in rounds:
            detail = detail_payload(user, event, stage['id'])
            criteria = {row['id']: row for row in detail['criteria']}
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
                    state = 'submitted' if all(row.is_locked for row in scores) else 'draft'
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
                                'submitted_at_display': date_display(stamp)})
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
                if submission.assigned_round_id in stages and submission.participant_id in participants:
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
                definitions = {row.criterion_id: {'max_score': float(row.criterion.max_score or 0),
                    'weight_percent': float(row.category.overall_weight_percent * row.criterion.weight_percent / 100)} for row in sheet}
                total, maximum = score_total({row.criterion_id: float(row.raw_score) for row in sheet},
                                             definitions, portal.criteria_score_method)
                stamp = max((row.submitted_at for row in sheet if row.submitted_at), default=None)
                entries.append({'id': f'{event.pk}-entry-{participant_id}-{rid}', 'judging_event_id': event.pk,
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
                    'submitted_at_display': date_display(stamp)})
    return sorted(entries, key=lambda row: row['submitted_at'] or '', reverse=True)


@api_view(['GET'])
@permission_classes([IsJudgeUser])
def score_history(request):
    rows = history_payload(request.user)
    for param, lower in (('date_from', True), ('date_to', False)):
        raw = request.query_params.get(param)
        if raw:
            try:
                value = parse_date(raw)
            except ValueError:
                value = None
            if value is None:
                raise ValidationError({param: 'Use YYYY-MM-DD.'})
            rows = [row for row in rows if row['submitted_at'] and
                    ((row['submitted_at'][:10] >= value.isoformat()) if lower else
                     (row['submitted_at'][:10] <= value.isoformat()))]
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
    return Response({'greeting_name': request.user.first_name or request.user.username,
                     'assigned_events': rows, 'todays_assignments': [], 'upcoming_events': [],
                     'notification_count': 0, 'last_submission': None,
                     'stats': {'assigned': len(rows), 'pending': sum(row['contestants_remaining'] for row in rows),
                               'submitted': sum(row['contestants_scored'] for row in rows), 'draft': 0}})


@api_view(['GET'])
@permission_classes([IsJudgeUser])
def notifications(request):
    rows = [{'id': f'assignment-{event.pk}', 'icon': 'event_available', 'icon_color': '#0D7A62',
             'title': 'Event Assigned', 'body': f'You are assigned to judge {event.title}.',
             'time': event.date.isoformat(), 'is_unread': False}
            for event in assigned_events(request.user)]
    return Response(rows)
