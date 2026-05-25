"""
Mobile API URL patterns.
Mount at: path('api/mobile/', include('events.mobile_urls'))
"""
from django.urls import path
from . import mobile_views

urlpatterns = [
    # Dashboard
    path('dashboard/', mobile_views.mobile_dashboard, name='mobile_dashboard'),

    # Departments
    path('departments/', mobile_views.DepartmentListView.as_view(), name='mobile_departments'),

    # Events
    path('events/', mobile_views.EventListView.as_view(), name='mobile_events'),
    path('events/<int:pk>/', mobile_views.EventDetailView.as_view(), name='mobile_event_detail'),
    path('events/<int:event_id>/bracket/', mobile_views.EventBracketView.as_view(), name='mobile_event_bracket'),
    path('events/<int:event_id>/scoresheets/', mobile_views.EventScoreSheetsView.as_view(), name='mobile_event_scoresheets'),

    # Judging events
    path('judging-events/', mobile_views.JudgingEventListView.as_view(), name='mobile_judging_events'),
    path('judging-events/<int:pk>/', mobile_views.JudgingEventDetailView.as_view(), name='mobile_judging_event_detail'),
]
