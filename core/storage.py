"""Route new images to Cloudinary while retaining local documents and images."""

from pathlib import PurePosixPath
from uuid import uuid4

from django.conf import settings
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.functional import cached_property


class ImageMediaStorage(Storage):
    # Persist a marker so existing local paths remain valid after enabling Cloudinary.
    cloud_prefix = 'cloudinary/'
    image_directories = (
        'event_images/', 'department_logos/', 'teams/', 'candidates/',
        'registry_candidates/', 'scoresheets/ocr/', 'system/',
    )

    @cached_property
    def local(self):
        return FileSystemStorage()

    @cached_property
    def cloud(self):
        from cloudinary_storage.storage import MediaCloudinaryStorage

        return MediaCloudinaryStorage()

    def _backend(self, name):
        if name.startswith(self.cloud_prefix):
            return self.cloud, name[len(self.cloud_prefix):]
        return self.local, name

    def get_available_name(self, name, max_length=None):
        if settings.USE_CLOUDINARY and name.startswith(self.image_directories):
            # A short unique basename leaves room for Cloudinary's suffix and our
            # marker within the ImageFields' 100-character database limit.
            path = PurePosixPath(name)
            name = str(path.with_name(uuid4().hex + path.suffix))
            if max_length is not None:
                max_length -= len(self.cloud_prefix) + 16
        return super().get_available_name(name, max_length=max_length)

    def _save(self, name, content):
        if settings.USE_CLOUDINARY and name.startswith(self.image_directories):
            return self.cloud_prefix + self.cloud.save(name, content)
        backend, stored_name = self._backend(name)
        saved_name = backend.save(stored_name, content)
        return self.cloud_prefix + saved_name if name.startswith(self.cloud_prefix) else saved_name

    def _open(self, name, mode='rb'):
        backend, stored_name = self._backend(name)
        return backend.open(stored_name, mode)

    def delete(self, name):
        backend, stored_name = self._backend(name)
        return backend.delete(stored_name)

    def exists(self, name):
        backend, stored_name = self._backend(name)
        return backend.exists(stored_name)

    def size(self, name):
        backend, stored_name = self._backend(name)
        return backend.size(stored_name)

    def url(self, name):
        backend, stored_name = self._backend(name)
        return backend.url(stored_name)

    def path(self, name):
        backend, stored_name = self._backend(name)
        return backend.path(stored_name)
