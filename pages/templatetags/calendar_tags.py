import hashlib
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import template
from django.conf import settings
from django.utils import timezone

register = template.Library()

# Keep location grouping visually distinct without using pink, magenta, red, or
# orange. A location's normalized text still selects its color deterministically.
COOL_LOCATION_HUES = (145, 155, 165, 175, 185, 195, 205, 215, 225, 235)


@register.filter
def in_timezone(value, timezone_name):
    if value is None:
        return value
    try:
        tz = ZoneInfo(timezone_name or settings.TIME_ZONE)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo(settings.TIME_ZONE)
    return timezone.localtime(value, tz)


@register.filter
def google_maps_directions(destination, origin=None):
    if not destination:
        return ""
    parameters = {"api": "1"}
    if origin:
        parameters["origin"] = str(origin)
    parameters["destination"] = str(destination)
    return f"https://www.google.com/maps/dir/?{urlencode(parameters)}"


@register.filter
def location_hue(value):
    """Return a stable color hue for a normalized location name or address."""
    if not value:
        return 0
    normalized = " ".join(str(value).casefold().split())
    digest = hashlib.sha256(normalized.encode()).digest()
    palette_index = int.from_bytes(digest[:2], "big") % len(COOL_LOCATION_HUES)
    return COOL_LOCATION_HUES[palette_index]
