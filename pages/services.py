import hashlib
import ipaddress
import re
import socket
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import recurring_ical_events
import requests
from django.conf import settings
from django.utils import timezone
from icalendar import Calendar as ICalendar

MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024
MAX_EVENTS = 2000
MAX_REDIRECTS = 3
PAST_WINDOW_DAYS = 30
FUTURE_WINDOW_DAYS = 365


class CalendarImportError(Exception):
    """A safe, user-facing calendar import error."""


@dataclass
class EventData:
    external_uid: str
    recurrence_id: str
    title: str
    description: str
    starts_at: datetime
    ends_at: datetime
    is_all_day: bool
    location: str
    address: str
    team1: str
    team2: str
    event_url: str
    status: str
    event_type: str = "other"
    raw_data: dict = field(default_factory=dict)

    def to_session(self):
        data = asdict(self)
        data["starts_at"] = self.starts_at.isoformat()
        data["ends_at"] = self.ends_at.isoformat()
        return data

    @classmethod
    def from_session(cls, data):
        values = data.copy()
        values["starts_at"] = datetime.fromisoformat(values["starts_at"])
        values["ends_at"] = datetime.fromisoformat(values["ends_at"])
        return cls(**values)


@dataclass
class ImportResult:
    name: str
    timezone: str
    events: list[EventData]
    warnings: list[str] = field(default_factory=list)

    def to_session(self):
        return {
            "name": self.name,
            "timezone": self.timezone,
            "events": [event.to_session() for event in self.events],
            "warnings": self.warnings,
        }

    @classmethod
    def from_session(cls, data):
        return cls(
            name=data["name"],
            timezone=data["timezone"],
            events=[EventData.from_session(event) for event in data["events"]],
            warnings=data.get("warnings", []),
        )


def _validate_remote_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise CalendarImportError("The calendar URL must use HTTP or HTTPS.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise CalendarImportError("The calendar URL is not valid.")

    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    except socket.gaierror as exc:
        raise CalendarImportError("The calendar host could not be found.") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise CalendarImportError(
                "Calendar URLs cannot point to local or private network addresses."
            )


def download_calendar(url):
    """Download a feed with basic SSRF, redirect, timeout, and size protection."""
    current_url = url
    headers = {"User-Agent": "Gamescal/0.1 calendar importer"}

    for redirect_count in range(MAX_REDIRECTS + 1):
        _validate_remote_url(current_url)
        try:
            response = requests.get(
                current_url,
                headers=headers,
                stream=True,
                timeout=(5, 15),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise CalendarImportError("The calendar could not be downloaded.") from exc

        if response.is_redirect or response.is_permanent_redirect:
            if redirect_count == MAX_REDIRECTS:
                response.close()
                raise CalendarImportError("The calendar URL redirected too many times.")
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise CalendarImportError("The calendar returned an invalid redirect.")
            current_url = urljoin(current_url, location)
            continue

        try:
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_DOWNLOAD_BYTES:
                raise CalendarImportError("The calendar feed is too large.")

            chunks = []
            size = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    raise CalendarImportError("The calendar feed is too large.")
                chunks.append(chunk)
            return b"".join(chunks)
        except (requests.RequestException, ValueError) as exc:
            raise CalendarImportError("The calendar could not be downloaded.") from exc
        finally:
            response.close()

    raise CalendarImportError("The calendar could not be downloaded.")


def _calendar_timezone(_calendar):
    # Convert every feed to Arizona time for consistent parsing and display.
    # TZ-aware values retain their instant; floating values are interpreted as Arizona.
    return settings.TIME_ZONE


def _as_aware(value, calendar_tz):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, time.min)
    else:
        raise ValueError("Unsupported calendar date")

    if timezone.is_naive(result):
        result = result.replace(tzinfo=ZoneInfo(calendar_tz))
    return result.astimezone(dt_timezone.utc)


def _decoded(component, key, default=None):
    try:
        return component.decoded(key)
    except (KeyError, AttributeError, ValueError):
        return default


def _text(component, key):
    value = component.get(key)
    return str(value).strip() if value is not None else ""


def _teams_from_title(title):
    patterns = [r"\s+vs\.?\s+", r"\s+v\.?\s+", r"\s+@\s+", r"\s+at\s+"]
    for pattern in patterns:
        teams = re.split(pattern, title, maxsplit=1, flags=re.IGNORECASE)
        if len(teams) == 2:
            return teams[0].strip(), teams[1].strip()
    return "", ""


def _recurrence_id(component, calendar_tz):
    value = _decoded(component, "RECURRENCE-ID")
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return _as_aware(value, calendar_tz).isoformat()
    return str(value)


def _rule_value(event, match_field):
    if match_field == "category":
        return " ".join(event.raw_data.get("categories", []))
    if match_field == "team":
        return f"{event.team1} {event.team2}".strip()
    return str(getattr(event, match_field, "") or "")


def visibility_for_event(event, rules=()):
    """Return visibility and an explanation for active show/hide rules."""
    active_rules = [rule for rule in rules if rule.is_active]
    show_rules = [rule for rule in active_rules if rule.action == "show"]
    matched = [
        rule
        for rule in active_rules
        if rule.pattern.casefold() in _rule_value(event, rule.match_field).casefold()
    ]
    matched_show = [rule for rule in matched if rule.action == "show"]
    matched_hide = [rule for rule in matched if rule.action == "hide"]

    if matched_hide:
        names = ", ".join(rule.name for rule in matched_hide)
        return False, f"Hidden by: {names}", matched
    if show_rules and not matched_show:
        return False, "Hidden because no Show only rule matched.", matched
    if matched_show:
        names = ", ".join(rule.name for rule in matched_show)
        return True, f"Shown by: {names}", matched
    return True, "Shown because there are no active Show only rules.", matched


def classify_event(event, rules=()):
    """Classify an event, applying a calendar's first matching rule before defaults."""
    for rule in rules:
        if not rule.is_active:
            continue
        candidate = _rule_value(event, rule.match_field)
        if rule.pattern.casefold() in candidate.casefold():
            return rule.event_type

    categories = " ".join(event.raw_data.get("categories", []))
    searchable = f"{event.title} {categories}".casefold()
    if re.search(r"\b(practice|training|workout)\b", searchable):
        return "practice"
    if re.search(r"\b(tournament|championship|playoffs?)\b", searchable):
        return "tournament"
    if re.search(r"\b(game|match|scrimmage)\b", searchable) or _teams_from_title(
        event.title
    ) != ("", ""):
        return "game"
    return "other"


def _event_data(component, calendar_tz):
    start_value = _decoded(component, "DTSTART")
    if start_value is None:
        return None

    is_all_day = isinstance(start_value, date) and not isinstance(start_value, datetime)
    starts_at = _as_aware(start_value, calendar_tz)

    end_value = _decoded(component, "DTEND")
    if end_value is not None:
        ends_at = _as_aware(end_value, calendar_tz)
    else:
        duration = _decoded(component, "DURATION")
        if isinstance(duration, timedelta):
            ends_at = starts_at + duration
        else:
            ends_at = starts_at + (timedelta(days=1) if is_all_day else timedelta())

    title = _text(component, "SUMMARY") or "Untitled event"
    location = _text(component, "LOCATION")
    team1, team2 = _teams_from_title(title)
    uid = _text(component, "UID")
    if not uid:
        identity = f"{title}|{starts_at.isoformat()}|{location}"
        uid = hashlib.sha256(identity.encode()).hexdigest()

    categories = component.get("CATEGORIES")
    if categories is None:
        category_values = []
    elif hasattr(categories, "cats"):
        category_values = [str(value) for value in categories.cats]
    else:
        category_values = [str(categories)]

    event = EventData(
        external_uid=uid[:255],
        recurrence_id=_recurrence_id(component, calendar_tz)[:255],
        title=title[:500],
        description=_text(component, "DESCRIPTION"),
        starts_at=starts_at,
        ends_at=max(ends_at, starts_at),
        is_all_day=is_all_day,
        location=location[:500],
        address=location[:500],
        team1=team1[:255],
        team2=team2[:255],
        event_url=_text(component, "URL")[:2000],
        status=(_text(component, "STATUS") or "CONFIRMED").lower()[:20],
        raw_data={
            "categories": category_values,
            "organizer": _text(component, "ORGANIZER"),
        },
    )
    event.event_type = classify_event(event)
    return event


def parse_calendar(content, source_url=""):
    try:
        calendar = ICalendar.from_ical(content)
    except Exception as exc:
        raise CalendarImportError(
            "This feed is not a valid iCalendar (ICS) calendar."
        ) from exc

    calendar_tz = _calendar_timezone(calendar)
    # recurring_ical_events uses X-WR-TIMEZONE for floating values, so override
    # feed metadata to ensure those values are interpreted as Arizona wall time.
    calendar["X-WR-TIMEZONE"] = calendar_tz
    start = timezone.now() - timedelta(days=PAST_WINDOW_DAYS)
    end = timezone.now() + timedelta(days=FUTURE_WINDOW_DAYS)

    try:
        components = recurring_ical_events.of(
            calendar, skip_bad_series=True
        ).between(start, end)
    except Exception as exc:
        raise CalendarImportError("The calendar's events could not be parsed.") from exc

    events = []
    seen = set()
    warnings = []
    for component in components:
        event = _event_data(component, calendar_tz)
        if event is None:
            warnings.append("An event without a start date was skipped.")
            continue

        key = (event.external_uid, event.recurrence_id)
        if key in seen:
            # Some feeds reuse a UID without identifying recurrence instances.
            event.recurrence_id = event.starts_at.isoformat()
            key = (event.external_uid, event.recurrence_id)
        if key in seen:
            continue
        seen.add(key)
        events.append(event)
        if len(events) == MAX_EVENTS:
            warnings.append(f"Only the first {MAX_EVENTS} events were imported.")
            break

    events.sort(key=lambda event: (event.starts_at, event.title))
    parsed_host = urlparse(source_url).hostname or "Imported calendar"
    name = str(calendar.get("X-WR-CALNAME", "")).strip() or parsed_host
    return ImportResult(
        name=name[:255], timezone=calendar_tz, events=events, warnings=warnings
    )


def fetch_and_parse_calendar(url):
    return parse_calendar(download_calendar(url), source_url=url)
