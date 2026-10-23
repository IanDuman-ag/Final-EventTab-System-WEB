import base64
from io import BytesIO, StringIO
import json
import tempfile
import threading
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .cover_catalog import BY_KEY, cover_for_event
from .cover_service import CoverError, generate_cover, optimize_cover, prepare_event_cover, request_cover
from .models import Event, EventCoverAsset, EventCoverBudget


def photograph():
    output = BytesIO()
    Image.new('RGB', (1536, 1024), '#0b2349').save(output, 'JPEG')
    return output.getvalue()


@override_settings(OPENAI_API_KEY='test-only-key', EVENT_COVERS_ENABLED=True,
                   EVENT_COVER_DAILY_LIMIT=5, EVENT_COVER_MAX_ATTEMPTS=2,
                   STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                             'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class EventCoverTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage_settings = override_settings(MEDIA_ROOT=self.directory.name)
        self.storage_settings.enable()
        self.addCleanup(self.storage_settings.disable)

    def event(self, name='Volleyball', **kwargs):
        return Event.objects.create(name=name, category='Sports', event_date=timezone.localdate(),
                                    venue='Gym', **kwargs)

    def test_matching_title_precedes_category_and_includes_supported_events(self):
        cases = {'Street Hiphop Dance': 'dance', 'Vocal Duet': 'duet', 'Photo Contest': 'photography',
                 'Poster Making': 'poster', 'Quiz Bee': 'quiz', "Women's Table Tennis": 'table_tennis',
                 "Men's Volleyball": 'volleyball', 'Pageants': 'pageant', 'Unrecognized': 'intramurals'}
        for title, expected in cases.items():
            with self.subTest(title=title):
                event = Event(name=title, category='Pageant')
                # Category is used only if no title/sport match exists.
                if title == 'Unrecognized':
                    event.category = 'Other'
                self.assertEqual(cover_for_event(event).key, expected)

    def test_bundled_cover_saved_to_media_and_reused_without_api(self):
        first, second = self.event('Basketball'), self.event("Women's Basketball")
        with patch('events.cover_service.request_cover') as api:
            asset = prepare_event_cover(first.pk)
            prepare_event_cover(second.pk)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.image.name, second.image.name)
        self.assertEqual(asset.status, 'ready')
        self.assertEqual(asset.source, 'bundled')
        self.assertTrue(first.image.storage.exists(first.image.name))
        self.assertEqual(EventCoverAsset.objects.count(), 1)
        api.assert_not_called()

    def test_admin_upload_is_never_replaced(self):
        event = self.event(image=ContentFile(photograph(), name='uploaded.jpg'))
        original = event.image.name
        self.assertIsNone(prepare_event_cover(event.pk))
        with patch('events.cover_service.request_cover', return_value=photograph()):
            generate_cover('volleyball')
        event.refresh_from_db()
        self.assertEqual(event.image.name, original)

    def test_title_changes_remap_only_managed_covers(self):
        event = self.event('Basketball')
        prepare_event_cover(event.pk)
        event.refresh_from_db()
        old_image = event.image.name
        event.name = 'Vocal Duet'
        event.save(update_fields=['name'])
        prepare_event_cover(event.pk)
        event.refresh_from_db()
        self.assertNotEqual(event.image.name, old_image)
        self.assertEqual(event.image.name, EventCoverAsset.objects.get(key='duet').image.name)

    def test_generation_is_single_shared_persisted_image(self):
        first, second = self.event(), self.event("Women's Volleyball")
        with patch('events.cover_service.request_cover', return_value=photograph()) as api:
            asset = generate_cover('volleyball')
            generate_cover('volleyball')
        api.assert_called_once()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.image.name, asset.image.name)
        self.assertEqual(second.image.name, asset.image.name)
        self.assertEqual(EventCoverBudget.objects.get().attempts, 1)
        with first.image.open('rb') as saved:
            image = Image.open(saved)
            self.assertEqual(image.size, (1200, 675))
            self.assertEqual(image.format, 'WEBP')
        self.assertLess(first.image.size, 300_000)

    def test_daily_and_per_cover_limits_include_failed_attempts(self):
        with override_settings(EVENT_COVER_DAILY_LIMIT=1):
            with patch('events.cover_service.request_cover', side_effect=CoverError('API unavailable')):
                with self.assertRaises(CoverError):
                    generate_cover('volleyball')
            with self.assertRaisesMessage(CoverError, 'Daily cover generation limit'):
                generate_cover('chess')
        with patch('events.cover_service.request_cover', side_effect=CoverError('API unavailable')) as api:
            with self.assertRaisesMessage(CoverError, 'explicit --retry-failed'):
                generate_cover('volleyball')
            with self.assertRaises(CoverError):
                generate_cover('volleyball', retry_failed=True)
            with self.assertRaisesMessage(CoverError, 'Maximum generation attempts'):
                generate_cover('volleyball', retry_failed=True)
            self.assertEqual(api.call_count, 1)

    def test_inflight_cover_is_not_generated_again(self):
        EventCoverAsset.objects.create(key='volleyball', status='generating', attempts=1)
        with patch('events.cover_service.request_cover') as api:
            generate_cover('volleyball')
        api.assert_not_called()

    def test_storage_failure_is_safe_and_does_not_attach_image(self):
        event = self.event()
        with patch('events.cover_service.request_cover', return_value=photograph()), patch(
                'django.core.files.storage.FileSystemStorage._save', side_effect=OSError('private storage detail')):
            with self.assertRaisesMessage(CoverError, 'storage or processing failed'):
                generate_cover('volleyball')
        event.refresh_from_db()
        self.assertFalse(event.image)
        asset = EventCoverAsset.objects.get(key='volleyball')
        self.assertEqual(asset.status, 'failed')
        self.assertNotIn('private', asset.error)

    def test_upload_during_generation_is_preserved(self):
        event = self.event()
        def generating(_definition):
            Event.objects.filter(pk=event.pk).update(image='event_images/admin-upload.jpg')
            return photograph()
        with patch('events.cover_service.request_cover', side_effect=generating):
            generate_cover('volleyball')
        event.refresh_from_db()
        self.assertEqual(event.image.name, 'event_images/admin-upload.jpg')

    def test_post_save_prepares_only_after_successful_commit(self):
        with patch('events.cover_service.prepare_event_cover') as prepare:
            with self.captureOnCommitCallbacks(execute=True):
                event = self.event()
                prepare.assert_not_called()
            prepare.assert_called_once_with(event.pk)

    def test_public_visits_never_generate_or_persist_covers(self):
        event = self.event('Photo Contest', scoring_method='criteria')
        with patch('events.cover_service.request_cover') as api:
            for _ in range(2):
                response = self.client.get(reverse('public_games'))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'data:image/webp;base64,')
        api.assert_not_called()
        self.assertEqual(EventCoverAsset.objects.count(), 0)
        event.refresh_from_db()
        self.assertFalse(event.image)
        self.assertNotContains(response, 'test-only-key')

    def test_command_generates_only_with_explicit_flag_and_honors_run_limit(self):
        self.event()
        self.event('Chess')
        with patch('events.cover_service.request_cover', return_value=photograph()) as api:
            call_command('prepare_event_covers', stdout=StringIO())
            api.assert_not_called()
            call_command('prepare_event_covers', generate=True, limit=1, stdout=StringIO())
            api.assert_called_once()
        with self.assertRaises(CommandError):
            call_command('prepare_event_covers', generate=True, limit=100, stdout=StringIO())

    def test_missing_key_blocks_api_without_counting_an_attempt(self):
        with override_settings(OPENAI_API_KEY=''), patch('events.cover_service.urlopen') as network:
            with self.assertRaises(CoverError):
                generate_cover('volleyball')
        self.assertFalse(EventCoverBudget.objects.exists())
        network.assert_not_called()

    def test_api_errors_and_malformed_images_do_not_leak_credentials(self):
        errors = [HTTPError('https://api.openai.com', 429, 'test-only-key', {}, None),
                  URLError('test-only-key'), TimeoutError('test-only-key')]
        for error in errors:
            with patch('events.cover_service.urlopen', side_effect=error):
                with self.assertRaises(CoverError) as caught:
                    request_cover(BY_KEY['volleyball'])
                self.assertNotIn('test-only-key', str(caught.exception))
        with self.assertRaises(CoverError):
            optimize_cover(b'invalid image')

    def test_api_payload_uses_one_landscape_image_and_fixed_endpoint(self):
        payload = json.dumps({'data': [{'b64_json': base64.b64encode(photograph()).decode()}]}).encode()
        with patch('events.cover_service.urlopen') as network:
            network.return_value.__enter__.return_value.read.return_value = payload
            raw = request_cover(BY_KEY['volleyball'])
        self.assertEqual(raw, photograph())
        request = network.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/images/generations')
        body = json.loads(request.data)
        self.assertEqual(body['n'], 1)
        self.assertEqual(body['size'], '1536x1024')
        self.assertNotIn('Authorization', body)


@override_settings(OPENAI_API_KEY='test-only-key', EVENT_COVERS_ENABLED=True,
                   EVENT_COVER_DAILY_LIMIT=5, EVENT_COVER_MAX_ATTEMPTS=2,
                   STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}})
class EventCoverConcurrencyTests(TransactionTestCase):
    def test_overlapping_workers_claim_a_cover_once(self):
        entered, release = threading.Event(), threading.Event()
        errors = []

        def api(_definition):
            entered.set()
            if not release.wait(10):
                raise RuntimeError('Test worker timed out')
            return photograph()

        def worker():
            close_old_connections()
            try:
                generate_cover('volleyball')
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            with patch('events.cover_service.request_cover', side_effect=api) as network:
                thread = threading.Thread(target=worker)
                thread.start()
                try:
                    self.assertTrue(entered.wait(10))
                    self.assertEqual(generate_cover('volleyball').status, 'generating')
                finally:
                    release.set()
                    thread.join(timeout=15)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                network.assert_called_once()
                self.assertEqual(EventCoverBudget.objects.get().attempts, 1)
                self.assertEqual(EventCoverAsset.objects.get().status, 'ready')
