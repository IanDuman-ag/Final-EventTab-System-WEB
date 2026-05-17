from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class Event(models.Model):
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
    event_time         = models.TimeField(null=True, blank=True)
    venue              = models.CharField(max_length=200)
    image              = models.ImageField(upload_to='event_images/', null=True, blank=True)
    status             = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_UPCOMING)
    max_participants   = models.PositiveIntegerField(null=True, blank=True)
    num_teams          = models.PositiveIntegerField(null=True, blank=True)
    faculty_in_charge  = models.CharField(max_length=150, blank=True, default='')
    student_in_charge  = models.CharField(max_length=150, blank=True, default='')
    mechanics          = models.TextField(blank=True, default='')
    scoring_criteria   = models.TextField(blank=True, default='')
    created_by         = models.ForeignKey(
        User,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='created_events',
    )
    created_at         = models.DateTimeField(auto_now_add=True)
    updated_at         = models.DateTimeField(auto_now=True)

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

class BracketTeam(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='bracket_teams')
    name = models.CharField(max_length=200)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='bracket_teams')
    members = models.TextField(blank=True, default='')
    seed = models.PositiveIntegerField(default=0)
    loss_count = models.PositiveIntegerField(default=0)
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
