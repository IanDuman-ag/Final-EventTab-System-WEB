"""Stable legacy mobile IDs for normalized, round-scoped judging data."""
from decimal import Decimal

from django.db import transaction

from .models import Candidate, Criterion, CriterionSubcriterion, Event, JudgeScore, CriteriaScoreSubmission


def _original_mobile_criterion(event, judging_event, definition, round_id):
    """Reuse an existing mobile ID only when the stored source identity is exact."""
    source = next((row for row in event.judging_criteria_config or []
                   if str(row.get('id')) == f'esc_{definition.pk}'
                   and str(row.get('round_id')) == round_id), None)
    if not source:
        return None
    candidates = list(judging_event.criteria.filter(
        name=source.get('name'), order=source.get('order'),
        max_score=source.get('max_score'), weight_percent=source.get('weight'),
    ))
    return candidates[0] if len(candidates) == 1 else None


def _preserve_first_round_submissions(context):
    """Bridge finalized historical cells without guessing a later round's provenance."""
    event = context['event']
    judge_ids = event.assigned_judges.filter(is_active=True).values_list('pk', flat=True)
    locked = JudgeScore.objects.filter(
        judge_id__in=judge_ids, candidate_id__in=context['candidate_entries'],
        criterion_id__in=context['normalized'], is_locked=True,
    ).select_related('criterion').prefetch_related('subcriterion_scores__subcriterion')
    existing = set(CriteriaScoreSubmission.objects.filter(
        event=event, assigned_round_id=context['round_id'],
    ).values_list('judge_id', 'participant_id', 'criterion_id'))
    for score in locked:
        participant_id = context['candidate_entries'][score.candidate_id]
        definition = context['normalized'][score.criterion_id]
        if (score.judge_id, participant_id, definition.pk) in existing:
            continue
        if not score.score.is_finite() or definition.max_score <= 0 or not definition.min_score <= score.score <= definition.max_score:
            continue
        submission, created = CriteriaScoreSubmission.objects.get_or_create(
            event=event, assigned_round_id=context['round_id'],
            judge_id=score.judge_id, participant_id=context['candidate_entries'][score.candidate_id],
            category=definition.category, criterion=definition, is_active=True,
            defaults={'participant_type':'entry', 'source':'MOBILE', 'status':'SUBMITTED',
                      'raw_score':score.score,
                      'normalized_score':score.score / definition.max_score * definition.weight_percent,
                      'submitted_at':score.submitted_at, 'submission_key':score.verification_id},
        )
        if created:
            from .models import CriteriaSubcriterionScore
            children = {row.display_order: row for row in definition.subcriteria.all()}
            for child_score in score.subcriterion_scores.all():
                target = children.get(child_score.subcriterion.display_order)
                if target and child_score.score <= target.max_score:
                    CriteriaSubcriterionScore.objects.create(
                        submission=submission, subcriterion=target, raw_score=child_score.score,
                    )


@transaction.atomic
def mobile_round_context(judging_event):
    event = Event.objects.select_for_update().filter(judging_event=judging_event, scoring_method='criteria').first()
    if event is None or not event.scoring_categories.exists():
        return None
    from .criteria_faculty_service import criteria_rounds, round_is_scoreable, round_participant_ids
    rounds = criteria_rounds(event)
    active = next((i for i in range(len(rounds)) if round_is_scoreable(event, i)), None)
    for index, row in enumerate(rounds):
        row['can_score'] = index == active
        row.setdefault('status', 'open' if index == active else 'locked')
    config = dict(event.result_processing_config or {})
    mapping = dict(config.get('mobile_round_mapping') or {})
    entries = list(event.criteria_entries.all())
    candidate_map = dict(mapping.get('candidates') or {})
    existing = list(judging_event.candidates.all())
    next_number = max((row.number for row in existing), default=0) + 1
    for entry in entries:
        key = str(entry.id)
        if key in candidate_map and judging_event.candidates.filter(pk=candidate_map[key]).exists():
            continue
        used = set(candidate_map.values())
        matches = [row for row in existing if row.id not in used and row.name == entry.display_name[:200]]
        candidate = matches[0] if len(matches) == 1 else Candidate.objects.create(
            event=judging_event, name=entry.display_name[:200], number=next_number)
        if len(matches) != 1:
            next_number += 1
        candidate_map[key] = candidate.id
    criterion_map = dict(mapping.get('criteria') or {})
    criteria = []
    normalized = {}
    round_id = str(rounds[active]['id']) if active is not None else None
    if active is not None:
        categories = event.scoring_categories.filter(assigned_round_id=round_id, purpose='official').prefetch_related('criteria__subcriteria')
        for category in categories:
            for definition in category.criteria.all():
                key = f'{round_id}:{definition.id}'
                mobile = judging_event.criteria.filter(pk=criterion_map.get(key)).first()
                if mobile is None and active == 0:
                    mobile = _original_mobile_criterion(event, judging_event, definition, round_id)
                    if mobile is not None:
                        criterion_map[key] = mobile.pk
                if mobile is None:
                    mobile = Criterion.objects.create(event=judging_event, name=definition.name[:100],
                        description=definition.judging_description, max_score=definition.max_score or 100,
                        weight_percent=category.overall_weight_percent * definition.weight_percent / Decimal('100'),
                        order=len(criteria))
                    for child in definition.subcriteria.all():
                        CriterionSubcriterion.objects.create(criterion=mobile, name=child.name,
                            max_score=child.max_score, display_order=child.display_order)
                    criterion_map[key] = mobile.id
                criteria.append(mobile)
                normalized[mobile.id] = definition
    mapping.update(candidates=candidate_map, criteria=criterion_map)
    if config.get('mobile_round_mapping') != mapping:
        config['mobile_round_mapping'] = mapping
        event.result_processing_config = config
        event.save(update_fields=['result_processing_config'])
    eligible = set(round_participant_ids(event, active)) if active is not None else set()
    candidate_entries = {value: int(key) for key, value in candidate_map.items() if int(key) in eligible}
    context = {'event': event, 'rounds': rounds, 'active_round': rounds[active] if active is not None else None,
            'round_id': round_id, 'criteria': criteria, 'normalized': normalized,
            'candidate_entries': candidate_entries,
            'candidates': list(judging_event.candidates.filter(id__in=candidate_entries))}
    if active == 0:
        _preserve_first_round_submissions(context)
    return context
