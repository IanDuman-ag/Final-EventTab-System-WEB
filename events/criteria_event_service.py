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

SPECIAL_EVENT_TYPES = {'', 'pageant'}
PAGEANT_FORMATS = {
    'male_female',
    'individual',
    'pairs',
    'custom',
    # Legacy aliases still accepted on load
    'male_only',
    'female_only',
    'open',
}
PAGEANT_FORMAT_CATEGORIES = {
    'male_female': ['Male Category', 'Female Category'],
    'individual': ['Open Category'],
    'pairs': ['Pair Category'],
    'custom': ['Open Category'],
    'male_only': ['Male Category'],
    'female_only': ['Female Category'],
    'open': ['Open Category'],
}
PAGEANT_FORMAT_ALIASES = {
    'male_only': 'male_female',
    'female_only': 'male_female',
    'open': 'individual',
}
DEFAULT_PAGEANT_SEGMENTS = [
    {
        'name': 'Production Number',
        'weight': 15,
        'enabled': True,
        'counts_toward_main_ranking': True,
        'criteria': [
            {'name': 'Choreography', 'description': '', 'weight': 40, 'max_score': 100},
            {'name': 'Stage Presence', 'description': '', 'weight': 35, 'max_score': 100},
            {'name': 'Energy', 'description': '', 'weight': 25, 'max_score': 100},
        ],
    },
    {
        'name': 'Talent Competition',
        'weight': 25,
        'enabled': True,
        'counts_toward_main_ranking': True,
        'criteria': [
            {'name': 'Skill', 'description': '', 'weight': 40, 'max_score': 100},
            {'name': 'Creativity', 'description': '', 'weight': 30, 'max_score': 100},
            {'name': 'Overall Impact', 'description': '', 'weight': 30, 'max_score': 100},
        ],
    },
    {
        'name': 'Swimwear / Sportswear',
        'weight': 20,
        'enabled': True,
        'counts_toward_main_ranking': True,
        'criteria': [
            {'name': 'Poise', 'description': '', 'weight': 40, 'max_score': 100},
            {'name': 'Presentation', 'description': '', 'weight': 35, 'max_score': 100},
            {'name': 'Confidence', 'description': '', 'weight': 25, 'max_score': 100},
        ],
    },
    {
        'name': 'Evening Gown',
        'weight': 20,
        'enabled': True,
        'counts_toward_main_ranking': True,
        'criteria': [
            {'name': 'Elegance', 'description': '', 'weight': 40, 'max_score': 100},
            {'name': 'Carriage', 'description': '', 'weight': 30, 'max_score': 100},
            {'name': 'Overall Look', 'description': '', 'weight': 30, 'max_score': 100},
        ],
    },
    {
        'name': 'Question and Answer',
        'weight': 20,
        'enabled': True,
        'counts_toward_main_ranking': True,
        'criteria': [
            {'name': 'Content', 'description': '', 'weight': 40, 'max_score': 100},
            {'name': 'Delivery', 'description': '', 'weight': 35, 'max_score': 100},
            {'name': 'Insight', 'description': '', 'weight': 25, 'max_score': 100},
        ],
    },
]


def is_pageant_event(data_or_event):
    """True when Category is Special Event and type is Pageant."""
    if hasattr(data_or_event, 'category'):
        category = (data_or_event.category or '').strip()
        special = (getattr(data_or_event, 'special_event_type', '') or '').strip().lower()
    else:
        category = (data_or_event.get('category') or '').strip()
        special = (data_or_event.get('special_event_type') or '').strip().lower()
    return category == 'Special Event' and special == 'pageant'


def recommended_categories_for_format(pageant_format):
    key = PAGEANT_FORMAT_ALIASES.get(pageant_format, pageant_format)
    return list(PAGEANT_FORMAT_CATEGORIES.get(key, ['Open Category']))


def _parse_time(value, label, required=False):
    raw = (value or '').strip()
    if not raw:
        if required:
            raise CriteriaEventValidationError(f'{label} is required.')
        return None
    try:
        parts = raw.split(':')
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        from datetime import time as time_cls
        return time_cls(hour=hour, minute=minute)
    except (TypeError, ValueError, IndexError) as exc:
        raise CriteriaEventValidationError(f'{label} must be a valid time.') from exc


def _ensure_registry_candidates(pending_rows):
    """Create or reuse RegistryCandidate rows for manually added candidates."""
    created_ids = []
    for row in pending_rows or []:
        if not isinstance(row, dict):
            continue
        existing_id = row.get('id')
        if existing_id:
            try:
                cand = RegistryCandidate.objects.filter(
                    pk=int(existing_id),
                    status=RegistryCandidate.STATUS_ACTIVE,
                ).first()
            except (TypeError, ValueError):
                cand = None
            if cand:
                created_ids.append(cand.id)
                continue
        name = str(row.get('name') or '').strip()
        number = str(row.get('number') or '').strip()
        if not name:
            continue
        if not number:
            # Generate a unique number based on next available pageant slot.
            used = set(RegistryCandidate.objects.values_list('number', flat=True))
            idx = 1
            while str(idx) in used or f'P{idx}' in used:
                idx += 1
            number = f'P{idx}'
        cand = RegistryCandidate.objects.filter(number=number).first()
        if cand:
            cand.name = name[:200]
            cand.status = RegistryCandidate.STATUS_ACTIVE
            cand.save(update_fields=['name', 'status', 'updated_at'])
        else:
            cand = RegistryCandidate.objects.create(
                number=number[:20],
                name=name[:200],
                status=RegistryCandidate.STATUS_ACTIVE,
            )
        created_ids.append(cand.id)
    return created_ids


def _flatten_segment_criteria(segments):
    """Flatten per-segment criteria into a global judging_criteria_config list.

    Segment weights scale criterion weights so faculty/mobile consumers that
    only read judging_criteria_config still see a coherent 100% total across
    main-ranking segments.
    """
    main_segments = [
        seg for seg in segments
        if seg.get('enabled', True) and seg.get('counts_toward_main_ranking', True)
    ]
    if not main_segments:
        main_segments = [seg for seg in segments if seg.get('enabled', True)]
    flattened = []
    order = 1
    for seg in main_segments:
        seg_weight = float(seg.get('weight') or 0)
        criteria = seg.get('criteria') or []
        if not criteria:
            continue
        crit_weight_sum = sum(float(c.get('weight') or 0) for c in criteria) or 100.0
        for crit in criteria:
            local_w = float(crit.get('weight') or 0)
            scaled = round((local_w / crit_weight_sum) * seg_weight, 2) if seg_weight else local_w
            flattened.append({
                'id': str(crit.get('id') or f'c{order}'),
                'name': f"{seg.get('name', 'Segment')}: {crit.get('name', 'Criterion')}"[:120],
                'description': str(crit.get('description') or '')[:500],
                'weight': scaled,
                'max_score': float(crit.get('max_score') or 100),
                'order': order,
                'segment_name': str(seg.get('name') or '')[:80],
            })
            order += 1
    # Normalize tiny float drift to 100 when close.
    total = round(sum(row['weight'] for row in flattened), 2)
    if flattened and abs(total - 100) <= 0.1:
        drift = round(100 - total, 2)
        flattened[-1]['weight'] = round(flattened[-1]['weight'] + drift, 2)
    return flattened


def _validated_pageant_segments(rows, strict=True):
    if not isinstance(rows, list):
        raise CriteriaEventValidationError('Pageant segments configuration is invalid.')
    if strict and not rows:
        raise CriteriaEventValidationError('Add at least one pageant segment.')
    cleaned = []
    main_weights = []
    names = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        name = str(row.get('name') or '').strip()
        if strict and not name:
            raise CriteriaEventValidationError('Each segment needs a name.')
        if not name:
            name = f'Segment {index + 1}'
        key = name.lower()
        if key in names:
            raise CriteriaEventValidationError('Each segment name must be unique.')
        names.add(key)
        try:
            weight = float(row.get('weight') or 0)
        except (TypeError, ValueError) as exc:
            raise CriteriaEventValidationError('Each segment needs a numeric weight.') from exc
        enabled = bool(row.get('enabled', True))
        counts_main = bool(row.get('counts_toward_main_ranking', True))
        criteria_rows = row.get('criteria') or []
        if not isinstance(criteria_rows, list):
            criteria_rows = []
        cleaned_criteria = []
        crit_weights = []
        for c_index, crit in enumerate(criteria_rows):
            if not isinstance(crit, dict):
                continue
            c_name = str(crit.get('name') or '').strip()
            if strict and not c_name:
                raise CriteriaEventValidationError(
                    f'Each criterion in "{name}" needs a name.'
                )
            try:
                c_weight = float(crit.get('weight') or 0)
                c_max = float(crit.get('max_score') or 100)
            except (TypeError, ValueError) as exc:
                raise CriteriaEventValidationError(
                    f'Criteria in "{name}" need numeric weight and maximum score.'
                ) from exc
            if strict and (c_weight <= 0 or c_max <= 0):
                raise CriteriaEventValidationError(
                    f'Criteria in "{name}" need weight and maximum score greater than zero.'
                )
            cleaned_criteria.append({
                'id': str(crit.get('id') or f's{index + 1}c{c_index + 1}'),
                'name': (c_name or f'Criterion {c_index + 1}')[:120],
                'description': str(crit.get('description') or '')[:500],
                'weight': c_weight,
                'max_score': c_max,
            })
            crit_weights.append(c_weight)
        if strict and enabled and not cleaned_criteria:
            raise CriteriaEventValidationError(f'Segment "{name}" needs at least one criterion.')
        if strict and enabled and cleaned_criteria:
            _percent_total(crit_weights, f'Criteria weight for "{name}"')
        if enabled and counts_main:
            main_weights.append(weight)
        eligible = row.get('eligible_categories') or []
        if not isinstance(eligible, list):
            eligible = []
        schedule = row.get('schedule') or {}
        if not isinstance(schedule, dict):
            schedule = {}
        cleaned.append({
            'stage_number': index + 1,
            'name': name[:80],
            'weight': weight,
            'enabled': enabled,
            'counts_toward_main_ranking': counts_main,
            'qualification_method': row.get('qualification_method'),
            'qualifiers': row.get('qualifiers'),
            'minimum_score': row.get('minimum_score'),
            'carry_previous_scores': bool(row.get('carry_previous_scores', index > 0)),
            'require_faculty_confirmation': bool(row.get('require_faculty_confirmation', False)),
            'is_final': index == len(rows) - 1,
            'status': 'open' if index == 0 else 'locked',
            'eligible_categories': [str(x)[:40] for x in eligible],
            'schedule': {
                'date': str(schedule.get('date') or '')[:20],
                'start_time': str(schedule.get('start_time') or '')[:10],
                'end_time': str(schedule.get('end_time') or '')[:10],
                'venue': str(schedule.get('venue') or '')[:120],
            },
            'criteria': cleaned_criteria,
        })
    if strict and main_weights:
        _percent_total(main_weights, 'Total segment weight for main ranking')
    return cleaned


def _validated_pageant_config(data, strict=True):
    config = _json_dict(data.get('pageant_config'), 'Pageant configuration')
    pageant_format = str(config.get('pageant_format') or data.get('pageant_format') or '').strip()
    pageant_format = PAGEANT_FORMAT_ALIASES.get(pageant_format, pageant_format)
    if strict and pageant_format not in {'male_female', 'individual', 'pairs', 'custom'}:
        raise CriteriaEventValidationError('Select a pageant format.')
    if pageant_format and pageant_format not in PAGEANT_FORMATS:
        raise CriteriaEventValidationError('Select a valid pageant format.')
    categories = config.get('competition_categories')
    if not isinstance(categories, list) or not categories:
        categories = recommended_categories_for_format(pageant_format) if pageant_format else []
    categories = [str(c).strip()[:40] for c in categories if str(c).strip()]
    if strict and not categories:
        raise CriteriaEventValidationError('Add at least one competition category.')

    awards = config.get('special_awards') or []
    if not isinstance(awards, list):
        awards = []
    cleaned_awards = []
    for award in awards:
        if not isinstance(award, dict):
            continue
        title = str(award.get('title') or award.get('name') or '').strip()
        if not title:
            continue
        cleaned_awards.append({
            'id': str(award.get('id') or ''),
            'title': title[:120],
            'description': str(award.get('description') or '')[:300],
            'category': str(award.get('category') or '')[:40],
        })

    pairs = config.get('pair_entries') or []
    if not isinstance(pairs, list):
        pairs = []
    cleaned_pairs = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        cleaned_pairs.append({
            'id': str(pair.get('id') or ''),
            'label': str(pair.get('label') or '')[:120],
            'male_id': pair.get('male_id'),
            'female_id': pair.get('female_id'),
            'partner_a_id': pair.get('partner_a_id') or pair.get('male_id'),
            'partner_b_id': pair.get('partner_b_id') or pair.get('female_id'),
        })
    if strict and pageant_format == 'pairs' and not cleaned_pairs:
        raise CriteriaEventValidationError('Add at least one pair entry for pair format pageants.')

    advancement_enabled = bool(config.get('advancement_enabled', False))
    advancement = config.get('advancement') or {}
    if not isinstance(advancement, dict):
        advancement = {}
    if strict and advancement_enabled:
        try:
            top_n = int(advancement.get('top_n') or 0)
        except (TypeError, ValueError) as exc:
            raise CriteriaEventValidationError('Advancement Top N must be numeric.') from exc
        if top_n < 1:
            raise CriteriaEventValidationError('Advancement requires at least one finalist.')
        advancement = {
            'top_n': top_n,
            'source_segment': str(advancement.get('source_segment') or '')[:80],
            'notes': str(advancement.get('notes') or '')[:300],
        }

    pending = config.get('pending_candidates') or []
    if not isinstance(pending, list):
        pending = []
    candidate_extras = config.get('candidate_extras') or {}
    if not isinstance(candidate_extras, dict):
        candidate_extras = {}

    return {
        'pageant_format': pageant_format,
        'competition_categories': categories,
        'description': str(config.get('description') or '')[:2000],
        'rules': str(config.get('rules') or data.get('rules_guidelines') or '')[:5000],
        'segment_template': str(config.get('segment_template') or 'standard')[:40],
        'advancement_enabled': advancement_enabled,
        'advancement': advancement if advancement_enabled else {},
        'special_awards': cleaned_awards,
        'pair_entries': cleaned_pairs,
        'pending_candidates': pending,
        'candidate_extras': candidate_extras,
        'start_time': str(config.get('start_time') or data.get('start_time') or '')[:10],
        'end_time': str(config.get('end_time') or data.get('end_time') or '')[:10],
        'team_ids': config.get('team_ids') if isinstance(config.get('team_ids'), list) else [],
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


def _save_pageant_event(data, user, files=None, instance=None):
    """Persist a Special Event / Pageant with draft-lenient / publish-strict rules."""
    User = get_user_model()
    files = files or {}
    publication = (data.get('publication_status') or Event.PUBLICATION_DRAFT).strip().lower()
    if publication not in dict(Event.PUBLICATION_CHOICES):
        raise CriteriaEventValidationError('Status must be Draft or Published.')
    strict = publication == Event.PUBLICATION_PUBLISHED

    name = (data.get('event_name') or '').strip()
    if strict and not name:
        raise CriteriaEventValidationError('Event Name is required.')
    if not name:
        name = 'Untitled Pageant'
    name = name[:200]

    category = (data.get('category') or '').strip() or 'Special Event'
    if category != 'Special Event':
        raise CriteriaEventValidationError('Pageants must use the Special Event category.')
    special_event_type = (data.get('special_event_type') or 'pageant').strip().lower()
    if special_event_type != 'pageant':
        raise CriteriaEventValidationError('Special Event Type must be Pageant.')

    classification = (data.get('event_classification') or '').strip()
    if strict and classification not in ALLOWED_CLASSIFICATIONS:
        raise CriteriaEventValidationError('Select Major Event or Minor Event.')
    if classification not in ALLOWED_CLASSIFICATIONS:
        classification = Event.CLASSIFICATION_MAJOR

    venue = (data.get('venue') or '').strip()
    if strict and not venue:
        raise CriteriaEventValidationError('Venue is required.')
    venue = (venue or 'TBD')[:200]

    start_raw = (data.get('start_date') or '').strip()
    end_raw = (data.get('end_date') or '').strip()
    if strict and (not start_raw or not end_raw):
        raise CriteriaEventValidationError('Start Date and End Date are required.')
    start_date = _parse_date(start_raw, 'Start Date') if start_raw else timezone.localdate()
    end_date = _parse_date(end_raw, 'End Date') if end_raw else start_date
    if end_date < start_date:
        raise CriteriaEventValidationError('End Date cannot precede Start Date.')

    pageant_config = _validated_pageant_config(data, strict=strict)
    start_time = _parse_time(pageant_config.get('start_time') or data.get('start_time'), 'Start Time')
    end_time = _parse_time(pageant_config.get('end_time') or data.get('end_time'), 'End Time')

    # Merge pending manual candidates into RegistryCandidate, then validate IDs.
    pending = pageant_config.get('pending_candidates') or []
    created_ids = _ensure_registry_candidates(pending)
    raw_ids = _json_list(data.get('participant_ids'), 'Candidates')
    try:
        participant_ids = list(dict.fromkeys(
            [int(value) for value in raw_ids] + [int(i) for i in created_ids]
        ))
    except (TypeError, ValueError) as exc:
        raise CriteriaEventValidationError('Candidates contain an invalid selection.') from exc
    if strict and not participant_ids:
        raise CriteriaEventValidationError('Add at least one candidate.')
    if participant_ids:
        found = list(RegistryCandidate.objects.filter(
            id__in=participant_ids,
            status=RegistryCandidate.STATUS_ACTIVE,
        ))
        by_id = {item.id: item for item in found}
        missing = [i for i in participant_ids if i not in by_id]
        if missing and strict:
            raise CriteriaEventValidationError('One or more selected candidates are unavailable.')
        participant_ids = [i for i in participant_ids if i in by_id]

    pageant_config['pending_candidates'] = []

    rounds_raw = _json_list(data.get('rounds_config'), 'Pageant segments')
    if not rounds_raw and pageant_config.get('segment_template') == 'standard':
        rounds_raw = [
            {**seg, 'id': f's{idx + 1}', 'criteria': [
                {**c, 'id': f's{idx + 1}c{cidx + 1}'} for cidx, c in enumerate(seg.get('criteria') or [])
            ]}
            for idx, seg in enumerate(DEFAULT_PAGEANT_SEGMENTS)
        ]
    segments = _validated_pageant_segments(rounds_raw, strict=strict)
    flattened = _flatten_segment_criteria(segments)
    if strict and not flattened:
        raise CriteriaEventValidationError('Configure criteria inside at least one enabled segment.')
    if flattened and strict:
        _percent_total([row['weight'] for row in flattened], 'Flattened criteria weight')

    score_method = (data.get('criteria_score_method') or Event.CRITERIA_SCORE_WEIGHTED).strip()
    if score_method not in ALLOWED_SCORE_METHODS:
        score_method = Event.CRITERIA_SCORE_WEIGHTED
    try:
        score_settings = _validated_score_settings(data)
    except CriteriaEventValidationError:
        if strict:
            raise
        score_settings = dict(DEFAULT_SCORE_SETTINGS)

    deductions_enabled, deductions = False, []
    try:
        deductions_enabled, deductions = _validated_deductions(data)
    except CriteriaEventValidationError:
        if strict:
            raise

    chief = judges = faculty = None
    try:
        chief, judges, faculty = _validated_users(data, User)
    except CriteriaEventValidationError:
        if strict:
            raise
        judges = []

    result_processing = {
        **DEFAULT_RESULT_PROCESSING,
        **_json_dict(data.get('result_processing_config'), 'Result processing'),
    }
    result_processing['multi_stage'] = True
    result_processing['active_stage_index'] = 0
    result_processing.setdefault('confirmed_stages', {})
    result_processing.setdefault('qualified_participant_ids', {'0': list(participant_ids)})
    result_processing['pageant'] = True
    result_processing['advancement_enabled'] = pageant_config.get('advancement_enabled', False)

    judge_settings = {
        **DEFAULT_JUDGE_SETTINGS,
        **_json_dict(data.get('judge_settings'), 'Judge settings'),
    }
    if strict and judge_settings.get('remove_high_low') and len(judges) < 3:
        raise CriteriaEventValidationError(
            'Remove Highest and Lowest Judge Scores requires at least three assigned judges.'
        )

    criteria_for_ties = flattened or [{'id': 'c1', 'name': 'Overall'}]
    try:
        tie_breaks = _validated_tie_breaks(data, criteria_for_ties)
    except CriteriaEventValidationError:
        if strict:
            raise
        tie_breaks = []
    points = _validated_points(data, classification)

    division = (data.get('division') or '').strip()
    if division not in ALLOWED_DIVISIONS:
        division = ''

    creating = instance is None
    event = instance or Event()
    if instance and instance.results_finalized and not user.is_superuser:
        raise CriteriaEventValidationError(
            'Finalized events cannot be edited without administrator authorization.'
        )

    event.name = name
    event.category = category
    event.special_event_type = 'pageant'
    event.pageant_config = pageant_config
    event.event_classification = classification
    event.division = division
    event.venue = venue
    event.event_date = start_date
    event.end_date = end_date
    event.event_time = start_time
    event.publication_status = publication
    event.status = Event.STATUS_UPCOMING if start_date > timezone.localdate() else Event.STATUS_ACTIVE
    event.scoring_method = 'criteria'
    event.participation_type = Event.PARTICIPATION_INDIVIDUAL
    event.event_format = Event.FORMAT_MULTIPLE_STAGE
    event.criteria_score_method = score_method
    event.rounds_config = segments
    event.judging_criteria_config = flattened
    event.score_settings = score_settings
    event.deductions_enabled = deductions_enabled
    event.deductions_config = deductions
    event.result_processing_config = result_processing
    event.judge_settings = judge_settings
    event.tie_break_rules = tie_breaks
    event.participant_ids = participant_ids
    event.rules_guidelines = pageant_config.get('rules') or (data.get('rules_guidelines') or '')
    event.mechanics = pageant_config.get('description') or ''
    event.chief_judge = chief
    event.faculty_account = faculty
    if faculty:
        event.faculty_in_charge = faculty.get_full_name().strip() or faculty.username
    elif not event.faculty_in_charge:
        event.faculty_in_charge = ''

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
    event.require_faculty_confirmation = bool(
        result_processing.get('require_faculty_confirmation', False)
    )
    if creating:
        event.created_by = user
    event.save()
    if judges:
        event.assigned_judges.set(judges)
    elif creating:
        event.assigned_judges.clear()

    LogEntry.objects.create(
        user=user,
        content_type=ContentType.objects.get_for_model(Event),
        object_id=str(event.pk),
        object_repr=event.name,
        action_flag=ADDITION if creating else CHANGE,
        change_message=(
            f'{"Created" if creating else "Updated"} pageant event '
            f'({event.get_publication_status_display()}).'
        ),
    )

    recipients = []
    if chief:
        recipients.append(chief)
    if faculty:
        recipients.append(faculty)
    recipients.extend(judges or [])
    if recipients:
        uniq = list({u.id: u for u in recipients}.values())
        transaction.on_commit(lambda: _notify_assignees(event, uniq))
    try:
        sync_event_to_mobile(event)
    except Exception:
        pass
    return event


@transaction.atomic
def save_criteria_event(data, user, files=None, instance=None):
    User = get_user_model()
    files = files or {}

    if is_pageant_event(data) or (
        instance is not None
        and is_pageant_event(instance)
        and (data.get('special_event_type') or 'pageant').strip().lower() == 'pageant'
        and (data.get('category') or instance.category) == 'Special Event'
    ):
        # Prefer explicit POST flags; fall back to instance when editing a pageant.
        if not (data.get('special_event_type') or '').strip():
            mutable = data.copy() if hasattr(data, 'copy') else dict(data)
            mutable['special_event_type'] = 'pageant'
            mutable['category'] = mutable.get('category') or 'Special Event'
            data = mutable
        return _save_pageant_event(data, user, files=files, instance=instance)

    name = _required(data, 'event_name', 'Event Name')[:200]
    category = _required(data, 'category', 'Category')
    if category not in ALLOWED_CATEGORIES:
        raise CriteriaEventValidationError('Select a valid category.')
    special_event_type = (data.get('special_event_type') or '').strip().lower()
    if category == 'Special Event' and special_event_type and special_event_type not in SPECIAL_EVENT_TYPES:
        raise CriteriaEventValidationError('Select a valid Special Event Type.')
    if category != 'Special Event':
        special_event_type = ''
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
    event.special_event_type = special_event_type
    event.pageant_config = {}
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
    pageant_config = event.pageant_config or {}
    is_pageant = is_pageant_event(event)
    format_key = pageant_config.get('pageant_format') or ''
    format_labels = {
        'male_female': 'Male and Female Pageant',
        'individual': 'Individual Pageant',
        'pairs': 'Pair or Couple Pageant',
        'custom': 'Custom Format',
        'male_only': 'Male Only',
        'female_only': 'Female Only',
        'open': 'Open / Mixed',
    }
    return {
        'id': event.id,
        'name': event.name,
        'category': event.category,
        'special_event_type': event.special_event_type or '',
        'is_pageant': is_pageant,
        'pageant_config': pageant_config,
        'pageant_format': format_key,
        'pageant_format_label': format_labels.get(format_key, format_key or '—'),
        'competition_categories': pageant_config.get('competition_categories') or [],
        'classification': event.event_classification,
        'classification_label': event.get_event_classification_display() or '—',
        'division': event.division or '—',
        'participation_type': event.participation_type or 'team',
        'participation_label': event.get_participation_type_display() or 'Team',
        'venue': event.venue,
        'start_date': event.event_date.isoformat() if event.event_date else '',
        'end_date': (event.end_date or event.event_date).isoformat() if event.event_date else '',
        'start_time': event.event_time.strftime('%H:%M') if event.event_time else (pageant_config.get('start_time') or ''),
        'end_time': pageant_config.get('end_time') or '',
        'publication_status': event.publication_status,
        'publication_label': event.get_publication_status_display(),
        'event_format': (
            'multiple_stage' if event.event_format in {'multiple_rounds', 'preliminary_final'}
            else (event.event_format or 'single_performance')
        ),
        'event_format_label': (
            'Pageant Segments'
            if is_pageant
            else (
                'Multiple Stage Competition'
                if (event.event_format or '') in {
                    'multiple_stage', 'multiple_rounds', 'preliminary_final',
                }
                else (event.get_event_format_display() or '—')
            )
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
        'rules_guidelines': event.rules_guidelines or '',
        'description': event.mechanics or pageant_config.get('description') or '',
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
        'next_actions': (
            [
                'Assign or confirm judges',
                'Open scoresheet for the first segment',
                'Review candidate roster and pair entries',
            ] if is_pageant else []
        ),
    }
