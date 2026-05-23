"""
Custom template filters for the Manage Events page.
"""
from django import template

register = template.Library()


@register.filter(name='get_item')
def get_item(dictionary, key):
    """Allow `{{ mydict|get_item:key }}` lookups in templates."""
    if dictionary is None:
        return []
    if isinstance(dictionary, dict):
        return dictionary.get(key, [])
    return []
