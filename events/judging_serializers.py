from rest_framework import serializers

from .models import Candidate, Criterion, EventCategory, JudgeScore, JudgingEvent


class EventCategorySerializer(serializers.ModelSerializer):
    event_count = serializers.SerializerMethodField()

    class Meta:
        model = EventCategory
        fields = ['id', 'name', 'category_type', 'description', 'icon', 'color', 'event_count']

    def get_event_count(self, obj):
        request = self.context.get('request')
        if request is not None:
            from .mobile_judge import assigned_events
            return assigned_events(request.user).filter(category=obj).count()
        return obj.events.count()


class CriterionSerializer(serializers.ModelSerializer):
    subcriteria = serializers.SerializerMethodField()

    class Meta:
        model = Criterion
        fields = ['id', 'name', 'description', 'max_score', 'weight_percent', 'order', 'subcriteria']

    def get_subcriteria(self, obj):
        return [{'id': row.id, 'name': row.name, 'max_score': str(row.max_score),
                 'display_order': row.display_order} for row in obj.subcriteria.all()]


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

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        if request is not None:
            from .mobile_judge import detail_payload
            detail = detail_payload(request.user, instance, request.query_params.get('round_id'))
            criterion_ids = [row['id'] for row in detail['criteria']]
            candidate_ids = [row['id'] for row in detail['participants'] if row['is_qualified']]
            data.update(criteria=CriterionSerializer(instance.criteria.filter(pk__in=criterion_ids), many=True).data,
                        candidates=CandidateSerializer(instance.candidates.filter(pk__in=candidate_ids), many=True).data,
                        candidate_count=len(candidate_ids), rounds=detail['stages'],
                        active_round=detail['current_stage'], round_id=detail['selected_round_id'],
                        segment_performance_orders=detail.get('segment_performance_orders', []))
            if detail.get('divisions_enabled'):
                stored = {row['id']: row for row in data['candidates']}
                data['candidates'] = [dict(stored[row['id']], division=row['division'], appearance_order=row['appearance_order'], number=row['number'], candidate_number=row.get('candidate_number', str(row['number'])))
                    for row in detail['participants'] if row['is_qualified'] and row['id'] in stored]
                data['divisions_enabled'] = True
                data['divisions'] = [{'name': group['name'], 'candidate_ids': [row['id'] for row in group['participants'] if row['is_qualified']]} for group in detail['divisions']]
        return data


class JudgeScoreSerializer(serializers.ModelSerializer):
    is_locked = serializers.SerializerMethodField()

    def get_is_locked(self, obj):
        from .mobile_scoring_compatibility import legacy_score_is_submitted
        return legacy_score_is_submitted(obj)

    criterion_name = serializers.CharField(source='criterion.name', read_only=True)
    criterion_max = serializers.DecimalField(
        source='criterion.max_score', max_digits=7, decimal_places=2, read_only=True,
    )
    subcriterion_scores = serializers.SerializerMethodField()

    def get_subcriterion_scores(self, obj):
        return [{'subcriterion_id': row.subcriterion_id, 'name': row.subcriterion.name,
                 'score': str(row.score), 'max_score': str(row.subcriterion.max_score)}
                for row in obj.subcriterion_scores.select_related('subcriterion').all()]

    class Meta:
        model = JudgeScore
        fields = [
            'id', 'criterion', 'criterion_name', 'criterion_max',
            'score', 'subcriterion_scores', 'is_locked', 'submitted_at', 'verification_id',
        ]


class SubmitScoreItemSerializer(serializers.Serializer):
    criterion_id = serializers.IntegerField(min_value=1)
    score = serializers.DecimalField(max_digits=7, decimal_places=2, required=False)
    subcriteria = serializers.ListField(child=serializers.DictField(), required=False)

    def validate(self, value):
        if 'score' not in value and 'subcriteria' not in value:
            raise serializers.ValidationError('A score or subcriteria scores are required.')
        return value


class SubmitScoresSerializer(serializers.Serializer):
    candidate_id = serializers.IntegerField(min_value=1)
    round_id = serializers.CharField(required=False)
    assigned_round_id = serializers.CharField(required=False)
    scores = SubmitScoreItemSerializer(many=True, allow_empty=False)
