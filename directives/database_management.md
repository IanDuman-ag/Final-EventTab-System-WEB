# Directive: Database Management

This SOP defines the process for managing migrations and database states in the EventTabs project.

## Goals
- Apply migrations safely.
- Create new migrations when models change.
- Verify database connectivity.

## Tools/Scripts
- `execution/django_cmd.py`

## Steps

1. **Check Database Status**:
   - Run `python execution/django_cmd.py showmigrations`.

2. **Create Migrations** (if models changed):
   - Run `python execution/django_cmd.py makemigrations`.
   - Review the generated migration files.

3. **Apply Migrations**:
   - Run `python execution/django_cmd.py migrate`.

4. **Verify**:
   - Ensure no errors were reported during migration.

## Edge Cases
- **Migration Conflict**: If multiple migrations are created simultaneously, merge them or resolve conflicts manually.
- **Connection Refused**: Ensure PostgreSQL is running and credentials in `.env` are correct.
