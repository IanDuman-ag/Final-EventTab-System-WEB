
from datetime import date

from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.views import get_assignment_role
from .models import JudgingEvent


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def judge_dashboard(request):
    user = request.user

    # Only authorized judges can access this endpoint.
    if get_assignment_role(user) != "Judge":
        return Response(
            {"detail": "Judge access required."},
            status=403,
        )

    # Only events assigned to this judge.
    assigned = (
        JudgingEvent.objects
        .filter(assigned_judges=user)
        .select_related("category")
        .distinct()
        .order_by("date", "time")
    )

    events = []

    for event in assigned:
        if event.status == "completed":
            status_value = "completed"
        elif event.status == "active":
            status_value = "ongoing"
        else:
            status_value = "upcoming"

        events.append({
            "id": event.id,
            "title": event.title,
            "status": status_value,
            "date": event.date.isoformat() if event.date else None,
            "time": event.time.isoformat() if event.time else None,
            "venue": event.venue,
            "assignment_type": "CRITERIA BASED",
            "criteria_count": event.criteria.count(),
            "candidate_count": event.candidates.count(),
        })

    today = date.today()

    todays_assignments = [
        e for e in events if e["date"] == today.isoformat()
    ]

    upcoming_events = [
        e for e in events if e["status"] == "upcoming"
    ]

    return Response({
        "greeting_name": user.first_name or user.username,
        "assigned_events": events,
        "todays_assignments": todays_assignments,
        "upcoming_events": upcoming_events,
        "stats": {
            "total_events": len(events),
            "ongoing_events": sum(
                1 for e in events if e["status"] == "ongoing"
            ),
            "completed_events": sum(
                1 for e in events if e["status"] == "completed"
            ),
        },
        "last_submission": None,
        "notification_count": 0,
    })
