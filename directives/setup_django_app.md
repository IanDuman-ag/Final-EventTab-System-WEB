# Directive: Setup Django App

This SOP defines the process for creating and registering a new Django app within the EventTabs project.

## Goals
- Create a standardized Django app structure.
- Register the app in `core/settings.py`.
- Ensure basic connectivity (urls, views).

## Inputs
- `app_name`: The name of the app to create.

## Tools/Scripts
- `execution/create_app_structure.py`

## Steps

1. **Check for existing app**:
   - Ensure the `app_name` doesn't already exist in the project root.

2. **Run execution script**:
   - Run `python execution/create_app_structure.py <app_name>`.
   - This script creates the basic files: `models.py`, `views.py`, `urls.py`, and a `templates/<app_name>/` directory.

3. **Register the app**:
   - Open `core/settings.py`.
   - Add the `app_name` to the `INSTALLED_APPS` list.

4. **Connect URLs**:
   - Open `core/urls.py`.
   - Add a path for the new app's URLs (e.g., `path('<app_name>/', include('<app_name>.urls'))`).

5. **Verify**:
   - Run `python manage.py check`.

## Edge Cases
- **App name conflict**: If the app name is already used by Django internals or another app, abort.
- **Settings update failure**: If `INSTALLED_APPS` cannot be found or updated, report error.
