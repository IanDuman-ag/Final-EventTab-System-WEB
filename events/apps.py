from django.apps import AppConfig


class EventsConfig(AppConfig):
    name = 'events'

    def ready(self):
        from . import cover_signals  # noqa: F401
