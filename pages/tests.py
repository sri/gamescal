from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Calendar, CalendarEvent, CalendarEventRule, GeoapifyAPILog
from .services import (
    CalendarImportError,
    EventData,
    ImportResult,
    _validate_remote_url,
    parse_calendar,
)
from .templatetags.calendar_tags import google_maps_directions, location_hue


@override_settings(
    ENABLE_API_LOG_VIEW=True,
    ENABLE_DEMO_TOOLS=True,
    GEOAPIFY_API_KEY="",
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
)
class PageTests(TestCase):
    def sample_result(self):
        return ImportResult(
            name="City League",
            timezone="America/Phoenix",
            events=[
                EventData(
                    external_uid="game-1@example.com",
                    recurrence_id="",
                    title="Falcons vs Bears",
                    description="Opening game",
                    starts_at=timezone.now() + timedelta(days=2),
                    ends_at=timezone.now() + timedelta(days=2, hours=2),
                    is_all_day=False,
                    location="Central Stadium",
                    address="Central Stadium",
                    team1="Falcons",
                    team2="Bears",
                    event_url="https://example.com/games/1",
                    status="confirmed",
                    raw_data={},
                )
            ],
        )

    def test_home_page(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/home.html")
        self.assertContains(response, "Gamescal")
        self.assertContains(response, "Add Calendar")
        self.assertContains(response, "No calendars configured yet.")
        self.assertNotContains(response, "Upcoming events")
        self.assertNotContains(response, "Events from your active calendars.")
        self.assertNotContains(response, "All your events in one place.")
        self.assertContains(response, "Populate Demo")
        self.assertContains(response, "API Logs")

    @override_settings(ENABLE_API_LOG_VIEW=False, ENABLE_DEMO_TOOLS=False)
    def test_debug_query_enables_developer_tools(self):
        regular = self.client.get(reverse("home"))
        self.assertNotContains(regular, "Populate Demo")
        self.assertNotContains(regular, "API Logs")

        debug_home = self.client.get(reverse("home"), {"debug": "1"})
        self.assertContains(debug_home, "Populate Demo")
        self.assertContains(debug_home, "API Logs")
        self.assertContains(
            debug_home, f'{reverse("geoapify_api_logs")}?debug=1'
        )
        self.assertContains(
            debug_home, f'{reverse("calendar_demo_populate")}?debug=1'
        )

        logs = self.client.get(reverse("geoapify_api_logs"), {"debug": "1"})
        self.assertEqual(logs.status_code, 200)
        demo = self.client.post(
            f'{reverse("calendar_demo_populate")}?debug=1'
        )
        self.assertRedirects(demo, f'{reverse("home")}?debug=1')

    def test_geoapify_log_page_shows_counts_filters_and_payloads(self):
        GeoapifyAPILog.objects.create(
            request_type=GeoapifyAPILog.RequestType.GEOCODING,
            endpoint="https://api.geoapify.com/v1/geocode/search",
            request_params={"text": "Central Stadium", "apiKey": "[redacted]"},
            response_status=200,
            response_body='{"results": [{"lat": 33.4}]}',
            response_size_bytes=32,
            duration_ms=125,
            success=True,
        )
        GeoapifyAPILog.objects.create(
            request_type=GeoapifyAPILog.RequestType.ROUTING,
            endpoint="https://api.geoapify.com/v1/routing",
            request_params={"waypoints": "33.4,-112|33.5,-112"},
            response_status=401,
            response_body='{"message": "Invalid apiKey"}',
            response_size_bytes=29,
            duration_ms=75,
            success=False,
            error_message="Geoapify returned HTTP 401.",
        )

        response = self.client.get(reverse("geoapify_api_logs"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/geoapify_logs.html")
        self.assertEqual(response.context["stats"]["total"], 2)
        self.assertEqual(response.context["stats"]["successful"], 1)
        self.assertEqual(response.context["stats"]["failed"], 1)
        self.assertEqual(response.context["stats"]["success_rate"], 50.0)
        self.assertContains(response, "Central Stadium")
        self.assertContains(response, "Invalid apiKey")
        self.assertContains(response, "[redacted]")

        filtered = self.client.get(
            reverse("geoapify_api_logs"), {"type": "routing", "result": "error"}
        )
        self.assertEqual(len(filtered.context["page"]), 1)
        self.assertEqual(
            filtered.context["page"][0].request_type,
            GeoapifyAPILog.RequestType.ROUTING,
        )

    def test_clear_geoapify_logs(self):
        GeoapifyAPILog.objects.create(
            request_type=GeoapifyAPILog.RequestType.GEOCODING,
            endpoint="https://api.geoapify.com/v1/geocode/search",
        )

        response = self.client.post(reverse("geoapify_api_logs_clear"))

        self.assertRedirects(response, reverse("geoapify_api_logs"))
        self.assertEqual(GeoapifyAPILog.objects.count(), 0)

    @override_settings(ENABLE_API_LOG_VIEW=False)
    def test_geoapify_logs_are_unavailable_when_disabled(self):
        self.assertEqual(
            self.client.get(reverse("geoapify_api_logs")).status_code, 404
        )
        self.assertEqual(
            self.client.post(reverse("geoapify_api_logs_clear")).status_code, 404
        )

    def test_populate_demo_uses_existing_locations_and_scenarios(self):
        source_calendar = Calendar.objects.create(
            name="Source", cal_url="https://example.com/source-locations.ics"
        )
        source_locations = (
            "Demo Field A, Phoenix, AZ",
            "Demo Field B, Phoenix, AZ",
            "Demo Field C, Phoenix, AZ",
            "Demo Field D, Phoenix, AZ",
        )
        for index, location in enumerate(source_locations):
            CalendarEvent.objects.create(
                calendar=source_calendar,
                external_uid=f"source-{index}",
                title=f"Source game {index}",
                starts_at=timezone.now() + timedelta(days=index + 1),
                ends_at=timezone.now() + timedelta(days=index + 1, minutes=50),
                event_type=CalendarEvent.EventType.GAME,
                location=location,
                address=location,
            )

        response = self.client.post(reverse("calendar_demo_populate"))

        self.assertRedirects(response, reverse("home"))
        demo = Calendar.objects.get(cal_url="https://gamescal.local/travel-demo.ics")
        events = list(demo.events.order_by("starts_at"))
        self.assertEqual(len(events), 7)
        self.assertTrue({event.location for event in events} <= set(source_locations))
        offsets = [
            round((following.starts_at - current.starts_at).total_seconds() / 60)
            for current, following in zip(events, events[1:])
        ]
        self.assertEqual(offsets, [50, 60, 160, 50, 30, 130])
        self.assertEqual(events[0].location, events[1].location)
        self.assertNotEqual(events[1].location, events[2].location)
        self.assertEqual(events[3].location, events[4].location)

    @override_settings(ENABLE_DEMO_TOOLS=False)
    def test_populate_demo_is_unavailable_outside_debug_mode(self):
        response = self.client.post(reverse("calendar_demo_populate"))

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            Calendar.objects.filter(
                cal_url="https://gamescal.local/travel-demo.ics"
            ).exists()
        )

    def test_home_lists_only_events_from_active_calendars(self):
        active = Calendar.objects.create(
            name="Active",
            cal_url="https://active.test/a.ics",
            website_url="https://active.test/schedule",
        )
        inactive = Calendar.objects.create(
            name="Inactive", cal_url="https://inactive.test/a.ics", is_active=False
        )
        for calendar, title in ((active, "Visible game"), (inactive, "Hidden game")):
            CalendarEvent.objects.create(
                calendar=calendar,
                external_uid=title,
                title=title,
                starts_at=timezone.now() + timedelta(days=1),
                ends_at=timezone.now() + timedelta(days=1, hours=1),
                event_type=CalendarEvent.EventType.GAME,
            )

        response = self.client.get(reverse("home"), {"range": "all"})

        self.assertContains(response, "Visible game")
        self.assertContains(response, "mobile-event-card")
        self.assertContains(response, "d-none d-md-block table-responsive")
        self.assertContains(
            response, 'href="https://active.test/schedule"', count=2
        )
        self.assertNotContains(response, "Hidden game")

    def test_event_calendar_link_falls_back_to_feed_url(self):
        calendar = Calendar.objects.create(
            name="Feed only", cal_url="https://example.com/fallback.ics"
        )
        CalendarEvent.objects.create(
            calendar=calendar,
            external_uid="fallback-link",
            title="Fallback link game",
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, minutes=50),
            event_type=CalendarEvent.EventType.GAME,
        )

        response = self.client.get(reverse("home"), {"range": "all"})

        self.assertContains(
            response, 'href="https://example.com/fallback.ics"', count=2
        )

    @patch("pages.views.timezone.now")
    def test_home_event_type_views_and_week_filter(self, mocked_now):
        mocked_now.return_value = datetime(
            2026, 8, 12, 16, 0, tzinfo=dt_timezone.utc
        )
        calendar = Calendar.objects.create(
            name="League",
            cal_url="https://example.com/views.ics",
            timezone="America/Phoenix",
        )
        arizona = ZoneInfo("America/Phoenix")
        for title, starts_at, event_type in (
            (
                "This week game",
                datetime(2026, 8, 12, 10, 0, tzinfo=arizona),
                CalendarEvent.EventType.GAME,
            ),
            (
                "This week practice",
                datetime(2026, 8, 13, 10, 0, tzinfo=arizona),
                CalendarEvent.EventType.PRACTICE,
            ),
            (
                "Next week game",
                datetime(2026, 8, 17, 10, 0, tzinfo=arizona),
                CalendarEvent.EventType.GAME,
            ),
        ):
            CalendarEvent.objects.create(
                calendar=calendar,
                external_uid=title,
                title=title,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(hours=1),
                event_type=event_type,
            )

        games = self.client.get(reverse("home"))
        self.assertEqual(games.context["event_range"], "week")
        self.assertContains(games, "This week game")
        self.assertNotContains(games, "This week practice")
        self.assertNotContains(games, "Next week game")
        self.assertNotContains(games, "Weekend only")

        practices = self.client.get(reverse("home"), {"view": "practices"})
        self.assertContains(practices, "This week practice")
        self.assertNotContains(practices, "This week game")
        self.assertNotContains(practices, "Next week game")

        all_events = self.client.get(reverse("home"), {"view": "all"})
        self.assertContains(all_events, "This week game")
        self.assertContains(all_events, "This week practice")
        self.assertNotContains(all_events, "Next week game")
        self.assertContains(all_events, 'aria-label="Event types"')

        all_upcoming = self.client.get(
            reverse("home"), {"view": "all", "range": "all"}
        )
        self.assertEqual(all_upcoming.context["event_range"], "all")
        self.assertContains(all_upcoming, "Next week game")

    @patch("pages.views.get_route_estimate")
    def test_game_gaps_use_a_fifty_minute_game_length(self, route_estimate):
        route_estimate.return_value = (
            SimpleNamespace(
                is_available=True,
                duration_seconds=20 * 60,
                distance_meters=16093,
            ),
            False,
        )
        calendar = Calendar.objects.create(
            name="Tournament", cal_url="https://example.com/tournament.ics"
        )
        first_start = (timezone.now() + timedelta(days=1)).astimezone(
            ZoneInfo("America/Phoenix")
        ).replace(hour=12, minute=0, second=0, microsecond=0)
        starts = (
            first_start,
            first_start + timedelta(minutes=50),
            first_start + timedelta(minutes=130),
        )
        locations = ("Central Stadium", "Central Stadium", "North Field")
        for index, (starts_at, location) in enumerate(
            zip(starts, locations), start=1
        ):
            CalendarEvent.objects.create(
                calendar=calendar,
                external_uid=f"gap-game-{index}",
                title=f"Gap game {index}",
                starts_at=starts_at,
                # Deliberately inaccurate feed durations: gap calculation uses 50 minutes.
                ends_at=starts_at + timedelta(hours=3),
                event_type=CalendarEvent.EventType.GAME,
                location=location,
                address=location,
            )

        response = self.client.get(reverse("home"), {"range": "all"})
        events = list(response.context["events"])

        self.assertEqual(events[0].game_gap_after, "")
        self.assertEqual(events[1].game_gap_after, "30 min")
        self.assertEqual(events[1].game_drive_after, "20 min")
        self.assertEqual(events[1].game_drive_distance_after, "10 mi")
        self.assertEqual(events[1].game_buffer_after, "10 min buffer")
        self.assertEqual(events[2].game_gap_after, "")
        self.assertContains(response, "30 min between games", count=2)
        self.assertContains(response, "~20 min drive")
        self.assertContains(response, "10 min buffer")

    def test_game_gaps_and_travel_are_hidden_between_different_days(self):
        calendar = Calendar.objects.create(
            name="Two-day event", cal_url="https://example.com/two-days.ics"
        )
        first_start = timezone.now() + timedelta(days=1)
        for index, (starts_at, location) in enumerate(
            (
                (first_start, "Central Stadium"),
                (first_start + timedelta(days=1), "North Field"),
            ),
            start=1,
        ):
            CalendarEvent.objects.create(
                calendar=calendar,
                external_uid=f"day-{index}",
                title=f"Day {index} game",
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=50),
                event_type=CalendarEvent.EventType.GAME,
                location=location,
                address=location,
            )

        response = self.client.get(reverse("home"), {"range": "all"})
        events = list(response.context["events"])

        self.assertEqual(events[0].game_gap_after, "")
        self.assertEqual(events[1].directions_origin, "")
        self.assertNotContains(response, "between games")
        self.assertNotContains(response, "drive")

    def test_changed_game_location_routes_from_previous_game(self):
        calendar = Calendar.objects.create(
            name="Travel schedule", cal_url="https://example.com/travel.ics"
        )
        start = (timezone.now() + timedelta(days=1)).astimezone(
            ZoneInfo("America/Phoenix")
        ).replace(hour=12, minute=0, second=0, microsecond=0)
        event_specs = (
            ("First game", CalendarEvent.EventType.GAME, "Central Stadium"),
            ("Same venue", CalendarEvent.EventType.GAME, " central   stadium "),
            ("Practice stop", CalendarEvent.EventType.PRACTICE, "Practice Gym"),
            ("Away game", CalendarEvent.EventType.GAME, "North Field"),
        )
        for index, (title, event_type, location) in enumerate(event_specs):
            CalendarEvent.objects.create(
                calendar=calendar,
                external_uid=f"travel-{index}",
                title=title,
                starts_at=start + timedelta(hours=index),
                ends_at=start + timedelta(hours=index, minutes=50),
                event_type=event_type,
                location=location,
                address=location,
            )

        response = self.client.get(
            reverse("home"), {"view": "all", "range": "all"}
        )
        events = {event.title: event for event in response.context["events"]}

        self.assertEqual(events["Same venue"].directions_origin, "")
        self.assertEqual(
            events["Away game"].directions_origin, " central   stadium "
        )
        self.assertEqual(events["Practice stop"].directions_origin, "")
        self.assertContains(response, "Directions from the previous game")

    def test_about_page(self):
        response = self.client.get(reverse("about"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/about.html")

    @patch("pages.views.fetch_and_parse_calendar")
    def test_preview_then_confirm_import(self, fetch):
        fetch.return_value = self.sample_result()

        response = self.client.post(
            reverse("calendar_add"),
            {
                "name": "",
                "cal_url": "https://example.com/schedule.ics",
                "website_url": "https://example.com/league",
            },
        )

        self.assertRedirects(
            response,
            reverse("calendar_preview", kwargs={"token": response.url.split("/")[-2]}),
        )
        token = response.url.split("/")[-2]
        preview = self.client.get(response.url)
        self.assertContains(preview, "City League")
        self.assertContains(preview, "Falcons vs Bears")
        self.assertContains(preview, "event-location-colored")
        self.assertEqual(Calendar.objects.count(), 0)

        confirm = self.client.post(
            reverse("calendar_confirm", kwargs={"token": token})
        )

        self.assertRedirects(confirm, reverse("home"))
        calendar = Calendar.objects.get()
        self.assertEqual(calendar.name, "City League")
        self.assertEqual(calendar.events.get().team1, "Falcons")
        self.assertIsNotNone(calendar.last_synced_at)

    @patch("pages.views.fetch_and_parse_calendar")
    def test_import_error_is_shown_on_form(self, fetch):
        fetch.side_effect = CalendarImportError("That feed is broken.")

        response = self.client.post(
            reverse("calendar_add"),
            {"cal_url": "https://example.com/broken.ics"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "That feed is broken.")
        self.assertEqual(Calendar.objects.count(), 0)

    @patch("pages.views.fetch_and_parse_calendar")
    def test_refresh_replaces_events(self, fetch):
        calendar = Calendar.objects.create(
            name="League", cal_url="https://example.com/schedule.ics"
        )
        CalendarEvent.objects.create(
            calendar=calendar,
            external_uid="old",
            title="Old game",
            starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(hours=1),
        )
        CalendarEventRule.objects.create(
            calendar=calendar,
            name="Team practices",
            pattern="Falcons",
            event_type=CalendarEvent.EventType.PRACTICE,
            priority=1,
        )
        fetch.return_value = self.sample_result()

        response = self.client.post(
            reverse("calendar_refresh", kwargs={"pk": calendar.pk})
        )

        self.assertRedirects(response, reverse("home"))
        self.assertFalse(calendar.events.filter(title="Old game").exists())
        refreshed_event = calendar.events.get(title="Falcons vs Bears")
        self.assertEqual(refreshed_event.event_type, CalendarEvent.EventType.PRACTICE)

    def test_calendar_rule_creation_reclassifies_existing_events(self):
        calendar = Calendar.objects.create(
            name="League", cal_url="https://example.com/rules.ics"
        )
        event = CalendarEvent.objects.create(
            calendar=calendar,
            external_uid="session-1",
            title="Team session",
            description="Regular PRACTICE session",
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=1),
        )

        response = self.client.post(
            reverse("calendar_rules", kwargs={"pk": calendar.pk}),
            {
                "name": "Practice descriptions",
                "match_field": "description",
                "pattern": "practice",
                "event_type": CalendarEvent.EventType.PRACTICE,
                "priority": 10,
                "is_active": "on",
            },
        )

        self.assertRedirects(
            response, reverse("calendar_rules", kwargs={"pk": calendar.pk})
        )
        event.refresh_from_db()
        self.assertEqual(event.event_type, CalendarEvent.EventType.PRACTICE)

    def test_disabling_and_deleting_rule_reclassifies_events(self):
        calendar = Calendar.objects.create(
            name="League", cal_url="https://example.com/rule-actions.ics"
        )
        event = CalendarEvent.objects.create(
            calendar=calendar,
            external_uid="practice-1",
            title="Practice",
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=1),
            event_type=CalendarEvent.EventType.GAME,
        )
        rule = CalendarEventRule.objects.create(
            calendar=calendar,
            name="Treat practice as game",
            pattern="practice",
            event_type=CalendarEvent.EventType.GAME,
            priority=1,
        )

        self.client.post(
            reverse(
                "calendar_rule_toggle",
                kwargs={"pk": calendar.pk, "rule_pk": rule.pk},
            )
        )
        event.refresh_from_db()
        self.assertEqual(event.event_type, CalendarEvent.EventType.PRACTICE)

        self.client.post(
            reverse(
                "calendar_rule_delete",
                kwargs={"pk": calendar.pk, "rule_pk": rule.pk},
            )
        )
        self.assertFalse(CalendarEventRule.objects.filter(pk=rule.pk).exists())

    def test_calendars_are_collapsed_on_home_page(self):
        calendar = Calendar.objects.create(
            name="Collapsed League", cal_url="https://example.com/collapsed.ics"
        )

        response = self.client.get(reverse("home"))

        self.assertContains(response, 'class="collapse" id="calendarsCollapse"')
        self.assertContains(response, 'aria-expanded="false"')
        self.assertContains(
            response, reverse("calendar_edit", kwargs={"pk": calendar.pk})
        )

    def test_calendar_edit_shows_information_and_event_counts(self):
        calendar = Calendar.objects.create(
            name="League", cal_url="https://example.com/details.ics"
        )
        for index, event_type in enumerate(
            (CalendarEvent.EventType.GAME, CalendarEvent.EventType.PRACTICE), start=1
        ):
            CalendarEvent.objects.create(
                calendar=calendar,
                external_uid=f"event-{index}",
                title=f"Event {index}",
                starts_at=timezone.now() + timedelta(days=index),
                ends_at=timezone.now() + timedelta(days=index, hours=1),
                event_type=event_type,
            )

        response = self.client.get(
            reverse("calendar_edit", kwargs={"pk": calendar.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/calendar_edit.html")
        self.assertEqual(response.context["event_counts"]["total"], 2)
        self.assertEqual(response.context["event_counts"]["games"], 1)
        self.assertEqual(response.context["event_counts"]["practices"], 1)
        self.assertContains(response, "Delete Calendar")
        self.assertContains(response, "Refresh Events")
        self.assertContains(response, "Manage Classification Rules")

    def test_calendar_edit_updates_details(self):
        calendar = Calendar.objects.create(
            name="Old name", cal_url="https://example.com/old.ics"
        )

        response = self.client.post(
            reverse("calendar_edit", kwargs={"pk": calendar.pk}),
            {
                "name": "New name",
                "cal_url": "https://example.com/new.ics",
                "website_url": "https://example.com/team",
            },
        )

        self.assertRedirects(
            response, reverse("calendar_edit", kwargs={"pk": calendar.pk})
        )
        calendar.refresh_from_db()
        self.assertEqual(calendar.name, "New name")
        self.assertEqual(calendar.cal_url, "https://example.com/new.ics")
        self.assertEqual(calendar.website_url, "https://example.com/team")

    def test_delete_calendar_removes_its_events_and_rules(self):
        calendar = Calendar.objects.create(
            name="Delete me", cal_url="https://example.com/delete.ics"
        )
        CalendarEvent.objects.create(
            calendar=calendar,
            external_uid="delete-event",
            title="Delete event",
            starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(hours=1),
        )
        CalendarEventRule.objects.create(
            calendar=calendar,
            name="Delete rule",
            pattern="practice",
            event_type=CalendarEvent.EventType.PRACTICE,
        )

        response = self.client.post(
            reverse("calendar_delete", kwargs={"pk": calendar.pk})
        )

        self.assertRedirects(response, reverse("home"))
        self.assertFalse(Calendar.objects.filter(pk=calendar.pk).exists())
        self.assertEqual(CalendarEvent.objects.count(), 0)
        self.assertEqual(CalendarEventRule.objects.count(), 0)

    def test_delete_calendar_requires_post(self):
        calendar = Calendar.objects.create(
            name="Keep me", cal_url="https://example.com/keep.ics"
        )

        response = self.client.get(
            reverse("calendar_delete", kwargs={"pk": calendar.pk})
        )

        self.assertEqual(response.status_code, 405)
        self.assertTrue(Calendar.objects.filter(pk=calendar.pk).exists())

    def test_toggle_calendar(self):
        calendar = Calendar.objects.create(
            name="League", cal_url="https://example.com/schedule.ics"
        )

        response = self.client.post(
            reverse("calendar_toggle", kwargs={"pk": calendar.pk}), {"next": "edit"}
        )

        self.assertRedirects(
            response, reverse("calendar_edit", kwargs={"pk": calendar.pk})
        )
        calendar.refresh_from_db()
        self.assertFalse(calendar.is_active)


class CalendarParsingTests(TestCase):
    def test_same_location_has_same_color(self):
        self.assertEqual(
            location_hue(" Central   Stadium, Phoenix AZ "),
            location_hue("central stadium, phoenix az"),
        )
        self.assertNotEqual(
            location_hue("Central Stadium, Phoenix AZ"),
            location_hue("North Field, Phoenix AZ"),
        )

    def test_google_maps_directions_url(self):
        self.assertEqual(
            google_maps_directions("Central Stadium, Phoenix AZ"),
            "https://www.google.com/maps/dir/?api=1&destination=Central+Stadium%2C+Phoenix+AZ",
        )
        self.assertEqual(
            google_maps_directions("North Field", "Central Stadium"),
            "https://www.google.com/maps/dir/?api=1&origin=Central+Stadium&destination=North+Field",
        )

    def test_parse_calendar_extracts_events_and_expands_recurrence(self):
        content = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Gamescal Tests//EN\r
X-WR-CALNAME:Test League\r
X-WR-TIMEZONE:America/New_York\r
BEGIN:VEVENT\r
UID:weekly-game\r
DTSTART;TZID=America/New_York:20261001T190000\r
DTEND;TZID=America/New_York:20261001T210000\r
RRULE:FREQ=WEEKLY;COUNT=3\r
SUMMARY:Falcons vs Bears\r
LOCATION:Central Stadium\r
URL:https://example.com/game\r
END:VEVENT\r
END:VCALENDAR\r
"""
        with patch("pages.services.timezone.now") as now:
            now.return_value = datetime(2026, 9, 15, tzinfo=dt_timezone.utc)
            result = parse_calendar(content, "https://example.com/schedule.ics")

        self.assertEqual(result.name, "Test League")
        self.assertEqual(result.timezone, "America/Phoenix")
        self.assertEqual(len(result.events), 3)
        self.assertEqual(result.events[0].team1, "Falcons")
        self.assertEqual(result.events[0].team2, "Bears")
        self.assertEqual(result.events[0].location, "Central Stadium")
        self.assertEqual(result.events[0].event_type, "game")
        self.assertEqual(
            result.events[0].starts_at.astimezone(ZoneInfo("America/Phoenix")).hour,
            16,
        )
        self.assertTrue(result.events[0].starts_at.tzinfo)

    def test_floating_times_are_interpreted_as_arizona_time(self):
        content = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Gamescal Tests//EN\r
X-WR-CALNAME:Arizona League\r
X-WR-TIMEZONE:America/New_York\r
BEGIN:VEVENT\r
UID:az-game\r
DTSTART:20261001T190000\r
DTEND:20261001T210000\r
SUMMARY:Falcons vs Bears\r
END:VEVENT\r
END:VCALENDAR\r
"""
        with patch("pages.services.timezone.now") as now:
            now.return_value = datetime(2026, 9, 15, tzinfo=dt_timezone.utc)
            result = parse_calendar(content, "https://example.com/arizona.ics")

        start_in_arizona = result.events[0].starts_at.astimezone(
            ZoneInfo("America/Phoenix")
        )
        self.assertEqual(result.timezone, "America/Phoenix")
        self.assertEqual(start_in_arizona.hour, 19)
        self.assertEqual(start_in_arizona.utcoffset(), timedelta(hours=-7))

    def test_private_calendar_urls_are_blocked(self):
        with self.assertRaises(CalendarImportError):
            _validate_remote_url("http://127.0.0.1/calendar.ics")
