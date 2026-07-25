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
    PARTICIPATION_INDIVIDUAL = 'individual'
    PARTICIPATION_CHOICES = [
        (PARTICIPATION_TEAM, 'Team'),
        (PARTICIPATION_INDIVIDUAL, 'Individual'),
    ]
    FORMAT_SINGLE = 'single_performance'
    FORMAT_MULTIPLE = 'multiple_rounds'
    FORMAT_PRELIM_FINAL = 'preliminary_final'
    FORMAT_CHOICES = [
        (FORMAT_SINGLE, 'Single Performance'),
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
        return cls.objects.create(
            judge=judge,
            action=action,
            details=details,
            event=event,
            candidate=candidate,
            ip_address=ip_address,
        )


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
