"""Queue/import covers after successful admin transactions; never calls an AI API."""
import logging

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Event

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Event, dispatch_uid='eventtab_event_cover')
def event_saved(sender, instance, raw=False, using='default', **kwargs):
    if raw or not settings.EVENT_COVERS_ENABLED or using != 'default':
        return
    if instance.image and not instance.image.name.startswith(('event_covers/', 'cloudinary/event_covers/')):
        return

    def assign():
        from .cover_service import prepare_event_cover
        try:
            prepare_event_cover(instance.pk)
        except Exception:
            # Optional artwork must never undo scoring/event creation.
            logger.exception('Unable to prepare cover for event id=%s', instance.pk)

    transaction.on_commit(assign, using=using)
