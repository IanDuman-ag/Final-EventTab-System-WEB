from django.conf import settings
from django.db import DatabaseError, connection
from django.http import JsonResponse


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
