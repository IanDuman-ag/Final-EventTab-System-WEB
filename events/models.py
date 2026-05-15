from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class Event(models.Model):
    STATUS_UPCOMING   = 'upcoming'
    STATUS_ACTIVE     = 'active'
    STATUS_COMPLETED  = 'completed'
    STATUS_CHOICES = [
        (STATUS_UPCOMING,  'Upcoming'),
        (STATUS_ACTIVE,    'Active'),
        (STATUS_COMPLETED, 'Completed'),
    ]

    name               = models.CharField(max_length=200)
    category           = models.CharField(max_length=100)
    division           = models.CharField(max_length=100, blank=True, default='')
    department         = models.CharField(max_length=150, blank=True, default='')
    event_date         = models.DateField()
    event_time         = models.TimeField(null=True, blank=True)
    venue              = models.CharField(max_length=200)
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
