"""Import/reuse covers; explicit --generate processes a bounded number of types."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from events.cover_catalog import BY_KEY
from events.cover_service import CoverError, EMPTY_IMAGE, attach_cover, generate_cover, prepare_event_cover
from events.models import Event, EventCoverAsset


class Command(BaseCommand):
    help = 'Prepare missing event covers. AI generation requires explicit --generate.'

    def add_arguments(self, parser):
        parser.add_argument('--event', type=int, action='append', dest='event_ids')
        parser.add_argument('--generate', action='store_true')
        parser.add_argument('--limit', type=int, default=2, help='Maximum AI calls in this run.')
        parser.add_argument('--retry-failed', action='store_true')

    def handle(self, *args, **options):
        if not settings.EVENT_COVERS_ENABLED:
            raise CommandError('EVENT_COVERS_ENABLED is false.')
        if not 1 <= options['limit'] <= max(0, settings.EVENT_COVER_RUN_LIMIT):
            raise CommandError('Limit must be between 1 and EVENT_COVER_RUN_LIMIT.')
        if options['generate'] and not settings.OPENAI_API_KEY:
            raise CommandError('Set OPENAI_API_KEY in the backend environment before using --generate.')
        events = Event.objects.filter(EMPTY_IMAGE).order_by('pk')
        if options['event_ids']:
            events = events.filter(pk__in=options['event_ids'])
        keys = set()
        for event_id in events.values_list('pk', flat=True).iterator(chunk_size=100):
            asset = prepare_event_cover(event_id)
            if asset:
                keys.add(asset.key)
        attempted = 0
        failures = []
        for key in sorted(keys):
            asset = EventCoverAsset.objects.get(key=key)
            if asset.status == 'ready':
                attach_cover(asset, options['event_ids'])
            elif (options['generate'] and attempted < options['limit']
                  and asset.status != 'generating' and key in BY_KEY
                  and (asset.status != 'failed' or options['retry_failed'])):
                attempted += 1
                try:
                    generate_cover(key, retry_failed=options['retry_failed'])
                    self.stdout.write(f'{key}: saved')
                except CoverError as exc:
                    failures.append(key)
                    self.stderr.write(f'{key}: {exc}')
        self.stdout.write(f'Prepared {len(keys)} cover type(s); attempted {attempted} AI generation(s).')
        if failures:
            raise CommandError('Cover processing failed for: ' + ', '.join(failures))
