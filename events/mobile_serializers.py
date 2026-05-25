"""
Mobile API serializers — expose admin-created data to the Flutter app.
All read-only. Auth: Token (same as judging app).
"""
from rest_framework import serializers
from .models import (
    Event, Department, BracketTeam, BracketMatch, ScoreSheet,
    JudgingEvent, Criterion, Candidate, JudgeScore, EventCategory,
)


# ── Department ──────────────────────────────────────────────────

class DepartmentSerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = Department
        fields = [
            'id', 'name', 'code', 'unit_number',
            'delegation_color', 'logo_url', 'head', 'status',
        ]

    def get_logo_url(self, obj):
        request = self.context.get('request')
        if obj.logo and request:
            return request.build_absolute_uri(obj.logo.url)
        return None


# ── Event ────────────────────────────────────────────────────────

class EventListSerializer(serializers.ModelSerializer):
    """Lightweight list — used for event cards in the mobile app."""
    image_url = serializers.SerializerMethodField()
    schedule_label = serializers.CharField(read_only=True)

    class Meta:
        model = Event
        fields = [
            'id', 'name', 'category', 'division', 'department',
            'event_date', 'event_time', 'venue', 'status',
            'image_url', 'schedule_label',
        ]

    def get_image_url(self, obj):
        request = self.context.get('request')
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        return None


class EventDetailSerializer(serializers.ModelSerializer):
    """Full detail — used for the event detail screen."""
    image_url = serializers.SerializerMethodField()
    schedule_label = serializers.CharField(read_only=True)
    team_count = serializers.SerializerMethodField()
    match_count = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            'id', 'name', 'category', 'division', 'department',
            'event_date', 'event_time', 'venue', 'status',
            'max_participants', 'num_teams',
            'faculty_in_charge', 'student_in_charge',
            'mechanics', 'scoring_criteria',
            'image_url', 'schedule_label',
            'team_count', 'match_count',
        ]

    def get_image_url(self, obj):
        request = self.context.get('request')
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        return None

    def get_team_count(self, obj):
        return obj.bracket_teams.count()

    def get_match_count(self, obj):
        return obj.bracket_matches.count()


# ── Bracket ──────────────────────────────────────────────────────

class BracketTeamSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(
        source='department.name', default=None, read_only=True
    )
    department_color = serializers.CharField(
        source='department.delegation_color', default=None, read_only=True
    )

    class Meta:
        model = BracketTeam
        fields = [
            'id', 'name', 'seed', 'loss_count',
            'members', 'department_name', 'department_color',
        ]


class BracketMatchSerializer(serializers.ModelSerializer):
    team_a_name = serializers.CharField(source='team_a.name', default=None, read_only=True)
    team_b_name = serializers.CharField(source='team_b.name', default=None, read_only=True)
    winner_name = serializers.CharField(source='winner.name', default=None, read_only=True)

    class Meta:
        model = BracketMatch
        fields = [
            'id', 'match_number', 'round_name',
            'team_a', 'team_a_name',
            'team_b', 'team_b_name',
            'winner', 'winner_name',
            'score_a', 'score_b',
            'status', 'match_date', 'match_time', 'venue',
        ]


# ── ScoreSheet ───────────────────────────────────────────────────

class ScoreSheetSerializer(serializers.ModelSerializer):
    event_name = serializers.CharField(source='event.name', read_only=True)
    match_number = serializers.IntegerField(source='match.match_number', read_only=True)
    winner_name = serializers.CharField(source='winner.name', default=None, read_only=True)

    class Meta:
        model = ScoreSheet
        fields = [
            'id', 'event', 'event_name',
            'match', 'match_number',
            'score_team_a', 'score_team_b',
            'winner', 'winner_name',
            'judges_names', 'status', 'created_at',
        ]


# ── Judging (re-export for mobile convenience) ───────────────────

class MobileCriterionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Criterion
        fields = ['id', 'name', 'description', 'max_score', 'weight_percent', 'order']


class MobileCandidateSerializer(serializers.ModelSerializer):
    photo_url = serializers.SerializerMethodField()
    total_score = serializers.SerializerMethodField()

    class Meta:
        model = Candidate
        fields = ['id', 'number', 'name', 'description', 'photo_url', 'total_score']

    def get_photo_url(self, obj):
        request = self.context.get('request')
        if obj.photo and request:
            return request.build_absolute_uri(obj.photo.url)
        return None

    def get_total_score(self, obj):
        """Average total across all judges for this candidate."""
        scores = JudgeScore.objects.filter(candidate=obj)
        if not scores.exists():
            return None
        total = sum(float(s.score) for s in scores)
        return round(total / scores.count(), 2)


class MobileJudgingEventSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_type = serializers.CharField(source='category.category_type', read_only=True)
    criteria = MobileCriterionSerializer(many=True, read_only=True)
    candidates = serializers.SerializerMethodField()

    class Meta:
        model = JudgingEvent
        fields = [
            'id', 'title', 'category_name', 'category_type',
            'date', 'time', 'venue', 'status', 'description',
            'criteria', 'candidates',
        ]

    def get_candidates(self, obj):
        candidates = obj.candidates.all()
        return MobileCandidateSerializer(
            candidates, many=True, context=self.context
        ).data
