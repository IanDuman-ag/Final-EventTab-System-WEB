# EventTabs - Quick Start Guide

## 🚀 Get Started in 5 Minutes

### Step 1: Setup PostgreSQL Database (2 minutes)

**Option A: Using pgAdmin**
1. Open pgAdmin
2. Create database: `eventtabs`
3. Create user: `event_users` with password: `event_pass`
4. Grant all privileges to user

**Option B: Using Command Line**
```cmd
psql -U postgres -f setup_database.sql
```

---

### Step 2: Install Python Dependencies (1 minute)

```cmd
cd c:\Users\Admin\Desktop\eventtabs
venv\Scripts\activate
pip install -r requirements.txt
```

---

### Step 3: Configure Environment (30 seconds)

```cmd
copy .env.example .env
```

Edit `.env` if needed (default values work for local development)

---

### Step 4: Setup Django (1 minute)

```cmd
python manage.py migrate
python manage.py createsuperuser
```

Enter your superuser credentials when prompted.

---

### Step 5: Run the Server (30 seconds)

```cmd
python manage.py runserver
```

---

## 🎉 You're Done!

Open your browser: **http://127.0.0.1:8000/login/**

### Login Credentials
- **Email**: (the email you created in Step 4)
- **Password**: (the password you created in Step 4)
- **Role**: Super Admin

---

## 📍 Important URLs

| Page | URL |
|------|-----|
| Login | http://127.0.0.1:8000/login/ |
| Dashboard | http://127.0.0.1:8000/super-admin/dashboard/ |
| Django Admin | http://127.0.0.1:8000/admin/ |

---

## 🔧 Common Commands

```cmd
# Start server
python manage.py runserver

# Create superuser
python manage.py createsuperuser

# Apply database changes
python manage.py migrate

# Activate virtual environment
venv\Scripts\activate
```

---

## ❓ Having Issues?

### PostgreSQL not connecting?
1. Check PostgreSQL is running
2. Verify credentials in `.env` file
3. Ensure database `eventtabs` exists

### Can't install psycopg2?
```cmd
pip install psycopg2-binary
```

### Template not found?
Make sure you're in the project root directory when running commands.

---

## 📚 Need More Help?

- Read [SETUP.md](SETUP.md) for detailed instructions
- Read [README.md](README.md) for project overview
