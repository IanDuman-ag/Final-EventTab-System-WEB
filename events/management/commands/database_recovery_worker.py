from django.core.management.base import BaseCommand
from events.recovery_worker import main


class Command(BaseCommand):
    help = 'Run the private database recovery worker outside the web service.'
    requires_system_checks = []

    def handle(self, *args, **options):
        main()
