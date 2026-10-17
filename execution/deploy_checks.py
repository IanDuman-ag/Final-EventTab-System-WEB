"""Local gates in a verified disposable PostgreSQL cluster, never production."""
import argparse
import os
from pathlib import Path
import sys
import tempfile


def configure_isolation(settings, temporary):
    # Block all access while provisioning. Only the verified cluster context
    # can replace this with an actual connection before Django setup.
    settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.dummy'}}
    settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
    settings.USE_CLOUDINARY = False
    settings.MEDIA_ROOT = Path(temporary) / 'media'
    settings.STATIC_ROOT = Path(temporary) / 'staticfiles'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('labels', nargs='*', default=['core', 'events', 'accounts'])
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'core.settings'
    # Override before django.setup(), models, checks or test imports can query DB.
    from django.conf import settings
    with tempfile.TemporaryDirectory(prefix='eventtab-local-tests-') as temporary:
        configure_isolation(settings, temporary)
        from local_test_database import isolated_postgres
        with isolated_postgres(root / '.tmp' / 'deploy' / 'test-databases') as database:
            settings.DATABASES = {'default': database}
            import django
            django.setup()
            from django.core.management import call_command
            call_command('check')
            call_command('makemigrations', check=True, dry_run=True, interactive=False)
            call_command('test', *(args.labels or ['core', 'events', 'accounts']), interactive=False)
    print('Local checks passed (isolated PostgreSQL, including actual migrations).')


if __name__ == '__main__':
    main()
