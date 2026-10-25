import uuid
from decimal import Decimal, InvalidOperation
from ipaddress import ip_address

from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core.views import get_assignment_role

from .judging_serializers import (
    EventCategorySerializer,
    JudgeScoreSerializer,
    JudgingEventDetailSerializer,
    JudgingEventListSerializer,
    SubmitScoresSerializer,
)
from .models import Candidate, Criterion, Event, EventCategory, JudgeActivityLog, JudgeScore, JudgingEvent
from .subcriteria import save_judge_subscores, validated_child_scores
from .mobile_submit_support import submit_errors, matching_receipt, receipt


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    for raw in (xff.split(',')[0].strip() if xff else None, request.META.get('REMOTE_ADDR')):
        try:
            parsed = ip_address(raw)
            if getattr(parsed, 'scope_id', None) is not None:
                continue  # PostgreSQL inet does not accept interface zone IDs.
            return str(parsed)
        except (ValueError, TypeError):
            continue
    return None


class IsJudgeUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and get_assignment_role(request.user) == 'Judge'
        )


class EventCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = EventCategory.objects.all()
    serializer_class = EventCategorySerializer
    permission_classes = [IsJudgeUser]

    @action(detail=True, methods=['get'])
    def events(self, request, pk=None):
        category = self.get_object()
        from .mobile_judge import assigned_events
        events = assigned_events(request.user).filter(category=category)
        serializer = JudgingEventListSerializer(events, many=True)
        return Response(serializer.data)


class JudgingEventViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = JudgingEvent.objects.prefetch_related('criteria__subcriteria', 'candidates')
    permission_classes = [IsJudgeUser]

    def get_queryset(self):
        if self.action == 'submit_scores':
            # Preserve the existing POST lookup and its explicit 403 checks.
            qs = JudgingEvent.objects.exclude(portal_event__status=Event.STATUS_INACTIVE)
            return qs
        from .mobile_judge import assigned_events
        return assigned_events(self.request.user).prefetch_related('criteria__subcriteria', 'candidates')

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return JudgingEventDetailSerializer
        return JudgingEventListSerializer

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        event = self.get_object()
        JudgeActivityLog.log(judge=request.user, action=JudgeActivityLog.ACTION_VIEW_EVENT,
            details=f'Opened assigned event "{event.title}".', event=event,
            ip_address=_client_ip(request), request=request)
        return response

    @action(detail=True, methods=['post'])
    @submit_errors
    @transaction.atomic
    def submit_scores(self, request, pk=None):
        request._score_stage = 'authorization'
        event = self.get_object()
        from .criteria_mobile_rounds import read_mobile_round_context
        from .mobile_scoring_compatibility import legacy_score_is_submitted
        # Serialize submissions against Faculty transitions and other submissions.
        JudgingEvent.objects.select_for_update().get(pk=event.pk)
        portal = Event.objects.select_for_update().filter(judging_event=event, scoring_method='criteria').first()
        if not request.user.is_active or (portal is None and not event.assigned_judges.filter(pk=request.user.pk).exists()):
            return Response({'detail': 'You are not assigned to judge this event.'}, status=403)
        if portal is not None and not portal.assigned_judges.filter(pk=request.user.pk, is_active=True).exists():
            return Response({'detail': 'You are not assigned to judge this event.'}, status=403)
        request._score_stage = 'round_configuration'
        scoped = read_mobile_round_context(event)
        if scoped is not None and not scoped['compatible']:
            return Response({'detail': 'This round is locked or its mobile scoring configuration is incompatible.'}, status=400)
        if scoped is None and portal is not None and len(portal.rounds_config or []) > 1:
            return Response({'detail': 'Round-specific scoring categories must be configured before multi-round mobile judging.'}, status=400)
        if portal is not None and scoped is None:
            from .criteria_faculty_service import round_is_scoreable
            if not round_is_scoreable(portal, 0):
                return Response({'detail': 'This round is locked or already completed.'}, status=400)
        if portal is not None and portal.status in {Event.STATUS_COMPLETED, Event.STATUS_INACTIVE}:
            return Response({'detail': 'This event is closed for judging.'}, status=400)
        if scoped is not None and not scoped['event'].assigned_judges.filter(pk=request.user.pk, is_active=True).exists():
            return Response({'detail': 'You are not assigned to judge this event.'}, status=403)
        request._score_stage = 'validation'
        serializer = SubmitScoresSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        candidate_id = serializer.validated_data['candidate_id']
        scores_data = serializer.validated_data['scores']
        if scoped is not None:
            requested_round = serializer.validated_data.get('round_id', serializer.validated_data.get('assigned_round_id'))
            if scoped['round_id'] is None or (requested_round is not None and requested_round != scoped['round_id']):
                return Response({'detail': 'This round is locked or already completed.'}, status=400)
            if candidate_id not in scoped['candidate_entries']:
                return Response({'detail': 'Candidate is not eligible for this round.'}, status=400)

        try:
            candidate = Candidate.objects.get(id=candidate_id, event=event)
        except Candidate.DoesNotExist:
            return Response({'detail': 'Candidate not found.'}, status=status.HTTP_404_NOT_FOUND)

        locked = JudgeScore.objects.filter(judge=request.user, candidate=candidate).select_related('candidate__event')
        if scoped is not None:
            locked = locked.filter(criterion_id__in=scoped['normalized'])
        previous = list(locked)

        verification_id = str(uuid.uuid4())[:13].upper()
        submitted_at = timezone.now()
        created_scores = []
        prepared = []
        seen_criteria = set()
        for score_item in scores_data:
            try:
                criterion = Criterion.objects.get(id=score_item['criterion_id'], event=event)
            except (Criterion.DoesNotExist, ValueError, TypeError):
                return Response({'detail': 'Criterion not found for this event.'}, status=status.HTTP_400_BAD_REQUEST)
            if criterion.id in seen_criteria:
                return Response({'detail': 'Each criterion may be submitted once.'}, status=status.HTTP_400_BAD_REQUEST)
            seen_criteria.add(criterion.id)
            if scoped is not None and criterion.id not in scoped['normalized']:
                return Response({'detail': 'Criterion does not belong to the active round.'}, status=400)
            if scoped is not None and scoped['normalized'][criterion.id].category.judge_mode != 'scoring':
                return Response({'detail': 'Ranking mode is unavailable until its ranking rules and submission format are configured.'}, status=400)
            children = list(criterion.subcriteria.all())
            if children:
                try:
                    child_pairs, score_value = validated_child_scores(criterion, score_item.get('subcriteria'))
                except ValueError as exc:
                    return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
                if any(score.as_tuple().exponent < -2 for _, score in child_pairs):
                    return Response({'detail': 'Subcriterion scores may have at most two decimal places.'},
                                    status=status.HTTP_400_BAD_REQUEST)
            else:
                try:
                    score_value = Decimal(str(score_item['score']))
                except (KeyError, TypeError, ValueError, InvalidOperation):
                    return Response({'detail': 'A numeric criterion score is required.'}, status=status.HTTP_400_BAD_REQUEST)
                if not score_value.is_finite():
                    return Response({'detail': 'A finite criterion score is required.'}, status=status.HTTP_400_BAD_REQUEST)
                if score_value.as_tuple().exponent < -2:
                    return Response({'detail': 'Criterion scores may have at most two decimal places.'},
                                    status=status.HTTP_400_BAD_REQUEST)
                minimum = scoped['normalized'][criterion.id].min_score if scoped is not None else Decimal('0')
                if score_value < minimum or score_value > criterion.max_score:
                    return Response({'detail': 'Score is outside the allowed range.'}, status=400)
            prepared.append((criterion, score_value, score_item.get('subcriteria') if children else None))
            if scoped is not None:
                definition = scoped['normalized'][criterion.id]
                if score_value < definition.min_score or score_value > definition.max_score:
                    return Response({'detail': 'Score is outside the allowed range.'}, status=400)

        required = set(scoped['normalized']) if scoped is not None else set(event.criteria.values_list('id', flat=True))
        if seen_criteria != required:
            return Response({'detail': 'Submit every required criterion for this round.'}, status=400)
        if any(legacy_score_is_submitted(row) for row in previous):
            request._score_stage = 'retry_receipt'
            prior = matching_receipt(previous, prepared, scoped, request.user, candidate)
            if prior is not None:
                return Response(prior)
            return Response({'detail': 'Scores already submitted and locked.'}, status=400)
        if scoped is not None:
            from .models import CriteriaScoreSubmission
            if CriteriaScoreSubmission.objects.filter(event=scoped['event'], assigned_round_id=scoped['round_id'],
                    judge=request.user, participant_id=scoped['candidate_entries'][candidate.id],
                    criterion_id__in=[definition.id for definition in scoped['normalized'].values()],
                    is_active=True).exists():
                return Response({'detail': 'This round already has a submitted scoresheet for this candidate.'}, status=400)

        with transaction.atomic():
            for criterion, score_value, children in prepared:
                request._score_stage = 'legacy_score_write'
                judge_score, _ = JudgeScore.objects.update_or_create(
                    judge=request.user, candidate=candidate, criterion=criterion,
                    defaults={'score': score_value, 'is_locked': True,
                              'submitted_at': submitted_at, 'verification_id': verification_id},
                )
                if children is not None:
                    save_judge_subscores(judge_score, children)
                created_scores.append(judge_score)
                if scoped is not None:
                    request._score_stage = 'normalized_score_write'
                    from .models import CriteriaScoreSubmission, CriteriaSubcriterionScore
                    definition = scoped['normalized'][criterion.id]
                    normalized_submission = CriteriaScoreSubmission.objects.create(
                        event=scoped['event'], assigned_round_id=scoped['round_id'],
                        category=definition.category, criterion=definition, judge=request.user,
                        participant_type='entry', participant_id=scoped['candidate_entries'][candidate.id],
                        source=CriteriaScoreSubmission.SOURCE_MOBILE, status=CriteriaScoreSubmission.STATUS_SUBMITTED,
                        raw_score=score_value, normalized_score=score_value / criterion.max_score * definition.weight_percent,
                        submitted_at=submitted_at, submission_key=verification_id)
                    if children is not None:
                        normalized_children = {row.display_order: row for row in definition.subcriteria.all()}
                        for child_score in judge_score.subcriterion_scores.select_related('subcriterion'):
                            target_child = normalized_children.get(child_score.subcriterion.display_order)
                            if target_child is None or child_score.score > target_child.max_score:
                                transaction.set_rollback(True)
                                return Response({'detail': 'Subcriterion definitions changed; reload the active round before scoring.'}, status=400)
                            CriteriaSubcriterionScore.objects.create(
                                submission=normalized_submission, subcriterion=target_child,
                                raw_score=child_score.score,
                            )


        request._score_stage = 'receipt'
        result = receipt(created_scores, scoped)

        from core.views import get_assignment_role
        role = get_assignment_role(request.user) or 'Judge'
        request._score_stage = 'audit'
        JudgeActivityLog.log(
            judge=request.user,
            action=JudgeActivityLog.ACTION_SUBMIT_SCORE,
            details=(
                f'{role} scored candidate #{candidate.number} {candidate.name} '
                f'in "{event.title}" — total: {result["total_score"]} '
                f'({len(created_scores)} criteria; round: {scoped["round_id"] if scoped else "single round"}) [VID: {verification_id}]'
            ),
            event=event,
            candidate=candidate,
            ip_address=_client_ip(request),
            request=request,
            metadata={'judging_event_id': event.pk, 'candidate_id': candidate.pk,
                      'round_id': scoped['round_id'] if scoped else None,
                      'verification_id': verification_id, 'criteria_count': len(created_scores)},
        )

        request._score_stage = 'commit'
        return Response(result)

    @action(detail=True, methods=['get'])
    def my_scores(self, request, pk=None):
        event = self.get_object()
        candidate_id = request.query_params.get('candidate_id')
        if not candidate_id:
            return Response({'detail': 'candidate_id required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            candidate_id = int(candidate_id)
            if candidate_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return Response({'detail': 'candidate_id must be a positive integer.'}, status=400)

        candidate = Candidate.objects.filter(pk=candidate_id, event=event).first()
        if candidate is not None:
            JudgeActivityLog.log(judge=request.user, action=JudgeActivityLog.ACTION_VIEW_SCORES,
                details=f'Started scoring contestant #{candidate.number} {candidate.name} in "{event.title}".',
                event=event, candidate=candidate, ip_address=_client_ip(request), request=request)

        scores = JudgeScore.objects.filter(
            judge=request.user,
            candidate_id=candidate_id,
            candidate__event=event,
        )
        from .mobile_judge import detail_payload
        detail = detail_payload(request.user, event, request.query_params.get('round_id'))
        scores = scores.filter(criterion_id__in=[row['id'] for row in detail['criteria']])
        participant = next((row for row in detail['participants'] if row['id'] == candidate_id), None)
        from .mobile_judge import portal_for
        portal = portal_for(event)
        if participant is None and portal and (portal.result_processing_config or {}).get('participant_removals'):
            return Response([])
        if participant and participant.get('entry_id') is not None:
            from .models import CriteriaScoreSubmission
            definitions = {row['normalized_criterion_id']: row['id'] for row in detail['criteria']
                           if row.get('normalized_criterion_id') and row['id'] is not None}
            children = {child['normalized_subcriterion_id']: child['id'] for criterion in detail['criteria']
                        for child in criterion.get('subcriteria', [])
                        if child.get('normalized_subcriterion_id') and child['id'] is not None}
            normalized = CriteriaScoreSubmission.objects.filter(
                event=event.portal_event, judge=request.user, participant_id=participant['entry_id'],
                assigned_round_id=detail['selected_round_id'], is_active=True,
                category__purpose='official', criterion_id__in=definitions,
            ).exclude(status=CriteriaScoreSubmission.STATUS_SUPERSEDED).select_related('category', 'criterion').prefetch_related('subcriterion_scores')
            normalized_rows = [{'criterion': definitions[row.criterion_id], 'score': str(row.raw_score),
                'submitted_at': row.submitted_at.isoformat() if row.submitted_at else None,
                'verification_id': row.submission_key,
                'is_locked': row.status not in {'DRAFT', 'REOPENED', 'REJECTED'},
                'is_draft': row.status == 'DRAFT',
                'subcriterion_scores': [{'subcriterion_id': children[child.subcriterion_id], 'score': str(child.raw_score)}
                    for child in row.subcriterion_scores.all() if child.subcriterion_id in children]} for row in normalized
                if row.category.event_id == event.portal_event.pk
                and row.criterion.category_id == row.category_id
                and row.category.assigned_round_id == detail['selected_round_id']]
            if normalized_rows:
                return Response(normalized_rows)
        serializer = JudgeScoreSerializer(scores, many=True)
        return Response(serializer.data)
