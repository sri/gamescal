import json
import logging
from datetime import timedelta
from time import perf_counter

import requests
from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone

from .models import GeoapifyAPILog, GeocodedLocation, RouteEstimate

logger = logging.getLogger(__name__)

GEOCODING_URL = "https://api.geoapify.com/v1/geocode/search"
ROUTING_URL = "https://api.geoapify.com/v1/routing"
PHOENIX_BIAS = "proximity:-112.0740,33.4484"
ROUTE_TTL = timedelta(days=30)
ERROR_RETRY_DELAY = timedelta(hours=1)
REQUEST_TIMEOUT = (5, 10)
MAX_LOG_RESPONSE_CHARACTERS = 100_000


def _geoapify_get(request_type, endpoint, params):
    started_at = perf_counter()
    response = None
    payload = {}
    success = False
    error_message = ""
    safe_params = {
        key: "[redacted]" if key.casefold() == "apikey" else value
        for key, value in params.items()
    }

    try:
        response = requests.get(
            endpoint,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}
        response.raise_for_status()
        success = True
        return payload
    except requests.RequestException:
        status = getattr(response, "status_code", None)
        error_message = (
            f"Geoapify returned HTTP {status}." if status else "Geoapify request failed."
        )
        raise
    finally:
        duration_ms = max(0, round((perf_counter() - started_at) * 1000))
        response_body = json.dumps(payload, indent=2, sort_keys=True, default=str)
        api_key = settings.GEOAPIFY_API_KEY
        if api_key:
            response_body = response_body.replace(api_key, "[redacted]")
        response_size = len(response_body.encode())
        response_truncated = len(response_body) > MAX_LOG_RESPONSE_CHARACTERS
        if response_truncated:
            response_body = (
                response_body[:MAX_LOG_RESPONSE_CHARACTERS]
                + "\n… response truncated by Gamescal …"
            )
        response_status = getattr(response, "status_code", None)
        if not isinstance(response_status, int):
            response_status = None
        try:
            GeoapifyAPILog.objects.create(
                request_type=request_type,
                endpoint=endpoint,
                request_params=safe_params,
                response_status=response_status,
                response_body=response_body,
                response_size_bytes=response_size,
                response_truncated=response_truncated,
                duration_ms=duration_ms,
                success=success,
                error_message=error_message,
            )
        except DatabaseError:
            logger.exception("Could not save the Geoapify API log.")


def normalize_location(value):
    return " ".join(str(value or "").casefold().split())[:500]


def _recently_attempted(instance):
    return instance.attempted_at >= timezone.now() - ERROR_RETRY_DELAY


def _geocode_location(label):
    normalized_name = normalize_location(label)
    if not normalized_name:
        return None, False

    location, created = GeocodedLocation.objects.get_or_create(
        normalized_name=normalized_name,
        defaults={"display_name": str(label)[:500]},
    )
    if location.has_coordinates:
        return location, False
    if not created and _recently_attempted(location):
        return None, False

    try:
        payload = _geoapify_get(
            GeoapifyAPILog.RequestType.GEOCODING,
            GEOCODING_URL,
            {
                "text": label,
                "format": "json",
                "filter": "countrycode:us",
                "bias": PHOENIX_BIAS,
                "limit": 1,
                "apiKey": settings.GEOAPIFY_API_KEY,
            },
        )
        results = payload.get("results", [])
        if not results:
            raise ValueError("No matching location")
        result = results[0]
        location.display_name = str(result.get("formatted") or label)[:500]
        location.latitude = float(result["lat"])
        location.longitude = float(result["lon"])
        location.last_error = ""
        location.save()
        return location, True
    except (requests.RequestException, KeyError, TypeError, ValueError):
        location.last_error = "The location could not be geocoded."
        location.save(update_fields=["last_error", "attempted_at"])
        logger.warning("Geoapify could not geocode a calendar location.")
        return None, True


def get_route_estimate(origin_label, destination_label, allow_fetch=True):
    """Return a cached or newly fetched driving estimate and whether APIs were called."""
    origin_key = normalize_location(origin_label)
    destination_key = normalize_location(destination_label)
    if not origin_key or not destination_key or origin_key == destination_key:
        return None, False

    estimate = (
        RouteEstimate.objects.select_related("origin", "destination")
        .filter(
            origin__normalized_name=origin_key,
            destination__normalized_name=destination_key,
        )
        .first()
    )
    if (
        estimate
        and estimate.is_available
        and estimate.calculated_at >= timezone.now() - ROUTE_TTL
    ):
        return estimate, False
    if estimate and estimate.last_error and _recently_attempted(estimate):
        return estimate if estimate.is_available else None, False
    if not allow_fetch or not settings.GEOAPIFY_API_KEY:
        return estimate if estimate and estimate.is_available else None, False

    origin, origin_requested = _geocode_location(origin_label)
    if not origin:
        return estimate if estimate and estimate.is_available else None, origin_requested
    destination, destination_requested = _geocode_location(destination_label)
    requested = origin_requested or destination_requested
    if not destination:
        return estimate if estimate and estimate.is_available else None, requested

    estimate, _created = RouteEstimate.objects.get_or_create(
        origin=origin,
        destination=destination,
    )
    try:
        payload = _geoapify_get(
            GeoapifyAPILog.RequestType.ROUTING,
            ROUTING_URL,
            {
                "waypoints": (
                    f"{origin.latitude},{origin.longitude}"
                    f"|{destination.latitude},{destination.longitude}"
                ),
                "mode": "drive",
                "apiKey": settings.GEOAPIFY_API_KEY,
            },
        )
        properties = payload["features"][0]["properties"]
        estimate.duration_seconds = max(0, round(float(properties["time"])))
        estimate.distance_meters = max(0, round(float(properties["distance"])))
        estimate.last_error = ""
        estimate.calculated_at = timezone.now()
        estimate.save()
        return estimate, True
    except (requests.RequestException, IndexError, KeyError, TypeError, ValueError):
        estimate.last_error = "The driving route could not be calculated."
        estimate.save(update_fields=["last_error", "attempted_at"])
        logger.warning("Geoapify could not calculate a driving route.")
        return estimate if estimate.is_available else None, True
