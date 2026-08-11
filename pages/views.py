import re
import secrets
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from .forms import CalendarEditForm, CalendarEventRuleForm, CalendarImportForm
from .models import Calendar, CalendarEvent, CalendarEventRule, GeoapifyAPILog
from .services import (
    CalendarImportError,
    ImportResult,
    classify_event,
    fetch_and_parse_calendar,
)
from .travel import get_route_estimate

PREVIEW_SESSION_KEY = "calendar_import_preview"
GAME_DURATION = timedelta(minutes=50)
MAX_NEW_ROUTE_ESTIMATES_PER_PAGE = 5
GAME_EVENT_TYPES = {
    CalendarEvent.EventType.GAME,
    CalendarEvent.EventType.TOURNAMENT,
}
DEMO_CALENDAR_URL = "https://gamescal.local/travel-demo.ics"


def _debug_tools_requested(request):
    return request.GET.get("debug") == "1"


def _debug_redirect(request, view_name):
    url = reverse(view_name)
    if _debug_tools_requested(request):
        url = f"{url}?debug=1"
    return redirect(url)


def _format_game_gap(total_minutes):
    days, remainder = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hr")
    if minutes and not days:
        parts.append(f"{minutes} min")
    return " ".join(parts) or "<1 min"


def _normalized_location(value):
    return " ".join(str(value or "").casefold().split())


def _same_game_day(first, second):
    local_timezone = ZoneInfo(settings.TIME_ZONE)
    first_date = timezone.localtime(first.starts_at, local_timezone).date()
    second_date = timezone.localtime(second.starts_at, local_timezone).date()
    return first_date == second_date


def _annotate_game_directions(events):
    """Route each game from the previous game's location when it changed."""
    previous_game = None
    for event in events:
        event.directions_origin = ""
        if event.event_type not in GAME_EVENT_TYPES:
            continue
        if event.status == CalendarEvent.Status.CANCELLED:
            continue

        if previous_game is not None and _same_game_day(previous_game, event):
            previous_destination = previous_game.address or previous_game.location
            current_destination = event.address or event.location
            previous_key = _normalized_location(
                previous_game.location or previous_destination
            )
            current_key = _normalized_location(event.location or current_destination)
            if (
                previous_destination
                and current_destination
                and previous_key != current_key
            ):
                event.directions_origin = previous_destination
        previous_game = event
    return events


def _annotate_game_gaps(events):
    """Add a display-only gap to adjacent, non-cancelled game rows."""
    for event in events:
        event.game_gap_after = ""
        event.game_gap_minutes = 0
        event.next_game = None
        event.game_drive_after = ""
        event.game_drive_distance_after = ""
        event.game_buffer_after = ""
        event.game_travel_tight = False

    for current, following in zip(events, events[1:]):
        if current.event_type not in GAME_EVENT_TYPES:
            continue
        if following.event_type not in GAME_EVENT_TYPES:
            continue
        if current.status == CalendarEvent.Status.CANCELLED:
            continue
        if following.status == CalendarEvent.Status.CANCELLED:
            continue
        if not _same_game_day(current, following):
            continue

        gap = following.starts_at - (current.starts_at + GAME_DURATION)
        if gap.total_seconds() <= 0:
            continue
        total_minutes = max(1, int((gap.total_seconds() + 30) // 60))
        current.game_gap_after = _format_game_gap(total_minutes)
        current.game_gap_minutes = total_minutes
        current.next_game = following

    return events


def _annotate_travel_times(events):
    new_estimates = 0
    for event in events:
        following = event.next_game
        if not following:
            continue
        origin = event.address or event.location
        destination = following.address or following.location
        if not origin or not destination:
            continue
        if _normalized_location(event.location or origin) == _normalized_location(
            following.location or destination
        ):
            continue

        estimate, fetched = get_route_estimate(
            origin,
            destination,
            allow_fetch=new_estimates < MAX_NEW_ROUTE_ESTIMATES_PER_PAGE,
        )
        if fetched:
            new_estimates += 1
        if not estimate or not estimate.is_available:
            continue

        drive_minutes = max(1, (estimate.duration_seconds + 59) // 60)
        event.game_drive_after = _format_game_gap(drive_minutes)
        distance_miles = estimate.distance_meters / 1609.344
        rounded_distance = round(distance_miles, 1)
        event.game_drive_distance_after = (
            f"{rounded_distance:.1f} mi"
            if rounded_distance < 10
            else f"{distance_miles:.0f} mi"
        )
        buffer_minutes = event.game_gap_minutes - drive_minutes
        if buffer_minutes > 0:
            event.game_buffer_after = f"{_format_game_gap(buffer_minutes)} buffer"
        elif buffer_minutes < 0:
            event.game_buffer_after = f"{_format_game_gap(-buffer_minutes)} short"
            event.game_travel_tight = True
        else:
            event.game_buffer_after = "no buffer"
    return events


class HomePageView(TemplateView):
    template_name = "pages/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event_view = self.request.GET.get("view", "all")
        if event_view not in {"games", "practices", "all"}:
            event_view = "all"

        now = timezone.now()
        local_timezone = ZoneInfo(settings.TIME_ZONE)
        local_today = timezone.localtime(now, local_timezone).date()
        week_start_date = local_today - timedelta(days=local_today.weekday())
        week_start = datetime.combine(
            week_start_date, time.min, tzinfo=local_timezone
        )
        week_end = week_start + timedelta(days=7)
        events = CalendarEvent.objects.select_related("calendar").filter(
            calendar__is_active=True,
            ends_at__gte=now,
            starts_at__gte=week_start,
            starts_at__lt=week_end,
        )
        if event_view == "games":
            events = events.filter(
                event_type__in=[
                    CalendarEvent.EventType.GAME,
                    CalendarEvent.EventType.TOURNAMENT,
                ]
            )
        elif event_view == "practices":
            events = events.filter(event_type=CalendarEvent.EventType.PRACTICE)

        events = list(events.order_by("starts_at", "title")[:2000])
        events = _annotate_game_gaps(events[:500])
        _annotate_game_directions(events)
        _annotate_travel_times(events)
        context["calendars"] = Calendar.objects.annotate(event_count=Count("events"))
        context["events"] = events
        context["event_view"] = event_view
        request_debug = _debug_tools_requested(self.request)
        context["request_debug"] = request_debug
        context["show_demo_tools"] = settings.ENABLE_DEMO_TOOLS or request_debug
        context["show_api_logs"] = settings.ENABLE_API_LOG_VIEW or request_debug
        return context


class AboutPageView(TemplateView):
    template_name = "pages/about.html"


def geoapify_api_logs(request):
    if not (settings.ENABLE_API_LOG_VIEW or _debug_tools_requested(request)):
        raise Http404

    logs = GeoapifyAPILog.objects.all()
    request_type = request.GET.get("type", "")
    result = request.GET.get("result", "")
    if request_type in {
        GeoapifyAPILog.RequestType.GEOCODING,
        GeoapifyAPILog.RequestType.ROUTING,
    }:
        logs = logs.filter(request_type=request_type)
    else:
        request_type = ""
    if result == "success":
        logs = logs.filter(success=True)
    elif result == "error":
        logs = logs.filter(success=False)
    else:
        result = ""

    all_logs = GeoapifyAPILog.objects.all()
    stats = all_logs.aggregate(
        total=Count("id"),
        successful=Count("id", filter=Q(success=True)),
        failed=Count("id", filter=Q(success=False)),
        geocoding=Count(
            "id",
            filter=Q(request_type=GeoapifyAPILog.RequestType.GEOCODING),
        ),
        routing=Count(
            "id", filter=Q(request_type=GeoapifyAPILog.RequestType.ROUTING)
        ),
        average_duration_ms=Avg("duration_ms"),
        response_bytes=Sum("response_size_bytes"),
    )
    stats["today"] = all_logs.filter(created_at__date=timezone.localdate()).count()
    stats["success_rate"] = (
        round(stats["successful"] / stats["total"] * 100, 1)
        if stats["total"]
        else 0
    )
    page = Paginator(logs, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "pages/geoapify_logs.html",
        {
            "page": page,
            "stats": stats,
            "request_type": request_type,
            "result": result,
            "request_debug": _debug_tools_requested(request),
        },
    )


@require_POST
def clear_geoapify_api_logs(request):
    if not (settings.ENABLE_API_LOG_VIEW or _debug_tools_requested(request)):
        raise Http404
    deleted, _details = GeoapifyAPILog.objects.all().delete()
    messages.success(request, f"Cleared {deleted} Geoapify API log entries.")
    return _debug_redirect(request, "geoapify_api_logs")


def _demo_location_key(value):
    normalized = str(value).casefold()
    normalized = re.sub(r"\b(united states|usa)\b", "", normalized)
    replacements = {
        "road": "rd",
        "street": "st",
        "avenue": "ave",
        "boulevard": "blvd",
        "east": "e",
        "west": "w",
        "north": "n",
        "south": "s",
    }
    for original, replacement in replacements.items():
        normalized = re.sub(rf"\b{original}\b", replacement, normalized)
    return re.sub(r"[^a-z0-9]+", "", normalized)


@require_POST
def populate_demo_calendar(request):
    if not (settings.ENABLE_DEMO_TOOLS or _debug_tools_requested(request)):
        raise Http404

    unique_locations = {}
    source_events = CalendarEvent.objects.exclude(calendar__cal_url=DEMO_CALENDAR_URL)
    for address, location in source_events.exclude(location="").values_list(
        "address", "location"
    ):
        label = address or location
        unique_locations.setdefault(_demo_location_key(label), label)

    locations = sorted(unique_locations.values())
    if len(locations) < 3:
        messages.error(
            request,
            "Add calendars containing at least three distinct locations first.",
        )
        return _debug_redirect(request, "home")

    venue_a = locations[0]
    venue_b = locations[-1]
    venue_c = locations[1]
    venue_d = locations[-2] if len(locations) > 3 else locations[0]

    local_now = timezone.localtime(timezone.now(), ZoneInfo(settings.TIME_ZONE))
    days_until_saturday = (5 - local_now.weekday()) % 7
    if days_until_saturday == 0 and local_now.time() >= time(hour=8):
        days_until_saturday = 7
    demo_date = local_now.date() + timedelta(days=days_until_saturday)
    first_start = datetime.combine(
        demo_date, time(hour=8), tzinfo=ZoneInfo(settings.TIME_ZONE)
    )

    scenarios = (
        ("Demo · Back-to-back opener", 0, venue_a, "Starts the demo schedule."),
        ("Demo · Back-to-back follow-up", 50, venue_a, "No gap indicator."),
        (
            "Demo · Tight travel game",
            110,
            venue_b,
            "Only ten minutes are available to reach a distant venue.",
        ),
        (
            "Demo · Reachable travel game",
            270,
            venue_c,
            "A long travel window should leave a positive buffer.",
        ),
        ("Demo · Same-venue follow-up", 320, venue_c, "Back-to-back, same venue."),
        (
            "Demo · Overlapping travel game",
            350,
            venue_d,
            "Starts before the previous fifty-minute game finishes.",
        ),
        (
            "Demo · Comfortable same-venue gap",
            480,
            venue_d,
            "A later game at the same venue with no drive required.",
        ),
    )

    with transaction.atomic():
        calendar, _created = Calendar.objects.update_or_create(
            cal_url=DEMO_CALENDAR_URL,
            defaults={
                "name": "Travel Time Demo",
                "website_url": "",
                "is_active": True,
                "timezone": settings.TIME_ZONE,
                "last_synced_at": timezone.now(),
                "last_sync_error": "",
            },
        )
        calendar.events.all().delete()
        CalendarEvent.objects.bulk_create(
            [
                CalendarEvent(
                    calendar=calendar,
                    external_uid=f"gamescal-travel-demo-{index}",
                    title=title,
                    description=description,
                    starts_at=first_start + timedelta(minutes=offset),
                    ends_at=first_start + timedelta(minutes=offset + 50),
                    location=location,
                    address=location,
                    event_type=CalendarEvent.EventType.GAME,
                )
                for index, (title, offset, location, description) in enumerate(
                    scenarios, start=1
                )
            ]
        )

    messages.success(
        request,
        "Populated the Travel Time Demo calendar with seven scheduling scenarios.",
    )
    return _debug_redirect(request, "home")


def add_calendar(request):
    if request.method == "POST":
        form = CalendarImportForm(request.POST)
        if form.is_valid():
            try:
                result = fetch_and_parse_calendar(form.cleaned_data["cal_url"])
            except CalendarImportError as exc:
                form.add_error("cal_url", str(exc))
            else:
                token = secrets.token_urlsafe(24)
                request.session[PREVIEW_SESSION_KEY] = {
                    "token": token,
                    "name": form.cleaned_data["name"],
                    "cal_url": form.cleaned_data["cal_url"],
                    "website_url": form.cleaned_data["website_url"],
                    "result": result.to_session(),
                }
                return redirect("calendar_preview", token=token)
    else:
        form = CalendarImportForm()

    return render(request, "pages/calendar_form.html", {"form": form})


def _preview_from_session(request, token):
    preview = request.session.get(PREVIEW_SESSION_KEY)
    if not preview or not secrets.compare_digest(preview.get("token", ""), token):
        raise Http404("This calendar preview has expired.")
    return preview, ImportResult.from_session(preview["result"])


def calendar_preview(request, token):
    preview, result = _preview_from_session(request, token)
    _annotate_game_gaps(result.events)
    _annotate_game_directions(result.events)
    _annotate_travel_times(result.events)
    return render(
        request,
        "pages/calendar_preview.html",
        {
            "token": token,
            "calendar_name": preview["name"] or result.name,
            "cal_url": preview["cal_url"],
            "website_url": preview["website_url"],
            "result": result,
        },
    )


def _event_model(calendar, event, rules=()):
    valid_statuses = {value for value, _label in CalendarEvent.Status.choices}
    status = event.status if event.status in valid_statuses else CalendarEvent.Status.CONFIRMED
    return CalendarEvent(
        calendar=calendar,
        external_uid=event.external_uid,
        recurrence_id=event.recurrence_id,
        title=event.title,
        description=event.description,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        is_all_day=event.is_all_day,
        location=event.location,
        address=event.address,
        team1=event.team1,
        team2=event.team2,
        event_url=event.event_url,
        status=status,
        event_type=classify_event(event, rules),
        raw_data=event.raw_data,
    )


@require_POST
def confirm_calendar(request, token):
    preview, result = _preview_from_session(request, token)
    try:
        with transaction.atomic():
            calendar = Calendar.objects.create(
                name=preview["name"] or result.name,
                cal_url=preview["cal_url"],
                website_url=preview["website_url"],
                timezone=result.timezone,
                last_synced_at=timezone.now(),
            )
            CalendarEvent.objects.bulk_create(
                [_event_model(calendar, event) for event in result.events]
            )
    except IntegrityError:
        messages.error(request, "That calendar has already been added.")
        return redirect("calendar_add")

    request.session.pop(PREVIEW_SESSION_KEY, None)
    messages.success(
        request,
        f'Added “{calendar.name}” with {len(result.events)} events.',
    )
    return redirect("home")


def calendar_edit(request, pk):
    calendar = get_object_or_404(Calendar, pk=pk)
    if request.method == "POST":
        form = CalendarEditForm(request.POST, instance=calendar)
        if form.is_valid():
            form.save()
            messages.success(request, f'Updated “{calendar.name}”.')
            return redirect("calendar_edit", pk=calendar.pk)
    else:
        form = CalendarEditForm(instance=calendar)

    event_counts = calendar.events.aggregate(
        total=Count("id"),
        upcoming=Count("id", filter=Q(ends_at__gte=timezone.now())),
        games=Count("id", filter=Q(event_type=CalendarEvent.EventType.GAME)),
        practices=Count(
            "id", filter=Q(event_type=CalendarEvent.EventType.PRACTICE)
        ),
        tournaments=Count(
            "id", filter=Q(event_type=CalendarEvent.EventType.TOURNAMENT)
        ),
        other=Count("id", filter=Q(event_type=CalendarEvent.EventType.OTHER)),
    )
    return render(
        request,
        "pages/calendar_edit.html",
        {
            "calendar": calendar,
            "form": form,
            "event_counts": event_counts,
            "rule_count": calendar.event_rules.count(),
        },
    )


def _redirect_after_calendar_action(request, calendar):
    if request.POST.get("next") == "edit":
        return redirect("calendar_edit", pk=calendar.pk)
    return redirect("home")


@require_POST
def refresh_calendar(request, pk):
    calendar = get_object_or_404(Calendar, pk=pk)
    try:
        result = fetch_and_parse_calendar(calendar.cal_url)
    except CalendarImportError as exc:
        calendar.last_sync_error = str(exc)
        calendar.save(update_fields=["last_sync_error", "updated_at"])
        messages.error(request, f'Could not refresh “{calendar.name}”: {exc}')
    else:
        with transaction.atomic():
            rules = list(calendar.event_rules.filter(is_active=True))
            calendar.events.all().delete()
            CalendarEvent.objects.bulk_create(
                [_event_model(calendar, event, rules) for event in result.events]
            )
            calendar.timezone = result.timezone
            calendar.last_synced_at = timezone.now()
            calendar.last_sync_error = ""
            calendar.save(
                update_fields=[
                    "timezone",
                    "last_synced_at",
                    "last_sync_error",
                    "updated_at",
                ]
            )
        messages.success(
            request,
            f'Refreshed “{calendar.name}” with {len(result.events)} events.',
        )
    return _redirect_after_calendar_action(request, calendar)


@require_POST
def toggle_calendar(request, pk):
    calendar = get_object_or_404(Calendar, pk=pk)
    calendar.is_active = not calendar.is_active
    calendar.save(update_fields=["is_active", "updated_at"])
    state = "activated" if calendar.is_active else "deactivated"
    messages.success(request, f'“{calendar.name}” was {state}.')
    return _redirect_after_calendar_action(request, calendar)


@require_POST
def delete_calendar(request, pk):
    calendar = get_object_or_404(Calendar, pk=pk)
    name = calendar.name
    event_count = calendar.events.count()
    calendar.delete()
    messages.success(
        request,
        f'Deleted “{name}” and {event_count} associated event(s).',
    )
    return redirect("home")


def _reclassify_calendar(calendar):
    rules = list(calendar.event_rules.filter(is_active=True))
    changed = []
    for event in calendar.events.all():
        event_type = classify_event(event, rules)
        if event.event_type != event_type:
            event.event_type = event_type
            event.updated_at = timezone.now()
            changed.append(event)
    if changed:
        CalendarEvent.objects.bulk_update(changed, ["event_type", "updated_at"])
    return len(changed)


def _rules_context(calendar, form, editing_rule=None):
    return {
        "calendar": calendar,
        "rules": calendar.event_rules.all(),
        "form": form,
        "editing_rule": editing_rule,
    }


def calendar_rules(request, pk):
    calendar = get_object_or_404(Calendar, pk=pk)
    if request.method == "POST":
        form = CalendarEventRuleForm(request.POST)
        if form.is_valid():
            rule = form.save(commit=False)
            rule.calendar = calendar
            rule.save()
            changed = _reclassify_calendar(calendar)
            messages.success(
                request,
                f'Added rule “{rule.name}” and reclassified {changed} event(s).',
            )
            return redirect("calendar_rules", pk=calendar.pk)
    else:
        form = CalendarEventRuleForm()
    return render(
        request,
        "pages/calendar_rules.html",
        _rules_context(calendar, form),
    )


def edit_calendar_rule(request, pk, rule_pk):
    calendar = get_object_or_404(Calendar, pk=pk)
    rule = get_object_or_404(CalendarEventRule, pk=rule_pk, calendar=calendar)
    if request.method == "POST":
        form = CalendarEventRuleForm(request.POST, instance=rule)
        if form.is_valid():
            form.save()
            changed = _reclassify_calendar(calendar)
            messages.success(
                request,
                f'Updated rule “{rule.name}” and reclassified {changed} event(s).',
            )
            return redirect("calendar_rules", pk=calendar.pk)
    else:
        form = CalendarEventRuleForm(instance=rule)
    return render(
        request,
        "pages/calendar_rules.html",
        _rules_context(calendar, form, editing_rule=rule),
    )


@require_POST
def toggle_calendar_rule(request, pk, rule_pk):
    calendar = get_object_or_404(Calendar, pk=pk)
    rule = get_object_or_404(CalendarEventRule, pk=rule_pk, calendar=calendar)
    rule.is_active = not rule.is_active
    rule.save(update_fields=["is_active", "updated_at"])
    changed = _reclassify_calendar(calendar)
    state = "enabled" if rule.is_active else "disabled"
    messages.success(
        request,
        f'Rule “{rule.name}” was {state}; {changed} event(s) reclassified.',
    )
    return redirect("calendar_rules", pk=calendar.pk)


@require_POST
def delete_calendar_rule(request, pk, rule_pk):
    calendar = get_object_or_404(Calendar, pk=pk)
    rule = get_object_or_404(CalendarEventRule, pk=rule_pk, calendar=calendar)
    name = rule.name
    rule.delete()
    changed = _reclassify_calendar(calendar)
    messages.success(
        request, f'Deleted rule “{name}” and reclassified {changed} event(s).'
    )
    return redirect("calendar_rules", pk=calendar.pk)
