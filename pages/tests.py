from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Calendar,
    CalendarEvent,
    CalendarEventRule,
    CalendarVisibilityRule,
    GeoapifyAPILog,
    SavedLink,
)
from .services import (
    CalendarImportError,
    EventData,
    ImportResult,
    _validate_remote_url,
    parse_calendar,
)
from .templatetags.calendar_tags import (
    COOL_LOCATION_HUES,
    google_maps_directions,
    location_hue,
)


TEST_NOW = datetime(2026, 8, 12, 16, 0, tzinfo=dt_timezone.utc)


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

    @patch("pages.views.timezone.now", return_value=TEST_NOW)
    def test_home_lists_only_events_from_active_calendars(self, _mocked_now):
        active = Calendar.objects.create(
            name="Active",
            cal_url="https://active.test/a.ics",
            website_url="https://active.test/schedule",
            is_mine=True,
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
                is_mine=calendar.is_mine,
            )

        response = self.client.get(reverse("home"))

        self.assertContains(response, "Visible game")
        self.assertContains(response, "mobile-event-card")
        self.assertContains(response, "d-none d-md-block table-responsive")
        self.assertContains(
            response, 'href="https://active.test/schedule"', count=2
        )
        self.assertNotContains(response, "Hidden game")

    @patch("pages.views.timezone.now", return_value=TEST_NOW)
    def test_games_and_all_can_filter_by_ownership(self, _mocked_now):
        mine = Calendar.objects.create(
            name="Coach schedule",
            cal_url="https://example.com/coach.ics",
            is_mine=True,
        )
        other = Calendar.objects.create(
            name="League schedule",
            cal_url="https://example.com/league.ics",
        )
        # Ownership filtering follows the calendar, not a team-name match stored
        # on an individual event.
        for calendar, title, is_mine in (
            (mine, "My game", False),
            (other, "Other game", True),
        ):
            CalendarEvent.objects.create(
                calendar=calendar,
                external_uid=title,
                title=title,
                starts_at=TEST_NOW + timedelta(days=1),
                ends_at=TEST_NOW + timedelta(days=1, hours=1),
                event_type=CalendarEvent.EventType.GAME,
                is_mine=is_mine,
            )

        mine_only = self.client.get(reverse("home"), {"view": "games"})
        self.assertEqual(mine_only.context["event_scope"], "mine")
        self.assertContains(mine_only, "My game")
        self.assertNotContains(mine_only, "Other game")
        self.assertContains(mine_only, 'aria-label="Calendar ownership"')
        self.assertNotContains(mine_only, "Apply Calendars")
        self.assertNotContains(mine_only, "Select All")
        scope_filters = mine_only.content.decode().split(
            'aria-label="Calendar ownership"', 1
        )[1][:600]
        self.assertLess(scope_filters.index(">Mine</a>"), scope_filters.index(">All</a>"))
        self.assertLess(scope_filters.index(">All</a>"), scope_filters.index(">Others</a>"))

        others_only = self.client.get(
            reverse("home"), {"view": "all", "scope": "others"}
        )
        self.assertNotContains(others_only, "My game")
        self.assertContains(others_only, "Other game")

        all_games = self.client.get(
            reverse("home"), {"view": "games", "scope": "all"}
        )
        self.assertContains(all_games, "My game")
        self.assertContains(all_games, "Other game")
        self.assertContains(all_games, '<tr class="event-external-calendar">')
        self.assertContains(all_games, "mobile-event-external-calendar")

    @patch("pages.views.timezone.now", return_value=TEST_NOW)
    def test_event_calendar_link_falls_back_to_feed_url(self, _mocked_now):
        calendar = Calendar.objects.create(
            name="Feed only",
            cal_url="https://example.com/fallback.ics",
            is_mine=True,
        )
        CalendarEvent.objects.create(
            calendar=calendar,
            external_uid="fallback-link",
            title="Fallback link game",
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, minutes=50),
            event_type=CalendarEvent.EventType.GAME,
            is_mine=True,
        )

        response = self.client.get(reverse("home"))

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
            is_mine=True,
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
                "Sunday practice",
                datetime(2026, 8, 16, 10, 0, tzinfo=arizona),
                CalendarEvent.EventType.PRACTICE,
            ),
            (
                "Next week game",
                datetime(2026, 8, 17, 10, 0, tzinfo=arizona),
                CalendarEvent.EventType.GAME,
            ),
            (
                "Next week practice",
                datetime(2026, 8, 18, 10, 0, tzinfo=arizona),
                CalendarEvent.EventType.PRACTICE,
            ),
            (
                "Future team meeting",
                datetime(2026, 8, 19, 10, 0, tzinfo=arizona),
                CalendarEvent.EventType.OTHER,
            ),
        ):
            CalendarEvent.objects.create(
                calendar=calendar,
                external_uid=title,
                title=title,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(hours=1),
                event_type=event_type,
                is_mine=True,
            )

        all_events = self.client.get(reverse("home"), {"view": "all"})
        self.assertEqual(all_events.context["event_view"], "all")
        self.assertContains(all_events, "This week game")
        self.assertContains(all_events, "This week practice")
        self.assertContains(all_events, "Sunday practice")
        self.assertContains(all_events, "Next week game")
        self.assertContains(all_events, "Next week practice")
        self.assertContains(all_events, "Future team meeting")
        self.assertContains(all_events, 'aria-label="Event views"')
        self.assertContains(all_events, 'id="all-event-type"')
        self.assertEqual(all_events.context["all_event_type"], "all")
        self.assertNotContains(all_events, "This week</a>")
        self.assertNotContains(all_events, "All upcoming")
        self.assertNotContains(all_events, "Weekend only")

        games = self.client.get(reverse("home"))
        self.assertEqual(games.context["event_view"], "games")
        self.assertContains(games, "This week game")
        self.assertNotContains(games, "This week practice")
        self.assertNotContains(games, "Next week game")
        self.assertNotContains(games, 'id="all-event-type"')

        practices = self.client.get(reverse("home"), {"view": "practices"})
        self.assertContains(practices, "This week practice")
        self.assertContains(practices, "Sunday practice")
        self.assertNotContains(practices, "This week game")
        self.assertNotContains(practices, "Next week practice")
        self.assertNotContains(practices, 'id="all-event-type"')

        all_games = self.client.get(
            reverse("home"), {"view": "all", "type": "games"}
        )
        self.assertEqual(all_games.context["all_event_type"], "games")
        self.assertContains(all_games, "This week game")
        self.assertContains(all_games, "Next week game")
        self.assertNotContains(all_games, "This week practice")
        self.assertNotContains(all_games, "Future team meeting")

        all_practices = self.client.get(
            reverse("home"), {"view": "all", "type": "practices"}
        )
        self.assertEqual(all_practices.context["all_event_type"], "practices")
        self.assertContains(all_practices, "This week practice")
        self.assertContains(all_practices, "Next week practice")
        self.assertNotContains(all_practices, "This week game")
        self.assertNotContains(all_practices, "Future team meeting")

    @patch("pages.views.timezone.now", return_value=TEST_NOW)
    def test_home_defaults_to_practices_when_week_has_no_games(self, _mocked_now):
        calendar = Calendar.objects.create(
            name="Practice calendar",
            cal_url="https://example.com/practice-default.ics",
            timezone="America/Phoenix",
        )
        arizona = ZoneInfo("America/Phoenix")
        practice_start = datetime(2026, 8, 13, 10, 0, tzinfo=arizona)
        next_week_game = datetime(2026, 8, 17, 10, 0, tzinfo=arizona)
        for title, starts_at, event_type in (
            ("Available practice", practice_start, CalendarEvent.EventType.PRACTICE),
            ("Later game", next_week_game, CalendarEvent.EventType.GAME),
        ):
            CalendarEvent.objects.create(
                calendar=calendar,
                external_uid=title,
                title=title,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(hours=1),
                event_type=event_type,
            )

        response = self.client.get(reverse("home"))

        self.assertEqual(response.context["event_view"], "practices")
        self.assertContains(response, "Available practice")
        self.assertNotContains(response, "Later game")

    @patch("pages.views.timezone.now", return_value=TEST_NOW)
    @patch("pages.views.get_route_estimate")
    def test_game_gaps_use_a_fifty_minute_game_length(
        self, route_estimate, _mocked_now
    ):
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

        response = self.client.get(
            reverse("home"), {"view": "games", "scope": "all"}
        )
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

    @patch("pages.views.timezone.now", return_value=TEST_NOW)
    def test_matching_games_from_different_calendars_warn_when_schedules_disagree(
        self, _mocked_now
    ):
        coach = Calendar.objects.create(
            name="Coach",
            cal_url="https://example.com/coach-conflict.ics",
            is_mine=True,
            team_aliases="Falcons",
        )
        league = Calendar.objects.create(
            name="League",
            cal_url="https://example.com/league-conflict.ics",
            team_aliases="Phoenix Falcons\nFalcons",
        )
        first_start = TEST_NOW + timedelta(days=1)
        for calendar, starts_at, location in (
            (coach, first_start, "Central Stadium"),
            (league, first_start + timedelta(hours=3), "North Field"),
        ):
            CalendarEvent.objects.create(
                calendar=calendar,
                external_uid=f"conflict-{calendar.pk}",
                title="Falcons vs Bears",
                team1="Falcons",
                team2="Bears",
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=50),
                event_type=CalendarEvent.EventType.GAME,
                is_mine=True,
                location=location,
            )

        response = self.client.get(
            reverse("home"), {"view": "games", "scope": "all"}
        )
        events = list(response.context["events"])

        self.assertEqual(len(events), 2)
        self.assertTrue(all(event.schedule_conflict for event in events))
        self.assertTrue(all("Coach:" in event.schedule_conflict_details for event in events))
        self.assertTrue(all("League:" in event.schedule_conflict_details for event in events))
        self.assertContains(response, "Schedule conflict", count=4)
        self.assertContains(response, "Central Stadium")
        self.assertContains(response, "North Field")

    @patch("pages.views.timezone.now", return_value=TEST_NOW)
    def test_game_gaps_and_travel_are_hidden_between_different_days(
        self, _mocked_now
    ):
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

        response = self.client.get(
            reverse("home"), {"view": "games", "scope": "all"}
        )
        events = list(response.context["events"])

        self.assertEqual(events[0].game_gap_after, "")
        self.assertEqual(events[1].directions_origin, "")
        self.assertNotContains(response, "between games")
        self.assertNotContains(response, "drive")

    @patch("pages.views.timezone.now", return_value=TEST_NOW)
    def test_changed_game_location_routes_from_previous_game(self, _mocked_now):
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
            reverse("home"), {"view": "all", "scope": "all"}
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
                "is_mine": "on",
                "team_aliases": "Falcons",
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
        self.assertTrue(calendar.is_mine)
        self.assertEqual(calendar.team_aliases, "Falcons")
        self.assertEqual(calendar.events.get().team1, "Falcons")
        self.assertTrue(calendar.events.get().is_mine)
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
    def test_refresh_deletes_old_events_then_previews_and_adds_approved_events(
        self, fetch
    ):
        calendar = Calendar.objects.create(
            name="League",
            cal_url="https://example.com/schedule.ics",
            team_aliases="Falcons",
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
        CalendarVisibilityRule.objects.create(
            calendar=calendar,
            name="Hide Falcons",
            action=CalendarVisibilityRule.Action.HIDE,
            match_field=CalendarVisibilityRule.MatchField.TITLE,
            pattern="Falcons",
        )
        fetch.return_value = self.sample_result()

        response = self.client.post(
            reverse("calendar_refresh", kwargs={"pk": calendar.pk})
        )

        token = response.url.split("/")[-2]
        self.assertRedirects(
            response,
            reverse("calendar_preview", kwargs={"token": token}),
        )
        self.assertEqual(calendar.events.count(), 0)

        preview = self.client.get(response.url)
        self.assertContains(preview, "Replacement preview")
        self.assertContains(preview, "Falcons vs Bears")
        self.assertContains(preview, "Practice")
        self.assertContains(preview, "Approve &amp; Add Events", count=2)
        self.assertContains(preview, "The previous events have been deleted.")
        self.assertEqual(calendar.events.count(), 0)

        confirm = self.client.post(
            reverse("calendar_confirm", kwargs={"token": token})
        )

        self.assertRedirects(
            confirm, reverse("calendar_edit", kwargs={"pk": calendar.pk})
        )
        refreshed_event = calendar.events.get(title="Falcons vs Bears")
        self.assertEqual(refreshed_event.event_type, CalendarEvent.EventType.PRACTICE)
        self.assertTrue(refreshed_event.is_mine)
        self.assertFalse(refreshed_event.is_visible)

    @patch("pages.views.fetch_and_parse_calendar")
    def test_refresh_keeps_old_events_when_download_fails(self, fetch):
        calendar = Calendar.objects.create(
            name="League", cal_url="https://example.com/broken-refresh.ics"
        )
        old_event = CalendarEvent.objects.create(
            calendar=calendar,
            external_uid="old",
            title="Old game",
            starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(hours=1),
        )
        fetch.side_effect = CalendarImportError("Feed unavailable.")

        response = self.client.post(
            reverse("calendar_refresh", kwargs={"pk": calendar.pk}),
            {"next": "edit"},
            follow=True,
        )

        self.assertRedirects(
            response, reverse("calendar_edit", kwargs={"pk": calendar.pk})
        )
        self.assertTrue(calendar.events.filter(pk=old_event.pk).exists())
        self.assertContains(response, "Feed unavailable.")

    @patch("pages.views.fetch_and_parse_calendar")
    def test_refresh_all_replaces_events_in_every_calendar(self, fetch):
        calendars = [
            Calendar.objects.create(
                name=name, cal_url=f"https://example.com/{name.casefold()}.ics"
            )
            for name in ("Alpha", "Beta")
        ]
        for calendar in calendars:
            CalendarEvent.objects.create(
                calendar=calendar,
                external_uid="old",
                title=f"Old {calendar.name} event",
                starts_at=timezone.now(),
                ends_at=timezone.now() + timedelta(hours=1),
            )
        fetch.side_effect = [self.sample_result(), self.sample_result()]

        response = self.client.post(reverse("calendars_refresh_all"))

        self.assertRedirects(response, reverse("home"))
        self.assertEqual(fetch.call_count, 2)
        for calendar in calendars:
            self.assertEqual(calendar.events.count(), 1)
            self.assertEqual(calendar.events.get().title, "Falcons vs Bears")

    @patch("pages.views.fetch_and_parse_calendar")
    def test_refresh_all_keeps_old_events_when_a_feed_fails(self, fetch):
        failed = Calendar.objects.create(
            name="Broken", cal_url="https://example.com/broken.ics"
        )
        working = Calendar.objects.create(
            name="Working", cal_url="https://example.com/working.ics"
        )
        for calendar in (failed, working):
            CalendarEvent.objects.create(
                calendar=calendar,
                external_uid="old",
                title=f"Old {calendar.name} event",
                starts_at=timezone.now(),
                ends_at=timezone.now() + timedelta(hours=1),
            )
        fetch.side_effect = [
            CalendarImportError("Feed unavailable."),
            self.sample_result(),
        ]

        response = self.client.post(reverse("calendars_refresh_all"), follow=True)

        self.assertRedirects(response, reverse("home"))
        self.assertTrue(failed.events.filter(title="Old Broken event").exists())
        self.assertEqual(failed.events.count(), 1)
        self.assertTrue(working.events.filter(title="Falcons vs Bears").exists())
        failed.refresh_from_db()
        self.assertEqual(failed.last_sync_error, "Feed unavailable.")
        self.assertContains(response, "Kept the existing events")

    def test_refresh_all_requires_post(self):
        response = self.client.get(reverse("calendars_refresh_all"))

        self.assertEqual(response.status_code, 405)

    def test_visibility_rules_mark_every_event_shown_or_hidden_in_preview(self):
        calendar = Calendar.objects.create(
            name="League",
            cal_url="https://example.com/visibility.ics",
            is_mine=True,
        )
        matching = CalendarEvent.objects.create(
            calendar=calendar,
            external_uid="falcons",
            title="Falcons vs Bears",
            team1="Falcons",
            team2="Bears",
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=1),
            event_type=CalendarEvent.EventType.GAME,
        )
        unrelated = CalendarEvent.objects.create(
            calendar=calendar,
            external_uid="tigers",
            title="Tigers vs Wolves",
            team1="Tigers",
            team2="Wolves",
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=1),
            event_type=CalendarEvent.EventType.GAME,
        )

        add_show = self.client.post(
            reverse("calendar_visibility_rules", kwargs={"pk": calendar.pk}),
            {
                "name": "Teams we follow",
                "action": CalendarVisibilityRule.Action.SHOW,
                "match_field": CalendarVisibilityRule.MatchField.TEAM,
                "pattern": "Falcons",
                "priority": 10,
                "is_active": "on",
            },
        )

        self.assertRedirects(
            add_show,
            reverse("calendar_visibility_rules", kwargs={"pk": calendar.pk}),
        )
        matching.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertTrue(matching.is_visible)
        self.assertFalse(unrelated.is_visible)

        preview = self.client.get(
            reverse("calendar_visibility_rules", kwargs={"pk": calendar.pk})
        )
        self.assertContains(preview, "All imported events")
        self.assertContains(preview, "Falcons vs Bears")
        self.assertContains(preview, "Tigers vs Wolves")
        self.assertContains(preview, "1 shown")
        self.assertContains(preview, "1 hidden")
        self.assertContains(preview, "Shown by: Teams we follow")
        self.assertContains(preview, "no Show only rule matched")

        add_hide = self.client.post(
            reverse("calendar_visibility_rules", kwargs={"pk": calendar.pk}),
            {
                "name": "Hide Bears",
                "action": CalendarVisibilityRule.Action.HIDE,
                "match_field": CalendarVisibilityRule.MatchField.TEAM,
                "pattern": "Bears",
                "priority": 20,
                "is_active": "on",
            },
        )
        self.assertEqual(add_hide.status_code, 302)
        matching.refresh_from_db()
        self.assertFalse(matching.is_visible)

        home = self.client.get(
            reverse("home"), {"view": "all", "scope": "mine"}
        )
        self.assertNotContains(home, "Falcons vs Bears")
        self.assertNotContains(home, "Tigers vs Wolves")

    def test_visibility_rule_toggle_and_delete_reapply_existing_events(self):
        calendar = Calendar.objects.create(
            name="League", cal_url="https://example.com/visibility-actions.ics"
        )
        event = CalendarEvent.objects.create(
            calendar=calendar,
            external_uid="hidden-event",
            title="Hidden game",
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=1),
            is_visible=False,
        )
        rule = CalendarVisibilityRule.objects.create(
            calendar=calendar,
            name="Hide games",
            action=CalendarVisibilityRule.Action.HIDE,
            match_field=CalendarVisibilityRule.MatchField.TITLE,
            pattern="game",
        )

        self.client.post(
            reverse(
                "calendar_visibility_rule_toggle",
                kwargs={"pk": calendar.pk, "rule_pk": rule.pk},
            )
        )
        event.refresh_from_db()
        self.assertTrue(event.is_visible)

        self.client.post(
            reverse(
                "calendar_visibility_rule_delete",
                kwargs={"pk": calendar.pk, "rule_pk": rule.pk},
            )
        )
        self.assertFalse(CalendarVisibilityRule.objects.filter(pk=rule.pk).exists())

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
        self.assertNotContains(response, "Refresh All Events")
        self.assertNotContains(response, reverse("calendars_refresh_all"))
        self.assertContains(response, "Add URL")

    def test_saved_urls_can_be_added_opened_edited_and_deleted_inline(self):
        add = self.client.post(
            reverse("saved_link_add"),
            {
                "name": "Team schedule",
                "url": "https://example.com/team-schedule",
            },
        )

        self.assertRedirects(
            add,
            f'{reverse("home")}?calendars=open#calendarsCollapse',
            fetch_redirect_response=False,
        )
        link = SavedLink.objects.get()

        home = self.client.get(reverse("home"), {"calendars": "open"})
        self.assertContains(home, 'class="collapse show" id="calendarsCollapse"')
        self.assertContains(
            home,
            'href="https://example.com/team-schedule" target="_blank" rel="noopener noreferrer"',
            count=2,
        )
        self.assertContains(home, "Team schedule", count=3)
        self.assertContains(home, 'class="footer-saved-links"')
        self.assertNotContains(home, ">https://example.com/team-schedule <")
        self.assertContains(
            home, reverse("saved_link_edit", kwargs={"pk": link.pk})
        )
        self.assertContains(
            home, reverse("saved_link_delete", kwargs={"pk": link.pk})
        )

        edit = self.client.post(
            reverse("saved_link_edit", kwargs={"pk": link.pk}),
            {
                "name": "Updated schedule",
                "url": "https://example.com/updated-schedule",
            },
        )

        self.assertEqual(edit.status_code, 302)
        link.refresh_from_db()
        self.assertEqual(link.name, "Updated schedule")
        self.assertEqual(link.url, "https://example.com/updated-schedule")

        delete = self.client.post(
            reverse("saved_link_delete", kwargs={"pk": link.pk})
        )

        self.assertEqual(delete.status_code, 302)
        self.assertFalse(SavedLink.objects.exists())

    def test_saved_url_without_a_name_displays_its_url_at_the_bottom(self):
        SavedLink.objects.create(url="https://example.com/unnamed")

        response = self.client.get(reverse("home"))

        self.assertContains(
            response,
            ">https://example.com/unnamed<span aria-hidden=\"true\">",
        )
        self.assertContains(response, 'aria-label="Saved links"')

    def test_saved_url_actions_require_post(self):
        link = SavedLink.objects.create(url="https://example.com/schedule")

        self.assertEqual(self.client.get(reverse("saved_link_add")).status_code, 405)
        self.assertEqual(
            self.client.get(
                reverse("saved_link_edit", kwargs={"pk": link.pk})
            ).status_code,
            405,
        )
        self.assertEqual(
            self.client.get(
                reverse("saved_link_delete", kwargs={"pk": link.pk})
            ).status_code,
            405,
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
        self.assertContains(response, "Replace All Events")
        self.assertContains(response, "Classification rules are preserved.")
        self.assertContains(response, "Manage Classification Rules")
        self.assertContains(response, "Manage Event Visibility")

    def test_calendar_edit_reclassifies_existing_events_from_team_names(self):
        calendar = Calendar.objects.create(
            name="League", cal_url="https://example.com/team-matching.ics"
        )
        matching = CalendarEvent.objects.create(
            calendar=calendar,
            external_uid="falcons-game",
            title="Phoenix Falcons vs Bears",
            team1="Phoenix Falcons",
            team2="Bears",
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=1),
            event_type=CalendarEvent.EventType.GAME,
        )
        unrelated = CalendarEvent.objects.create(
            calendar=calendar,
            external_uid="wheatland-game",
            title="Wheatland vs Bears",
            team1="Wheatland",
            team2="Bears",
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=1),
            event_type=CalendarEvent.EventType.GAME,
        )

        response = self.client.post(
            reverse("calendar_edit", kwargs={"pk": calendar.pk}),
            {
                "name": calendar.name,
                "cal_url": calendar.cal_url,
                "website_url": "",
                "team_aliases": "Falcons",
            },
        )

        self.assertRedirects(
            response, reverse("calendar_edit", kwargs={"pk": calendar.pk})
        )
        matching.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertTrue(matching.is_mine)
        self.assertFalse(unrelated.is_mine)

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

    def test_location_colors_use_only_the_cool_palette(self):
        hues = {location_hue(f"Venue {index}") for index in range(100)}

        self.assertTrue(hues)
        self.assertTrue(hues.issubset(set(COOL_LOCATION_HUES)))
        self.assertGreaterEqual(min(hues), 145)
        self.assertLessEqual(max(hues), 235)

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
