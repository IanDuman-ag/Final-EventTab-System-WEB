from django.core.management.base import BaseCommand

from events.mobile_sync import sync_all_events_to_mobile


class Command(BaseCommand):
    help = 'Sync all web admin events to judge mobile judging models.'

    def handle(self, *args, **options):
        count = sync_all_events_to_mobile()
        self.stdout.write(self.style.SUCCESS(f'Synced {count} event(s) to mobile.'))
