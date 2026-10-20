from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import FileSystemStorage
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from events.match_event_service import MatchEventValidationError, validate_event_image


def image_upload():
    buffer = BytesIO()
    Image.new('RGB', (8, 8), 'navy').save(buffer, format='PNG')
    return SimpleUploadedFile('event.png', buffer.getvalue(), content_type='image/png')


class EventImageValidationTests(SimpleTestCase):
    def test_valid_image_can_be_read_after_validation(self):
        upload = image_upload()
        validate_event_image(upload)
        self.assertEqual(upload.tell(), 0)

    def test_raster_formats_are_accepted_without_trusting_mime_or_extension(self):
        for fmt in ('JPEG', 'PNG', 'WEBP', 'GIF', 'BMP', 'TIFF', 'ICO', 'PPM'):
            with self.subTest(format=fmt):
                buffer = BytesIO()
                Image.new('RGB', (32, 32), 'navy').save(buffer, format=fmt)
                upload = SimpleUploadedFile('wrong.bin', buffer.getvalue(), content_type='application/octet-stream')
                result = validate_event_image(upload)
                self.assertEqual(upload.tell(), 0)
                expected = fmt if fmt in {'JPEG', 'PNG', 'WEBP', 'GIF'} else 'PNG'
                with Image.open(result) as image:
                    self.assertEqual(image.format, expected)
                    image.load()
                self.assertNotEqual(result.name, 'wrong.bin')

    def test_heic_and_heif_are_converted_to_png(self):
        from pillow_heif import register_heif_opener
        register_heif_opener()
        for extension in ('heic', 'heif'):
            with self.subTest(extension=extension):
                buffer = BytesIO()
                Image.new('RGB', (32, 32), 'navy').save(buffer, format='HEIF')
                result = validate_event_image(SimpleUploadedFile(
                    f'phone.{extension}', buffer.getvalue(), content_type='application/octet-stream'
                ))
                self.assertTrue(result.name.endswith('.png'))
                with Image.open(result) as image:
                    self.assertEqual(image.format, 'PNG')
                    self.assertEqual(image.size, (32, 32))

    def test_gif_animation_is_preserved(self):
        buffer = BytesIO()
        Image.new('RGB', (8, 8), 'red').save(
            buffer, format='GIF', save_all=True,
            append_images=[Image.new('RGB', (8, 8), 'blue')], duration=100, loop=0,
        )
        result = validate_event_image(SimpleUploadedFile('animation.gif', buffer.getvalue()))
        with Image.open(result) as image:
            self.assertEqual(image.n_frames, 2)

    def test_disguised_file_is_rejected(self):
        with self.assertRaises(MatchEventValidationError):
            validate_event_image(SimpleUploadedFile('event.png', b'not an image', content_type='image/png'))

    def test_oversized_image_is_rejected(self):
        upload = image_upload()
        upload.size = 5 * 1024 * 1024 + 1
        with self.assertRaises(MatchEventValidationError):
            validate_event_image(upload)


class EventImageUploadTests(TestCase):
    def test_criteria_image_saved_and_invalid_replacement_rejected(self):
        from events.criteria_event_service import _save_event_image, CriteriaEventValidationError, serialize_criteria_event
        from events.models import Event
        event = Event.objects.create(name='Singing', event_date='2026-10-10', scoring_method='criteria')
        with TemporaryDirectory() as directory, patch.object(
            Event._meta.get_field('image'), 'storage', FileSystemStorage(location=directory)
        ):
            event = Event.objects.get(pk=event.pk)
            _save_event_image(event, {'event_image': image_upload()})
            event.save()
            event.refresh_from_db()
            name = event.image.name
            self.assertTrue(serialize_criteria_event(event)['image_url'])
            _save_event_image(event, {})
            self.assertEqual(event.image.name, name)
            with self.assertRaises(CriteriaEventValidationError):
                _save_event_image(event, {'event_image': SimpleUploadedFile('bad.png', b'bad')})
            event.refresh_from_db()
            self.assertEqual(event.image.name, name)

    def test_criteria_tiff_is_stored_as_png(self):
        from events.criteria_event_service import _save_event_image
        from events.models import Event
        buffer = BytesIO()
        Image.new('RGB', (8, 8), 'navy').save(buffer, format='TIFF')
        event = Event.objects.create(name='TIFF event', event_date='2026-10-10', scoring_method='criteria')
        with TemporaryDirectory() as directory, patch.object(
            Event._meta.get_field('image'), 'storage', FileSystemStorage(location=directory)
        ):
            event = Event.objects.get(pk=event.pk)
            _save_event_image(event, {'event_image': SimpleUploadedFile('photo.tiff', buffer.getvalue())})
            event.save()
            event.refresh_from_db()
            self.assertTrue(event.image.name.endswith('.png'))
            with event.image.open('rb') as stored:
                self.assertEqual(Image.open(stored).format, 'PNG')

    def test_admin_upload_persists_and_edit_without_upload_keeps_image(self):
        from events.tests import MatchEventWorkflowTests
        from events.models import Event
        fixture = MatchEventWorkflowTests()
        fixture.client = Client()
        fixture.setUp()
        self.client.force_login(fixture.admin)
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory), patch.object(
            Event._meta.get_field('image'), 'storage', FileSystemStorage(location=directory)
        ):
            payload = fixture.payload()
            response = self.client.post(reverse('admin_match_events'), {**payload, 'event_image': image_upload()})
            self.assertEqual(response.status_code, 302)
            event = Event.objects.get(name=payload['event_name'])
            self.assertTrue(event.image.name.startswith('event_images/'))
            saved_name = event.image.name
            with event.image.open('rb') as stored:
                self.assertEqual(Image.open(stored).format, 'PNG')
            response = self.client.post(f'/admin/events/match/{event.pk}/edit/', payload)
            self.assertEqual(response.status_code, 302)
            event.refresh_from_db()
            self.assertEqual(event.image.name, saved_name)
            response = self.client.post(f'/admin/events/match/{event.pk}/edit/', {**payload, 'event_image': image_upload()})
            self.assertEqual(response.status_code, 302)
            event.refresh_from_db()
            self.assertNotEqual(event.image.name, saved_name)
            replacement_name = event.image.name
            with patch.object(Event._meta.get_field('image').storage, 'save', side_effect=OSError('Storage unavailable')):
                response = self.client.post(f'/admin/events/match/{event.pk}/edit/', {**payload, 'event_image': image_upload()})
                self.assertEqual(response.status_code, 302)
            event.refresh_from_db()
            self.assertEqual(event.image.name, replacement_name)
