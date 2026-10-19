from urllib.parse import urlencode

from django.conf import settings
from django.db import DatabaseError, connection
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@never_cache
@require_GET
def portal_notifications(request):
    """Read-only updates scoped to the authenticated dashboard role."""
    from events.models import AuditLog, Event
    from events.faculty_service import is_faculty_user

    user = request.user
    if not user.is_authenticated:
        return JsonResponse({'message': 'Please sign in to view notifications.'}, status=401)
    if not user.is_active:
        return JsonResponse({'message': 'Account is inactive.'}, status=403)
    if not (user.is_staff or user.is_superuser or is_faculty_user(user)):
        return JsonResponse({'message': 'Dashboard access required.'}, status=403)
    events = Event.objects.all()
    if not (user.is_staff or user.is_superuser):
        assigned = Q(faculty_account=user) | Q(assigned_tabulators=user)
        for name in (user.username, user.get_full_name().strip()):
            if name:
                assigned |= Q(faculty_account__isnull=True, faculty_in_charge__iexact=name)
        events = events.filter(assigned).distinct()
    notes = []
    for event in events.order_by('-updated_at', '-pk')[:8]:
        if user.is_superuser:
            url = reverse('superadmin_event_detail', args=[event.pk])
        elif user.is_staff:
            route = 'admin_view_match_event' if event.scoring_method == 'match' else 'admin_view_criteria_event'
            url = reverse(route, args=[event.pk])
        else:
            url = reverse('faculty_event_manage', args=[event.pk])
        notes.append({'title': event.name, 'message': f'Event update · {event.get_status_display()}',
                      'url': url, 'timestamp': event.updated_at})
    if user.is_superuser:
        for log in AuditLog.objects.order_by('-created_at', '-pk')[:8]:
            notes.append({'title': log.action, 'message': (log.description or log.module)[:180],
                          'url': reverse('superadmin_activity_logs') + '?' + urlencode({'q': log.action}),
                          'timestamp': log.created_at})
    notes.sort(key=lambda note: note['timestamp'], reverse=True)
    for note in notes:
        note['timestamp'] = timezone.localtime(note['timestamp']).isoformat()
    return JsonResponse({'notifications': notes[:8], 'count': min(8, len(notes))})


def health_check(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except DatabaseError as error:
        return JsonResponse(
            {
                'status': 'error',
                'backend': 'django',
                'database': {
                    'name': settings.DATABASES['default']['NAME'],
                    'host': settings.DATABASES['default']['HOST'],
                },
                'message': str(error),
            },
            status=503,
        )

    return JsonResponse({'status': 'ok', 'backend': 'django'})
