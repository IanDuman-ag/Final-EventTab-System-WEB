# Directive: Static Files Management

This SOP defines the process for collecting and managing static files.

## Goals
- Collect all static files into `staticfiles/` directory for production.
- Ensure CSS/JS changes are reflected in the application.

## Tools/Scripts
- `execution/django_cmd.py`

## Steps

1. **Collect Static Files**:
   - Run `python execution/django_cmd.py collectstatic --no-input`.

2. **Verify**:
   - Check if `staticfiles/` directory exists and contains the expected files.

## Edge Cases
- **Permission Denied**: Ensure the system has write access to `staticfiles/`.
- **Missing Files**: Ensure `STATICFILES_DIRS` is correctly configured in `core/settings.py`.
