from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .judging_views import EventCategoryViewSet, JudgingEventViewSet

router = DefaultRouter()
router.register(r'categories', EventCategoryViewSet)
router.register(r'judging-events', JudgingEventViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
