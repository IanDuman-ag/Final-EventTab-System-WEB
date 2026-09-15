from tempfile import TemporaryDirectory
from unittest.mock import Mock

from django.core.files.base import ContentFile
from django.test import SimpleTestCase, override_settings

from core.storage import ImageMediaStorage


class ImageMediaStorageTests(SimpleTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=directory.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.storage = ImageMediaStorage()
        self.cloud = Mock()
        self.storage.cloud = self.cloud

    @override_settings(USE_CLOUDINARY=True)
    def test_new_image_uses_cloudinary_and_keeps_remote_marker(self):
        self.cloud.save.return_value = 'event_images/photo_123'
        name = self.storage.save('event_images/photo.png', ContentFile(b'image'))
        self.assertEqual(name, 'cloudinary/event_images/photo_123')
        self.storage.url(name)
        self.cloud.url.assert_called_once_with('event_images/photo_123')

    @override_settings(USE_CLOUDINARY=True)
    def test_long_image_names_leave_room_for_remote_marker(self):
        self.cloud.save.side_effect = lambda name, content: name + '_abcdef'
        name = self.storage.save(
            'department_logos/' + 'a' * 200 + '.png',
            ContentFile(b'image'), max_length=100,
        )
        self.assertLessEqual(len(name), 100)
        self.assertTrue(name.startswith('cloudinary/department_logos/'))

    @override_settings(USE_CLOUDINARY=True)
    def test_documents_stay_local(self):
        name = self.storage.save('event_rules/rules.pdf', ContentFile(b'document'))
        with self.storage.open(name) as uploaded:
            self.assertEqual(uploaded.read(), b'document')
        self.cloud.save.assert_not_called()

    @override_settings(USE_CLOUDINARY=False)
    def test_local_images_remain_accessible_when_enabled(self):
        name = self.storage.save('teams/photo.png', ContentFile(b'image'))
        with override_settings(USE_CLOUDINARY=True):
            self.assertEqual(self.storage.url(name), '/assets/teams/photo.png')
            self.assertTrue(self.storage.exists(name))
        self.cloud.save.assert_not_called()

    @override_settings(USE_CLOUDINARY=False)
    def test_remote_files_remain_remote_when_new_uploads_are_local(self):
        name = 'cloudinary/teams/photo_123'
        self.storage.open(name)
        self.storage.size(name)
        self.storage.delete(name)
        self.cloud.open.assert_called_once_with('teams/photo_123', 'rb')
        self.cloud.size.assert_called_once_with('teams/photo_123')
        self.cloud.delete.assert_called_once_with('teams/photo_123')
