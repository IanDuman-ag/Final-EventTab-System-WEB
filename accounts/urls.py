from django.urls import path

from . import views

urlpatterns = [
    path('register/', views.register, name='auth_register'),
    path('login/', views.login, name='auth_login'),
    path('logout/', views.logout, name='auth_logout'),
    path('forgot-password/', views.forgot_password, name='auth_forgot_password'),
    path('reset-password/', views.reset_password, name='auth_reset_password'),
    path('me/', views.me, name='auth_me'),
]
