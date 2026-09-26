"""Upload existing local ImageField files to Cloudinary without deleting originals."""

from pathlib import PurePosixPath

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.storage import ImageMediaStorage
from events.models import (
    Candidate,
    Department,
    Event,
    RegistryCandidate,
    ScoreSheet,
    SystemSettings,
    Team,
)


IMAGE_FIELDS = (
    (Event, 'image'),
    (Department, 'logo'),
    (Team, 'image'),
    (RegistryCandidate, 'image'),
    (Candidate, 'photo'),
    (ScoreSheet, 'ocr_image'),
    (SystemSettings, 'school_logo'),
)


class Command(BaseCommand):
    help = (
        'Upload locally stored model images to Cloudinary and update their database paths. '
        'Local source files are retained as backups.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Perform uploads. Without this flag, only list images that need migration.',
        )

    def handle(self, *args, **options):
        if not settings.USE_CLOUDINARY:
            raise CommandError('USE_CLOUDINARY must be True before migrating images.')

        apply_changes = options['apply']
        pending = migrated = failed = 0
        migration_storage = ImageMediaStorage()

        for model, field_name in IMAGE_FIELDS:
            queryset = (
                model.objects.exclude(**{field_name: ''})
                .filter(**{f'{field_name}__isnull': False})
                .exclude(**{f'{field_name}__startswith': 'cloudinary/'})
            )
            for instance in queryset.iterator():
                image = getattr(instance, field_name)
                original_name = image.name
                pending += 1
                label = f'{model.__name__} #{instance.pk} {field_name}: {original_name}'

                if not apply_changes:
                    self.stdout.write(f'PENDING {label}')
                    continue

                try:
                    upload_dir = str(instance._meta.get_field(field_name).upload_to).strip('/')
                    target_name = f'{upload_dir}/{PurePosixPath(original_name).name}'
                    with migration_storage.local.open(original_name, 'rb') as source:
                        cloud_name = migration_storage.save(target_name, source)
                    if not cloud_name.startswith(migration_storage.cloud_prefix):
                        raise RuntimeError('Storage did not return a Cloudinary-backed path.')
                    setattr(instance, field_name, cloud_name)
                    instance.save(update_fields=[field_name])
                    migrated += 1
                    self.stdout.write(self.style.SUCCESS(f'MIGRATED {label} -> {cloud_name}'))
                except Exception as exc:
                    failed += 1
                    self.stderr.write(self.style.ERROR(f'FAILED {label}: {exc}'))

        action = 'Migrated' if apply_changes else 'Found'
        self.stdout.write(
            self.style.SUCCESS(
                f'{action} {migrated if apply_changes else pending} image(s); '
                f'{failed} failed. Local originals were not deleted.'
            )
        )
        if failed:
            raise CommandError(f'{failed} image migration(s) failed.')
