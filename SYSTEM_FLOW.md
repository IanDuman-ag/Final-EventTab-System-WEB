# EventTabs System Flow & Architecture

## System Overview

EventTabs is a role-based event management and scoring system designed to manage competitions, sports events, esports tournaments, pageants, and other competitive activities. The system provides secure access control, score management, and real-time result tracking.

---

## Role-Based Access Control (RBAC)

### 1. Super Admin
**Level:** System-wide access  
**Assigned by:** System initialization

#### Responsibilities:
- Manage the entire system
- Create and assign Admin accounts
- Monitor all events and system activities
- Control system settings and permissions
- Oversee all users and event records

#### Capabilities:
- ✅ View all events across all departments
- ✅ Create/Edit/Delete Admin accounts
- ✅ Manage system settings
- ✅ View system activity logs
- ✅ Generate system-wide reports
- ✅ Manage user roles and permissions
- ✅ Access audit trails

---

### 2. Admin
**Level:** Department/Course-level access  
**Assigned by:** Super Admin  
**Assigned to:** Department Heads, Course Handlers

#### Responsibilities:
- Create and manage events for specific course/department
- Define judging criteria and scoring systems
- Assign judges to events
- Assign tabulators
- Manage participants and brackets
- Monitor event progress

#### Capabilities:
- ✅ Create events (sports, esports, pageants, etc.)
- ✅ Define event types and categories
- ✅ Set judging criteria and scoring rules
- ✅ Assign judges to events
- ✅ Assign tabulators to events
- ✅ Manage participants/teams
- ✅ Create and manage brackets
- ✅ Monitor event progress
- ✅ View submitted scores
- ✅ Generate department-level reports
- ❌ Cannot approve final scores (Tabulator does this)
- ❌ Cannot access other departments' events

---

### 3. Tabulator
**Level:** Event-level access  
**Assigned by:** Admin  
**Assigned to:** Score verification specialists

#### Responsibilities:
- Review and verify submitted scores
- Detect inconsistencies and errors
- Edit score sheets when necessary
- Finalize and approve scores
- Generate rankings and results

#### Capabilities:
- ✅ View all score sheets for assigned events
- ✅ Review judge submissions
- ✅ Verify score accuracy and completeness
- ✅ Detect and flag inconsistencies
- ✅ Edit score sheets (with audit trail)
- ✅ Finalize and approve scores
- ✅ Generate rankings and results
- ✅ Export results
- ❌ Cannot create events
- ❌ Cannot assign judges
- ❌ Cannot modify event rules
- ❌ Cannot access other events

---

### 4. Viewer
**Level:** Read-only access  
**Assigned by:** Admin or Super Admin  
**Assigned to:** Audience, participants, guests

#### Responsibilities:
- View event information and results
- Monitor real-time or final scores
- View brackets and standings

#### Capabilities:
- ✅ View event information
- ✅ View scores and rankings
- ✅ View brackets
- ✅ View participant standings
- ✅ View real-time results (if enabled)
- ✅ View final results
- ❌ Cannot edit any data
- ❌ Cannot submit scores
- ❌ Cannot approve scores
- ❌ Cannot delete data
- ❌ Cannot access admin functions

---

## System Workflows

### Workflow 1: Event Creation & Setup

```
Super Admin
    ↓
Creates Admin Account
    ↓
Admin (Department Head)
    ↓
Creates Event
    ├─ Define Event Type (Sports, Esports, Pageant, etc.)
    ├─ Set Event Categories/Divisions
    ├─ Define Judging Criteria
    ├─ Set Scoring System
    └─ Create Event Schedule
    ↓
Admin Assigns Judges
    ├─ Select judges for each category
    └─ Assign scoring criteria
    ↓
Admin Assigns Tabulators
    ├─ Select tabulators for event
    └─ Set tabulation permissions
    ↓
Admin Manages Participants
    ├─ Add participants/teams
    ├─ Assign to categories
    └─ Create brackets (if applicable)
    ↓
Event Ready for Judging
```

### Workflow 2: Judging & Score Submission

```
Event Starts
    ↓
Judges Access Event
    ├─ View assigned categories
    ├─ View participants
    └─ View scoring criteria
    ↓
Judges Submit Scores
    ├─ Enter scores for each participant
    ├─ Add comments/notes
    └─ Submit score sheet
    ↓
Score Sheet Submitted
    └─ Status: "Pending Review"
```

### Workflow 3: Score Tabulation & Verification

```
Tabulator Receives Notification
    ↓
Tabulator Reviews Score Sheets
    ├─ View all submitted scores
    ├─ Check for completeness
    ├─ Verify accuracy
    └─ Detect inconsistencies
    ↓
Issues Found?
    ├─ YES → Edit/Flag Score Sheet
    │         ├─ Add notes
    │         ├─ Request judge clarification
    │         └─ Return to pending
    │
    └─ NO → Approve Score Sheet
            ├─ Mark as verified
            ├─ Generate rankings
            └─ Status: "Approved"
    ↓
All Scores Approved?
    ├─ YES → Generate Final Results
    │         ├─ Calculate rankings
    │         ├─ Generate reports
    │         └─ Publish results
    │
    └─ NO → Wait for remaining scores
```

### Workflow 4: Results Viewing

```
Results Published
    ↓
Viewer Accesses System
    ├─ View event information
    ├─ View scores and rankings
    ├─ View brackets
    └─ View participant standings
    ↓
View-Only Access
    └─ Cannot modify any data
```

---

## Data Flow Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     EventTabs System                         │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────┐      ┌──────────────┐      ┌────────────┐ │
│  │  Super Admin │      │    Admin     │      │ Tabulator  │ │
│  │   Portal     │      │   Portal     │      │  Portal    │ │
│  └──────┬───────┘      └──────┬───────┘      └─────┬──────┘ │
│         │                     │                    │         │
│         ├─ Manage Admins      ├─ Create Events    ├─ Review  │
│         ├─ System Settings    ├─ Assign Judges    │  Scores  │
│         ├─ View All Events    ├─ Manage Brackets  ├─ Verify  │
│         └─ Activity Logs      └─ Monitor Progress └─ Approve │
│                                                               │
│  ┌──────────────────────────────────────────────────────────┐│
│  │              PostgreSQL Database                         ││
│  ├──────────────────────────────────────────────────────────┤│
│  │ • Users (Super Admin, Admin, Tabulator, Viewer)          ││
│  │ • Departments & Courses                                  ││
│  │ • Events & Categories                                    ││
│  │ • Participants & Teams                                   ││
│  │ • Judges & Assignments                                   ││
│  │ • Score Sheets & Submissions                             ││
│  │ • Brackets & Matchups                                    ││
│  │ • Rankings & Results                                     ││
│  │ • Activity Logs & Audit Trail                            ││
│  └──────────────────────────────────────────────────────────┘│
│                                                               │
│  ┌──────────────┐                                            │
│  │   Viewer     │                                            │
│  │   Portal     │                                            │
│  └──────┬───────┘                                            │
│         │                                                    │
│         └─ View Results (Read-Only)                          │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Features by Role

### Super Admin Features
1. **User Management**
   - Create/Edit/Delete Admin accounts
   - Manage user roles and permissions
   - View all users in system

2. **System Management**
   - Configure system settings
   - Manage departments
   - View system activity logs
   - Generate system-wide reports

3. **Monitoring**
   - View all events
   - Monitor all activities
   - Access audit trails

### Admin Features
1. **Event Management**
   - Create events
   - Define event types and categories
   - Set event schedules
   - Manage event status

2. **Judging Setup**
   - Define judging criteria
   - Set scoring systems
   - Assign judges to events
   - Manage judge assignments

3. **Participant Management**
   - Add/Edit/Delete participants
   - Assign participants to categories
   - Manage teams

4. **Bracket Management**
   - Create brackets
   - Define matchups
   - Update bracket status

5. **Tabulator Assignment**
   - Assign tabulators to events
   - Set tabulation permissions

6. **Monitoring**
   - View event progress
   - Monitor score submissions
   - View submitted scores

### Tabulator Features
1. **Score Review**
   - View all score sheets
   - Review judge submissions
   - Check score completeness

2. **Score Verification**
   - Verify score accuracy
   - Detect inconsistencies
   - Flag errors

3. **Score Management**
   - Edit score sheets (with audit trail)
   - Add verification notes
   - Request judge clarification

4. **Results Generation**
   - Finalize scores
   - Generate rankings
   - Export results

### Viewer Features
1. **Results Viewing**
   - View event information
   - View scores and rankings
   - View brackets
   - View participant standings

2. **Real-Time Updates**
   - View live scores (if enabled)
   - View final results

---

## Event Types Supported

1. **Sports Events**
   - Team competitions
   - Individual competitions
   - Bracket-based tournaments

2. **Esports Events**
   - Gaming tournaments
   - Team-based competitions
   - Ranking-based scoring

3. **Pageants**
   - Beauty pageants
   - Talent competitions
   - Judging-based scoring

4. **Other Competitions**
   - Academic competitions
   - Creative competitions
   - Custom event types

---

## Scoring Systems

### Types of Scoring
1. **Points-Based**
   - Judges assign points
   - Total points determine ranking

2. **Ranking-Based**
   - Judges rank participants
   - Aggregate rankings determine winner

3. **Criteria-Based**
   - Multiple judging criteria
   - Weighted scoring system

4. **Custom Scoring**
   - Admin-defined scoring rules
   - Flexible calculation methods

---

## Security & Audit

### Access Control
- Role-based access control (RBAC)
- Department-level isolation
- Event-level permissions

### Audit Trail
- Track all score submissions
- Log all edits and approvals
- Record user activities
- Timestamp all actions

### Data Integrity
- Score sheet versioning
- Edit history tracking
- Verification checkpoints
- Approval workflows

---

## System Status & Notifications

### Event Status
- Draft
- Setup
- Active (Judging)
- Tabulation
- Completed
- Archived

### Score Sheet Status
- Draft
- Submitted
- Pending Review
- Flagged (Issues)
- Approved
- Published

### Notifications
- Score submission alerts
- Tabulation requests
- Approval notifications
- Result publication alerts

---

## Database Entities

### Core Entities
1. **User** - System users with roles
2. **Department** - Organizational units
3. **Course** - Course/Category groupings
4. **Event** - Competitions and activities
5. **Category** - Event divisions/categories
6. **Participant** - Competitors/Teams
7. **Judge** - Scoring officials
8. **Tabulator** - Score verifiers
9. **ScoreSheet** - Judge submissions
10. **Bracket** - Tournament structures
11. **Result** - Final rankings
12. **ActivityLog** - Audit trail

---

## Next Steps for Development

1. **Create Django Models**
   - User roles and permissions
   - Event and category models
   - Score sheet models
   - Bracket models

2. **Build Admin Portals**
   - Super Admin dashboard
   - Admin event management
   - Tabulator score review
   - Viewer results portal

3. **Implement Features**
   - Event creation workflow
   - Score submission system
   - Tabulation process
   - Results generation

4. **Add Security**
   - Role-based access control
   - Audit logging
   - Data validation
   - Permission checks

5. **Create APIs**
   - Event management APIs
   - Score submission APIs
   - Results APIs
   - User management APIs

---

**System Status:** Ready for Django model development and portal implementation
