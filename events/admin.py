from django.contrib import admin

from .models import (
    BracketMatch,
    BracketTeam,
    Candidate,
    Criterion,
    Department,
    Event,
    EventCategory,
    JudgeScore,
    JudgingEvent,
    ScoreSheet,
)


@admin.register(EventCategory)
class EventCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'category_type')


class CriterionInline(admin.TabularInline):
    model = Criterion
    extra = 0


class CandidateInline(admin.TabularInline):
    model = Candidate
    extra = 0


@admin.register(JudgingEvent)
class JudgingEventAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'date', 'status')
    filter_horizontal = ('assigned_judges',)
    inlines = [CriterionInline, CandidateInline]


admin.site.register(Event)
admin.site.register(Department)
admin.site.register(BracketTeam)
admin.site.register(BracketMatch)
admin.site.register(ScoreSheet)
admin.site.register(JudgeScore)
