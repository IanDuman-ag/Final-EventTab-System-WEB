from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .judging_views import EventCategoryViewSet, JudgingEventViewSet
from .judge_mobile_api import judge_dashboard
from . import mobile_judge, mobile_viewer

router = DefaultRouter()
router.register(r'categories', EventCategoryViewSet)
router.register(r'judging-events', JudgingEventViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('judge/dashboard/', judge_dashboard, name='mobile_judge_dashboard'),
    path('judge/assignments/', mobile_judge.assignments, name='mobile_judge_assignments'),
    path('judge/assignments/<int:judging_event_id>/', mobile_judge.assignment_detail, name='mobile_judge_assignment_detail'),
    path('judge/portal-assignments/<int:portal_event_id>/', mobile_judge.portal_assignment_detail, name='mobile_judge_portal_assignment_detail'),
    path('judge/score-history/', mobile_judge.score_history, name='mobile_judge_score_history'),
    path('judge/profile/', mobile_judge.profile, name='mobile_judge_profile'),
    path('judge-notifications/', mobile_judge.notifications, name='mobile_judge_notifications'),
    path('judge/notifications/', mobile_judge.notifications, name='mobile-judge-notifications'),
    path('viewer/dashboard/', mobile_viewer.dashboard, name='mobile_viewer_dashboard'),
    path('viewer/events/', mobile_viewer.events, name='mobile_viewer_events'),
    path('viewer/rankings/', mobile_viewer.rankings, name='mobile_viewer_rankings'),
    path('viewer/live/', mobile_viewer.live, name='mobile_viewer_live'),
    path('viewer/bracket/', mobile_viewer.bracket, name='mobile_viewer_bracket'),
    path('viewer/profile/', mobile_viewer.profile, name='mobile_viewer_profile'),
    path('viewer/announcements/', mobile_viewer.announcements, name='mobile_viewer_announcements'),
    path('viewer/notifications/', mobile_viewer.notifications, name='mobile_viewer_notifications'),
    path('viewer/about/', mobile_viewer.about, name='mobile_viewer_about'),
    path('viewer/criteria-events/<int:judging_event_id>/', mobile_viewer.criteria_detail, name='mobile_viewer_criteria_detail'),
    path('viewer/match-events/<int:match_id>/', mobile_viewer.match_detail, name='mobile_viewer_match_detail'),
]

# Retain deployed reverse names alongside the local names. Both names resolve
# to the same path/view; the live dashboard remains the deployed implementation.
urlpatterns += [
    path('judge/dashboard/', judge_dashboard, name='judge-mobile-dashboard'),
    path('judge/assignments/', mobile_judge.assignments, name='mobile-judge-assignments'),
    path('judge/assignments/<int:judging_event_id>/', mobile_judge.assignment_detail, name='mobile-judge-assignment-detail'),
    path('judge/portal-assignments/<int:portal_event_id>/', mobile_judge.portal_assignment_detail, name='mobile-judge-portal-assignment-detail'),
    path('judge/score-history/', mobile_judge.score_history, name='mobile-judge-score-history'),
    path('judge/profile/', mobile_judge.profile, name='mobile-judge-profile'),
    path('viewer/dashboard/', mobile_viewer.dashboard, name='mobile-viewer-dashboard'),
    path('viewer/events/', mobile_viewer.events, name='mobile-viewer-events'),
    path('viewer/rankings/', mobile_viewer.rankings, name='mobile-viewer-rankings'),
    path('viewer/live/', mobile_viewer.live, name='mobile-viewer-live'),
    path('viewer/bracket/', mobile_viewer.bracket, name='mobile-viewer-bracket'),
    path('viewer/profile/', mobile_viewer.profile, name='mobile-viewer-profile'),
    path('viewer/announcements/', mobile_viewer.announcements, name='mobile-viewer-announcements'),
    path('viewer/notifications/', mobile_viewer.notifications, name='mobile-viewer-notifications'),
    path('viewer/about/', mobile_viewer.about, name='mobile-viewer-about'),
    path('viewer/criteria-events/<int:judging_event_id>/', mobile_viewer.criteria_detail, name='mobile-viewer-criteria-detail'),
    path('viewer/match-events/<int:match_id>/', mobile_viewer.match_detail, name='mobile-viewer-match-detail'),
]
