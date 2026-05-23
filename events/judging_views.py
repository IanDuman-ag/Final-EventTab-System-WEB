import uuid

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
from .models import Candidate, Criterion, Event, EventCategory, JudgeScore, JudgingEvent


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
        events = category.events.all()
        serializer = JudgingEventListSerializer(events, many=True)
        return Response(serializer.data)


class JudgingEventViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = JudgingEvent.objects.all()
    permission_classes = [IsJudgeUser]

    def get_queryset(self):
        qs = JudgingEvent.objects.exclude(portal_event__status=Event.STATUS_INACTIVE)
        assigned = qs.filter(assigned_judges=self.request.user).distinct()
        if assigned.exists():
            return assigned
        return qs

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return JudgingEventDetailSerializer
        return JudgingEventListSerializer

    @action(detail=True, methods=['post'])
    def submit_scores(self, request, pk=None):
        event = self.get_object()
        serializer = SubmitScoresSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        candidate_id = serializer.validated_data['candidate_id']
        scores_data = serializer.validated_data['scores']

        try:
            candidate = Candidate.objects.get(id=candidate_id, event=event)
        except Candidate.DoesNotExist:
            return Response({'detail': 'Candidate not found.'}, status=status.HTTP_404_NOT_FOUND)

        if JudgeScore.objects.filter(judge=request.user, candidate=candidate, is_locked=True).exists():
            return Response(
                {'detail': 'Scores already submitted and locked.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        verification_id = str(uuid.uuid4())[:13].upper()
        submitted_at = timezone.now()
        created_scores = []

        for score_item in scores_data:
            try:
                criterion = Criterion.objects.get(id=score_item['criterion_id'], event=event)
            except Criterion.DoesNotExist:
                continue

            score_value = min(max(float(score_item['score']), 0), float(criterion.max_score))
            judge_score, _ = JudgeScore.objects.update_or_create(
                judge=request.user,
                candidate=candidate,
                criterion=criterion,
                defaults={
                    'score': score_value,
                    'is_locked': True,
                    'submitted_at': submitted_at,
                    'verification_id': verification_id,
                },
            )
            created_scores.append(judge_score)

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
            })

        return Response({
            'verification_id': verification_id,
            'submitted_at': submitted_at.isoformat(),
            'total_score': round(total_score, 1),
            'breakdown': breakdown,
            'is_locked': True,
        })

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
        serializer = JudgeScoreSerializer(scores, many=True)
        return Response(serializer.data)
