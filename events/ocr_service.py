"""OCR drafts and confirmation into the existing criteria scoring workflow."""
import hashlib
import json
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .audit_service import record_audit
from .criteria_faculty_service import _can_mutate, criteria_rounds, round_is_scoreable, round_participant_ids
from .criteria_scoring import normalized_criterion_score
from .faculty_service import FacultyServiceError
from .models import (CriteriaScoreSubmission, CriteriaSubcriterionScore, Event, JudgeScore,
                     JudgingEvent, OCRScoreSheet)
from .mobile_scoring_compatibility import read_mobile_mapping


class OCRSheetError(FacultyServiceError):
    pass


def authorize(event, user):
    if event.scoring_method != 'criteria' or not _can_mutate(event, user):
        raise OCRSheetError('Only the assigned Faculty-in-Charge can access this score sheet.')


def definition(event, user, round_id, judge_id, *, writable=False):
    authorize(event, user)
    rounds = criteria_rounds(event)
    index = next((i for i, row in enumerate(rounds) if row['id'] == str(round_id)), None)
    if index is None:
        raise OCRSheetError('Select a round belonging to this event.')
    judge = event.assigned_judges.filter(pk=judge_id, is_active=True).first()
    if not judge:
        raise OCRSheetError('Select an active judge assigned to this event.')
    if writable and (not round_is_scoreable(event, index) or event.status in {'completed', 'inactive'}):
        raise OCRSheetError('This round is locked, completed, or published.')
    ids = round_participant_ids(event, index)
    entries = list(event.criteria_entries.filter(pk__in=ids).order_by('display_order', 'pk'))
    if not entries or {entry.pk for entry in entries} != set(ids):
        raise OCRSheetError('The administrator must configure this round’s eligible participant entries first.')
    categories = list(event.scoring_categories.filter(assigned_round_id=round_id, purpose='official')
                      .prefetch_related('criteria__subcriteria').order_by('display_order', 'pk'))
    if not categories or sum(row.overall_weight_percent for row in categories) != 100:
        raise OCRSheetError('Official category weights must total 100%.')
    modes = {row.judge_mode for row in categories}
    if len(modes) != 1 or (event.criteria_score_method == 'ranking_based' and modes != {'ranking'}):
        raise OCRSheetError('Configure a consistent scoring or ranking mode before preparing a paper sheet.')
    if modes == {'ranking'} and (event.result_processing_config or {}).get('ranking_aggregation') not in {'mean_rank','weighted_mean_rank'}:
        raise OCRSheetError('Configure the authorized ranking aggregation rule first.')
    columns = []
    for category in categories:
        criteria = list(category.criteria.all())
        if not criteria or sum(row.weight_percent for row in criteria) != 100:
            raise OCRSheetError('Each category must have configured criteria totaling 100%.')
        for criterion in criteria:
            children = list(criterion.subcriteria.all())
            if category.judge_mode == 'ranking' and children:
                raise OCRSheetError('Ranking criteria cannot contain numerical subcriteria.')
            if not criterion.max_score or criterion.max_score <= 0:
                raise OCRSheetError('Each criterion needs a positive maximum score.')
            if children and sum(row.max_score for row in children) != criterion.max_score:
                raise OCRSheetError('Subcriteria must total the parent criterion maximum.')
            for child in children or [None]:
                ranking = category.judge_mode == 'ranking'
                columns.append({'key': f'{criterion.pk}:{child.pk if child else 0}',
                    'criterion': criterion.pk, 'child': child.pk if child else None, 'category': category.pk,
                    'name': f'{category.name} · {criterion.name}' + (f' / {child.name}' if child else ''),
                    'maximum': str(len(entries) if ranking else child.max_score if child else criterion.max_score),
                    'minimum': str(1 if ranking else 0 if child else criterion.min_score), 'ranking': ranking,
                    'parent_minimum': str(criterion.min_score), 'weight': str(criterion.weight_percent),
                    'category_weight': str(category.overall_weight_percent)})
    if not columns or len(entries) * len(columns) > 600:
        raise OCRSheetError('A sheet must contain between 1 and 600 configured score cells.')
    return {'template_version': 1, 'event_id': event.pk, 'event': event.name, 'round_id': str(round_id), 'round': rounds[index]['name'],
            'judge_id': judge.pk, 'judge': judge.get_full_name() or judge.username,
            'method': event.criteria_score_method,
            'participants': [{'id': row.pk, 'name': row.display_name} for row in entries], 'columns': columns}


def fingerprint(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def validate_scores(snapshot, values, *, complete=True):
    if not isinstance(values, dict):
        raise OCRSheetError('Scores must be a cell-to-value mapping.')
    expected = {f'{row["id"]}/{col["key"]}': col for row in snapshot['participants'] for col in snapshot['columns']}
    if set(values) - set(expected) or (complete and set(values) != set(expected)):
        raise OCRSheetError('Scores contain missing, duplicate, or unexpected participant/criterion cells.')
    cleaned = {}
    for key, value in values.items():
        if value in ('', None) and not complete:
            continue
        try:
            score = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise OCRSheetError(f'Enter a numeric score for {key}.')
        col = expected[key]
        if not score.is_finite() or score.as_tuple().exponent < -2 or not Decimal(col['minimum']) <= score <= Decimal(col['maximum']):
            raise OCRSheetError(f'{col["name"]}: use {col["minimum"]}–{col["maximum"]} with at most 2 decimal places.')
        if col['ranking'] and score != score.to_integral_value():
            raise OCRSheetError('Ranks must be whole numbers.')
        cleaned[key] = str(score)
    if complete:
        for col in snapshot['columns']:
            if col['ranking'] and len({Decimal(cleaned[f'{row["id"]}/{col["key"]}']) for row in snapshot['participants']}) != len(snapshot['participants']):
                raise OCRSheetError('Each participant must have a unique rank in each ranking criterion.')
        for participant in snapshot['participants']:
            for criterion in {col['criterion'] for col in snapshot['columns']}:
                columns = [col for col in snapshot['columns'] if col['criterion'] == criterion]
                if not columns[0]['ranking'] and sum(Decimal(cleaned[f'{participant["id"]}/{col["key"]}']) for col in columns) < Decimal(columns[0]['parent_minimum']):
                    raise OCRSheetError('A criterion total is below the configured minimum.')
    return cleaned


def lock_event(event_id):
    # Match Flutter's lock order to serialize OCR/mobile submissions and round transitions.
    mobile_id = Event.objects.values_list('judging_event_id', flat=True).get(pk=event_id)
    if mobile_id:
        JudgingEvent.objects.select_for_update().get(pk=mobile_id)
    return Event.objects.select_for_update().get(pk=event_id)


def ensure_no_submission(event, round_id, judge):
    if CriteriaScoreSubmission.objects.filter(event=event, assigned_round_id=round_id, judge_id=judge, is_active=True).exists():
        raise OCRSheetError('This judge already has mobile or paper scores for this round. Resolve that submission first.')
    if event.judging_event_id:
        mapping = read_mobile_mapping(event, event.judging_event)
        criterion_ids = [value for key, value in mapping['criteria'].items() if key.startswith(f'{round_id}:')]
        legacy = JudgeScore.objects.filter(judge_id=judge, candidate__event_id=event.judging_event_id, is_locked=True)
        if (legacy.filter(criterion_id__in=criterion_ids) if criterion_ids else legacy).exists():
            raise OCRSheetError('This judge already has locked mobile scores for this round.')


@transaction.atomic
def create_sheet(event_id, user, round_id, judge_id):
    event = lock_event(event_id)
    snapshot = definition(event, user, round_id, judge_id, writable=True)
    ensure_no_submission(event, round_id, judge_id)
    for existing in OCRScoreSheet.objects.filter(event=event, judge_id=judge_id, round_id=round_id, original='').exclude(verification_status='confirmed'):
        if fingerprint(existing.snapshot) == fingerprint(snapshot):
            return existing
    return OCRScoreSheet.objects.create(event=event, judge_id=judge_id, uploaded_by=user, round_id=round_id, snapshot=snapshot)


def locked_sheet(sheet_id, user, *, writable=True):
    stub = OCRScoreSheet.objects.get(pk=sheet_id)
    event = lock_event(stub.event_id)
    sheet = OCRScoreSheet.objects.select_for_update().get(pk=sheet_id)
    current = definition(event, user, sheet.round_id, sheet.judge_id, writable=writable)
    if writable and (fingerprint(current) != fingerprint(sheet.snapshot) or sheet.verification_status == 'confirmed'):
        raise OCRSheetError('This sheet is confirmed or its configuration changed. Generate a new sheet if required.')
    return event, sheet


@transaction.atomic
def save_draft(sheet_id, user, values, *, verify=False, attested=False):
    event, sheet = locked_sheet(sheet_id, user)
    if sheet.ocr_status == 'processing':
        raise OCRSheetError('Wait for OCR processing to finish before editing.')
    if verify and (not attested or not sheet.original):
        raise OCRSheetError('Upload the original and confirm that you compared every score before verification.')
    cleaned = validate_scores(sheet.snapshot, values, complete=verify)
    before = sheet.corrected
    sheet.corrections = [*sheet.corrections, {'by': user.pk, 'at': timezone.now().isoformat(), 'before': before, 'after': cleaned, 'action': 'verify' if verify else 'save'}]
    sheet.corrected = cleaned
    sheet.verification_status = 'verified' if verify else 'for_review'
    sheet.save(update_fields=['corrected', 'corrections', 'verification_status', 'updated_at'])
    record_audit(user=user, action='Verified OCR Scores' if verify else 'Saved OCR Draft', module='Results',
                 event_name=event.name, metadata={'sheet': str(sheet.pk)})
    return sheet


@transaction.atomic
def confirm_sheet(sheet_id, user):
    event, sheet = locked_sheet(sheet_id, user)
    if sheet.verification_status != 'verified':
        raise OCRSheetError('Verify every score against the original sheet before confirming.')
    if not sheet.original:
        raise OCRSheetError('Upload the original paper sheet before confirming its scores.')
    ensure_no_submission(event, sheet.round_id, sheet.judge_id)
    values = validate_scores(sheet.snapshot, sheet.corrected)
    now = timezone.now()
    definitions = {row.pk: row for category in event.scoring_categories.prefetch_related('criteria') for row in category.criteria.all()}
    for participant in sheet.snapshot['participants']:
        groups = {}
        for col in sheet.snapshot['columns']:
            groups.setdefault(col['criterion'], []).append((col, Decimal(values[f'{participant["id"]}/{col["key"]}'])))
        for criterion_id, cells in groups.items():
            criterion = definitions[criterion_id]
            raw = sum(value for _, value in cells)
            ranking = criterion.category.judge_mode == 'ranking'
            if not ranking and raw < criterion.min_score:
                raise OCRSheetError('A criterion total is below its configured minimum.')
            normalized = raw if ranking else normalized_criterion_score(raw, criterion.max_score, criterion.weight_percent)
            submission = CriteriaScoreSubmission.objects.create(event=event, assigned_round_id=sheet.round_id,
                category=criterion.category, criterion=criterion, judge=sheet.judge, participant_type='entry',
                participant_id=participant['id'], source='OCR', status='SUBMITTED', raw_score=raw,
                normalized_score=normalized, submission_key=str(sheet.pk), submitted_at=now)
            for col, value in cells:
                if col['child']:
                    CriteriaSubcriterionScore.objects.create(submission=submission, subcriterion_id=col['child'], raw_score=value)
    sheet.verification_status = 'confirmed'
    sheet.confirmed_at = now
    sheet.save(update_fields=['verification_status', 'confirmed_at', 'updated_at'])
    record_audit(user=user, action='Confirmed OCR Judge Submission', module='Results', event_name=event.name,
                 metadata={'sheet': str(sheet.pk), 'judge': sheet.judge_id, 'round': sheet.round_id, 'source': 'OCR'})
    return sheet
