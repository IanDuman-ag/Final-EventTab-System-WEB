"""Read-only participant judging activity for Super Admin monitoring."""
from datetime import timedelta

from django.db.models import F, Max
from django.utils import timezone

from .mobile_judge import detail_payload
from .models import AuditLog, CriteriaScoreSubmission, JudgeActivityLog, JudgeScore


def participant_judging_activity(event):
    if event.scoring_method != 'criteria' or not event.judging_event_id:
        return []
    judges = list(event.assigned_judges.all())
    if event.chief_judge_id and all(j.pk != event.chief_judge_id for j in judges):
        judges.append(event.chief_judge)
    rows = []
    recent = timezone.now() - timedelta(minutes=2)
    for judge in judges:
        detail = detail_payload(judge, event.judging_event)
        rid = detail['selected_round_id']
        opening = AuditLog.objects.filter(user=judge, action='Started Scoring Contestant',
            metadata__judging_event_id=event.judging_event_id, metadata__round_id=rid,
            created_at__gte=recent).order_by('-created_at').first()
        opened_candidate = opening.metadata.get('candidate_id') if opening else None
        opened_at = opening.created_at if opening else None
        # Older activity rows have no round identity; only use them for single-round events.
        if not opening and len(detail['stages']) == 1:
            legacy = JudgeActivityLog.objects.filter(judge=judge, event_id=event.judging_event_id,
                action=JudgeActivityLog.ACTION_VIEW_SCORES, timestamp__gte=recent).order_by('-timestamp').first()
            has_round = legacy and AuditLog.objects.filter(metadata__judge_activity_id=legacy.pk, metadata__has_key='round_id').exists()
            if legacy and not has_round and legacy.details.startswith('Started scoring contestant'):
                opened_candidate, opened_at = legacy.candidate_id, legacy.timestamp
        criterion_ids = [row['id'] for row in detail['criteria'] if row['id'] is not None]
        saved = dict(JudgeScore.objects.filter(judge=judge, candidate__event=event.judging_event,
            criterion_id__in=criterion_ids).values('candidate_id').annotate(last=Max('submitted_at'))
            .values_list('candidate_id', 'last'))
        normalized_ids = [row['normalized_criterion_id'] for row in detail['criteria'] if row.get('normalized_criterion_id')]
        normalized = dict(CriteriaScoreSubmission.objects.filter(event=event, judge=judge,
            criterion_id__in=normalized_ids, category_id=F('criterion__category_id'),
            assigned_round_id=rid, is_active=True, category__purpose='official',
            category__assigned_round_id=rid, category__event=event).exclude(status=CriteriaScoreSubmission.STATUS_SUPERSEDED)
            .values('participant_id').annotate(last=Max('updated_at')).values_list('participant_id','last'))
        for participant in detail['participants']:
            if not participant['is_qualified']:
                continue
            state = participant['scoring_status']
            labels = {'submitted':'Submitted', 'draft':'Saved draft', 'pending':'Awaiting scores', 'returned':'Returned'}
            stamp = normalized.get(participant['entry_id']) or saved.get(participant['id'])
            if participant['id'] is not None and participant['id'] == opened_candidate:
                if state != 'submitted' and (stamp is None or opened_at > stamp):
                    state = 'opened'
                stamp = max(filter(None, (stamp, opened_at)))
            rows.append({'judge':judge.get_full_name() or judge.username,
                'participant':participant['name'], 'number':participant['number'],
                'round':detail['current_round'], 'state':state,
                'status':'Scoring opened' if state == 'opened' else labels.get(state, state.replace('_',' ').title()),
                'updated_at':stamp})
    return rows
