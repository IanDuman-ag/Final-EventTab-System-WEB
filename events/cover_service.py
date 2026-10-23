"""Durable, reusable covers. Only generate_cover() can call the external API."""
import base64
import binascii
from io import BytesIO
import json
from pathlib import Path
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from .cover_catalog import BY_KEY, cover_for_event
from .models import Event, EventCoverAsset, EventCoverBudget

MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
EMPTY_IMAGE = Q(image='') | Q(image__isnull=True)


class CoverError(ValueError):
    pass


def optimize_cover(raw):
    """Strip metadata and standardize all saved covers to a light 16:9 WebP."""
    if len(raw) > MAX_IMAGE_BYTES:
        raise CoverError('Cover image exceeds the size limit.')
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.width * source.height > 24_000_000 or min(source.size) < 100:
                raise CoverError('Cover image dimensions are invalid.')
            image = ImageOps.exif_transpose(source).convert('RGB')
            image = ImageOps.fit(image, (1200, 675), method=Image.Resampling.LANCZOS)
            output = BytesIO()
            for quality in (82, 72, 60):
                output.seek(0)
                output.truncate()
                image.save(output, format='WEBP', quality=quality, method=6)
                if output.tell() <= 300_000:
                    return output.getvalue()
            raise CoverError('Cover image could not be optimized within the size limit.')
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise CoverError('Cover response was not a supported image.') from exc


def attach_cover(asset, event_ids=None):
    """Conditional update protects uploads made while the worker was running."""
    if asset.status != 'ready' or not asset.image:
        return 0
    events = Event.objects.filter(EMPTY_IMAGE)
    if event_ids is not None:
        events = events.filter(pk__in=event_ids)
    identifiers = [event.pk for event in events.only(
        'id', 'name', 'sport_type', 'special_event_type', 'category')
        if cover_for_event(event).key == asset.key]
    return Event.objects.filter(EMPTY_IMAGE, pk__in=identifiers).update(image=asset.image.name)


def save_asset_image(asset, raw, source):
    optimized = optimize_cover(raw)
    asset.image.save(f'{asset.key}-{uuid.uuid4().hex}.webp', ContentFile(optimized), save=False)
    asset.status, asset.source, asset.error = 'ready', source, ''
    try:
        asset.save(update_fields=['image', 'status', 'source', 'error', 'updated_at'])
    except Exception:
        # This unique file is not yet referenced by any event if the DB write failed.
        asset.image.storage.delete(asset.image.name)
        raise


def prepare_event_cover(event_id):
    """Resolve an event, import bundled art once, or queue its missing cover type."""
    event = Event.objects.filter(pk=event_id).first()
    if event is None:
        return None
    definition = cover_for_event(event)
    if event.image:
        previous = EventCoverAsset.objects.filter(image=event.image.name, status='ready').first()
        if previous is None or previous.key == definition.key:
            return None
        # A changed event type may replace only our managed cover, never an upload.
        if not Event.objects.filter(pk=event.pk, image=event.image.name).update(image=''):
            return None
    asset, _ = EventCoverAsset.objects.get_or_create(key=definition.key)
    if asset.status != 'ready' and definition.bundled:
        path = Path(settings.BASE_DIR) / 'frontend/landingpage/assets/competitions' / definition.bundled
        if path.is_file():
            with transaction.atomic():
                asset = EventCoverAsset.objects.select_for_update().get(pk=asset.pk)
                if asset.status not in {'ready', 'generating'}:
                    save_asset_image(asset, path.read_bytes(), 'bundled')
    if asset.status == 'ready':
        attach_cover(asset, [event_id])
    return asset


def request_cover(definition):
    if not settings.OPENAI_API_KEY:
        raise CoverError('OPENAI_API_KEY is not configured on the backend.')
    prompt = (
        f'Realistic cinematic editorial photograph of {definition.subject}. '
        'University intramurals event cover, deep navy blue shadows, clean white '
        'spotlights and warm yellow-gold highlights. Natural realistic materials, '
        'professional photography, wide landscape composition. Keep the subject '
        'centered and visible in a wide cropped card. No text, logos, watermarks, '
        'illustration, graphic symbols or collage.'
    )
    payload = json.dumps({
        'model': settings.EVENT_COVER_MODEL, 'prompt': prompt, 'n': 1,
        'size': '1536x1024', 'quality': 'medium', 'output_format': 'jpeg',
    }).encode()
    request = Request('https://api.openai.com/v1/images/generations', data=payload, headers={
        'Authorization': 'Bearer ' + settings.OPENAI_API_KEY,
        'Content-Type': 'application/json',
    }, method='POST')
    try:
        # No automatic retries: an ambiguous timeout may already have been billed.
        with urlopen(request, timeout=180) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise CoverError('Image API response exceeds the size limit.')
        result = json.loads(raw)
        encoded = result['data'][0]['b64_json']
        if not isinstance(encoded, str) or len(encoded) > 14_000_000:
            raise CoverError('Image API returned an invalid image payload.')
        return base64.b64decode(encoded, validate=True)
    except HTTPError as exc:
        raise CoverError(f'Image API request failed (HTTP {exc.code}).') from None
    except (URLError, TimeoutError, OSError):
        raise CoverError('Image API connection failed or timed out. Retry explicitly after checking usage.') from None
    except (KeyError, IndexError, TypeError, ValueError, binascii.Error):
        raise CoverError('Image API returned an invalid image response.') from None


def generate_cover(key, retry_failed=False):
    if key not in BY_KEY:
        raise CoverError('Unknown cover type.')
    if not settings.EVENT_COVERS_ENABLED or not settings.OPENAI_API_KEY:
        raise CoverError('Cover generation is disabled or OPENAI_API_KEY is not configured.')
    with transaction.atomic():
        # All workers lock the daily budget first, then the cover: consistent order.
        budget, _ = EventCoverBudget.objects.get_or_create(day=timezone.localdate())
        budget = EventCoverBudget.objects.select_for_update().get(pk=budget.pk)
        asset, _ = EventCoverAsset.objects.get_or_create(key=key)
        asset = EventCoverAsset.objects.select_for_update().get(pk=asset.pk)
        if asset.status in {'ready', 'generating'}:
            return asset
        if asset.status == 'failed' and not retry_failed:
            raise CoverError('Failed covers require an explicit --retry-failed.')
        if asset.attempts >= max(0, settings.EVENT_COVER_MAX_ATTEMPTS):
            raise CoverError('Maximum generation attempts reached for this cover.')
        if budget.attempts >= max(0, settings.EVENT_COVER_DAILY_LIMIT):
            raise CoverError('Daily cover generation limit reached.')
        budget.attempts += 1
        budget.save(update_fields=['attempts'])
        asset.attempts += 1
        asset.status, asset.error, asset.started_at = 'generating', '', timezone.now()
        asset.save(update_fields=['attempts', 'status', 'error', 'started_at', 'updated_at'])
    try:
        raw = request_cover(BY_KEY[key])
        save_asset_image(asset, raw, 'generated')
    except Exception as exc:
        # Do not persist third-party error bodies, prompts or credentials.
        message = str(exc) if isinstance(exc, CoverError) else 'Cover storage or processing failed.'
        EventCoverAsset.objects.filter(pk=asset.pk).update(status='failed', error=message[:200])
        raise CoverError(message) from None
    attach_cover(asset)
    return asset
