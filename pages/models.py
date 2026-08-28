import json

from django.db import models


class Calendar(models.Model):
    name = models.CharField(max_length=255)
    cal_url = models.URLField("calendar URL", max_length=2000, unique=True)
    website_url = models.URLField(max_length=2000, blank=True)
    is_active = models.BooleanField(default=True)
    timezone = models.CharField(max_length=64, default="America/Phoenix")
    source_format = models.CharField(max_length=20, default="ics", editable=False)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SavedLink(models.Model):
    name = models.CharField(max_length=255, blank=True)
    url = models.URLField(max_length=2000, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["url"]

    def __str__(self):
        return self.name or self.url


class CalendarEvent(models.Model):
    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"
        TENTATIVE = "tentative", "Tentative"
        CANCELLED = "cancelled", "Cancelled"

    class EventType(models.TextChoices):
        GAME = "game", "Game"
        PRACTICE = "practice", "Practice"
        TOURNAMENT = "tournament", "Tournament"
        OTHER = "other", "Other"

    calendar = models.ForeignKey(
        Calendar, on_delete=models.CASCADE, related_name="events"
    )
    external_uid = models.CharField(max_length=255)
    recurrence_id = models.CharField(max_length=255, blank=True)
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField()
    is_all_day = models.BooleanField(default=False)
    location = models.CharField(max_length=500, blank=True)
    address = models.CharField(max_length=500, blank=True)
    team1 = models.CharField(max_length=255, blank=True)
    team2 = models.CharField(max_length=255, blank=True)
    event_url = models.URLField(max_length=2000, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.CONFIRMED
    )
    event_type = models.CharField(
        max_length=20, choices=EventType.choices, default=EventType.OTHER, db_index=True
    )
    raw_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_at", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["calendar", "external_uid", "recurrence_id"],
                name="unique_calendar_event_instance",
            )
        ]

    def __str__(self):
        return self.title


class GeocodedLocation(models.Model):
    normalized_name = models.CharField(max_length=500, unique=True)
    display_name = models.CharField(max_length=500)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    attempted_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_name"]

    @property
    def has_coordinates(self):
        return self.latitude is not None and self.longitude is not None

    def __str__(self):
        return self.display_name


class RouteEstimate(models.Model):
    origin = models.ForeignKey(
        GeocodedLocation, on_delete=models.CASCADE, related_name="routes_from"
    )
    destination = models.ForeignKey(
        GeocodedLocation, on_delete=models.CASCADE, related_name="routes_to"
    )
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    distance_meters = models.PositiveIntegerField(null=True, blank=True)
    provider = models.CharField(max_length=30, default="geoapify")
    last_error = models.TextField(blank=True)
    calculated_at = models.DateTimeField(null=True, blank=True)
    attempted_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["origin", "destination"], name="unique_route_estimate"
            )
        ]

    @property
    def is_available(self):
        return self.duration_seconds is not None

    def __str__(self):
        return f"{self.origin} → {self.destination}"


class GeoapifyAPILog(models.Model):
    class RequestType(models.TextChoices):
        GEOCODING = "geocoding", "Geocoding"
        ROUTING = "routing", "Routing"

    request_type = models.CharField(max_length=20, choices=RequestType.choices)
    method = models.CharField(max_length=10, default="GET")
    endpoint = models.URLField(max_length=500)
    request_params = models.JSONField(default=dict)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True)
    response_size_bytes = models.PositiveIntegerField(default=0)
    response_truncated = models.BooleanField(default=False)
    duration_ms = models.PositiveIntegerField(default=0)
    success = models.BooleanField(default=False)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["request_type", "created_at"]),
            models.Index(fields=["success", "created_at"]),
        ]

    @property
    def request_params_pretty(self):
        return json.dumps(self.request_params, indent=2, sort_keys=True)

    def __str__(self):
        status = self.response_status or "error"
        return f"{self.get_request_type_display()} · {status}"


class CalendarEventRule(models.Model):
    class MatchField(models.TextChoices):
        TITLE = "title", "Title"
        DESCRIPTION = "description", "Description"
        LOCATION = "location", "Location"
        CATEGORY = "category", "ICS category"

    calendar = models.ForeignKey(
        Calendar, on_delete=models.CASCADE, related_name="event_rules"
    )
    name = models.CharField(max_length=100)
    match_field = models.CharField(
        max_length=20, choices=MatchField.choices, default=MatchField.TITLE
    )
    pattern = models.CharField(
        max_length=200,
        help_text="Case-insensitive text to look for, such as ‘practice’.",
    )
    event_type = models.CharField(max_length=20, choices=CalendarEvent.EventType.choices)
    priority = models.PositiveSmallIntegerField(
        default=100, help_text="Lower numbers are applied first."
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "created_at"]

    def __str__(self):
        return f"{self.calendar}: {self.name}"
