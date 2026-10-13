import uuid
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
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


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    return xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR')


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
        events = category.events.filter(
            ~Q(portal_event__scoring_method='criteria') | Q(assigned_judges=request.user)
        ).distinct()
        serializer = JudgingEventListSerializer(events, many=True)
        return Response(serializer.data)


class JudgingEventViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = JudgingEvent.objects.prefetch_related('criteria__subcriteria', 'candidates')
    permission_classes = [IsJudgeUser]

    def get_queryset(self):
        qs = JudgingEvent.objects.exclude(
            portal_event__status=Event.STATUS_INACTIVE,
        ).prefetch_related('criteria__subcriteria', 'candidates')
        assigned = qs.filter(assigned_judges=self.request.user).distinct()
        if assigned.exists():
            return assigned
        return qs.filter(portal_event__isnull=True)

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return JudgingEventDetailSerializer
        return JudgingEventListSerializer

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def submit_scores(self, request, pk=None):
        event = self.get_object()
        if not event.assigned_judges.filter(pk=request.user.pk).exists():
            return Response({'detail': 'You are not assigned to judge this event.'}, status=403)
        from .criteria_mobile_rounds import mobile_round_context
        scoped = mobile_round_context(event)
        portal = Event.objects.filter(judging_event=event, scoring_method='criteria').first()
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

        locked = JudgeScore.objects.filter(judge=request.user, candidate=candidate, is_locked=True)
        if scoped is not None:
            locked = locked.filter(criterion_id__in=scoped['normalized'])
        if locked.exists():
            return Response(
                {'detail': 'Scores already submitted and locked.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
        if scoped is not None:
            from .models import CriteriaScoreSubmission
            if CriteriaScoreSubmission.objects.filter(event=scoped['event'], assigned_round_id=scoped['round_id'],
                    judge=request.user, participant_id=scoped['candidate_entries'][candidate.id],
                    criterion_id__in=[definition.id for definition in scoped['normalized'].values()],
                    is_active=True).exists():
                return Response({'detail': 'This round already has a submitted scoresheet for this candidate.'}, status=400)

        with transaction.atomic():
            for criterion, score_value, children in prepared:
                judge_score, _ = JudgeScore.objects.update_or_create(
                    judge=request.user, candidate=candidate, criterion=criterion,
                    defaults={'score': score_value, 'is_locked': True,
                              'submitted_at': submitted_at, 'verification_id': verification_id},
                )
                if children is not None:
                    save_judge_subscores(judge_score, children)
                created_scores.append(judge_score)
                if scoped is not None:
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


        total_score = 0
        breakdown = []
        for js in created_scores:
            weighted = float(js.score) * float(js.criterion.weight_percent) / float(js.criterion.max_score)
            total_score += weighted
            breakdown.append({
                'criterion': js.criterion.name,
                'score': float(js.score),
                'max_score': float(js.criterion.max_score),
                'weight': float(js.criterion.weight_percent),
                'weighted_score': round(weighted, 2),
                'subcriteria': [{'id': row.subcriterion_id, 'name': row.subcriterion.name,
                                 'score': float(row.score), 'max_score': float(row.subcriterion.max_score)}
                                for row in js.subcriterion_scores.select_related('subcriterion').all()],
            })

        from core.views import get_assignment_role
        role = get_assignment_role(request.user) or 'Judge'
        JudgeActivityLog.log(
            judge=request.user,
            action=JudgeActivityLog.ACTION_SUBMIT_SCORE,
            details=(
                f'{role} scored candidate #{candidate.number} {candidate.name} '
                f'in "{event.title}" — total: {round(total_score, 1)} '
                f'({len(created_scores)} criteria) [VID: {verification_id}]'
            ),
            event=event,
            candidate=candidate,
            ip_address=_client_ip(request),
        )

        return Response({
            'verification_id': verification_id,
            'submitted_at': submitted_at.isoformat(),
            'total_score': round(total_score, 1),
            'breakdown': breakdown,
            'is_locked': True,
        })

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        event = self.get_object()
        JudgeActivityLog.log(
            judge=request.user,
            action=JudgeActivityLog.ACTION_VIEW_EVENT,
            details=f'Viewed event "{event.title}"',
            event=event,
            ip_address=_client_ip(request),
        )
        return response

    @action(detail=True, methods=['get'])
    def my_scores(self, request, pk=None):
        event = self.get_object()
        candidate_id = request.query_params.get('candidate_id')
        if not candidate_id:
            return Response({'detail': 'candidate_id required.'}, status=status.HTTP_400_BAD_REQUEST)

        scores = JudgeScore.objects.filter(
            judge=request.user,
            candidate_id=candidate_id,
            candidate__event=event,
        )
        from .criteria_mobile_rounds import mobile_round_context
        scoped = mobile_round_context(event)
        if scoped is not None:
            scores = scores.filter(criterion_id__in=scoped['normalized'])
        serializer = JudgeScoreSerializer(scores, many=True)

        JudgeActivityLog.log(
            judge=request.user,
            action=JudgeActivityLog.ACTION_VIEW_SCORES,
            details=f'Viewed their scores for candidate #{candidate_id} in "{event.title}"',
            event=event,
            ip_address=_client_ip(request),
        )

        return Response(serializer.data)
