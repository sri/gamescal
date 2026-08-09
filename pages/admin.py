from django.contrib import admin

from .models import (
    Calendar,
    CalendarEvent,
    CalendarEventRule,
    GeoapifyAPILog,
    GeocodedLocation,
    RouteEstimate,
)


class CalendarEventInline(admin.TabularInline):
    model = CalendarEvent
    fields = ("title", "starts_at", "location", "event_type", "status")
    extra = 0
    show_change_link = True


@admin.register(Calendar)
class CalendarAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "timezone", "last_synced_at", "updated_at")
    list_filter = ("is_active", "source_format")
    search_fields = ("name", "cal_url", "website_url")
    readonly_fields = ("last_synced_at", "created_at", "updated_at")
    inlines = (CalendarEventInline,)


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "calendar",
        "starts_at",
        "location",
        "event_type",
        "status",
    )
    list_filter = ("calendar", "event_type", "status", "is_all_day")
    search_fields = ("title", "description", "location", "team1", "team2")
    date_hierarchy = "starts_at"
    readonly_fields = ("created_at", "updated_at")


@admin.register(GeoapifyAPILog)
class GeoapifyAPILogAdmin(admin.ModelAdmin):
    list_display = (
        "request_type",
        "response_status",
        "success",
        "duration_ms",
        "response_size_bytes",
        "created_at",
    )
    list_filter = ("request_type", "success", "response_status")
    readonly_fields = (
        "request_type",
        "method",
        "endpoint",
        "request_params",
        "response_status",
        "response_body",
        "response_size_bytes",
        "response_truncated",
        "duration_ms",
        "success",
        "error_message",
        "created_at",
    )


@admin.register(GeocodedLocation)
class GeocodedLocationAdmin(admin.ModelAdmin):
    list_display = (
        "display_name",
        "latitude",
        "longitude",
        "attempted_at",
        "last_error",
    )
    search_fields = ("display_name", "normalized_name")
    readonly_fields = ("created_at", "attempted_at")


@admin.register(RouteEstimate)
class RouteEstimateAdmin(admin.ModelAdmin):
    list_display = (
        "origin",
        "destination",
        "duration_seconds",
        "distance_meters",
        "calculated_at",
        "last_error",
    )
    search_fields = ("origin__display_name", "destination__display_name")
    readonly_fields = ("created_at", "attempted_at", "calculated_at")


@admin.register(CalendarEventRule)
class CalendarEventRuleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "calendar",
        "match_field",
        "pattern",
        "event_type",
        "priority",
        "is_active",
    )
    list_filter = ("calendar", "event_type", "match_field", "is_active")
    search_fields = ("name", "pattern", "calendar__name")
