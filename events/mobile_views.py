"""
Mobile API views — read-only endpoints for the Flutter app.
Authentication: Token (Authorization: Token <key>)
All endpoints require a valid token except /api/auth/login/ and /api/auth/register/.
"""
from rest_framework import filters, generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .mobile_serializers import (
    BracketMatchSerializer,
    BracketTeamSerializer,
    DepartmentSerializer,
    EventDetailSerializer,
    EventListSerializer,
    MobileJudgingEventSerializer,
    ScoreSheetSerializer,
)
from .models import (
    BracketMatch, BracketTeam, Department, Event,
    JudgingEvent, ScoreSheet,
)


# ── Departments ──────────────────────────────────────────────────

class DepartmentListView(generics.ListAPIView):
    """GET /api/mobile/departments/  — all active departments"""
    serializer_class = DepartmentSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['name', 'code']

    def get_queryset(self):
        return Department.objects.filter(status=Department.STATUS_ACTIVE)


# ── Events ───────────────────────────────────────────────────────

class EventListView(generics.ListAPIView):
    """
    GET /api/mobile/events/
    Query params:
      ?status=upcoming|active|completed
      ?category=Sports
      ?search=keyword
    """
    serializer_class = EventListSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['name', 'category', 'venue']

    def get_queryset(self):
        qs = Event.objects.exclude(status=Event.STATUS_INACTIVE)
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        category_param = self.request.query_params.get('category')
        if category_param:
            qs = qs.filter(category__iexact=category_param)
        return qs


class EventDetailView(generics.RetrieveAPIView):
    """GET /api/mobile/events/<id>/"""
    serializer_class = EventDetailSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Event.objects.exclude(status=Event.STATUS_INACTIVE)


# ── Bracket ──────────────────────────────────────────────────────

class EventBracketView(generics.ListAPIView):
    """
    GET /api/mobile/events/<event_id>/bracket/
    Returns all teams and matches for an event.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, event_id):
        try:
            event = Event.objects.get(id=event_id)
        except Event.DoesNotExist:
            return Response({'detail': 'Event not found.'}, status=status.HTTP_404_NOT_FOUND)

        teams = BracketTeam.objects.filter(event=event)
        matches = BracketMatch.objects.filter(event=event)

        return Response({
            'event_id': event.id,
            'event_name': event.name,
            'teams': BracketTeamSerializer(teams, many=True, context={'request': request}).data,
            'matches': BracketMatchSerializer(matches, many=True, context={'request': request}).data,
        })


# ── Score Sheets ─────────────────────────────────────────────────

class EventScoreSheetsView(generics.ListAPIView):
    """
    GET /api/mobile/events/<event_id>/scoresheets/
    Returns finalized score sheets for an event.
    """
    serializer_class = ScoreSheetSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        event_id = self.kwargs['event_id']
        return ScoreSheet.objects.filter(
            event_id=event_id,
            status=ScoreSheet.STATUS_FINALIZED,
        ).select_related('event', 'match', 'winner')


# ── Judging Events ───────────────────────────────────────────────

class JudgingEventListView(generics.ListAPIView):
    """
    GET /api/mobile/judging-events/
    Returns all active/upcoming judging events with criteria and candidates.
    """
    serializer_class = MobileJudgingEventSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return JudgingEvent.objects.exclude(
            portal_event__status=Event.STATUS_INACTIVE
        ).prefetch_related('criteria', 'candidates')


class JudgingEventDetailView(generics.RetrieveAPIView):
    """GET /api/mobile/judging-events/<id>/"""
    serializer_class = MobileJudgingEventSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = JudgingEvent.objects.prefetch_related('criteria', 'candidates')


# ── Dashboard summary ────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def mobile_dashboard(request):
    """
    GET /api/mobile/dashboard/
    Returns summary counts for the mobile home screen.
    """
    total_events = Event.objects.exclude(status=Event.STATUS_INACTIVE).count()
    active_events = Event.objects.filter(status=Event.STATUS_ACTIVE).count()
    upcoming_events = Event.objects.filter(status=Event.STATUS_UPCOMING).count()
    completed_events = Event.objects.filter(status=Event.STATUS_COMPLETED).count()
    total_departments = Department.objects.filter(status=Department.STATUS_ACTIVE).count()

    # Latest 5 active/upcoming events
    latest_events = Event.objects.exclude(
        status__in=[Event.STATUS_INACTIVE, Event.STATUS_COMPLETED]
    ).order_by('event_date')[:5]

    return Response({
        'summary': {
            'total_events': total_events,
            'active_events': active_events,
            'upcoming_events': upcoming_events,
            'completed_events': completed_events,
            'total_departments': total_departments,
        },
        'latest_events': EventListSerializer(
            latest_events, many=True, context={'request': request}
        ).data,
    })
