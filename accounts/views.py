from random import randint

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.mail import send_mail
from django.conf import settings
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .serializers import ForgotPasswordSerializer, LoginSerializer, RegisterSerializer

User = get_user_model()
_RESET_TOKEN_TTL = 60 * 2


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    return xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR')


def _user_payload(user):
    return {
        'id': user.id,
        'username': user.username,
        'email': user.email,
    }


@api_view(['POST'])
@permission_classes([AllowAny])
def register(request):
    serializer = RegisterSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    user = serializer.save()
    token, _ = Token.objects.get_or_create(user=user)
    return Response({'token': token.key, 'user': _user_payload(user)}, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([AllowAny])
def login(request):
    """
    Mobile + web API login.
    Body: { identifier, password } where identifier is email or username.
    Optional: { require_judge: true } to restrict to Judge accounts.
    """
    serializer = LoginSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    user = serializer.validated_data['user']
    token, _ = Token.objects.get_or_create(user=user)

    from core.views import get_assignment_role
    if get_assignment_role(user) == 'Judge':
        from events.models import JudgeActivityLog
        JudgeActivityLog.log(
            judge=user,
            action=JudgeActivityLog.ACTION_LOGIN,
            details=f'Judge logged in via mobile app',
            ip_address=_client_ip(request),
        )

    return Response({'token': token.key, 'user': _user_payload(user)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout(request):
    from core.views import get_assignment_role
    if get_assignment_role(request.user) == 'Judge':
        from events.models import JudgeActivityLog
        JudgeActivityLog.log(
            judge=request.user,
            action=JudgeActivityLog.ACTION_LOGOUT,
            details='Judge logged out of mobile app',
            ip_address=_client_ip(request),
        )

    Token.objects.filter(user=request.user).delete()
    return Response({'detail': 'Logged out.'}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([AllowAny])
def forgot_password(request):
    serializer = ForgotPasswordSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    email = serializer.validated_data['email']
    try:
        user = User.objects.get(email__iexact=email)
        reset_code = str(randint(100000, 999999))
        cache.set(f'pwd_reset:{reset_code}', user.pk, timeout=_RESET_TOKEN_TTL)
        send_mail(
            subject='EventTab - Password Reset Code',
            message=(
                f'Hello {user.username},\n\n'
                f'Your reset code is: {reset_code}\n\n'
                'This code expires in 2 minutes.\n\n— EventTab Team'
            ),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@eventtab.local'),
            recipient_list=[user.email],
            fail_silently=True,
        )
    except User.DoesNotExist:
        pass

    return Response({'detail': 'If that email is registered, a reset code has been sent.'})


@api_view(['POST'])
@permission_classes([AllowAny])
def reset_password(request):
    reset_token = request.data.get('reset_token', '').strip()
    new_password = request.data.get('new_password', '')
    confirm_password = request.data.get('confirm_password', '')

    if not reset_token or not new_password or not confirm_password:
        return Response(
            {'detail': 'reset_token, new_password, and confirm_password are required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if new_password != confirm_password:
        return Response({'detail': 'Passwords do not match.'}, status=status.HTTP_400_BAD_REQUEST)

    user_pk = cache.get(f'pwd_reset:{reset_token}')
    if user_pk is None:
        return Response(
            {'detail': 'Invalid or expired reset code. Request a new one.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        user = User.objects.get(pk=user_pk)
    except User.DoesNotExist:
        return Response({'detail': 'User not found.'}, status=status.HTTP_400_BAD_REQUEST)

    user.set_password(new_password)
    user.save()
    cache.delete(f'pwd_reset:{reset_token}')
    Token.objects.filter(user=user).delete()
    return Response({'detail': 'Password updated successfully.'})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me(request):
    return Response(_user_payload(request.user))
