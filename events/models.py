from django.db import models
from django.db.models import Q
from django.contrib.auth import get_user_model

User = get_user_model()


class Event(models.Model):
    PUBLICATION_DRAFT = 'draft'
    PUBLICATION_PUBLISHED = 'published'
    PUBLICATION_CHOICES = [
        (PUBLICATION_DRAFT, 'Draft'),
        (PUBLICATION_PUBLISHED, 'Published'),
    ]
    CLASSIFICATION_MAJOR = 'major'
    CLASSIFICATION_MINOR = 'minor'
    CLASSIFICATION_CHOICES = [
        (CLASSIFICATION_MAJOR, 'Major Event'),
        (CLASSIFICATION_MINOR, 'Minor Event'),
    ]
    SCHEDULE_AUTO = 'auto'
    SCHEDULE_MANUAL = 'manual'
    SCHEDULE_MODE_CHOICES = [
        (SCHEDULE_AUTO, 'Auto Generate Schedule'),
        (SCHEDULE_MANUAL, 'Manual Scheduling'),
    ]
    STATUS_UPCOMING   = 'upcoming'
    STATUS_ACTIVE     = 'active'
    STATUS_COMPLETED  = 'completed'
    STATUS_INACTIVE   = 'inactive'
    STATUS_CHOICES = [
        (STATUS_UPCOMING,  'Upcoming'),
        (STATUS_ACTIVE,    'Active'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_INACTIVE,  'Deactivated'),
    ]
    name               = models.CharField(max_length=200)
    academic_year      = models.CharField(
        max_length=40,
        blank=True,
        default='',
        help_text='Intramurals season / academic year this event belongs to.',
    )
    category           = models.CharField(max_length=100)
    division           = models.CharField(max_length=100, blank=True, default='')
    department         = models.CharField(max_length=150, blank=True, default='')
    event_date         = models.DateField()
    end_date           = models.DateField(null=True, blank=True)
    event_time         = models.TimeField(null=True, blank=True)
    venue              = models.CharField(max_length=200)
    image              = models.ImageField(upload_to='event_images/', null=True, blank=True)
    status             = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_UPCOMING)
    max_participants   = models.PositiveIntegerField(null=True, blank=True)
    num_teams          = models.PositiveIntegerField(null=True, blank=True)
    scoring_method     = models.CharField(max_length=20, blank=True, default='')
    tournament_type    = models.CharField(max_length=50, blank=True, default='')
    bracket_locked     = models.BooleanField(default=False)
    faculty_in_charge  = models.CharField(max_length=150, blank=True, default='')
    faculty_account    = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='faculty_events',
    )
    scoresheet_template = models.ForeignKey(
        'ScoresheetTemplate',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_events',
    )
    event_classification = models.CharField(
        max_length=10,
        choices=CLASSIFICATION_CHOICES,
        blank=True,
        default='',
    )
    publication_status = models.CharField(
        max_length=12,
        choices=PUBLICATION_CHOICES,
        default=PUBLICATION_PUBLISHED,
    )
    include_third_place = models.BooleanField(default=False)
    seeding_method = models.CharField(max_length=20, default='random_draw')
    pairing_method = models.CharField(
        max_length=20,
        default='random_draw',
        help_text='random_draw | seeded_draw | manual_pairing',
    )
    sport_type = models.CharField(max_length=80, blank=True, default='')
    sport_custom_name = models.CharField(max_length=120, blank=True, default='')
    result_entry_format = models.CharField(max_length=40, blank=True, default='')
    schedule_config = models.JSONField(default=dict, blank=True)
    assigned_scorer = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='scorer_events',
    )
    bracket_draw_order = models.JSONField(default=list, blank=True)
    schedule_mode = models.CharField(
        max_length=10,
        choices=SCHEDULE_MODE_CHOICES,
        default=SCHEDULE_AUTO,
    )
    daily_start_time = models.TimeField(null=True, blank=True)
    daily_end_time = models.TimeField(null=True, blank=True)
    auto_update_bracket = models.BooleanField(default=True)
    allow_result_editing = models.BooleanField(default=True)
    require_faculty_confirmation = models.BooleanField(default=False)
    apply_championship_points = models.BooleanField(default=True)
    championship_points_config = models.JSONField(default=list, blank=True)
    PARTICIPATION_TEAM = 'team'
    PARTICIPATION_GROUP = 'group'
    PARTICIPATION_INDIVIDUAL = 'individual'
    PARTICIPATION_CHOICES = [
        (PARTICIPATION_TEAM, 'Team'),
        (PARTICIPATION_GROUP, 'Group'),
        (PARTICIPATION_INDIVIDUAL, 'Individual'),
    ]
    FORMAT_SINGLE = 'single_performance'
    FORMAT_MULTIPLE_STAGE = 'multiple_stage'
    FORMAT_MULTIPLE = 'multiple_rounds'  # legacy alias
    FORMAT_PRELIM_FINAL = 'preliminary_final'  # legacy alias
    FORMAT_CHOICES = [
        (FORMAT_SINGLE, 'Single Performance'),
        (FORMAT_MULTIPLE_STAGE, 'Multiple Stage Competition'),
        (FORMAT_MULTIPLE, 'Multiple Rounds'),
        (FORMAT_PRELIM_FINAL, 'Preliminary and Final Round'),
    ]
    CRITERIA_SCORE_WEIGHTED = 'weighted_percentage'
    CRITERIA_SCORE_RAW = 'raw_score'
    CRITERIA_SCORE_AVERAGE = 'average_score'
    CRITERIA_SCORE_RANKING = 'ranking_based'
    CRITERIA_SCORE_CHOICES = [
        (CRITERIA_SCORE_WEIGHTED, 'Weighted Percentage'),
        (CRITERIA_SCORE_RAW, 'Raw Score'),
        (CRITERIA_SCORE_AVERAGE, 'Average Score'),
        (CRITERIA_SCORE_RANKING, 'Ranking-Based Score'),
    ]
    participation_type = models.CharField(
        max_length=20,
        choices=PARTICIPATION_CHOICES,
        default=PARTICIPATION_TEAM,
        blank=True,
    )
    event_format = models.CharField(
        max_length=40,
        choices=FORMAT_CHOICES,
        default=FORMAT_SINGLE,
        blank=True,
    )
    criteria_score_method = models.CharField(
        max_length=40,
        choices=CRITERIA_SCORE_CHOICES,
        default=CRITERIA_SCORE_WEIGHTED,
        blank=True,
    )
    rules_guidelines = models.TextField(blank=True, default='')
    rules_file = models.FileField(upload_to='event_rules/', null=True, blank=True)
    rounds_config = models.JSONField(default=list, blank=True)
    judging_criteria_config = models.JSONField(default=list, blank=True)
    score_settings = models.JSONField(default=dict, blank=True)
    deductions_enabled = models.BooleanField(default=False)
    deductions_config = models.JSONField(default=list, blank=True)
    result_processing_config = models.JSONField(default=dict, blank=True)
    judge_settings = models.JSONField(default=dict, blank=True)
    tie_break_rules = models.JSONField(default=list, blank=True)
    participant_ids = models.JSONField(default=list, blank=True)
    special_event_type = models.CharField(
        max_length=40,
        blank=True,
        default='',
        help_text='Subtype when category is Special Event (e.g. pageant).',
    )
    pageant_config = models.JSONField(
        default=dict,
        blank=True,
        help_text='Pageant wizard metadata: format, categories, awards, pairs, etc.',
    )
    chief_judge = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='chief_judge_events',
    )
    results_finalized = models.BooleanField(default=False)
    student_in_charge  = models.CharField(max_length=150, blank=True, default='')
    mechanics          = models.TextField(blank=True, default='')
    scoring_criteria   = models.TextField(blank=True, default='')
    champion_points    = models.PositiveIntegerField(null=True, blank=True)
    first_runner_points = models.PositiveIntegerField(null=True, blank=True)
    second_runner_points = models.PositiveIntegerField(null=True, blank=True)
    participation_points = models.PositiveIntegerField(null=True, blank=True)
    created_by         = models.ForeignKey(
        User,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='created_events',
    )
    created_at         = models.DateTimeField(auto_now_add=True)
    updated_at         = models.DateTimeField(auto_now=True)
    judging_event      = models.OneToOneField(
        'JudgingEvent',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='portal_event',
    )
    assigned_judges    = models.ManyToManyField(
        User,
        blank=True,
        related_name='judging_events',
        limit_choices_to=(
            Q(groups__name__iexact='Judge')
            | Q(groups__name__iexact='Judges')
            | Q(groups__name__iexact='Scorer')
        ),
    )
    assigned_tabulators = models.ManyToManyField(
        User,
        blank=True,
        related_name='tabulating_events',
        limit_choices_to=Q(groups__name__iexact='Tabulator'),
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    @property
    def schedule_label(self):
        return self.event_date.strftime('%B %d, %Y') if self.event_date else ''

    @property
    def event_time_str(self):
        return self.event_time.strftime('%H:%M') if self.event_time else ''


class EventScoringCategory(models.Model):
    JUDGE_MODE_SCORING = 'scoring'
    JUDGE_MODE_RANKING = 'ranking'
    JUDGE_MODE_CHOICES = [
        (JUDGE_MODE_SCORING, 'Scoring Mode'),
        (JUDGE_MODE_RANKING, 'Ranking Mode'),
    ]
    PURPOSE_OFFICIAL = 'official'
    PURPOSE_SPECIAL_AWARD = 'special_award'
    PURPOSE_CHOICES = [
        (PURPOSE_OFFICIAL, 'Official Scoring Category'),
        (PURPOSE_SPECIAL_AWARD, 'Special Award Only'),
    ]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='scoring_categories')
    assigned_round_id = models.CharField(max_length=80, blank=True, default='')
    name = models.CharField(max_length=120)
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, default=PURPOSE_OFFICIAL)
    category_purpose = models.TextField(blank=True, default='')
    judge_mode = models.CharField(max_length=12, choices=JUDGE_MODE_CHOICES)
    display_order = models.PositiveIntegerField()
    overall_weight_percent = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        ordering = ['display_order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['event', 'name'], name='unique_event_scoring_category_name'),
            models.UniqueConstraint(fields=['event', 'display_order'], name='unique_event_scoring_category_order'),
        ]

    def __str__(self):
        return f'{self.event.name} · {self.name}'


class EventScoringCriterion(models.Model):
    category = models.ForeignKey(EventScoringCategory, on_delete=models.CASCADE, related_name='criteria')
    name = models.CharField(max_length=120)
    weight_percent = models.DecimalField(max_digits=5, decimal_places=2)
    min_score = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    max_score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    judging_description = models.TextField(blank=True, default='')
    tie_breaker_priority = models.PositiveIntegerField(null=True, blank=True)
    display_order = models.PositiveIntegerField()

    class Meta:
        ordering = ['display_order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['category', 'name'], name='unique_category_criterion_name'),
            models.UniqueConstraint(fields=['category', 'display_order'], name='unique_category_criterion_order'),
        ]

    def __str__(self):
        return f'{self.category.name} · {self.name}'


class CriteriaScoreSubmission(models.Model):
    SOURCE_MOBILE = 'MOBILE'
    SOURCE_OCR = 'OCR'
    SOURCE_MANUAL = 'MANUAL'
    SOURCE_CHOICES = [
        (SOURCE_MOBILE, 'Judge Mobile'),
        (SOURCE_OCR, 'Physical Scoresheet OCR'),
        (SOURCE_MANUAL, 'Manual Backup'),
    ]
    STATUS_DRAFT = 'DRAFT'
    STATUS_SUBMITTED = 'SUBMITTED'
    STATUS_NEEDS_REVIEW = 'NEEDS_REVIEW'
    STATUS_VERIFIED = 'VERIFIED'
    STATUS_APPROVED = 'APPROVED'
    STATUS_REJECTED = 'REJECTED'
    STATUS_REOPENED = 'REOPENED'
    STATUS_SUPERSEDED = 'SUPERSEDED'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_SUBMITTED, 'Submitted'),
        (STATUS_NEEDS_REVIEW, 'Needs Review'),
        (STATUS_VERIFIED, 'Verified'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
        (STATUS_REOPENED, 'Reopened'),
        (STATUS_SUPERSEDED, 'Superseded'),
    ]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='criteria_score_submissions')
    assigned_round_id = models.CharField(max_length=80)
    category = models.ForeignKey(EventScoringCategory, on_delete=models.CASCADE, related_name='score_submissions')
    criterion = models.ForeignKey(EventScoringCriterion, on_delete=models.CASCADE, related_name='score_submissions')
    judge = models.ForeignKey(User, on_delete=models.CASCADE, related_name='criteria_score_submissions')
    participant_type = models.CharField(max_length=20, default='participant')
    participant_id = models.PositiveIntegerField()
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    raw_score = models.DecimalField(max_digits=9, decimal_places=4)
    normalized_score = models.DecimalField(max_digits=9, decimal_places=4, default=0)
    is_active = models.BooleanField(default=True)
    submission_key = models.CharField(max_length=120, blank=True, default='', db_index=True)
    replacement_reason = models.TextField(blank=True, default='')
    device_session = models.CharField(max_length=200, blank=True, default='')
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [
            'event_id', 'assigned_round_id', 'category_id',
            'judge_id', 'participant_id', 'criterion_id',
        ]
        indexes = [
            models.Index(fields=['event', 'assigned_round_id', 'category', 'judge', 'participant_id'], name='events_crit_event_i_332f9b_idx'),
            models.Index(fields=['event', 'status', 'source', 'is_active'], name='events_crit_event_i_4ca988_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['event', 'assigned_round_id', 'category', 'judge', 'participant_id', 'criterion'],
                condition=Q(is_active=True),
                name='unique_active_criteria_score_source',
            ),
        ]

    def __str__(self):
        return f'{self.event.name} - {self.assigned_round_id} - judge {self.judge_id}'


class Department(models.Model):
    STATUS_ACTIVE = 'active'
    STATUS_INACTIVE = 'inactive'
    STATUS_ARCHIVED = 'archived'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_INACTIVE, 'Inactive'),
        (STATUS_ARCHIVED, 'Archived'),
    ]

    name = models.CharField(max_length=200)
    code = models.CharField(max_length=50, unique=True)
    unit_number = models.CharField(max_length=50, blank=True, default='')
    delegation_color = models.CharField(max_length=20, default='#022068')
    logo = models.ImageField(upload_to='department_logos/', null=True, blank=True)
    head = models.CharField(max_length=200, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    remarks = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class Team(models.Model):
    STATUS_ACTIVE = 'active'
    STATUS_INACTIVE = 'inactive'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_INACTIVE, 'Inactive'),
    ]

    name = models.CharField(max_length=200)
    code = models.CharField(max_length=50, unique=True)
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='teams',
    )
    image = models.ImageField(upload_to='teams/', null=True, blank=True)
    members = models.TextField(blank=True, default='')
    coach = models.CharField(max_length=200, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class RegistryCandidate(models.Model):
    """Standalone candidate registry for criteria-based events."""
    STATUS_ACTIVE = 'active'
    STATUS_INACTIVE = 'inactive'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_INACTIVE, 'Inactive'),
    ]

    number = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='registry_candidates',
    )
    image = models.ImageField(upload_to='candidates/', null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['number', 'name']

    def __str__(self):
        return f'#{self.number} {self.name}'


class BracketTeam(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='bracket_teams')
    source_team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='event_snapshots',
    )
    name = models.CharField(max_length=200)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='bracket_teams')
    members = models.TextField(blank=True, default='')
    seed = models.PositiveIntegerField(default=0)
    loss_count = models.PositiveIntegerField(default=0)
    points = models.IntegerField(default=0)
    is_champion = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['seed', 'name']

    def __str__(self):
        return self.name

class BracketMatch(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_ONGOING = 'ongoing'
    STATUS_COMPLETED = 'completed'
    STATUS_FORFEIT = 'forfeit'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_ONGOING, 'Ongoing'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_FORFEIT, 'Forfeit'),
    ]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='bracket_matches')
    client_key = models.CharField(max_length=40, blank=True, default='', db_index=True)
    match_number = models.PositiveIntegerField()
    round_name = models.CharField(max_length=100)
    team_a = models.ForeignKey(BracketTeam, on_delete=models.SET_NULL, null=True, blank=True, related_name='matches_as_a')
    team_b = models.ForeignKey(BracketTeam, on_delete=models.SET_NULL, null=True, blank=True, related_name='matches_as_b')
    winner = models.ForeignKey(BracketTeam, on_delete=models.SET_NULL, null=True, blank=True, related_name='matches_won')
    loser = models.ForeignKey(BracketTeam, on_delete=models.SET_NULL, null=True, blank=True, related_name='matches_lost')
    next_match_winner = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='previous_matches_winner')
    next_match_loser = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='previous_matches_loser')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    match_date = models.DateField(null=True, blank=True)
    match_time = models.TimeField(null=True, blank=True)
    venue = models.CharField(max_length=200, blank=True, default='')
    score_a = models.CharField(max_length=50, blank=True, default='')
    score_b = models.CharField(max_length=50, blank=True, default='')
    remarks = models.TextField(blank=True, default='')
    is_automatic_advance = models.BooleanField(
        default=False,
        help_text='True for Automatic Advance (BYE) slots — not an actual match.',
    )
    playing_area = models.CharField(max_length=120, blank=True, default='')
    dependency_label = models.CharField(
        max_length=200,
        blank=True,
        default='',
        help_text='e.g. Winner of Game 1 vs Winner of Game 2',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['match_number']

    def __str__(self):
        return f"{self.event.name} - Match {self.match_number} ({self.round_name})"

class ScoreSheet(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_FINALIZED = 'finalized'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_CONFIRMED, 'Confirmed'),
        (STATUS_FINALIZED, 'Finalized'),
    ]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='scoresheets')
    match = models.ForeignKey(BracketMatch, on_delete=models.CASCADE, related_name='scoresheets')
    tabulator = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='submitted_scoresheets')
    
    score_team_a = models.DecimalField(max_digits=8, decimal_places=2, default=0.00)
    score_team_b = models.DecimalField(max_digits=8, decimal_places=2, default=0.00)
    winner = models.ForeignKey(BracketTeam, on_delete=models.SET_NULL, null=True, blank=True, related_name='won_scoresheets')
    
    judges_names = models.CharField(max_length=500, blank=True, default='')
    remarks = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    
    ocr_image = models.ImageField(upload_to='scoresheets/ocr/', null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Scoresheet: {self.match.event.name} - Match {self.match.match_number} ({self.get_status_display()})"


class ScoresheetTemplate(models.Model):
    EVENT_MATCH = 'match'
    EVENT_CRITERIA = 'criteria'
    EVENT_TYPE_CHOICES = [
        (EVENT_MATCH, 'Match-Based'),
        (EVENT_CRITERIA, 'Criteria-Based'),
    ]
    STATUS_ACTIVE = 'active'
    STATUS_ARCHIVED = 'archived'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_ARCHIVED, 'Archived'),
    ]
    ORIENT_PORTRAIT = 'portrait'
    ORIENT_LANDSCAPE = 'landscape'
    ORIENTATION_CHOICES = [
        (ORIENT_PORTRAIT, 'Portrait'),
        (ORIENT_LANDSCAPE, 'Landscape'),
    ]

    name = models.CharField(max_length=200)
    event_type = models.CharField(max_length=20, choices=EVENT_TYPE_CHOICES, default=EVENT_MATCH)
    category = models.CharField(max_length=100, blank=True, default='')
    description = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    paper_size = models.CharField(max_length=20, default='a4')
    orientation = models.CharField(max_length=20, choices=ORIENTATION_CHOICES, default=ORIENT_PORTRAIT)
    layout = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='scoresheet_templates'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.name


class GeneratedScoresheet(models.Model):
    template = models.ForeignKey(
        ScoresheetTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name='generated'
    )
    event = models.ForeignKey(Event, null=True, blank=True, on_delete=models.SET_NULL, related_name='generated_scoresheets')
    event_label = models.CharField(max_length=200, blank=True, default='')
    item_label = models.CharField(max_length=200, blank=True, default='')
    generated_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='generated_scoresheets'
    )
    payload = models.JSONField(default=dict, blank=True)
    file = models.FileField(upload_to='scoresheets/generated/', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.event_label or 'Scoresheet'} · {self.item_label or self.pk}"


class EventCategory(models.Model):
    CATEGORY_CHOICES = [
        ('academic', 'Academic Event'),
        ('esports', 'Esports Event'),
        ('sports', 'Sports Event'),
        ('socio_cultural', 'Socio Cultural'),
    ]
    name = models.CharField(max_length=100)
    category_type = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default='academic')
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, default='school')
    color = models.CharField(max_length=7, default='#2196F3')

    class Meta:
        verbose_name_plural = 'Event Categories'

    def __str__(self):
        return self.name


class JudgingEvent(models.Model):
    STATUS_CHOICES = [
        ('upcoming', 'Upcoming'),
        ('active', 'Active'),
        ('completed', 'Completed'),
    ]
    title = models.CharField(max_length=200)
    category = models.ForeignKey(EventCategory, on_delete=models.CASCADE, related_name='events')
    date = models.DateField()
    time = models.TimeField()
    venue = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='upcoming')
    description = models.TextField(blank=True)
    assigned_judges = models.ManyToManyField(User, blank=True, related_name='assigned_judging_events')

    class Meta:
        ordering = ['date', 'time']

    def __str__(self):
        return self.title


class Criterion(models.Model):
    event = models.ForeignKey(JudgingEvent, on_delete=models.CASCADE, related_name='criteria')
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=200, blank=True)
    max_score = models.DecimalField(max_digits=5, decimal_places=1)
    weight_percent = models.DecimalField(max_digits=5, decimal_places=1)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f'{self.event.title} - {self.name}'


class Candidate(models.Model):
    event = models.ForeignKey(JudgingEvent, on_delete=models.CASCADE, related_name='candidates')
    name = models.CharField(max_length=200)
    number = models.PositiveIntegerField()
    department = models.CharField(max_length=150, blank=True, default='')
    photo = models.ImageField(upload_to='candidates/', null=True, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['number']
        unique_together = ['event', 'number']

    def __str__(self):
        return f'#{self.number} {self.name}'


class JudgeScore(models.Model):
    judge = models.ForeignKey(User, on_delete=models.CASCADE, related_name='judge_scores')
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='scores')
    criterion = models.ForeignKey(Criterion, on_delete=models.CASCADE, related_name='scores')
    score = models.DecimalField(max_digits=5, decimal_places=1)
    is_locked = models.BooleanField(default=False)
    submitted_at = models.DateTimeField(null=True, blank=True)
    verification_id = models.CharField(max_length=50, blank=True)

    class Meta:
        unique_together = ['judge', 'candidate', 'criterion']

    def __str__(self):
        return f'{self.judge.username} - {self.candidate.name} - {self.criterion.name}: {self.score}'


# ──────────────────────────────────────────────────────────────────
# Tab Models — used by the tabbed Manage Events page (Phase 2)
# Each tab has its own simple model with name + 2 dates.
# ──────────────────────────────────────────────────────────────────

class TabRecordBase(models.Model):
    """Abstract base for all tab records (Portion, Candidate No., etc.)."""
    name = models.CharField(max_length=200)
    date_start = models.DateField(null=True, blank=True)
    date_end = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class Portion(TabRecordBase):
    """A portion / round of an event (e.g. Talent, Q&A)."""
    pass


class CandidateNumber(TabRecordBase):
    """A candidate number assignment."""
    pass


class Contestant(TabRecordBase):
    """An individual contestant participating in events."""
    pass


class JudgeAssignment(TabRecordBase):
    """A judge assignment record (for the tabbed UI)."""
    pass


class ScoringCriterion(TabRecordBase):
    """A scoring criterion entry."""
    pass


class Chairperson(TabRecordBase):
    """A chairperson assigned to oversee an event/portion."""
    pass


class JudgeActivityLog(models.Model):
    ACTION_LOGIN = 'login'
    ACTION_LOGOUT = 'logout'
    ACTION_SUBMIT_SCORE = 'submit_score'
    ACTION_EDIT_SCORE = 'edit_score'
    ACTION_VIEW_EVENT = 'view_event'
    ACTION_VIEW_SCORES = 'view_scores'
    ACTION_CHOICES = [
        (ACTION_LOGIN, 'Logged In'),
        (ACTION_LOGOUT, 'Logged Out'),
        (ACTION_SUBMIT_SCORE, 'Submitted Score'),
        (ACTION_EDIT_SCORE, 'Edited Score'),
        (ACTION_VIEW_EVENT, 'Viewed Event'),
        (ACTION_VIEW_SCORES, 'Viewed Scores'),
    ]

    judge = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activity_logs')
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    details = models.TextField(blank=True)
    event = models.ForeignKey(
        JudgingEvent, null=True, blank=True, on_delete=models.SET_NULL, related_name='judge_logs',
    )
    candidate = models.ForeignKey(
        Candidate, null=True, blank=True, on_delete=models.SET_NULL,
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f'{self.judge.username} — {self.get_action_display()} — {self.timestamp:%Y-%m-%d %H:%M}'

    @classmethod
    def log(cls, judge, action, details='', event=None, candidate=None, ip_address=None):
        row = cls.objects.create(
            judge=judge,
            action=action,
            details=details,
            event=event,
            candidate=candidate,
            ip_address=ip_address,
        )
        try:
            from .audit_service import record_audit
            action_map = {
                cls.ACTION_LOGIN: 'Logged In',
                cls.ACTION_LOGOUT: 'Logged Out',
                cls.ACTION_SUBMIT_SCORE: 'Submitted Final Score',
                cls.ACTION_EDIT_SCORE: 'Edited Draft Score',
                cls.ACTION_VIEW_EVENT: 'Opened Assigned Event',
                cls.ACTION_VIEW_SCORES: 'Opened Assigned Event',
            }
            event_name = ''
            if event is not None:
                event_name = getattr(event, 'name', '') or str(event)
            contestant = ''
            if candidate is not None:
                contestant = getattr(candidate, 'name', '') or str(candidate)
            record_audit(
                user=judge,
                action=action_map.get(action, action),
                description=details or action_map.get(action, action),
                module='Criteria-Based Event' if action not in (cls.ACTION_LOGIN, cls.ACTION_LOGOUT) else 'Authentication',
                event_name=event_name,
                contestant=contestant,
                ip_address=ip_address,
            )
        except Exception:
            pass
        return row


class AssignmentAccountProfile(models.Model):
    """Stores display-only access codes for judge/scorer portal accounts."""
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='assignment_profile',
    )
    access_code = models.CharField(max_length=32, blank=True, default='')

    def __str__(self):
        return f'Profile for {self.user.username}'


class SystemSettings(models.Model):
    """Singleton system-wide configuration for Superadmin."""
    school_name = models.CharField(max_length=200, blank=True, default='EventTab')
    school_logo = models.ImageField(upload_to='system/', null=True, blank=True)
    academic_year = models.CharField(max_length=40, blank=True, default='')
    intramurals_name = models.CharField(max_length=200, blank=True, default='')
    password_min_length = models.PositiveSmallIntegerField(default=8)
    password_require_uppercase = models.BooleanField(default=False)
    password_require_number = models.BooleanField(default=False)
    password_require_symbol = models.BooleanField(default=False)
    password_expiry_days = models.PositiveIntegerField(default=0)
    session_timeout_minutes = models.PositiveIntegerField(default=60)
    max_login_attempts = models.PositiveSmallIntegerField(default=5)
    lockout_duration_minutes = models.PositiveIntegerField(default=15)
    email_notifications = models.BooleanField(default=True)
    mobile_notifications = models.BooleanField(default=True)
    system_notifications = models.BooleanField(default=True)
    notification_config = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='system_settings_updates',
    )

    class Meta:
        verbose_name = 'System settings'
        verbose_name_plural = 'System settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return self.school_name or 'System Settings'


class AuditLog(models.Model):
    """Immutable system audit trail for Superadmin monitoring (read-only in UI)."""
    STATUS_SUCCESS = 'success'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_SUCCESS, 'Success'),
        (STATUS_FAILED, 'Failed'),
    ]

    user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='audit_logs',
    )
    user_name = models.CharField(max_length=150, blank=True, default='')
    user_role = models.CharField(max_length=64, blank=True, default='')
    module = models.CharField(max_length=80, blank=True, default='System')
    action = models.CharField(max_length=120)
    description = models.TextField(blank=True, default='')
    event_name = models.CharField(max_length=200, blank=True, default='')
    contestant = models.CharField(max_length=200, blank=True, default='')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device = models.CharField(max_length=120, blank=True, default='')
    browser = models.CharField(max_length=120, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SUCCESS)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Audit log'
        verbose_name_plural = 'Audit logs'

    def __str__(self):
        return f'{self.created_at:%Y-%m-%d %H:%M} · {self.user_name} · {self.action}'

    def delete(self, *args, **kwargs):
        # Soft-protect: do not allow hard deletes from normal ORM callers in app code.
        # Admin shell can still force via QuerySet._raw_delete if ever needed.
        raise PermissionError('Audit logs are read-only and cannot be deleted.')


class AdminReportHistory(models.Model):
    """Saved administrator report generation history (metadata + optional file)."""
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=80, blank=True, default='')
    report_type = models.CharField(max_length=80)
    export_format = models.CharField(max_length=20, blank=True, default='')
    filters = models.JSONField(default=dict, blank=True)
    preview = models.JSONField(default=dict, blank=True)
    file = models.FileField(upload_to='admin_reports/', null=True, blank=True)
    generated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='admin_reports',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Admin report history'
        verbose_name_plural = 'Admin report history'

    def __str__(self):
        return f'{self.name} ({self.created_at:%Y-%m-%d %H:%M})'
