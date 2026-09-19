from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Calendar, CalendarEvent, EventTimeOverride
from .services import EventData, ImportResult
from .views import _replace_calendar_events


NOW = datetime(2026, 8, 12, 16, tzinfo=dt_timezone.utc)


@override_settings(
    GEOAPIFY_API_KEY="",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class EventTimeTests(TestCase):
    def setUp(self):
        self.calendar = Calendar.objects.create(
            name="Team", cal_url="https://example.com/team.ics", timezone="America/Phoenix"
        )
        self.event = CalendarEvent.objects.create(
            calendar=self.calendar,
            external_uid="game-1",
            title="Team game",
            starts_at=NOW + timedelta(days=1, hours=1),
            ends_at=NOW + timedelta(days=1, hours=2),
            event_type=CalendarEvent.EventType.GAME,
        )
        self.edit_url = reverse("event_edit_times", kwargs={"pk": self.event.pk})
        self.manual_data = {
            "starts_at": "2026-08-13T10:30:00",
            "ends_at": "2026-08-13T11:45:00",
        }

    def feed_result(self):
        return ImportResult(
            name="Team", timezone="America/Phoenix",
            events=[EventData(
                external_uid="game-1", recurrence_id="", title="Team game",
                description="", starts_at=NOW + timedelta(days=1, hours=3),
                ends_at=NOW + timedelta(days=1, hours=4), is_all_day=False,
                location="", address="", team1="", team2="", event_url="",
                status="confirmed",
            )],
        )

    def test_edit_form_uses_calendar_timezone_and_does_not_change_on_get(self):
        self.event.starts_at = self.event.starts_at.replace(second=37, microsecond=123456)
        self.event.save()
        response = self.client.get(self.edit_url)
        self.assertContains(response, 'type="datetime-local"')
        self.assertContains(response, 'value="2026-08-13T10:00"')
        self.assertContains(response, 'step="60"', count=2)
        self.event.refresh_from_db()
        self.assertEqual(self.event.starts_at.second, 37)
        self.assertContains(response, "America/Phoenix")
        self.assertFalse(EventTimeOverride.objects.exists())
        self.assertNotContains(response, "Use calendar times")

    @patch("pages.views.timezone.now", return_value=NOW)
    def test_games_and_practices_can_be_edited_and_marked_on_both_layouts(self, _now):
        for event_type in (CalendarEvent.EventType.GAME, CalendarEvent.EventType.PRACTICE):
            with self.subTest(event_type=event_type):
                self.event.event_type = event_type
                self.event.save()
                response = self.client.post(self.edit_url, self.manual_data)
                self.assertRedirects(response, self.edit_url)
                self.event.refresh_from_db()
                self.assertEqual(self.event.starts_at.hour, 17)
                self.assertEqual(self.event.starts_at.minute, 30)
                self.assertEqual(self.event.ends_at.hour, 18)
                self.assertEqual(self.event.ends_at.minute, 45)
                self.assertEqual(self.event.source_starts_at, NOW + timedelta(days=1, hours=1))
                self.assertEqual(EventTimeOverride.objects.count(), 1)
                home = self.client.get(reverse("home"), {"view": "all", "scope": "all"})
                self.assertContains(home, self.edit_url, count=2)
                self.assertNotContains(home, "Manual time")
                self.assertContains(home, 'class="manual-time-marker"', count=2)
                self.assertContains(home, 'title="Manually updated time">*</span>', count=2)
                self.assertContains(home, "10:30 AM")
                self.assertContains(
                    home,
                    f'<a class="event-time-link" href="{self.edit_url}" '
                    'title="Edit times" aria-label="Edit times for Team game">10:30 AM</a>',
                    count=2,
                    html=True,
                )
                self.assertNotContains(home, ">Edit times</a>")

    def test_saved_manual_times_always_have_zero_seconds(self):
        response = self.client.post(self.edit_url, {
            "starts_at": "2026-08-13T10:30:47.123456",
            "ends_at": "2026-08-13T11:45:59.654321",
        })
        self.assertRedirects(response, self.edit_url)
        self.event.refresh_from_db()
        override = EventTimeOverride.objects.get()
        for obj in (self.event, override):
            self.assertEqual(obj.starts_at.minute, 30)
            self.assertEqual(obj.ends_at.minute, 45)
            for value in (obj.starts_at, obj.ends_at):
                self.assertEqual(value.second, 0)
                self.assertEqual(value.microsecond, 0)

    @patch("pages.views.timezone.now", return_value=NOW)
    def test_all_day_label_is_an_edit_link_on_both_layouts(self, _now):
        self.event.is_all_day = True
        self.event.save()
        response = self.client.get(reverse("home"), {"view": "all", "scope": "all"})
        self.assertContains(response, 'class="event-time-link"', count=2)
        self.assertContains(response, self.edit_url, count=2)
        self.assertContains(response, "All day", count=2)
        self.assertNotContains(response, ">Edit times</a>")
        self.assertNotContains(response, 'class="manual-time-marker"')

    def test_repeated_edits_keep_original_times_and_reset_restores_them(self):
        original_start, original_end = self.event.starts_at, self.event.ends_at
        self.client.post(self.edit_url, self.manual_data)
        self.client.post(self.edit_url, {
            "starts_at": "2026-08-14T09:00", "ends_at": "2026-08-14T10:00"
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.source_starts_at, original_start)
        self.assertEqual(self.event.source_ends_at, original_end)
        response = self.client.post(reverse("event_reset_times", kwargs={"pk": self.event.pk}))
        self.assertRedirects(response, self.edit_url)
        self.event.refresh_from_db()
        self.assertEqual(self.event.starts_at, original_start)
        self.assertEqual(self.event.ends_at, original_end)
        self.assertIsNone(self.event.source_starts_at)
        self.assertIsNone(self.event.source_ends_at)
        self.assertIsNone(self.event.source_is_all_day)
        self.assertFalse(EventTimeOverride.objects.exists())

    def test_invalid_times_do_not_save(self):
        for data in (
            {"starts_at": "", "ends_at": ""},
            {"starts_at": "not a date", "ends_at": self.manual_data["ends_at"]},
            {"starts_at": "2026-08-13T12:00", "ends_at": "2026-08-13T11:00"},
            {"starts_at": "2026-08-13T12:00", "ends_at": "2026-08-13T12:00"},
            {"starts_at": "2026-08-13T12:00:01", "ends_at": "2026-08-13T12:00:59"},
            {**self.manual_data, "is_all_day": "on"},
        ):
            with self.subTest(data=data):
                response = self.client.post(self.edit_url, data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.event.refresh_from_db()
                self.assertEqual(self.event.starts_at, NOW + timedelta(days=1, hours=1))
                self.assertIsNone(self.event.source_starts_at)
                self.assertFalse(EventTimeOverride.objects.exists())

    def test_dst_ambiguous_and_nonexistent_times_are_rejected(self):
        self.calendar.timezone = "America/New_York"
        self.calendar.save()
        for start, end in (("2026-03-08T02:30", "2026-03-08T04:30"),
                           ("2026-11-01T01:30", "2026-11-01T03:30")):
            response = self.client.post(self.edit_url, {"starts_at": start, "ends_at": end})
            self.assertTrue(response.context["form"].has_error("starts_at"))
        self.assertFalse(EventTimeOverride.objects.exists())

    def test_all_day_can_be_changed_to_timed_and_restored(self):
        self.event.is_all_day = True
        self.event.starts_at = NOW.replace(hour=7) + timedelta(days=1)
        self.event.ends_at = self.event.starts_at + timedelta(days=1)
        self.event.save()
        self.client.post(self.edit_url, self.manual_data)
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_all_day)
        self.assertTrue(self.event.source_is_all_day)
        self.client.post(reverse("event_reset_times", kwargs={"pk": self.event.pk}))
        self.event.refresh_from_db()
        self.assertTrue(self.event.is_all_day)
        self.client.post(self.edit_url, {
            "starts_at": "2026-08-14T00:00", "ends_at": "2026-08-15T00:00",
            "is_all_day": "on",
        })
        self.event.refresh_from_db()
        self.assertTrue(self.event.is_all_day)
        self.assertEqual(self.event.starts_at, datetime(2026, 8, 14, 7, tzinfo=dt_timezone.utc))

    @patch("pages.views.timezone.now", return_value=NOW)
    @patch("pages.views.fetch_and_parse_calendar")
    def test_replacement_preview_and_confirm_preserve_manual_times(self, fetch, _now):
        self.client.post(self.edit_url, self.manual_data)
        self.event.refresh_from_db()
        manual_start = self.event.starts_at
        result = self.feed_result()
        # Even a past feed event is previewed if its manual time is upcoming.
        result.events[0].starts_at = NOW - timedelta(days=2)
        result.events[0].ends_at = result.events[0].starts_at + timedelta(hours=1)
        fetch.return_value = result
        response = self.client.post(reverse("calendar_refresh", kwargs={"pk": self.calendar.pk}))
        self.assertFalse(self.calendar.events.exists())
        self.assertTrue(self.calendar.time_overrides.exists())
        preview = self.client.get(response.url)
        self.assertNotContains(preview, "Manual time")
        self.assertContains(preview, 'class="manual-time-marker"', count=1)
        self.assertContains(preview, "10:30 AM")
        token = response.url.split("/")[-2]
        self.client.post(reverse("calendar_confirm", kwargs={"token": token}))
        event = self.calendar.events.get()
        self.assertEqual(event.starts_at, manual_start)
        self.assertEqual(event.source_starts_at, result.events[0].starts_at)
        self.client.post(reverse("event_reset_times", kwargs={"pk": event.pk}))
        event.refresh_from_db()
        self.assertEqual(event.starts_at, result.events[0].starts_at)

    @patch("pages.views.fetch_and_parse_calendar")
    def test_refresh_all_preserves_manual_times_and_updates_source_times(self, fetch):
        self.client.post(self.edit_url, self.manual_data)
        self.event.refresh_from_db()
        manual_start = self.event.starts_at
        fetch.return_value = self.feed_result()
        self.client.post(reverse("calendars_refresh_all"))
        event = self.calendar.events.get()
        self.assertEqual(event.starts_at, manual_start)
        self.assertEqual(event.source_starts_at, fetch.return_value.events[0].starts_at)
        self.assertEqual(event.source_ends_at, fetch.return_value.events[0].ends_at)

    def test_overrides_are_scoped_to_calendar_and_recurrence_and_survive_missing_event(self):
        self.client.post(self.edit_url, self.manual_data)
        override = EventTimeOverride.objects.get()
        result = self.feed_result()
        result.events[0].recurrence_id = "another-occurrence"
        _replace_calendar_events(self.calendar, result)
        event = self.calendar.events.get()
        self.assertEqual(event.starts_at, result.events[0].starts_at)
        self.assertIsNone(event.source_starts_at)
        self.assertEqual(self.calendar.time_overrides.count(), 1)

        other_calendar = Calendar.objects.create(name="Other")
        result.events[0].recurrence_id = ""
        _replace_calendar_events(other_calendar, result)
        self.assertIsNone(other_calendar.events.get().source_starts_at)
        _replace_calendar_events(self.calendar, result)
        self.assertEqual(self.calendar.events.get().starts_at, override.starts_at)
        self.calendar.delete()
        self.assertFalse(EventTimeOverride.objects.exists())

    @patch("pages.views.timezone.now", return_value=NOW)
    def test_manual_end_time_is_used_for_game_gap(self, _now):
        self.client.post(self.edit_url, self.manual_data)
        CalendarEvent.objects.create(
            calendar=self.calendar, external_uid="game-2", title="Next game",
            starts_at=NOW + timedelta(days=1, hours=3),
            ends_at=NOW + timedelta(days=1, hours=4),
            event_type=CalendarEvent.EventType.GAME,
        )
        response = self.client.get(reverse("home"), {"view": "all", "scope": "all"})
        first = response.context["events"][0]
        self.assertEqual(first.game_gap_minutes, 15)

    @patch("pages.views.timezone.now", return_value=NOW)
    @patch("pages.views.get_route_estimate")
    def test_gap_and_travel_buffer_recalculate_after_each_edit_and_reset(self, route, _now):
        route.return_value = (
            SimpleNamespace(is_available=True, duration_seconds=1200, distance_meters=5000),
            False,
        )
        self.event.location = "First venue"
        self.event.save()
        following = CalendarEvent.objects.create(
            calendar=self.calendar, external_uid="game-2", title="Next game",
            starts_at=NOW + timedelta(days=1, hours=3),
            ends_at=NOW + timedelta(days=1, hours=4),
            event_type=CalendarEvent.EventType.GAME, location="Second venue",
        )

        def assert_gap(minutes, buffer_text, tight=False):
            response = self.client.get(reverse("home"), {"view": "all", "scope": "all"})
            first = response.context["events"][0]
            self.assertEqual(first.pk, self.event.pk)
            self.assertEqual(first.next_game.pk, following.pk)
            self.assertEqual(first.game_gap_minutes, minutes)
            self.assertEqual(first.game_buffer_after, buffer_text)
            self.assertEqual(first.game_travel_tight, tight)
            self.assertContains(response, buffer_text)

        assert_gap(70, "50 min buffer")
        self.client.post(self.edit_url, self.manual_data)
        assert_gap(15, "5 min short", tight=True)
        self.client.post(self.edit_url, {
            **self.manual_data, "ends_at": "2026-08-13T11:15",
        })
        assert_gap(45, "25 min buffer")
        self.client.post(reverse("event_edit_times", kwargs={"pk": following.pk}), {
            "starts_at": "2026-08-13T12:30", "ends_at": "2026-08-13T13:30",
        })
        assert_gap(75, "55 min buffer")
        self.client.post(reverse("event_reset_times", kwargs={"pk": self.event.pk}))
        assert_gap(100, "1 hr 20 min buffer")

    @patch("pages.views.timezone.now", return_value=NOW)
    def test_manual_time_reorders_games_and_recalculates_gap(self, _now):
        following = CalendarEvent.objects.create(
            calendar=self.calendar, external_uid="game-2", title="Next game",
            starts_at=NOW + timedelta(days=1, hours=3),
            ends_at=NOW + timedelta(days=1, hours=4),
            event_type=CalendarEvent.EventType.GAME,
        )
        self.client.post(reverse("event_edit_times", kwargs={"pk": following.pk}), {
            "starts_at": "2026-08-13T09:00", "ends_at": "2026-08-13T09:30",
        })
        response = self.client.get(reverse("home"), {"view": "all", "scope": "all"})
        first, second = response.context["events"]
        self.assertEqual(first.pk, following.pk)
        self.assertEqual(first.next_game.pk, self.event.pk)
        self.assertEqual(first.game_gap_minutes, 30)
        self.assertEqual(second.game_gap_after, "")
        self.assertIsNone(second.next_game)

    @patch("pages.views.timezone.now", return_value=NOW)
    @patch("pages.views.fetch_and_parse_calendar")
    def test_replacement_preview_hides_event_manually_moved_to_past(self, fetch, _now):
        self.client.post(self.edit_url, {
            "starts_at": "2026-08-11T10:00", "ends_at": "2026-08-11T11:00",
        })
        fetch.return_value = self.feed_result()
        response = self.client.post(reverse("calendar_refresh", kwargs={"pk": self.calendar.pk}))
        preview = self.client.get(response.url)
        self.assertNotContains(preview, "Team game")
        self.assertContains(preview, "No current or future events")

    def test_reset_requires_post_and_missing_events_return_404(self):
        reset_url = reverse("event_reset_times", kwargs={"pk": self.event.pk})
        self.assertEqual(self.client.get(reset_url).status_code, 405)
        self.assertEqual(self.client.put(self.edit_url).status_code, 405)
        for name in ("event_edit_times", "event_reset_times"):
            response = self.client.post(reverse(name, kwargs={"pk": 99999}))
            self.assertEqual(response.status_code, 404)
