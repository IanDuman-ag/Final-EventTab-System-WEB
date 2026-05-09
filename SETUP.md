# EventTabs - Django + PostgreSQL Setup Guide

## Prerequisites

1. **Python 3.10+** installed
2. **PostgreSQL 12+** installed and running
3. **Git** (optional, for version control)

---

## Step 1: PostgreSQL Database Setup

### Option A: Using pgAdmin (GUI)
1. Open pgAdmin
2. Right-click on "Databases" → Create → Database
3. Database name: `eventtabs`
4. Click "Save"
5. Right-click on "Login/Group Roles" → Create → Login/Group Role
6. Name: `event_users`
7. Go to "Definition" tab → Password: `event_pass`
8. Go to "Privileges" tab → Enable "Can login?"
9. Click "Save"
10. Right-click on `eventtabs` database → Properties → Security
11. Add `event_users` with all privileges

### Option B: Using psql (Command Line)
```sql
-- Open psql as postgres user
psql -U postgres

-- Create database
CREATE DATABASE eventtabs;

-- Create user
CREATE USER event_users WITH PASSWORD 'event_pass';

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE eventtabs TO event_users;

-- Connect to the database
\c eventtabs

-- Grant schema privileges
GRANT ALL ON SCHEMA public TO event_users;

-- Exit
\q
```

---

## Step 2: Python Virtual Environment Setup

```cmd
cd c:\Users\Admin\Desktop\eventtabs

# Create virtual environment (if not exists)
python -m venv venv

# Activate virtual environment
venv\Scripts\activate

# You should see (venv) in your command prompt
```

---

## Step 3: Install Dependencies

```cmd
# Make sure virtual environment is activated
pip install --upgrade pip
pip install -r requirements.txt
```

### Required Packages:
- Django 6.0.3
- psycopg2-binary (PostgreSQL adapter)
- python-decouple (environment variables)
- Pillow (image processing)
- djangorestframework (API support)
- django-cors-headers (CORS support)

---

## Step 4: Environment Configuration

1. Copy `.env.example` to `.env`:
```cmd
copy .env.example .env
```

2. Edit `.env` file with your actual database credentials:
```env
SECRET_KEY=your-secret-key-here
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

DB_NAME=eventtabs
DB_USER=event_users
DB_PASSWORD=event_pass
DB_HOST=127.0.0.1
DB_PORT=5432
```

**Important:** Never commit `.env` to version control!

---

## Step 5: Django Database Migrations

```cmd
# Create migration files
python manage.py makemigrations

# Apply migrations to PostgreSQL
python manage.py migrate

# You should see tables created in PostgreSQL
```

---

## Step 6: Create Superuser

```cmd
python manage.py createsuperuser
```

Follow the prompts:
- Username: (your choice)
- Email: (your email)
- Password: (secure password)
- Password confirmation: (repeat password)

---

## Step 7: Collect Static Files (Production)

```cmd
python manage.py collectstatic
```

This collects all static files (CSS, JS) into the `staticfiles` directory.

---

## Step 8: Run Development Server

```cmd
python manage.py runserver
```

Server will start at: `http://127.0.0.1:8000/`

---

## Access Points

- **Login Page**: `http://127.0.0.1:8000/login/`
- **Super Admin Dashboard**: `http://127.0.0.1:8000/super-admin/dashboard/`
- **Django Admin**: `http://127.0.0.1:8000/admin/`
- **Logout**: `http://127.0.0.1:8000/logout/`

---

## Project Structure

```
eventtabs/
├── core/                      # Django project settings
│   ├── settings.py           # Main configuration (PostgreSQL, static files)
│   ├── urls.py               # URL routing
│   ├── views.py              # View functions
│   ├── wsgi.py               # WSGI configuration
│   └── asgi.py               # ASGI configuration
├── frontend/                  # HTML templates and static files
│   ├── login.html
│   ├── login.css
│   ├── superadmin_dashboard.html
│   └── superadmin_dashboard.css
├── venv/                      # Virtual environment (not in git)
├── media/                     # User uploaded files (not in git)
├── staticfiles/               # Collected static files (not in git)
├── .env                       # Environment variables (not in git)
├── .env.example               # Example environment file
├── .gitignore                 # Git ignore rules
├── requirements.txt           # Python dependencies
├── manage.py                  # Django management script
└── SETUP.md                   # This file

```

---

## Common Commands

### Database
```cmd
# Create new migrations
python manage.py makemigrations

# Apply migrations
python manage.py migrate

# Reset database (careful!)
python manage.py flush

# Open Django shell
python manage.py shell
```

### Server
```cmd
# Run development server
python manage.py runserver

# Run on specific port
python manage.py runserver 8080

# Run on all interfaces
python manage.py runserver 0.0.0.0:8000
```

### Static Files
```cmd
# Collect static files
python manage.py collectstatic

# Clear collected static files
python manage.py collectstatic --clear
```

---

## Troubleshooting

### PostgreSQL Connection Error
```
django.db.utils.OperationalError: could not connect to server
```
**Solution:**
1. Check PostgreSQL is running
2. Verify database credentials in `.env`
3. Check PostgreSQL port (default: 5432)
4. Ensure database `eventtabs` exists

### psycopg2 Installation Error
```
Error: pg_config executable not found
```
**Solution:**
```cmd
pip uninstall psycopg2
pip install psycopg2-binary
```

### Template Not Found Error
```
TemplateDoesNotExist at /path/
```
**Solution:**
1. Check `TEMPLATES` setting in `settings.py`
2. Verify template files exist in `frontend/` directory
3. Check template file names match view references

### Static Files Not Loading
```
404 error for CSS/JS files
```
**Solution:**
1. Run `python manage.py collectstatic`
2. Check `STATIC_URL` and `STATICFILES_DIRS` in settings
3. Ensure `{% load static %}` is at top of templates
4. Use `{% static 'filename.css' %}` in templates

---

## Security Notes

1. **Never commit `.env` file** - Contains sensitive credentials
2. **Change SECRET_KEY** in production
3. **Set DEBUG=False** in production
4. **Use strong passwords** for database and superuser
5. **Keep dependencies updated** - Run `pip list --outdated`

---

## Next Steps

1. Create Django apps for different features (events, users, departments)
2. Design database models
3. Create API endpoints (if needed)
4. Add user authentication and authorization
5. Implement event management features
6. Add department and course management
7. Create reporting and analytics

---

## Support

For Django documentation: https://docs.djangoproject.com/
For PostgreSQL documentation: https://www.postgresql.org/docs/
