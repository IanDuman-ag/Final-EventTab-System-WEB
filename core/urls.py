"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from .views import (
    admin_departments,
    admin_dashboard,
    admin_event_progress,
    admin_ocr,
    admin_scoresheets,
    admin_manage_account,
    admin_manage_events,
    activate_admin,
    create_assignment_account,
    create_admin,
    deactivate_admin,
    delete_assignment_account,
    delete_admin,
    login_view,
    logout_view,
    superadmin_activity_logs,
    superadmin_admins,
    superadmin_dashboard,
    superadmin_reports,
    tabulator_assigned_events,
    tabulator_dashboard,
    tabulator_scoresheets,
    update_assignment_account,
    update_admin,
)

urlpatterns = [
    path('', login_view, name='home'),
    path('login/', login_view, name='login'),
    path('tabulator/dashboard/', tabulator_dashboard, name='tabulator_dashboard'),
    path('tabulator/assigned/', tabulator_assigned_events, name='tabulator_assigned'),
    path('tabulator/scoresheets/', tabulator_scoresheets, name='tabulator_scoresheets'),
    path('admin/dashboard/', admin_dashboard, name='admin_dashboard'),
    path('admin/departments/', admin_departments, name='admin_departments'),
    path('admin/events/', admin_manage_events, name='admin_manage_events'),
    path('admin/results/', admin_event_progress, name='admin_event_progress'),
    path('admin/scoresheets/', admin_scoresheets, name='admin_scoresheets'),
    path('admin/ocr/', admin_ocr, name='admin_ocr'),
    path('admin/manage-account/', admin_manage_account, name='admin_manage_account'),
    path('admin/accounts/create/', create_assignment_account, name='create_assignment_account'),
    path('admin/accounts/<int:account_id>/update/', update_assignment_account, name='update_assignment_account'),
    path('admin/accounts/<int:account_id>/delete/', delete_assignment_account, name='delete_assignment_account'),
    path('super-admin/dashboard/', superadmin_dashboard, name='superadmin_dashboard'),
    path('super-admin/admins/', superadmin_admins, name='superadmin_admins'),
    path('super-admin/reports/', superadmin_reports, name='superadmin_reports'),
    path('super-admin/activity-logs/', superadmin_activity_logs, name='superadmin_activity_logs'),
    path('super-admin/admins/create/', create_admin, name='create_admin'),
    path('super-admin/admins/<int:admin_id>/update/', update_admin, name='update_admin'),
    path('super-admin/admins/<int:admin_id>/delete/', delete_admin, name='delete_admin'),
    path('super-admin/admins/<int:admin_id>/activate/', activate_admin, name='activate_admin'),
    path('super-admin/admins/<int:admin_id>/deactivate/', deactivate_admin, name='deactivate_admin'),
    path('logout/', logout_view, name='logout'),
    path('admin/', admin.site.urls),
]
