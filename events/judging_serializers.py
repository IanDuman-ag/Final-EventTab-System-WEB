from rest_framework import serializers

from .models import Candidate, Criterion, EventCategory, JudgeScore, JudgingEvent


class EventCategorySerializer(serializers.ModelSerializer):
    event_count = serializers.SerializerMethodField()

    class Meta:
        model = EventCategory
        fields = ['id', 'name', 'category_type', 'description', 'icon', 'color', 'event_count']

    def get_event_count(self, obj):
        return obj.events.count()


class CriterionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Criterion
        fields = ['id', 'name', 'description', 'max_score', 'weight_percent', 'order']


class CandidateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Candidate
        fields = ['id', 'name', 'number', 'photo', 'description']


class JudgingEventListSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_type = serializers.CharField(source='category.category_type', read_only=True)
    candidate_count = serializers.SerializerMethodField()

    class Meta:
        model = JudgingEvent
        fields = [
            'id', 'title', 'category_name', 'category_type',
            'date', 'time', 'venue', 'status', 'candidate_count',
        ]

    def get_candidate_count(self, obj):
        return obj.candidates.count()


class JudgingEventDetailSerializer(serializers.ModelSerializer):
    criteria = CriterionSerializer(many=True, read_only=True)
    candidates = CandidateSerializer(many=True, read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_type = serializers.CharField(source='category.category_type', read_only=True)
    candidate_count = serializers.SerializerMethodField()

    class Meta:
        model = JudgingEvent
        fields = [
            'id', 'title', 'category_name', 'category_type',
            'date', 'time', 'venue', 'status', 'description',
            'criteria', 'candidates', 'candidate_count',
        ]

    def get_candidate_count(self, obj):
        return obj.candidates.count()


class JudgeScoreSerializer(serializers.ModelSerializer):
    criterion_name = serializers.CharField(source='criterion.name', read_only=True)
    criterion_max = serializers.DecimalField(
        source='criterion.max_score', max_digits=5, decimal_places=1, read_only=True,
    )

    class Meta:
        model = JudgeScore
        fields = [
            'id', 'criterion', 'criterion_name', 'criterion_max',
            'score', 'is_locked', 'submitted_at', 'verification_id',
        ]


class SubmitScoresSerializer(serializers.Serializer):
    candidate_id = serializers.IntegerField()
    scores = serializers.ListField(child=serializers.DictField())

    def validate_scores(self, value):
        for item in value:
            if 'criterion_id' not in item or 'score' not in item:
                raise serializers.ValidationError('Each score must have criterion_id and score.')
        return value
