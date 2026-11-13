"""
Custom template filters for the Manage Events page.
"""
from django import template

register = template.Library()


@register.filter(name='event_title_artwork')
def event_title_artwork(title):
    """Reuse bundled event SVGs for title-based card illustrations."""
    from types import SimpleNamespace
    from core.public_views import game_artwork_path, game_artwork_url

    event = SimpleNamespace(name=str(title or ''), sport_type='', special_event_type='')
    return game_artwork_url(game_artwork_path(event))


@register.filter(name='get_item')
def get_item(dictionary, key):
    """Allow `{{ mydict|get_item:key }}` lookups in templates."""
    if dictionary is None:
        return []
    if isinstance(dictionary, dict):
        return dictionary.get(key, [])
    return []
