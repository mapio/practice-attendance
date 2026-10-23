"""The iCal calendar (docs/contract.md, *Import*): fetched, expanded within the
season, and reduced to the practices it holds, by key."""

import urllib.request
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

import icalendar
import recurring_ical_events

TIMEOUT = 10  # seconds
MAX_BYTES = 5_000_000


class CalendarUnavailable(Exception):
    pass


def fetch(url, base="."):
    """The calendar's bytes: an http(s) address, or a file path (relative to `base`) or `file://` URL
    (for tests and trials); anything else, slow or too big, is unavailable."""
    try:
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https"):
            with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
                data = response.read(MAX_BYTES + 1)
        elif parsed.scheme in ("", "file"):
            path = unquote(parsed.path) if parsed.scheme else url
            with (Path(base) / path).open("rb") as handle:
                data = handle.read(MAX_BYTES + 1)
        else:
            raise CalendarUnavailable("unsupported address")
    except (
        OSError,
        ValueError,
    ) as error:  # URLError, timeouts and HTTPError are OSErrors
        raise CalendarUnavailable(type(error).__name__) from None
    if len(data) > MAX_BYTES:
        raise CalendarUnavailable("calendar too big")
    return data


def _instant(value, tz):
    """A UTC datetime from a DTSTART/DTEND value, or None for a whole day."""
    if not isinstance(value, datetime):
        return None
    return (value if value.tzinfo else value.replace(tzinfo=tz)).astimezone(UTC)


def practices(data, team):
    """The calendar's practices within the season: `{key: (start, end,
    location)}`, in UTC. Raises `CalendarUnavailable` if it cannot be parsed."""
    tz = team.tz
    try:
        calendar = icalendar.Calendar.from_ical(data)
        recurring = {
            str(e.get("UID"))
            for e in calendar.walk("VEVENT")
            if "RRULE" in e or "RDATE" in e or "RECURRENCE-ID" in e
        }
        first = datetime.combine(team.season_from, time.min, tz)
        last = datetime.combine(team.season_to + timedelta(days=1), time.min, tz)
        found = {}
        for event in recurring_ical_events.of(calendar).between(first, last):
            title = str(event.get("SUMMARY", "")).strip().lower()
            if not title.startswith(team.calendar.title.strip().lower()):
                continue
            if str(event.get("STATUS", "")).upper() == "CANCELLED":
                continue
            start = _instant(event["DTSTART"].dt, tz)
            if start is None or not (
                team.season_from <= start.astimezone(tz).date() <= team.season_to
            ):
                continue
            end = (
                _instant(event.end, tz)
                if "DTEND" in event or "DURATION" in event
                else None
            )
            end = end if end and end > start else start + timedelta(hours=1)
            key = str(event.get("UID"))
            if key in recurring:  # a moved occurrence keeps its original start
                original = _instant(event["RECURRENCE-ID"].dt, tz)
                key += "#" + original.strftime("%Y%m%dT%H%M%SZ")
            found[key] = (start, end, str(event.get("LOCATION", "")))
    except Exception as error:  # noqa: BLE001 - the library's parse errors are many and unrelated
        raise CalendarUnavailable(type(error).__name__) from None
    return found
