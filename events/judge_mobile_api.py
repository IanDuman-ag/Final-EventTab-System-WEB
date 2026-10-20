
from zoneinfo import ZoneInfo

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.views import get_assignment_role
from .mobile_judge import assigned_events, detail_payload


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

    # Use the same authoritative portal assignments as the Events screen.
    # Legacy mirror assignments may be missing for upcoming events or stale
    # after an admin reassigns a judge.
    events = []
    for event in assigned_events(user).order_by("date", "time"):
        row = detail_payload(user, event)
        if row["status"] not in ("ongoing", "completed", "upcoming"):
            row["status"] = "upcoming"
        row["candidate_count"] = row["participant_count"]
        portal = getattr(event, "portal_event", None)
        clock = portal.event_time if portal else event.time
        row["time"] = clock.isoformat() if clock else None
        events.append(row)

    today = timezone.localdate(timezone=ZoneInfo('Asia/Manila'))

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
