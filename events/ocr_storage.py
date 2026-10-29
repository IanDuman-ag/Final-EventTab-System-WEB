"""Private Django storage, deliberately outside the publicly served MEDIA_ROOT."""
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateScoreSheetStorage(Storage):
    def backend(self):
        root = getattr(settings, 'OCR_PRIVATE_ROOT', Path(settings.BASE_DIR) / 'private_uploads' / 'score_sheets')
        return FileSystemStorage(location=root, file_permissions_mode=0o600, directory_permissions_mode=0o700)

    def _open(self, name, mode='rb'):
        return self.backend().open(name, mode)

    def _save(self, name, content):
        return self.backend().save(name, content)

    def exists(self, name):
        return self.backend().exists(name)

    def size(self, name):
        return self.backend().size(name)

    def delete(self, name):
        self.backend().delete(name)

    def url(self, name):
        raise ValueError('Score sheets require an authenticated download.')
