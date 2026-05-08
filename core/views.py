from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import Group
from django.shortcuts import redirect, render


ROLE_CHOICES = {
    'super-admin': 'Super Admin',
    'admin': 'Admin',
    'tabulator': 'Tabulator',
    'viewers': 'Viewers',
}


def authenticate_email(request, email, password):
    user = authenticate(request, username=email, password=password)
    if user is not None:
        return user

    User = get_user_model()
    user_match = User.objects.filter(email__iexact=email).first()
    if user_match is None:
        return None

    return authenticate(request, username=user_match.get_username(), password=password)


def user_has_role(user, role):
    if role == 'super-admin':
        return user.is_superuser

    if role == 'admin':
        return user.is_staff or user.groups.filter(name__iexact='Admin').exists()

    return user.groups.filter(name__iexact=ROLE_CHOICES[role]).exists()


def login_view(request):
    context = {'roles': ROLE_CHOICES}

    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        role = request.POST.get('role', '')

        context.update({
            'email': email,
            'selected_role': role,
        })

        if role not in ROLE_CHOICES:
            context['error'] = 'Select a valid role.'
            return render(request, 'login.html', context)

        user = authenticate_email(request, email, password)
        if user is None:
            context['error'] = 'Invalid email or password.'
            return render(request, 'login.html', context)

        if not user_has_role(user, role):
            context['error'] = 'Your account does not match the selected role.'
            return render(request, 'login.html', context)

        login(request, user)
        request.session['login_role'] = role
        if role == 'super-admin':
            return redirect('superadmin_dashboard')

        context['success'] = f'Logged in as {ROLE_CHOICES[role]}.'

    return render(request, 'login.html', context)


@login_required
@user_passes_test(lambda user: user.is_superuser, login_url='login')
def superadmin_dashboard(request):
    User = get_user_model()
    admins = User.objects.filter(is_staff=True).count()
    departments = Group.objects.count()
    total_users = User.objects.count()
    users = User.objects.order_by('id')[:5]

    return render(request, 'superadmin_dashboard.html', {
        'admins': admins,
        'departments': departments,
        'total_users': total_users,
        'users': users,
    })


def logout_view(request):
    logout(request)
    return redirect('login')
