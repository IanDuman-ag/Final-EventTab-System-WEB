# Directive: Database Interaction

This SOP defines how the AI agent interacts with the PostgreSQL database for exploration and data verification.

## Goals
- Query database tables to understand current data.
- Verify that operations (like creating users or events) were successful at the database level.
- Perform bulk data checks.

## Tools/Scripts
- `execution/db_query.py`

## Steps

1. **List Tables (Exploration)**:
   - Run `python execution/db_query.py sql "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"`

2. **Describe Table**:
   - Run `python execution/db_query.py sql "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '<table_name>'"`

3. **Query Data (SQL)**:
   - Run `python execution/db_query.py sql "SELECT * FROM <table_name> LIMIT 10"`

4. **ORM Query (Advanced)**:
   - Run `python execution/db_query.py orm "from django.contrib.auth import get_user_model; User = get_user_model(); result = list(User.objects.values('id', 'username', 'email')[:5])"`
   - Note: The ORM code MUST set the `result` variable.

## Edge Cases
- **Syntax Errors**: Check SQL/Python syntax before running.
- **Permission Denied**: Ensure the database user has SELECT permissions on the target tables.
- **Large Results**: Always use `LIMIT` in SQL to avoid huge outputs.
