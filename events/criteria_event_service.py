"""Transactional validation and persistence for criteria-based events."""
import json
from datetime import date

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Event, RegistryCandidate, Team
from .mobile_sync import sync_event_to_mobile


class CriteriaEventValidationError(ValueError):
    pass


ALLOWED_CATEGORIES = {
    'SOCIO-CULTURAL & LITERARY ARTS COMPETITION',
    'Special Event',
}
ALLOWED_CLASSIFICATIONS = {'major', 'minor'}
ALLOWED_DIVISIONS = {'Men', 'Women', 'Mixed', 'Open', ''}
ALLOWED_PARTICIPATION = {'team', 'individual'}
ALLOWED_FORMATS = {'single_performance', 'multiple_stage'}
LEGACY_FORMATS = {'multiple_rounds', 'preliminary_final'}
ALLOWED_SCORE_METHODS = {
    'weighted_percentage',
    'raw_score',
    'average_score',
    'ranking_based',
}
ALLOWED_QUALIFICATION_METHODS = {
    'top_ranking',
    'minimum_score',
    'manual_selection',
}
STAGE_STATUSES = {
    'locked',
    'open',
    'ongoing',
    'waiting_confirmation',
    'completed',
}
DEFAULT_POINTS_MAJOR = [
    {'label': '1st Place', 'points': 15},
    {'label': '2nd Place', 'points': 10},
    {'label': '3rd Place', 'points': 7},
    {'label': '4th Place', 'points': 5},
]
DEFAULT_POINTS_MINOR = [
    {'label': '1st Place', 'points': 10},
    {'label': '2nd Place', 'points': 7},
    {'label': '3rd Place', 'points': 5},
    {'label': '4th Place', 'points': 3},
]
DEFAULT_SCORE_SETTINGS = {
    'min_score': 0,
    'max_score': 100,
    'allow_decimal': True,
    'decimal_places': 2,
    'show_criteria_description': True,
}
DEFAULT_RESULT_PROCESSING = {
    'auto_calculate_final': True,
    'auto_generate_rankings': True,
    'allow_result_editing': True,
    'require_faculty_confirmation': False,
    'display_score_breakdown': True,
    'stage_tiebreak_method': 'highest_selected_criterion',
}
DEFAULT_JUDGE_SETTINGS = {
    'require_all_judges': True,
    'allow_edit_before_submit': True,
    'remove_high_low': False,
}
TIE_BREAK_OPTIONS = {
    'highest_selected_criterion',
    'highest_chief_judge',
    'lowest_deduction',
    'majority_decision',  # legacy
    'manual_decision',
}


def models_q_for_judges():
    return Q(groups__name__iexact='Judge') | Q(groups__name__iexact='Judges')


def models_q_for_faculty():
    """Active Faculty portal accounts only (Tabulator / Faculty groups)."""
    return (
        Q(groups__name__iexact='Tabulator')
        | Q(groups__name__iexact='Faculty')
    )


def _required(data, key, label):
    value = (data.get(key) or '').strip()
    if not value:
        raise CriteriaEventValidationError(f'{label} is required.')
    return value


def _parse_date(value, label):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise CriteriaEventValidationError(f'{label} must be a valid date.') from exc


def _json_list(value, label):
    try:
        parsed = json.loads(value or '[]')
    except (TypeError, json.JSONDecodeError) as exc:
        raise CriteriaEventValidationError(f'{label} is invalid.') from exc
    if not isinstance(parsed, list):
        raise CriteriaEventValidationError(f'{label} is invalid.')
    return parsed


def _json_dict(value, label):
    try:
        parsed = json.loads(value or '{}')
    except (TypeError, json.JSONDecodeError) as exc:
        raise CriteriaEventValidationError(f'{label} is invalid.') from exc
    if not isinstance(parsed, dict):
        raise CriteriaEventValidationError(f'{label} is invalid.')
    return parsed


def _boolish(data, key, default=False):
    raw = data.get(key)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {'1', 'true', 'on', 'yes'}


def _percent_total(values, label, required=True):
    total = round(sum(values), 2)
    if required and abs(total - 100) > 0.01:
        raise CriteriaEventValidationError(f'{label} must total 100% (currently {total}%).')
    return total


def _validated_points(data, classification):
    rows = _json_list(data.get('points_config'), 'Championship points configuration')
    if not rows:
        rows = DEFAULT_POINTS_MAJOR if classification == 'major' else DEFAULT_POINTS_MINOR
    cleaned = []
    labels = set()
    for row in rows:
        label = str(row.get('label', '')).strip()
        try:
            points = int(row.get('points'))
        except (TypeError, ValueError) as exc:
            raise CriteriaEventValidationError('Every championship points row needs a numeric value.') from exc
        if not label or points < 0:
            raise CriteriaEventValidationError('Championship point labels are required and values cannot be negative.')
        key = label.lower()
        if key in labels:
            raise CriteriaEventValidationError('Championship point rankings must be unique.')
        labels.add(key)
        cleaned.append({'label': label[:50], 'points': points})
    return cleaned


def _validated_criteria(data):
    rows = _json_list(data.get('judging_criteria_config'), 'Judging criteria')
    if not rows:
        raise CriteriaEventValidationError('Create at least one judging criterion.')
    cleaned = []
    weights = []
    for index, row in enumerate(rows):
        name = str(row.get('name', '')).strip()
        description = str(row.get('description', '')).strip()
        try:
            weight = float(row.get('weight'))
            max_score = float(row.get('max_score'))
        except (TypeError, ValueError) as exc:
            raise CriteriaEventValidationError('Each criterion needs a numeric weight and maximum score.') from exc
        if not name:
            raise CriteriaEventValidationError('Each criterion needs a name.')
        if weight <= 0 or max_score <= 0:
            raise CriteriaEventValidationError('Criterion weight and maximum score must be greater than zero.')
        cleaned.append({
            'id': str(row.get('id') or f'c{index + 1}'),
            'name': name[:120],
            'description': description[:500],
            'weight': weight,
            'max_score': max_score,
            'order': index + 1,
        })
        weights.append(weight)
    _percent_total(weights, 'Total criteria weight')
    return cleaned


def _validated_rounds(event_format, data, participant_count=0):
    if event_format == 'single_performance':
        return [{
            'stage_number': 1,
            'name': 'Performance',
            'weight': 100,
            'qualification_method': None,
            'qualifiers': None,
            'minimum_score': None,
            'carry_previous_scores': False,
            'require_faculty_confirmation': False,
            'is_final': True,
            'status': 'open',
        }]

    if event_format in LEGACY_FORMATS:
        event_format = 'multiple_stage'

    if event_format != 'multiple_stage':
        raise CriteriaEventValidationError('Select a valid event format.')

    rows = _json_list(data.get('rounds_config'), 'Competition stage configuration')
    if len(rows) < 2:
        raise CriteriaEventValidationError('Multiple Stage Competition requires at least two stages.')

    cleaned = []
    weights = []
    names = set()
    for index, row in enumerate(rows):
        is_final = index == len(rows) - 1 or bool(row.get('is_final'))
        name = str(row.get('name', '')).strip()
        if not name:
            raise CriteriaEventValidationError('Each stage needs a custom name.')
        if name.lower().startswith('round ') and name[6:].strip().isdigit():
            raise CriteriaEventValidationError(
                'Use a descriptive stage name instead of generic Round labels.'
            )
        key = name.lower()
        if key in names:
            raise CriteriaEventValidationError('Each stage name must be unique.')
        names.add(key)
        try:
            weight = float(row.get('weight'))
        except (TypeError, ValueError) as exc:
            raise CriteriaEventValidationError('Each stage needs a numeric weight.') from exc
        if weight <= 0:
            raise CriteriaEventValidationError('Stage weights must be greater than zero.')

        qualification_method = None
        qualifiers = None
        minimum_score = None
        require_confirmation = False
        if not is_final:
            qualification_method = str(row.get('qualification_method') or 'top_ranking').strip().lower()
            if qualification_method not in ALLOWED_QUALIFICATION_METHODS:
                raise CriteriaEventValidationError(
                    'Qualification method must be Top Ranking, Minimum Score, or Manual Selection.'
                )
            try:
                qualifiers = int(row.get('qualifiers') or 0)
            except (TypeError, ValueError) as exc:
                raise CriteriaEventValidationError('Each qualification stage needs a qualifier count.') from exc
            if qualifiers < 1:
                raise CriteriaEventValidationError('Each qualification stage needs at least one qualifier.')
            if participant_count and qualifiers > participant_count:
                raise CriteriaEventValidationError(
                    'Number of qualifiers cannot exceed the number of contestants.'
                )
            if qualification_method == 'minimum_score':
                try:
                    minimum_score = float(row.get('minimum_score') or 0)
                except (TypeError, ValueError) as exc:
                    raise CriteriaEventValidationError('Minimum score must be numeric.') from exc
                if minimum_score < 0:
                    raise CriteriaEventValidationError('Minimum score cannot be negative.')
            require_confirmation = bool(row.get('require_faculty_confirmation', True))

        carry = False if index == 0 else bool(row.get('carry_previous_scores', True))
        status = 'open' if index == 0 else 'locked'
        existing_status = str(row.get('status') or '').strip().lower()
        if existing_status in STAGE_STATUSES and index == 0:
            status = existing_status if existing_status != 'locked' else 'open'

        cleaned.append({
            'stage_number': index + 1,
            'name': name[:80],
            'weight': weight,
            'qualification_method': qualification_method,
            'qualifiers': qualifiers,
            'minimum_score': minimum_score,
            'carry_previous_scores': carry,
            'require_faculty_confirmation': require_confirmation,
            'is_final': is_final,
            'status': status,
        })
        weights.append(weight)

    _percent_total(weights, 'Total stage weight')
    return cleaned


def _validated_participants(participation_type, data):
    raw_ids = _json_list(data.get('participant_ids'), 'Participants')
    try:
        ids = list(dict.fromkeys(int(value) for value in raw_ids))
    except (TypeError, ValueError) as exc:
        raise CriteriaEventValidationError('Participants contain an invalid selection.') from exc
    if not ids:
        raise CriteriaEventValidationError('Select at least one participant or team.')
    if participation_type == 'individual':
        found = list(RegistryCandidate.objects.filter(id__in=ids, status=RegistryCandidate.STATUS_ACTIVE))
        by_id = {item.id: item for item in found}
        if len(by_id) != len(ids):
            raise CriteriaEventValidationError('One or more selected individuals are unavailable.')
        # one participant per entry already represented by unique candidate IDs
        return ids, [by_id[i] for i in ids]
    found = list(Team.objects.filter(id__in=ids, status=Team.STATUS_ACTIVE).select_related('department'))
    by_id = {item.id: item for item in found}
    if len(by_id) != len(ids):
        raise CriteriaEventValidationError('One or more selected teams are unavailable.')
    return ids, [by_id[i] for i in ids]


def _validated_score_settings(data):
    settings = {**DEFAULT_SCORE_SETTINGS, **_json_dict(data.get('score_settings'), 'Score settings')}
    try:
        min_score = float(settings.get('min_score', 0))
        max_score = float(settings.get('max_score', 100))
        decimal_places = int(settings.get('decimal_places', 2))
    except (TypeError, ValueError) as exc:
        raise CriteriaEventValidationError('Score settings must be numeric.') from exc
    if min_score >= max_score:
        raise CriteriaEventValidationError('Minimum score must be less than maximum score.')
    if decimal_places not in (0, 1, 2):
        raise CriteriaEventValidationError('Decimal places must be 0, 1, or 2.')
    return {
        'min_score': min_score,
        'max_score': max_score,
        'allow_decimal': bool(settings.get('allow_decimal', True)),
        'decimal_places': decimal_places,
        'show_criteria_description': bool(settings.get('show_criteria_description', True)),
    }


def _validated_deductions(data):
    enabled = _boolish(data, 'deductions_enabled')
    if not enabled:
        return False, []
    rows = _json_list(data.get('deductions_config'), 'Deductions configuration')
    cleaned = []
    for row in rows:
        name = str(row.get('name', '')).strip()
        description = str(row.get('description', '')).strip()
        dtype = str(row.get('deduction_type', 'fixed')).strip().lower()
        if dtype not in {'fixed', 'percentage', 'fixed_points'}:
            raise CriteriaEventValidationError('Deduction type must be Fixed Points or Percentage.')
        if dtype == 'fixed_points':
            dtype = 'fixed'
        try:
            value = float(row.get('value'))
        except (TypeError, ValueError) as exc:
            raise CriteriaEventValidationError('Each deduction needs a numeric value.') from exc
        if not name or value < 0:
            raise CriteriaEventValidationError('Each deduction needs a name and non-negative value.')
        cleaned.append({
            'name': name[:80],
            'description': description[:300],
            'deduction_type': dtype,
            'value': value,
        })
    return True, cleaned


def _validated_users(data, user_model):
    chief_raw = (data.get('chief_judge') or '').strip()
    faculty_raw = (data.get('faculty_account') or '').strip()
    judge_ids = _json_list(data.get('judge_ids'), 'Assigned judges')
    try:
        judge_ids = list(dict.fromkeys(int(value) for value in judge_ids))
    except (TypeError, ValueError) as exc:
        raise CriteriaEventValidationError('Assigned judges contain an invalid selection.') from exc
    if not judge_ids:
        raise CriteriaEventValidationError('Assign at least one judge.')
    judges = list(user_model.objects.filter(id__in=judge_ids, is_active=True).filter(models_q_for_judges()).distinct())
    by_id = {user.id: user for user in judges}
    if len(by_id) != len(judge_ids):
        raise CriteriaEventValidationError('One or more selected judges are not authorized.')
    ordered_judges = [by_id[i] for i in judge_ids]

    if not chief_raw.isdigit():
        raise CriteriaEventValidationError('Chief Judge is required.')
    chief = user_model.objects.filter(pk=int(chief_raw), is_active=True).filter(models_q_for_judges()).distinct().first()
    if not chief:
        raise CriteriaEventValidationError('The selected Chief Judge is not authorized.')

    if not faculty_raw.isdigit():
        raise CriteriaEventValidationError('Faculty In Charge is required.')
    faculty = user_model.objects.filter(pk=int(faculty_raw), is_active=True).filter(models_q_for_faculty()).distinct().first()
    if not faculty:
        raise CriteriaEventValidationError('The selected Faculty In Charge is not authorized.')
    return chief, ordered_judges, faculty


def _validated_tie_breaks(data, criteria):
    rules = _json_list(data.get('tie_break_rules'), 'Tie-breaking rules')
    if not rules:
        rules = [
            {'method': 'highest_selected_criterion', 'criterion_id': criteria[0]['id']},
            {'method': 'highest_chief_judge'},
            {'method': 'manual_decision'},
        ]
    cleaned = []
    for row in rules:
        method = str(row.get('method', '')).strip()
        if method not in TIE_BREAK_OPTIONS:
            raise CriteriaEventValidationError('Tie-breaking methods contain an unsupported option.')
        entry = {'method': method}
        if method == 'highest_selected_criterion':
            criterion_id = str(row.get('criterion_id') or '').strip()
            if criterion_id not in {item['id'] for item in criteria}:
                criterion_id = criteria[0]['id']
            entry['criterion_id'] = criterion_id
        cleaned.append(entry)
    return cleaned


def _notify_assignees(event, recipients):
    """Email assigned faculty/judges using their stored Gmail addresses."""
    from django.conf import settings

    subject = f'Assigned to EventTab event: {event.name}'
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None) or None
    for user in recipients:
        email = (user.email or '').strip()
        if not email:
            continue
        body = (
            f'Hello {user.get_full_name() or user.username},\n\n'
            f'You have been assigned to the criteria-based event "{event.name}".\n'
            f'Category: {event.category}\n'
            f'Dates: {event.event_date} to {event.end_date or event.event_date}\n'
            f'Venue: {event.venue}\n\n'
            f'Sign in to EventTab to review your assignment.\n\n'
            f'— EventTab Admin'
        )
        try:
            send_mail(subject, body, from_email, [email], fail_silently=True)
        except Exception:
            continue


@transaction.atomic
def save_criteria_event(data, user, files=None, instance=None):
    User = get_user_model()
    files = files or {}
    name = _required(data, 'event_name', 'Event Name')[:200]
    category = _required(data, 'category', 'Category')
    if category not in ALLOWED_CATEGORIES:
        raise CriteriaEventValidationError('Select a valid category.')
    classification = _required(data, 'event_classification', 'Event Classification')
    if classification not in ALLOWED_CLASSIFICATIONS:
        raise CriteriaEventValidationError('Select Major Event or Minor Event.')
    division = (data.get('division') or '').strip()
    if division not in ALLOWED_DIVISIONS:
        raise CriteriaEventValidationError('Select a valid division.')
    participation_type = _required(data, 'participation_type', 'Participation Type')
    if participation_type not in ALLOWED_PARTICIPATION:
        raise CriteriaEventValidationError('Participation Type must be Team or Individual.')
    venue = _required(data, 'venue', 'Venue')[:200]
    start_date = _parse_date(_required(data, 'start_date', 'Start Date'), 'Start Date')
    end_date = _parse_date(_required(data, 'end_date', 'End Date'), 'End Date')
    if end_date < start_date:
        raise CriteriaEventValidationError('End Date cannot precede Start Date.')
    publication = (data.get('publication_status') or Event.PUBLICATION_DRAFT).strip().lower()
    if publication not in dict(Event.PUBLICATION_CHOICES):
        raise CriteriaEventValidationError('Status must be Draft or Published.')
    event_format = _required(data, 'event_format', 'Event Format')
    if event_format in LEGACY_FORMATS:
        event_format = 'multiple_stage'
    if event_format not in ALLOWED_FORMATS:
        raise CriteriaEventValidationError('Select a valid event format.')
    score_method = _required(data, 'criteria_score_method', 'Scoring Method')
    if score_method not in ALLOWED_SCORE_METHODS:
        raise CriteriaEventValidationError('Select a valid scoring method.')

    participant_ids, participants = _validated_participants(participation_type, data)
    rounds_config = _validated_rounds(event_format, data, participant_count=len(participant_ids))
    criteria = _validated_criteria(data)
    score_settings = _validated_score_settings(data)
    deductions_enabled, deductions = _validated_deductions(data)
    chief, judges, faculty = _validated_users(data, User)
    result_processing = {**DEFAULT_RESULT_PROCESSING, **_json_dict(data.get('result_processing_config'), 'Result processing')}
    stage_tiebreak = str(result_processing.get('stage_tiebreak_method') or 'highest_selected_criterion').strip()
    if stage_tiebreak not in TIE_BREAK_OPTIONS:
        raise CriteriaEventValidationError('Select a valid stage tie-breaking method.')
    result_processing['stage_tiebreak_method'] = stage_tiebreak
    if event_format == 'multiple_stage':
        # Multi-stage events always need faculty confirmation gates where configured.
        result_processing['multi_stage'] = True
        result_processing['active_stage_index'] = 0
        result_processing.setdefault('confirmed_stages', {})
        result_processing.setdefault('qualified_participant_ids', {
            '0': list(participant_ids),
        })
    judge_settings = {**DEFAULT_JUDGE_SETTINGS, **_json_dict(data.get('judge_settings'), 'Judge settings')}
    if judge_settings.get('remove_high_low') and len(judges) < 3:
        raise CriteriaEventValidationError(
            'Remove Highest and Lowest Judge Scores requires at least three assigned judges.'
        )
    tie_breaks = _validated_tie_breaks(data, criteria)
    points = _validated_points(data, classification)

    creating = instance is None
    event = instance or Event()
    if instance and instance.results_finalized and not user.is_superuser:
        raise CriteriaEventValidationError('Finalized events cannot be edited without administrator authorization.')

    event.name = name
    event.category = category
    event.event_classification = classification
    event.division = division
    event.venue = venue
    event.event_date = start_date
    event.end_date = end_date
    event.publication_status = publication
    event.status = Event.STATUS_UPCOMING if start_date > timezone.localdate() else Event.STATUS_ACTIVE
    event.scoring_method = 'criteria'
    event.participation_type = participation_type
    event.event_format = event_format
    event.criteria_score_method = score_method
    event.rounds_config = rounds_config
    event.judging_criteria_config = criteria
    event.score_settings = score_settings
    event.deductions_enabled = deductions_enabled
    event.deductions_config = deductions
    event.result_processing_config = result_processing
    event.judge_settings = judge_settings
    event.tie_break_rules = tie_breaks
    event.participant_ids = participant_ids
    event.chief_judge = chief
    event.faculty_account = faculty
    event.faculty_in_charge = faculty.get_full_name().strip() or faculty.username
    raw_tpl = (data.get('scoresheet_template') or '').strip()
    if raw_tpl:
        from .models import ScoresheetTemplate
        tpl = ScoresheetTemplate.objects.filter(
            pk=raw_tpl,
            event_type=ScoresheetTemplate.EVENT_CRITERIA,
            status=ScoresheetTemplate.STATUS_ACTIVE,
        ).first()
        event.scoresheet_template = tpl
    else:
        event.scoresheet_template = None
    event.apply_championship_points = _boolish(data, 'apply_championship_points', True)
    event.championship_points_config = points
    event.allow_result_editing = bool(result_processing.get('allow_result_editing', True))
    event.require_faculty_confirmation = bool(result_processing.get('require_faculty_confirmation', False))
    if creating:
        event.created_by = user
    event.save()
    event.assigned_judges.set(judges)

    LogEntry.objects.create(
        user=user,
        content_type=ContentType.objects.get_for_model(Event),
        object_id=str(event.pk),
        object_repr=event.name,
        action_flag=ADDITION if creating else CHANGE,
        change_message=(
            f'{"Created" if creating else "Updated"} criteria-based event '
            f'({event.get_publication_status_display()}).'
        ),
    )

    recipients = list({chief.id: chief, faculty.id: faculty, **{j.id: j for j in judges}}.values())
    transaction.on_commit(lambda: _notify_assignees(event, recipients))
    try:
        sync_event_to_mobile(event)
    except Exception:
        pass
    return event


def can_delete_criteria_event(event, user):
    if event.results_finalized and not user.is_superuser:
        return False, 'Events with finalized results can only be deleted by an administrator.'
    return True, ''


def serialize_criteria_event(event):
    User = get_user_model()
    judge_ids = list(event.assigned_judges.values_list('id', flat=True))
    judges = list(User.objects.filter(id__in=judge_ids))
    judge_map = {user.id: (user.get_full_name().strip() or user.username) for user in judges}
    participant_names = []
    if event.participation_type == 'individual':
        for item in RegistryCandidate.objects.filter(id__in=event.participant_ids or []):
            participant_names.append(f'#{item.number} {item.name}')
    else:
        for item in Team.objects.filter(id__in=event.participant_ids or []):
            participant_names.append(item.name)
    return {
        'id': event.id,
        'name': event.name,
        'category': event.category,
        'classification': event.event_classification,
        'classification_label': event.get_event_classification_display() or '—',
        'division': event.division or '—',
        'participation_type': event.participation_type or 'team',
        'participation_label': event.get_participation_type_display() or 'Team',
        'venue': event.venue,
        'start_date': event.event_date.isoformat() if event.event_date else '',
        'end_date': (event.end_date or event.event_date).isoformat() if event.event_date else '',
        'publication_status': event.publication_status,
        'publication_label': event.get_publication_status_display(),
        'event_format': (
            'multiple_stage' if event.event_format in {'multiple_rounds', 'preliminary_final'}
            else (event.event_format or 'single_performance')
        ),
        'event_format_label': (
            'Multiple Stage Competition'
            if (event.event_format or '') in {
                'multiple_stage', 'multiple_rounds', 'preliminary_final',
            }
            else (event.get_event_format_display() or '—')
        ),
        'criteria_score_method': event.criteria_score_method,
        'criteria_score_method_label': event.get_criteria_score_method_display() or '—',
        'rounds_config': event.rounds_config or [],
        'judging_criteria_config': event.judging_criteria_config or [],
        'score_settings': event.score_settings or DEFAULT_SCORE_SETTINGS,
        'deductions_enabled': event.deductions_enabled,
        'deductions_config': event.deductions_config or [],
        'result_processing_config': event.result_processing_config or DEFAULT_RESULT_PROCESSING,
        'judge_settings': event.judge_settings or DEFAULT_JUDGE_SETTINGS,
        'tie_break_rules': event.tie_break_rules or [],
        'participant_ids': event.participant_ids or [],
        'participant_names': participant_names,
        'chief_judge_id': event.chief_judge_id,
        'chief_judge_name': (
            event.chief_judge.get_full_name().strip() or event.chief_judge.username
            if event.chief_judge else '—'
        ),
        'faculty_account_id': event.faculty_account_id,
        'faculty_name': (
            event.faculty_account.get_full_name().strip() or event.faculty_account.username
            if event.faculty_account else event.faculty_in_charge or '—'
        ),
        'scoresheet_template_id': event.scoresheet_template_id,
        'judge_ids': judge_ids,
        'judge_names': [judge_map.get(i, str(i)) for i in judge_ids],
        'apply_championship_points': event.apply_championship_points,
        'points_config': event.championship_points_config or [],
        'results_finalized': event.results_finalized,
    }
