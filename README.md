# EventTabs

A Django-based event management system with PostgreSQL database backend.

## Features

- **Super Admin Dashboard** - Centralized management interface
- **User Authentication** - Role-based login system (Super Admin, Admin, Tabulator, Viewers)
- **Admin Management** - Manage system administrators and their roles
- **Department Management** - Organize users by departments and courses
- **PostgreSQL Database** - Robust and scalable data storage
- **Responsive Design** - Mobile-friendly interface

## Technology Stack

- **Backend**: Django 6.0.3 (Python)
- **Database**: PostgreSQL
- **Frontend**: HTML5, CSS3, JavaScript
- **Authentication**: Django Auth System

## Quick Start

### 1. Prerequisites
- Python 3.10 or higher
- PostgreSQL 12 or higher
- pip (Python package manager)

### 2. Installation

```cmd
# Clone or download the project
cd c:\Users\Admin\Desktop\eventtabs

# Run automated setup (Windows)
setup.bat
```

Or manually:

```cmd
# Create virtual environment
python -m venv venv

# Activate virtual environment
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment file
copy .env.example .env

# Edit .env with your database credentials
notepad .env
```

### 3. Database Setup

**Option A: Using SQL Script**
```cmd
psql -U postgres -f setup_database.sql
```

**Option B: Manual Setup**
```sql
CREATE DATABASE eventtabs;
CREATE USER event_users WITH PASSWORD 'event_pass';
GRANT ALL PRIVILEGES ON DATABASE eventtabs TO event_users;
```

### 4. Django Setup

```cmd
# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Collect static files
python manage.py collectstatic

# Run development server
python manage.py runserver
```

### 5. Access the Application

- **Login**: http://127.0.0.1:8000/login/
- **Dashboard**: http://127.0.0.1:8000/super-admin/dashboard/
- **Admin Panel**: http://127.0.0.1:8000/admin/

## Project Structure

```
eventtabs/
├── core/                      # Django project configuration
│   ├── settings.py           # Settings (database, static files, etc.)
│   ├── urls.py               # URL routing
│   ├── views.py              # View functions
│   └── wsgi.py               # WSGI configuration
├── frontend/                  # Templates and static files
│   ├── login.html            # Login page
│   ├── login.css             # Login styles
│   ├── superadmin_dashboard.html  # Dashboard template
│   └── superadmin_dashboard.css   # Dashboard styles
├── venv/                      # Virtual environment (excluded from git)
├── .env                       # Environment variables (excluded from git)
├── .env.example               # Example environment configuration
├── requirements.txt           # Python dependencies
├── setup.bat                  # Automated setup script (Windows)
├── setup_database.sql         # PostgreSQL setup script
├── SETUP.md                   # Detailed setup guide
└── README.md                  # This file
```

## Configuration

### Environment Variables (.env)

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

### Database Settings

PostgreSQL connection is configured in `core/settings.py`:
- Database: `eventtabs`
- User: `event_users`
- Password: `event_pass`
- Host: `127.0.0.1`
- Port: `5432`

## User Roles

1. **Super Admin** - Full system access, manage all users and settings
2. **Admin** - Department-level administration
3. **Tabulator** - Event data entry and management
4. **Viewers** - Read-only access to events and reports

## Development

### Running the Server

```cmd
# Activate virtual environment
venv\Scripts\activate

# Run development server
python manage.py runserver

# Run on specific port
python manage.py runserver 8080
```

### Database Migrations

```cmd
# Create migrations after model changes
python manage.py makemigrations

# Apply migrations
python manage.py migrate

# Show migration status
python manage.py showmigrations
```

### Django Shell

```cmd
# Open Django shell for testing
python manage.py shell
```

## Troubleshooting

### PostgreSQL Connection Issues
- Verify PostgreSQL is running
- Check database credentials in `.env`
- Ensure database `eventtabs` exists
- Verify user `event_users` has proper permissions

### Static Files Not Loading
- Run `python manage.py collectstatic`
- Check `STATIC_URL` in settings.py
- Ensure `{% load static %}` is in templates

### Template Not Found
- Verify template files exist in `frontend/` directory
- Check `TEMPLATES` setting in `settings.py`
- Ensure template names match view references

## Security

- Never commit `.env` file to version control
- Change `SECRET_KEY` in production
- Set `DEBUG=False` in production
- Use strong passwords for database and superuser accounts
- Keep dependencies updated

## Documentation

- [Django Documentation](https://docs.djangoproject.com/)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [Detailed Setup Guide](SETUP.md)

## License

Proprietary - All rights reserved

## Support

For issues and questions, please contact the development team.
