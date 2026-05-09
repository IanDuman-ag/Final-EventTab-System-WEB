# EventTabs - Setup Verification Checklist

## ✅ Pre-Setup Checklist

- [ ] Python 3.10+ installed
- [ ] PostgreSQL 12+ installed
- [ ] PostgreSQL service running
- [ ] Git installed (optional)
- [ ] Text editor installed (VS Code, Notepad++, etc.)

---

## ✅ PostgreSQL Setup Checklist

- [ ] PostgreSQL server is running
- [ ] Database `eventtabs` created
- [ ] User `event_users` created
- [ ] Password `event_pass` set for user
- [ ] User has all privileges on database
- [ ] Can connect to database using credentials
- [ ] Port 5432 is accessible

**Verify with:**
```cmd
psql -U event_users -d eventtabs -h 127.0.0.1
```

---

## ✅ Python Environment Checklist

- [ ] Virtual environment created (`venv/` folder exists)
- [ ] Virtual environment activated (see `(venv)` in prompt)
- [ ] pip upgraded to latest version
- [ ] All packages from requirements.txt installed
- [ ] No installation errors

**Verify with:**
```cmd
pip list
```

Should show:
- Django 6.0.3
- psycopg2-binary
- python-decouple
- Pillow
- djangorestframework
- django-cors-headers

---

## ✅ Configuration Files Checklist

- [ ] `.env` file created (copied from `.env.example`)
- [ ] `.env` contains correct database credentials
- [ ] `.env` has SECRET_KEY set
- [ ] `.gitignore` excludes `.env` file
- [ ] `requirements.txt` exists
- [ ] `setup_database.sql` exists

**Verify .env contains:**
```env
DB_NAME=eventtabs
DB_USER=event_users
DB_PASSWORD=event_pass
DB_HOST=127.0.0.1
DB_PORT=5432
```

---

## ✅ Django Setup Checklist

- [ ] `manage.py` file exists
- [ ] `core/settings.py` configured correctly
- [ ] `core/urls.py` has all routes
- [ ] `core/views.py` has all view functions
- [ ] `frontend/` directory has all templates
- [ ] Templates directory configured in settings

**Verify with:**
```cmd
python manage.py check
```

Should show: "System check identified no issues"

---

## ✅ Database Migration Checklist

- [ ] Migrations created (`python manage.py makemigrations`)
- [ ] Migrations applied (`python manage.py migrate`)
- [ ] No migration errors
- [ ] Tables created in PostgreSQL
- [ ] Can see Django tables in database

**Verify with:**
```cmd
python manage.py showmigrations
```

All migrations should have [X] marks.

**Check PostgreSQL tables:**
```sql
\c eventtabs
\dt
```

Should show Django tables (auth_user, auth_group, etc.)

---

## ✅ Superuser Creation Checklist

- [ ] Superuser created (`python manage.py createsuperuser`)
- [ ] Username set
- [ ] Email set
- [ ] Password set (strong password)
- [ ] Can login to Django admin

**Verify with:**
Visit `http://127.0.0.1:8000/admin/` and login

---

## ✅ Static Files Checklist

- [ ] `frontend/` directory has CSS files
- [ ] `STATIC_URL` configured in settings
- [ ] `STATICFILES_DIRS` configured in settings
- [ ] Static files collected (`python manage.py collectstatic`)
- [ ] `staticfiles/` directory created
- [ ] CSS files loading in browser

**Verify with:**
Check if CSS is applied when viewing pages

---

## ✅ Server Running Checklist

- [ ] Development server starts without errors
- [ ] Server accessible at `http://127.0.0.1:8000/`
- [ ] No error messages in console
- [ ] Can access login page
- [ ] Can access dashboard after login

**Verify with:**
```cmd
python manage.py runserver
```

Should show:
```
Starting development server at http://127.0.0.1:8000/
Quit the server with CTRL-BREAK.
```

---

## ✅ URL Routing Checklist

- [ ] `/` redirects to login
- [ ] `/login/` shows login page
- [ ] `/super-admin/dashboard/` shows dashboard (after login)
- [ ] `/admin/` shows Django admin
- [ ] `/logout/` logs out user
- [ ] No 404 errors on main pages

**Test each URL in browser**

---

## ✅ Authentication Checklist

- [ ] Login page loads correctly
- [ ] Login form has email, password, role fields
- [ ] Can login with superuser credentials
- [ ] Role selection works (Super Admin)
- [ ] Redirects to dashboard after login
- [ ] Session persists (stays logged in)
- [ ] Logout works correctly
- [ ] Cannot access dashboard without login

**Test login flow:**
1. Go to `/login/`
2. Enter credentials
3. Select "Super Admin" role
4. Click login
5. Should redirect to dashboard

---

## ✅ Dashboard Checklist

- [ ] Dashboard loads without errors
- [ ] Statistics cards show correct data
- [ ] Role Management table displays users
- [ ] Admin Management section displays admins
- [ ] Activity alerts display
- [ ] Navigation sidebar works
- [ ] CSS styling applied correctly
- [ ] Responsive on mobile/tablet

**Verify dashboard shows:**
- Total Departments count
- Admins count
- Total Users count
- User list in table
- Admin accounts list

---

## ✅ Database Connection Checklist

- [ ] Django connects to PostgreSQL
- [ ] No connection errors
- [ ] Queries execute successfully
- [ ] Data displays in dashboard
- [ ] User authentication works
- [ ] Sessions stored in database

**Check for errors:**
```
django.db.utils.OperationalError
```

If you see this, database connection failed.

---

## ✅ Template Rendering Checklist

- [ ] Templates load without errors
- [ ] No "TemplateDoesNotExist" errors
- [ ] Static files load (CSS)
- [ ] Template variables render correctly
- [ ] Django template tags work ({% load static %})
- [ ] Context data displays in templates

**Common template errors to check:**
- TemplateDoesNotExist
- TemplateSyntaxError
- Static files 404

---

## ✅ Security Checklist

- [ ] `.env` file not committed to Git
- [ ] `.env` in `.gitignore`
- [ ] Strong SECRET_KEY set
- [ ] Strong database password
- [ ] Strong superuser password
- [ ] DEBUG=True only in development
- [ ] ALLOWED_HOSTS configured

**Verify .gitignore excludes:**
- .env
- venv/
- __pycache__/
- *.pyc
- staticfiles/
- media/

---

## ✅ Documentation Checklist

- [ ] README.md exists and is complete
- [ ] SETUP.md exists with detailed instructions
- [ ] QUICK_START.md exists for quick setup
- [ ] PROJECT_STRUCTURE.md explains architecture
- [ ] CHECKLIST.md (this file) for verification
- [ ] Comments in code are clear
- [ ] .env.example has all required variables

---

## ✅ Final Verification

Run all these commands without errors:

```cmd
# 1. Check Django configuration
python manage.py check

# 2. Show migrations status
python manage.py showmigrations

# 3. Test database connection
python manage.py dbshell

# 4. Run development server
python manage.py runserver
```

**All should complete successfully!**

---

## 🎉 Success Criteria

Your setup is complete when:

1. ✅ PostgreSQL database is running and accessible
2. ✅ Virtual environment is activated
3. ✅ All dependencies are installed
4. ✅ `.env` file is configured
5. ✅ Database migrations are applied
6. ✅ Superuser is created
7. ✅ Development server runs without errors
8. ✅ Can login at `/login/`
9. ✅ Dashboard displays at `/super-admin/dashboard/`
10. ✅ All pages load with proper styling

---

## 🐛 Troubleshooting

If any checklist item fails, refer to:
- **SETUP.md** - Detailed setup instructions
- **README.md** - Troubleshooting section
- Django error messages in console
- PostgreSQL logs

---

## 📞 Common Issues

### Issue: "could not connect to server"
**Solution:** Start PostgreSQL service

### Issue: "TemplateDoesNotExist"
**Solution:** Check TEMPLATES setting in settings.py

### Issue: "No module named 'psycopg2'"
**Solution:** `pip install psycopg2-binary`

### Issue: "Static files not loading"
**Solution:** Run `python manage.py collectstatic`

### Issue: "CSRF verification failed"
**Solution:** Check CSRF middleware in settings.py

---

## ✅ Post-Setup Tasks

After completing this checklist:

- [ ] Create additional superusers if needed
- [ ] Create user groups (Admin, Tabulator, Viewers)
- [ ] Add test data to database
- [ ] Test all user roles
- [ ] Configure email settings (if needed)
- [ ] Set up backup strategy
- [ ] Plan next development phase

---

**Date Completed:** _______________

**Completed By:** _______________

**Notes:** _______________________________________________
