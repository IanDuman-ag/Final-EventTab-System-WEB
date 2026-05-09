# EventTabs - Quick Reference Guide

## System Overview

**EventTabs** is an event management and scoring system with 4 user roles:
- **Super Admin** - System-wide management
- **Admin** - Department/Event management
- **Tabulator** - Score verification and results
- **Viewer** - Read-only results access

---

## Quick Start for Development

### 1. Understand the System
```
Read in this order:
1. SYSTEM_FLOW.md          (5 min) - Understand workflows
2. SPEC_SUMMARY.md         (5 min) - Overview of all specs
3. requirements.md         (15 min) - Detailed requirements
4. design.md              (15 min) - Technical design
5. tasks.md               (10 min) - Implementation plan
```

### 2. Set Up Development Environment
```bash
# Activate virtual environment
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create .env file
copy .env.example .env

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Run server
python manage.py runserver
```

### 3. Start Implementation
```
Follow tasks.md Phase by Phase:
- Phase 1: Create Django apps and models
- Phase 2: Implement authentication
- Phase 3-6: Build portals
- Phase 7: Testing and deployment
```

---

## Key Workflows

### Event Creation Workflow
```
Super Admin
    ↓
Creates Admin Account
    ↓
Admin
    ↓
Creates Event
    ├─ Define Type & Categories
    ├─ Set Judging Criteria
    ├─ Assign Judges
    ├─ Assign Tabulators
    └─ Add Participants
    ↓
Event Ready
```

### Scoring Workflow
```
Judges
    ↓
Submit Scores
    ↓
Tabulator
    ↓
Review & Verify
    ├─ Check Completeness
    ├─ Verify Accuracy
    └─ Detect Issues
    ↓
Approve Scores
    ↓
Generate Results
    ↓
Publish Results
    ↓
Viewers
    ↓
View Results
```

---

## Database Models (Quick Reference)

### User Management
- **User** - Django built-in user model
- **UserRole** - Role assignment (super_admin, admin, tabulator, viewer)
- **Department** - Organizational units
- **Course** - Course/Category groupings

### Event Management
- **Event** - Competitions/Activities
- **Category** - Event divisions
- **Participant** - Competitors
- **Team** - Team groupings
- **Judge** - Scoring officials
- **Tabulator** - Score verifiers

### Scoring
- **ScoringSystem** - Scoring rules
- **JudgingCriteria** - Scoring criteria
- **ScoreSheet** - Judge submissions
- **ScoreSheetEdit** - Audit trail

### Results
- **Bracket** - Tournament structure
- **Match** - Bracket matches
- **Result** - Final rankings

### Audit
- **ActivityLog** - Activity tracking
- **Notification** - User notifications

---

## URL Structure

### Authentication
```
/login/                     - Login page
/logout/                    - Logout
```

### Super Admin
```
/super-admin/dashboard/     - Dashboard
/super-admin/admins/        - Manage admins
/super-admin/departments/   - Manage departments
/super-admin/settings/      - System settings
/super-admin/logs/          - Activity logs
```

### Admin
```
/admin/dashboard/           - Dashboard
/admin/events/              - Event list
/admin/events/create/       - Create event
/admin/events/<id>/         - Event detail
/admin/events/<id>/judges/  - Manage judges
/admin/events/<id>/participants/ - Manage participants
```

### Tabulator
```
/tabulator/dashboard/       - Dashboard
/tabulator/scores/          - Score sheets
/tabulator/scores/<id>/     - Score detail
/tabulator/results/         - Generate results
```

### Viewer
```
/viewer/dashboard/          - Dashboard
/viewer/events/             - Event list
/viewer/results/            - Results
/viewer/brackets/<id>/      - View bracket
```

---

## Implementation Checklist

### Phase 1: Setup & Models (1 week)
- [ ] Create Django apps (users, events, scoring, results, audit)
- [ ] Define all 19 models
- [ ] Create migrations
- [ ] Test model creation and queries

### Phase 2: Auth & Authorization (1 week)
- [ ] Implement user roles
- [ ] Create permission system
- [ ] Build login/logout
- [ ] Create custom decorators
- [ ] Set up middleware

### Phase 3: Super Admin Portal (1 week)
- [ ] Create dashboard
- [ ] Admin management
- [ ] Department management
- [ ] System settings
- [ ] Activity logs

### Phase 4: Admin Portal (1 week)
- [ ] Create dashboard
- [ ] Event management
- [ ] Participant management
- [ ] Judge assignment
- [ ] Bracket management

### Phase 5: Tabulator Portal (1 week)
- [ ] Create dashboard
- [ ] Score review
- [ ] Score management
- [ ] Results generation
- [ ] Export functionality

### Phase 6: Viewer Portal (1 week)
- [ ] Create dashboard
- [ ] Results viewing
- [ ] Bracket viewing
- [ ] Search & filtering
- [ ] Real-time updates

### Phase 7: Testing & Deployment (1 week)
- [ ] Unit tests
- [ ] Integration tests
- [ ] Performance testing
- [ ] Production deployment

---

## Common Commands

### Django Management
```bash
# Create migrations
python manage.py makemigrations

# Apply migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Run development server
python manage.py runserver

# Open Django shell
python manage.py shell

# Create app
python manage.py startapp app_name

# Collect static files
python manage.py collectstatic
```

### Database
```bash
# Connect to PostgreSQL
psql -U event_users -d eventtabs -h 127.0.0.1

# Backup database
pg_dump -U event_users eventtabs > backup.sql

# Restore database
psql -U event_users eventtabs < backup.sql
```

### Testing
```bash
# Run all tests
python manage.py test

# Run specific app tests
python manage.py test app_name

# Run with coverage
coverage run --source='.' manage.py test
coverage report
```

---

## Key Files to Create

### Django Apps
```
users/
├── models.py          - User, UserRole, Department, Course
├── views.py           - User management views
├── urls.py            - User URLs
├── forms.py           - User forms
└── admin.py           - Django admin

events/
├── models.py          - Event, Category, Participant, Team, Judge, Tabulator
├── views.py           - Event management views
├── urls.py            - Event URLs
├── forms.py           - Event forms
└── admin.py           - Django admin

scoring/
├── models.py          - ScoringSystem, JudgingCriteria, ScoreSheet, ScoreSheetEdit
├── views.py           - Scoring views
├── urls.py            - Scoring URLs
├── forms.py           - Scoring forms
└── admin.py           - Django admin

results/
├── models.py          - Bracket, Match, Result
├── views.py           - Results views
├── urls.py            - Results URLs
└── admin.py           - Django admin

audit/
├── models.py          - ActivityLog, Notification
├── views.py           - Audit views
└── admin.py           - Django admin
```

### Templates
```
templates/
├── base.html                    - Base template
├── login.html                   - Login page
├── superadmin/
│   ├── dashboard.html
│   ├── admins.html
│   ├── departments.html
│   └── ...
├── admin/
│   ├── dashboard.html
│   ├── events.html
│   ├── participants.html
│   └── ...
├── tabulator/
│   ├── dashboard.html
│   ├── scores.html
│   ├── results.html
│   └── ...
└── viewer/
    ├── dashboard.html
    ├── results.html
    ├── brackets.html
    └── ...
```

### Static Files
```
static/
├── css/
│   ├── base.css
│   ├── superadmin.css
│   ├── admin.css
│   ├── tabulator.css
│   └── viewer.css
├── js/
│   ├── base.js
│   ├── events.js
│   ├── scoring.js
│   └── results.js
└── images/
    └── ...
```

---

## Important Considerations

### Security
- ✅ Use Django's built-in authentication
- ✅ Implement role-based access control
- ✅ Use HTTPS in production
- ✅ Hash passwords securely
- ✅ Validate all user input
- ✅ Protect against CSRF and XSS

### Performance
- ✅ Optimize database queries
- ✅ Use select_related/prefetch_related
- ✅ Add pagination for large datasets
- ✅ Cache frequently accessed data
- ✅ Minify CSS and JavaScript

### Scalability
- ✅ Use stateless application design
- ✅ Implement database connection pooling
- ✅ Use load balancing
- ✅ Plan for horizontal scaling

### Maintainability
- ✅ Write clean, well-documented code
- ✅ Follow Django best practices
- ✅ Use meaningful variable names
- ✅ Add comprehensive logging
- ✅ Write tests for all features

---

## Troubleshooting

### Common Issues

**Issue:** Database connection error
```
Solution:
1. Check PostgreSQL is running
2. Verify credentials in .env
3. Ensure database exists
4. Check port 5432 is accessible
```

**Issue:** Template not found
```
Solution:
1. Check TEMPLATES setting in settings.py
2. Verify template file exists
3. Check template path is correct
4. Verify app is in INSTALLED_APPS
```

**Issue:** Static files not loading
```
Solution:
1. Run: python manage.py collectstatic
2. Check STATIC_URL in settings.py
3. Verify static files exist
4. Check browser cache
```

**Issue:** Permission denied
```
Solution:
1. Check user role
2. Verify role has permission
3. Check department assignment
4. Check event assignment
```

---

## Resources

### Documentation
- [Django Documentation](https://docs.djangoproject.com/)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [Python Documentation](https://docs.python.org/)

### Project Docs
- SYSTEM_FLOW.md - System architecture
- requirements.md - Detailed requirements
- design.md - Technical design
- tasks.md - Implementation tasks
- coding-rules.md - Coding standards

---

## Contact & Support

For questions about:
- **Architecture:** See SYSTEM_FLOW.md
- **Requirements:** See requirements.md
- **Design:** See design.md
- **Tasks:** See tasks.md
- **Coding:** See coding-rules.md

---

**Last Updated:** May 8, 2026  
**Version:** 1.0  
**Status:** Ready for Development
