import hashlib
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import template
from django.conf import settings
from django.utils import timezone

register = template.Library()


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
    return int.from_bytes(digest[:2], "big") % 360
