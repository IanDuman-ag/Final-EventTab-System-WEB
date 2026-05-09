# EventTabs System - Design Specification

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Frontend Layer                            │
├─────────────────────────────────────────────────────────────┤
│  • Super Admin Portal      • Admin Portal                    │
│  • Tabulator Portal        • Viewer Portal                   │
│  • Login/Authentication    • Dashboard                       │
└────────────────┬──────────────────────────────────────────┘
                 │
┌────────────────▼──────────────────────────────────────────┐
│                  Django Application Layer                   │
├─────────────────────────────────────────────────────────────┤
│  • Views & URL Routing                                      │
│  • Business Logic                                           │
│  • Permission & Authorization                              │
│  • API Endpoints                                            │
│  • Form Validation                                          │
│  • Middleware & Decorators                                  │
└────────────────┬──────────────────────────────────────────┘
                 │
┌────────────────▼──────────────────────────────────────────┐
│                  Data Access Layer                          │
├─────────────────────────────────────────────────────────────┤
│  • Django ORM Models                                        │
│  • Database Queries                                         │
│  • Transactions & Integrity                                 │
└────────────────┬──────────────────────────────────────────┘
                 │
┌────────────────▼──────────────────────────────────────────┐
│              PostgreSQL Database                            │
├─────────────────────────────────────────────────────────────┤
│  • User & Role Tables                                       │
│  • Event & Category Tables                                  │
│  • Score & Result Tables                                    │
│  • Audit & Log Tables                                       │
└─────────────────────────────────────────────────────────────┘
```

---

## Database Schema Design

### Core Models

#### 1. User Model (Django Built-in)
```
User
├── id (PK)
├── username (unique)
├── email (unique)
├── password (hashed)
├── first_name
├── last_name
├── is_active
├── is_staff
├── is_superuser
├── date_joined
└── last_login
```

#### 2. UserRole Model
```
UserRole
├── id (PK)
├── user (FK → User)
├── role (choices: super_admin, admin, tabulator, viewer)
├── department (FK → Department, nullable)
├── assigned_by (FK → User)
├── assigned_date
└── is_active
```

#### 3. Department Model
```
Department
├── id (PK)
├── name
├── description
├── head (FK → User, nullable)
├── created_by (FK → User)
├── created_date
├── is_active
└── archived_date (nullable)
```

#### 4. Course Model
```
Course
├── id (PK)
├── department (FK → Department)
├── name
├── code
├── description
├── created_by (FK → User)
├── created_date
└── is_active
```

#### 5. Event Model
```
Event
├── id (PK)
├── department (FK → Department)
├── course (FK → Course, nullable)
├── name
├── event_type (choices: sports, esports, pageant, other)
├── description
├── start_date
├── end_date
├── status (choices: draft, setup, active, tabulation, completed, archived)
├── created_by (FK → User)
├── created_date
├── modified_date
└── is_active
```

#### 6. Category Model
```
Category
├── id (PK)
├── event (FK → Event)
├── name
├── description
├── participant_count
├── bracket_type (choices: single_elimination, double_elimination, round_robin, none)
├── scoring_system (FK → ScoringSystem)
├── created_by (FK → User)
└── created_date
```

#### 7. Participant Model
```
Participant
├── id (PK)
├── event (FK → Event)
├── category (FK → Category)
├── name
├── email
├── phone
├── team (FK → Team, nullable)
├── registration_date
├── status (choices: registered, active, disqualified, withdrawn)
└── notes
```

#### 8. Team Model
```
Team
├── id (PK)
├── event (FK → Event)
├── name
├── description
├── members (M2M → Participant)
├── created_by (FK → User)
└── created_date
```

#### 9. Judge Model
```
Judge
├── id (PK)
├── user (FK → User)
├── event (FK → Event)
├── category (FK → Category, nullable)
├── assigned_by (FK → User)
├── assigned_date
├── status (choices: assigned, active, completed)
└── notes
```

#### 10. Tabulator Model
```
Tabulator
├── id (PK)
├── user (FK → User)
├── event (FK → Event)
├── assigned_by (FK → User)
├── assigned_date
├── status (choices: assigned, active, completed)
└── permissions (JSON field)
```

#### 11. ScoringSystem Model
```
ScoringSystem
├── id (PK)
├── event (FK → Event)
├── name
├── description
├── scoring_type (choices: points, ranking, criteria, custom)
├── min_score
├── max_score
├── criteria (M2M → JudgingCriteria)
├── created_by (FK → User)
└── created_date
```

#### 12. JudgingCriteria Model
```
JudgingCriteria
├── id (PK)
├── scoring_system (FK → ScoringSystem)
├── name
├── description
├── min_points
├── max_points
├── weight (for weighted scoring)
├── order
└── created_date
```

#### 13. ScoreSheet Model
```
ScoreSheet
├── id (PK)
├── event (FK → Event)
├── category (FK → Category)
├── judge (FK → Judge)
├── participant (FK → Participant)
├── scores (JSON field - stores individual criterion scores)
├── total_score
├── comments
├── status (choices: draft, submitted, pending_review, flagged, approved, published)
├── submitted_date
├── reviewed_by (FK → Tabulator, nullable)
├── reviewed_date (nullable)
├── version
└── created_date
```

#### 14. ScoreSheetEdit Model (Audit Trail)
```
ScoreSheetEdit
├── id (PK)
├── score_sheet (FK → ScoreSheet)
├── edited_by (FK → User)
├── old_values (JSON)
├── new_values (JSON)
├── reason
├── edited_date
└── timestamp
```

#### 15. Bracket Model
```
Bracket
├── id (PK)
├── event (FK → Event)
├── category (FK → Category)
├── bracket_type (choices: single_elimination, double_elimination, round_robin)
├── structure (JSON - bracket structure)
├── created_by (FK → User)
├── created_date
└── modified_date
```

#### 16. Match Model
```
Match
├── id (PK)
├── bracket (FK → Bracket)
├── round_number
├── match_number
├── participant1 (FK → Participant, nullable)
├── participant2 (FK → Participant, nullable)
├── winner (FK → Participant, nullable)
├── score1
├── score2
├── status (choices: pending, in_progress, completed)
└── scheduled_date
```

#### 17. Result Model
```
Result
├── id (PK)
├── event (FK → Event)
├── category (FK → Category)
├── participant (FK → Participant)
├── rank
├── total_score
├── status (choices: pending, published)
├── published_date (nullable)
└── created_date
```

#### 18. ActivityLog Model
```
ActivityLog
├── id (PK)
├── user (FK → User)
├── action (choices: create, update, delete, approve, submit, etc.)
├── model_name
├── object_id
├── object_description
├── changes (JSON)
├── timestamp
└── ip_address
```

#### 19. Notification Model
```
Notification
├── id (PK)
├── user (FK → User)
├── title
├── message
├── notification_type (choices: assignment, submission, approval, alert)
├── related_object_id
├── is_read
├── created_date
└── read_date (nullable)
```

---

## Application Structure

```
eventtabs/
├── core/
│   ├── settings.py          # Django settings
│   ├── urls.py              # Main URL routing
│   ├── views.py             # Core views (login, logout)
│   ├── wsgi.py              # WSGI config
│   └── asgi.py              # ASGI config
│
├── apps/
│   ├── users/               # User management app
│   │   ├── models.py        # User, UserRole models
│   │   ├── views.py         # User views
│   │   ├── urls.py          # User URLs
│   │   ├── forms.py         # User forms
│   │   └── admin.py         # Django admin
│   │
│   ├── events/              # Event management app
│   │   ├── models.py        # Event, Category, Participant models
│   │   ├── views.py         # Event views
│   │   ├── urls.py          # Event URLs
│   │   ├── forms.py         # Event forms
│   │   └── admin.py         # Django admin
│   │
│   ├── scoring/             # Scoring & judging app
│   │   ├── models.py        # ScoreSheet, Judge, Tabulator models
│   │   ├── views.py         # Scoring views
│   │   ├── urls.py          # Scoring URLs
│   │   ├── forms.py         # Scoring forms
│   │   └── admin.py         # Django admin
│   │
│   ├── results/             # Results & rankings app
│   │   ├── models.py        # Result, Bracket, Match models
│   │   ├── views.py         # Results views
│   │   ├── urls.py          # Results URLs
│   │   └── admin.py         # Django admin
│   │
│   └── audit/               # Audit & logging app
│       ├── models.py        # ActivityLog, Notification models
│       ├── views.py         # Audit views
│       └── admin.py         # Django admin
│
├── frontend/                # Templates & static files
│   ├── templates/
│   │   ├── base.html
│   │   ├── login.html
│   │   ├── superadmin/
│   │   │   ├── dashboard.html
│   │   │   ├── admins.html
│   │   │   └── ...
│   │   ├── admin/
│   │   │   ├── dashboard.html
│   │   │   ├── events.html
│   │   │   └── ...
│   │   ├── tabulator/
│   │   │   ├── dashboard.html
│   │   │   ├── scores.html
│   │   │   └── ...
│   │   └── viewer/
│   │       ├── results.html
│   │       ├── brackets.html
│   │       └── ...
│   │
│   ├── static/
│   │   ├── css/
│   │   │   ├── base.css
│   │   │   ├── superadmin.css
│   │   │   ├── admin.css
│   │   │   ├── tabulator.css
│   │   │   └── viewer.css
│   │   │
│   │   ├── js/
│   │   │   ├── base.js
│   │   │   ├── events.js
│   │   │   ├── scoring.js
│   │   │   └── results.js
│   │   │
│   │   └── images/
│   │       └── ...
│   │
│   └── login.html           # Login page
│
├── utils/                   # Utility functions
│   ├── decorators.py        # Custom decorators
│   ├── permissions.py       # Permission checks
│   ├── validators.py        # Data validators
│   └── helpers.py           # Helper functions
│
├── middleware/              # Custom middleware
│   ├── auth.py              # Authentication middleware
│   └── logging.py           # Logging middleware
│
├── management/              # Management commands
│   └── commands/
│       ├── create_roles.py
│       ├── create_superadmin.py
│       └── ...
│
├── tests/                   # Test suite
│   ├── test_users.py
│   ├── test_events.py
│   ├── test_scoring.py
│   └── ...
│
├── requirements.txt         # Python dependencies
├── manage.py                # Django management
├── .env                     # Environment variables
└── README.md                # Documentation
```

---

## URL Routing Structure

### Authentication URLs
```
/login/                     - Login page
/logout/                    - Logout
/register/                  - Registration (if enabled)
```

### Super Admin URLs
```
/super-admin/dashboard/     - Dashboard
/super-admin/admins/        - Manage admins
/super-admin/departments/   - Manage departments
/super-admin/settings/      - System settings
/super-admin/logs/          - Activity logs
/super-admin/reports/       - System reports
```

### Admin URLs
```
/admin/dashboard/           - Dashboard
/admin/events/              - Event list
/admin/events/create/       - Create event
/admin/events/<id>/         - Event detail
/admin/events/<id>/edit/    - Edit event
/admin/events/<id>/judges/  - Manage judges
/admin/events/<id>/participants/ - Manage participants
/admin/events/<id>/brackets/ - Manage brackets
/admin/events/<id>/tabulators/ - Assign tabulators
```

### Tabulator URLs
```
/tabulator/dashboard/       - Dashboard
/tabulator/events/          - Event list
/tabulator/events/<id>/     - Event detail
/tabulator/scores/          - Score sheets
/tabulator/scores/<id>/     - Score sheet detail
/tabulator/scores/<id>/edit/ - Edit score sheet
/tabulator/results/         - Generate results
/tabulator/results/<id>/    - View results
```

### Viewer URLs
```
/viewer/dashboard/          - Dashboard
/viewer/events/             - Event list
/viewer/events/<id>/        - Event detail
/viewer/results/            - Results
/viewer/results/<id>/       - Category results
/viewer/brackets/<id>/      - View bracket
```

---

## Key Features Design

### 1. Authentication & Authorization
- Django's built-in authentication system
- Custom UserRole model for role management
- Decorators for permission checking
- Middleware for session management

### 2. Event Management
- Event creation with type and categories
- Event status workflow (draft → setup → active → tabulation → completed)
- Category management with scoring systems
- Participant and team management

### 3. Judging System
- Judge assignment to events/categories
- Score sheet creation and submission
- Scoring criteria definition
- Real-time score tracking

### 4. Tabulation Process
- Score sheet review and verification
- Edit tracking with audit trail
- Inconsistency detection
- Score approval workflow

### 5. Results Generation
- Automatic ranking calculation
- Multiple result formats (CSV, PDF)
- Real-time result updates
- Result publication control

### 6. Bracket Management
- Multiple bracket types support
- Automatic bracket generation
- Match tracking and updates
- Bracket visualization

### 7. Audit & Logging
- Activity logging for all actions
- Score edit history tracking
- User action tracking
- Compliance reporting

---

## Security Design

### Authentication
- Password hashing with Django's default (PBKDF2)
- Session-based authentication
- Login attempt tracking
- Secure password reset

### Authorization
- Role-based access control (RBAC)
- Department-level isolation
- Event-level permissions
- Decorator-based permission checking

### Data Protection
- HTTPS for all communications
- CSRF protection on forms
- SQL injection prevention (Django ORM)
- XSS protection (template escaping)

### Audit Trail
- All modifications logged
- User identification on all actions
- Timestamp on all records
- Immutable audit logs

---

## Performance Considerations

### Database Optimization
- Proper indexing on frequently queried fields
- Query optimization with select_related/prefetch_related
- Pagination for large datasets
- Caching for frequently accessed data

### Frontend Optimization
- Minified CSS and JavaScript
- Image optimization
- Lazy loading for large lists
- AJAX for real-time updates

### Scalability
- Stateless application design
- Database connection pooling
- Caching layer (Redis optional)
- Load balancing ready

---

## Deployment Architecture

```
┌─────────────────────────────────────────┐
│         Web Server (Nginx)              │
│  - Static file serving                  │
│  - Reverse proxy                        │
│  - SSL/TLS termination                  │
└────────────┬────────────────────────────┘
             │
┌────────────▼────────────────────────────┐
│    Application Server (Gunicorn)        │
│  - Django application                   │
│  - Multiple worker processes            │
│  - Load balancing                       │
└────────────┬────────────────────────────┘
             │
┌────────────▼────────────────────────────┐
│      PostgreSQL Database                │
│  - Data persistence                     │
│  - Backup & recovery                    │
│  - Replication (optional)               │
└─────────────────────────────────────────┘
```

---

## Development Workflow

### Phase 1: Setup & Core Models
- Create Django apps
- Define all models
- Create migrations
- Set up admin interface

### Phase 2: Authentication & Authorization
- Implement user roles
- Create permission system
- Build login/logout
- Set up decorators

### Phase 3: Super Admin Portal
- Dashboard
- Admin management
- Department management
- System settings

### Phase 4: Admin Portal
- Event management
- Participant management
- Judge assignment
- Bracket management

### Phase 5: Tabulator Portal
- Score review
- Score management
- Results generation
- Export functionality

### Phase 6: Viewer Portal
- Results viewing
- Bracket viewing
- Real-time updates
- Search & filtering

### Phase 7: Testing & Deployment
- Unit tests
- Integration tests
- Performance testing
- Production deployment

---

**Design Status:** Complete - Ready for Implementation
