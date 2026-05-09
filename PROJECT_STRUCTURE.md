# EventTabs - Project Structure & File Connections

## 📁 Complete File Structure

```
eventtabs/
│
├── 📂 core/                           # Django Project Core
│   ├── settings.py                   # ⚙️ Main configuration (PostgreSQL, static files)
│   ├── urls.py                       # 🔗 URL routing (connects URLs to views)
│   ├── views.py                      # 🎯 View functions (business logic)
│   ├── wsgi.py                       # 🌐 WSGI server configuration
│   ├── asgi.py                       # 🌐 ASGI server configuration
│   └── __init__.py                   # Python package marker
│
├── 📂 frontend/                       # Templates & Static Files
│   ├── login.html                    # 🔐 Login page template
│   ├── login.css                     # 🎨 Login page styles
│   ├── superadmin_dashboard.html     # 📊 Dashboard template
│   └── superadmin_dashboard.css      # 🎨 Dashboard styles
│
├── 📂 venv/                           # Virtual Environment (not in git)
│   └── (Python packages installed here)
│
├── 📂 media/                          # User Uploads (not in git)
│   └── (User uploaded files: images, documents)
│
├── 📂 staticfiles/                    # Collected Static Files (not in git)
│   └── (CSS, JS, images collected by Django)
│
├── 📂 .git/                           # Git Repository
│   └── (Version control data)
│
├── 📂 .kiro/                          # Kiro AI Configuration
│   └── steering/
│       └── coding-rules.md           # AI coding guidelines
│
├── 📄 manage.py                       # 🔧 Django management script
├── 📄 requirements.txt                # 📦 Python dependencies
├── 📄 .env                            # 🔒 Environment variables (not in git)
├── 📄 .env.example                    # 📋 Example environment file
├── 📄 .gitignore                      # 🚫 Git ignore rules
├── 📄 setup.bat                       # 🚀 Automated setup script (Windows)
├── 📄 setup_database.sql              # 🗄️ PostgreSQL setup script
├── 📄 README.md                       # 📖 Project overview
├── 📄 SETUP.md                        # 📚 Detailed setup guide
├── 📄 QUICK_START.md                  # ⚡ Quick start guide
└── 📄 PROJECT_STRUCTURE.md            # 📋 This file
```

---

## 🔗 File Connections & Data Flow

### 1. **Django Core Files**

```
manage.py
    ↓
core/settings.py  ← Reads .env file
    ↓
    ├── Database: PostgreSQL (eventtabs)
    ├── Templates: frontend/
    ├── Static Files: frontend/
    └── Apps: (future Django apps)
```

### 2. **URL Routing Flow**

```
Browser Request
    ↓
core/urls.py (URL patterns)
    ↓
core/views.py (View functions)
    ↓
    ├── Query PostgreSQL Database
    ├── Process Data
    └── Render Template
    ↓
frontend/*.html (Templates)
    ↓
Browser Response (HTML + CSS)
```

### 3. **Authentication Flow**

```
login.html (User enters credentials)
    ↓
POST to /login/ URL
    ↓
core/views.py → login_view()
    ↓
    ├── Authenticate against PostgreSQL
    ├── Check user role
    └── Create session
    ↓
Redirect to Dashboard
    ↓
superadmin_dashboard.html
```

### 4. **Database Connection**

```
.env file (credentials)
    ↓
core/settings.py (DATABASES config)
    ↓
PostgreSQL Server (127.0.0.1:5432)
    ↓
Database: eventtabs
    ↓
    ├── auth_user (Django users)
    ├── auth_group (User groups/roles)
    ├── django_session (User sessions)
    └── (other Django tables)
```

### 5. **Static Files Flow**

```
Development:
frontend/*.css
    ↓
{% load static %} in templates
    ↓
{% static 'filename.css' %}
    ↓
Served by Django dev server

Production:
frontend/*.css
    ↓
python manage.py collectstatic
    ↓
staticfiles/ directory
    ↓
Served by web server (Nginx/Apache)
```

---

## 🔌 Key File Connections

### **settings.py** connects to:
- `.env` - Environment variables
- `frontend/` - Template directory
- PostgreSQL - Database connection
- `staticfiles/` - Static files location
- `media/` - User uploads location

### **urls.py** connects to:
- `views.py` - View functions
- Django admin - Built-in admin panel
- URL patterns - Route definitions

### **views.py** connects to:
- `settings.py` - Configuration
- PostgreSQL - Database queries
- `frontend/*.html` - Templates
- Django auth - User authentication
- Django models - Data models

### **Templates (HTML)** connect to:
- `views.py` - Receive context data
- `*.css` - Styling via {% static %}
- Django template tags - {% load static %}, {% url %}
- Context variables - {{ variable }}

---

## 📊 Data Flow Example: Login to Dashboard

```
1. User visits /login/
   ↓
2. urls.py routes to login_view()
   ↓
3. views.py renders login.html
   ↓
4. User submits form (email, password, role)
   ↓
5. POST request to /login/
   ↓
6. views.py → login_view()
   ├── Authenticate user (PostgreSQL query)
   ├── Check role permissions
   └── Create session
   ↓
7. Redirect to /super-admin/dashboard/
   ↓
8. urls.py routes to superadmin_dashboard()
   ↓
9. views.py queries PostgreSQL:
   ├── Count admins
   ├── Count departments
   ├── Get user list
   └── Get admin rows
   ↓
10. Render superadmin_dashboard.html with data
    ↓
11. Template displays:
    ├── Statistics cards
    ├── Role management table
    ├── Admin management section
    └── Activity alerts
    ↓
12. Browser shows dashboard with CSS styling
```

---

## 🗄️ PostgreSQL Database Tables

### Django Default Tables:
- `auth_user` - User accounts
- `auth_group` - User groups (roles)
- `auth_permission` - Permissions
- `django_session` - User sessions
- `django_content_type` - Content types
- `django_migrations` - Migration history
- `django_admin_log` - Admin actions log

### Custom Tables (to be created):
- Events
- Departments
- Courses
- Tabulators
- Reports
- Activity Logs

---

## 🔐 Security & Configuration Files

### **.env** (Not in Git)
Contains sensitive data:
- SECRET_KEY
- Database credentials
- Debug settings
- Allowed hosts

### **.env.example** (In Git)
Template for .env file with placeholder values

### **.gitignore**
Excludes from version control:
- venv/
- .env
- __pycache__/
- *.pyc
- staticfiles/
- media/
- db.sqlite3

---

## 🚀 Deployment Files

### **requirements.txt**
Lists all Python packages needed:
- Django
- psycopg2-binary (PostgreSQL)
- python-decouple (environment vars)
- Pillow (images)
- djangorestframework (APIs)
- django-cors-headers (CORS)

### **setup.bat**
Automated setup script:
1. Creates virtual environment
2. Installs dependencies
3. Creates .env file
4. Runs migrations
5. Collects static files

### **setup_database.sql**
PostgreSQL setup script:
1. Creates database
2. Creates user
3. Grants privileges

---

## 📝 Documentation Files

- **README.md** - Project overview and quick start
- **SETUP.md** - Detailed setup instructions
- **QUICK_START.md** - 5-minute setup guide
- **PROJECT_STRUCTURE.md** - This file (architecture)

---

## 🔄 Development Workflow

```
1. Edit code (views.py, templates, etc.)
   ↓
2. Test locally (python manage.py runserver)
   ↓
3. Make database changes (models.py)
   ↓
4. Create migrations (python manage.py makemigrations)
   ↓
5. Apply migrations (python manage.py migrate)
   ↓
6. Collect static files (python manage.py collectstatic)
   ↓
7. Test thoroughly
   ↓
8. Commit to Git
   ↓
9. Deploy to production
```

---

## 🎯 Next Development Steps

1. **Create Django Apps**
   - events app (event management)
   - departments app (department/course management)
   - reports app (reporting and analytics)

2. **Define Models**
   - Event model
   - Department model
   - Course model
   - Tabulator model

3. **Create APIs**
   - REST API endpoints
   - API authentication
   - API documentation

4. **Add Features**
   - Event creation/editing
   - User management
   - Department management
   - Reporting dashboard
   - Activity logging

---

## 📞 File Relationships Summary

| File | Depends On | Used By |
|------|-----------|---------|
| settings.py | .env | All Django files |
| urls.py | views.py | Django routing |
| views.py | settings.py, models | urls.py, templates |
| templates | views.py, static files | Browser |
| .env | - | settings.py |
| requirements.txt | - | pip install |
| manage.py | settings.py | Command line |

---

This structure ensures all files are properly connected to Django and PostgreSQL!
