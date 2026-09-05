"""
Sync web admin Event records to mobile JudgingEvent API models.
"""
import re
from datetime import time as dt_time

from django.contrib.auth import get_user_model
from django.db import models, transaction

from .models import (
    BracketTeam,
    Candidate,
    Criterion,
    Event,
    EventCategory,
    JudgingEvent,
    JudgeScore,
)

User = get_user_model()

DEFAULT_EVENT_TIME = dt_time(9, 0)

CATEGORY_TYPE_BY_KEYWORD = (
    ('esport', 'esports'),
    ('sport', 'sports'),
    ('socio', 'socio_cultural'),
    ('cultural', 'socio_cultural'),
    ('pageant', 'socio_cultural'),
    ('academic', 'academic'),
)

CATEGORY_DISPLAY_NAMES = {
    'academic': 'Academic Event',
    'esports': 'Esports Event',
    'sports': 'Sports Event',
    'socio_cultural': 'Socio Cultural',
}


def resolve_category_type(category_label):
    normalized = (category_label or '').strip().lower().replace('-', ' ')
    for keyword, category_type in CATEGORY_TYPE_BY_KEYWORD:
        if keyword in normalized:
            return category_type
    return 'academic'


def get_or_create_mobile_category(event):
    category_type = resolve_category_type(event.category)
    label = (event.category or '').strip() or CATEGORY_DISPLAY_NAMES[category_type]
    category, created = EventCategory.objects.get_or_create(
        name=label,
        defaults={
            'category_type': category_type,
            'description': f'{label} competitions',
        },
    )
    if not created and category.category_type != category_type:
        category.category_type = category_type
        category.save(update_fields=['category_type'])
    return category


def map_judging_status(event):
    if event.status == Event.STATUS_INACTIVE:
        return 'completed'
    if event.status == Event.STATUS_ACTIVE:
        return 'active'
    if event.status == Event.STATUS_COMPLETED:
        return 'completed'
    return 'upcoming'


def parse_scoring_criteria(text):
    lines = [line.strip() for line in (text or '').splitlines() if line.strip()]
    if not lines:
        return [{
            'name': 'Overall Performance',
            'description': 'General scoring',
            'max_score': 10,
            'weight_percent': 100,
        }]

    criteria = []
    for index, line in enumerate(lines):
        weight_match = re.search(r'(\d+(?:\.\d+)?)\s*%', line)
        if weight_match:
            weight = float(weight_match.group(1))
            name = line[:weight_match.start()].strip(' -:–—') or f'Criterion {index + 1}'
        else:
            weight = round(100 / len(lines), 1)
            name = line
        criteria.append({
            'name': name[:100],
            'description': line[:200],
            'max_score': 10,
            'weight_percent': weight,
        })
    return criteria


def get_active_judge_users():
    return (
        User.objects.filter(is_active=True)
        .filter(
            models.Q(groups__name__iexact='Judge')
            | models.Q(groups__name__iexact='Judges')
        )
        .distinct()
    )


def _sync_criteria_from_config(judging_event, criteria_config):
    """Sync mobile Criterion rows from absolute event-weight judging_criteria_config."""
    if JudgeScore.objects.filter(criterion__event=judging_event).exists():
        return
    judging_event.criteria.all().delete()
    for order, item in enumerate(criteria_config or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or f'Criterion {order + 1}').strip()[:100]
        try:
            weight = float(item.get('weight') if item.get('weight') is not None else item.get('weight_percent') or 0)
        except (TypeError, ValueError):
            weight = 0
        try:
            max_score = float(item.get('max_score') or 100)
        except (TypeError, ValueError):
            max_score = 100
        Criterion.objects.create(
            event=judging_event,
            name=name or f'Criterion {order + 1}',
            description=str(item.get('description') or '')[:200],
            max_score=max_score,
            weight_percent=weight,
            order=int(item.get('order') or order),
        )


def _sync_criteria(judging_event, scoring_criteria_text):
    if JudgeScore.objects.filter(criterion__event=judging_event).exists():
        return
    judging_event.criteria.all().delete()
    for order, item in enumerate(parse_scoring_criteria(scoring_criteria_text)):
        Criterion.objects.create(
            event=judging_event,
            name=item['name'],
            description=item['description'],
            max_score=item['max_score'],
            weight_percent=item['weight_percent'],
            order=order,
        )


def _sync_candidates(judging_event, event, explicit_candidates=None):
    """Sync mobile candidates from admin form, bracket teams, or placeholders."""
    if JudgeScore.objects.filter(candidate__event=judging_event).exists():
        return
    if explicit_candidates:
        judging_event.candidates.all().delete()
        for index, c in enumerate(explicit_candidates, start=1):
            cand_name = (c.get('name') or '').strip()
            if not cand_name:
                continue
            Candidate.objects.create(
                event=judging_event,
                name=cand_name,
                number=c.get('number') or index,
                department=(c.get('department') or '').strip(),
                description=(c.get('description') or '').strip(),
            )
        return

    judging_event.candidates.all().delete()
    teams = list(event.bracket_teams.order_by('seed', 'name'))
    if teams:
        for index, team in enumerate(teams, start=1):
            dept_name = team.department.name if team.department else ''
            Candidate.objects.create(
                event=judging_event,
                name=team.name,
                number=index,
                department=dept_name,
                description=team.members or '',
            )
        return

    count = event.num_teams or event.max_participants or 1
    count = min(max(int(count), 1), 50)
    for index in range(1, count + 1):
        Candidate.objects.create(
            event=judging_event,
            name=f'Participant {index}',
            number=index,
            department='',
            description='',
        )


def _assign_judges(judging_event, event):
    if map_judging_status(event) != 'active':
        judging_event.assigned_judges.clear()
        return
    portal_assignees = list(event.assigned_judges.all())
    if portal_assignees:
        judging_event.assigned_judges.set(portal_assignees)
        return
    judges = get_active_judge_users()
    judging_event.assigned_judges.set(judges)


@transaction.atomic
def sync_event_to_mobile(event, explicit_candidates=None):
    """Create or update the mobile JudgingEvent for a web Event."""
    category = get_or_create_mobile_category(event)
    event_time = event.event_time or DEFAULT_EVENT_TIME
    description_parts = [
        part for part in [
            event.mechanics,
            f'Division: {event.division}' if event.division else '',
            f'Faculty in-charge: {event.faculty_in_charge}' if event.faculty_in_charge else '',
            f'Student in-charge: {event.student_in_charge}' if event.student_in_charge else '',
        ] if part
    ]
    description = '\n\n'.join(description_parts)

    judging_event = getattr(event, 'judging_event', None)
    if judging_event is None:
        judging_event = JudgingEvent.objects.create(
            title=event.name,
            category=category,
            date=event.event_date,
            time=event_time,
            venue=event.venue,
            status=map_judging_status(event),
            description=description,
        )
        event.judging_event = judging_event
        event.save(update_fields=['judging_event'])
    else:
        judging_event.title = event.name
        judging_event.category = category
        judging_event.date = event.event_date
        judging_event.time = event_time
        judging_event.venue = event.venue
        judging_event.status = map_judging_status(event)
        judging_event.description = description
        judging_event.save()

    if event.judging_criteria_config:
        _sync_criteria_from_config(judging_event, event.judging_criteria_config)
    else:
        _sync_criteria(judging_event, event.scoring_criteria)
    _sync_candidates(judging_event, event, explicit_candidates=explicit_candidates)
    _assign_judges(judging_event, event)
    return judging_event


def delete_mobile_for_event(event):
    judging_event = getattr(event, 'judging_event', None)
    if judging_event is not None:
        judging_event.delete()
        event.judging_event = None
        event.save(update_fields=['judging_event'])


@transaction.atomic
def sync_judge_to_mobile_events(user):
    """Assign a judge to all active mobile events when account is created/activated."""
    if not user.is_active:
        return
    from core.views import get_assignment_role

    if get_assignment_role(user) != 'Judge':
        return

    active_events = JudgingEvent.objects.filter(status='active')
    for judging_event in active_events:
        judging_event.assigned_judges.add(user)


def sync_all_events_to_mobile():
    """Backfill: sync every web Event to mobile models."""
    synced = 0
    for event in Event.objects.select_related('judging_event').iterator():
        sync_event_to_mobile(event)
        synced += 1
    return synced
