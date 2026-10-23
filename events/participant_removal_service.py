"""Round-scoped, reversible exclusions; participant and score rows are never deleted."""
from copy import deepcopy
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from .faculty_service import FacultyServiceError


REMOVAL_REASONS = {
    'disqualified': 'Disqualified',
    'voluntary_withdrawal': 'Voluntary withdrawal',
    'absent': 'Absent / No-show',
    'ineligible': 'Eligibility requirements not met',
    'other': 'Other reason',
}


def excluded_participant_ids(event, round_index=None):
    history = (event.result_processing_config or {}).get('participant_removals') or []
    return {
        row['participant_id'] for row in history
        if (not row.get('restored_at') if round_index is None else
            row['round_index'] <= round_index and
            (not row.get('restored_at') or round_index < row['restored_round_index']))
    }


def active_participant_ids(event, round_index=None):
    """Event-wide roster by default; explicit rounds retain historical eligibility."""
    excluded = excluded_participant_ids(event, round_index)
    return [participant_id for participant_id in event.participant_ids or [] if participant_id not in excluded]


def active_mobile_candidate_ids(event, round_index=None):
    """Resolve event entry identities without deleting legacy candidates or scores."""
    if not event.judging_event_id:
        return []
    candidates = list(event.judging_event.candidates.all())
    if not excluded_participant_ids(event, round_index):
        return [row.pk for row in candidates]
    from .mobile_scoring_compatibility import read_mobile_mapping
    mapping = read_mobile_mapping(event, event.judging_event)['candidates']
    from .criteria_faculty_service import _participants
    participants = _participants(event)
    for participant in participants:
        key = str(participant['id'])
        if key in mapping:
            continue
        matches = [row for row in candidates if row.name == participant['name']]
        peers = [row for row in participants if row['name'] == participant['name']]
        if len(matches) == len(peers) == 1:
            mapping[key] = matches[0].pk
    # Never guess an ambiguous legacy identity or expose an unmapped removed entry.
    eligible = set(active_participant_ids(event, round_index))
    mobile_ids = {mapping[str(entry_id)] for entry_id in eligible if str(entry_id) in mapping}
    return [row.pk for row in candidates if row.pk in mobile_ids]


def participant_change_round(event):
    """Approved snapshots are immutable; changes begin at the next unapproved round."""
    from .criteria_faculty_service import criteria_rounds
    cfg = event.result_processing_config or {}
    confirmed = cfg.get('confirmed_stages') or {}
    for index in range(int(cfg.get('active_stage_index') or 0), len(criteria_rounds(event))):
        if not confirmed.get(str(index), {}).get('approved_at'):
            return index
    raise FacultyServiceError('All rounds are approved. Participant changes are no longer available.')


def _save_change(event, user, cfg, index, action, record):
    from .criteria_faculty_service import _audit
    # A changed roster must be checked again, without modifying any scoring cells.
    verified = dict(cfg.get('verified_stages') or {})
    verified.pop(str(index), None)
    cfg['verified_stages'] = verified
    for key in ('tabulation_confirmed_at', 'tabulation_confirmed_by',
                'tabulation_confirmed_round_id', 'faculty_submitted_at',
                'faculty_submitted_by', 'faculty_submitted_by_name'):
        cfg.pop(key, None)
    cfg.update(ready_for_publication=False, approved=False, tabulation_status='pending')
    event.result_processing_config = cfg
    event.save(update_fields=['result_processing_config', 'updated_at'])
    _audit(event, user, action, {'removal_id': record['id'],
                               'participant_id': record['participant_id'], 'round_index': index})


@transaction.atomic
def remove_criteria_participant(event, user, participant_id, reason, explanation=''):
    from .criteria_faculty_service import _lock, _name, _participants, criteria_rounds
    _lock(event, user)
    index = participant_change_round(event)
    try:
        participant_id = int(participant_id)
    except (TypeError, ValueError) as exc:
        raise FacultyServiceError('Select a valid participant.') from exc
    participant = next((row for row in _participants(event) if row['id'] == participant_id), None)
    if not participant or participant_id in excluded_participant_ids(event):
        raise FacultyServiceError('This participant is unavailable or already removed.')
    if reason not in REMOVAL_REASONS:
        raise FacultyServiceError('Select a valid reason for removal.')
    explanation = explanation.strip()
    if len(explanation) > 2000:
        raise FacultyServiceError('Additional explanation must be at most 2,000 characters.')
    cfg = deepcopy(event.result_processing_config or {})
    record = {
        'id': uuid4().hex, 'participant_id': participant_id,
        'participant_name': participant['name'], 'reason': reason,
        'reason_label': REMOVAL_REASONS[reason], 'explanation': explanation,
        'round_index': index, 'round_name': criteria_rounds(event)[index].get('name', 'Round'),
        'removed_at': timezone.now().isoformat(), 'removed_by': user.pk,
        'removed_by_name': _name(user),
    }
    cfg.setdefault('participant_removals', []).append(record)
    _save_change(event, user, cfg, index, 'Removed Criteria Participant', record)
    return record


@transaction.atomic
def restore_criteria_participant(event, user, removal_id):
    from .criteria_faculty_service import _lock, _name
    _lock(event, user)
    index = participant_change_round(event)
    cfg = deepcopy(event.result_processing_config or {})
    record = next((row for row in cfg.get('participant_removals', []) if row['id'] == removal_id), None)
    if not record or record.get('restored_at'):
        raise FacultyServiceError('This removal is unavailable or already restored.')
    if record['participant_id'] not in event.participant_ids:
        raise FacultyServiceError('This participant is no longer assigned to this event.')
    record.update(restored_at=timezone.now().isoformat(), restored_by=user.pk,
                  restored_by_name=_name(user), restored_round_index=index)
    _save_change(event, user, cfg, index, 'Restored Criteria Participant', record)
    return record
