import os
import logging
import html
import uuid
from datetime import date, datetime, timedelta, timezone
from datetime_parser import parse_datetime, parse_datetime_for_ical, ParsedDateTime
from recurrence import expand_recurring
from helpers import event_to_dict, input_is_all_day, vevent_to_dict, build_event_feedback, all_day_end_date, set_all_day_dates
from caldav import DAVClient
from caldav.lib import error as _caldav_error
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("calendar-mcp")
# ── Config ──────────────────────────────────────────────────────────────────────

CALDAV_URL = os.environ.get("CALDAV_URL", "https://caldav.icloud.com/")
CALDAV_USER = os.environ.get("CALDAV_USER", "")
CALDAV_PASSWORD = os.environ.get("CALDAV_PASSWORD", "")
CALENDAR_NAME = os.environ.get("CALENDAR_NAME", "")
# Test mode: when enabled, all reads/writes are routed to the test calendar
# instead of the real one. Defaults ON so the MCP is never called against the
# real calendar by accident; set TEST_CAL=false to disable.
TEST_CAL = os.environ.get("TEST_CAL", "true").lower() in ("1", "true", "yes", "on")
TEST_CAL_NAME = os.environ.get("TEST_CAL_NAME", "Test Calendar")

# ── FastMCP server ──────────────────────────────────────────────────────────────

mcp = FastMCP("icloud-calendar", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

# ── Helpers ─────────────────────────────────────────────────────────────────────


def get_client() -> DAVClient:
    """Create and return an authenticated CalDAV client."""
    client = DAVClient(
        url=CALDAV_URL,
        username=CALDAV_USER,
        password=CALDAV_PASSWORD,
    )
    return client


def get_calendar(client: DAVClient):
    """Return the target calendar object.

    In test mode (TEST_CAL enabled), route to the test calendar so no real
    calendar is touched. Otherwise, if CALENDAR_NAME is set, find by name;
    else return the first calendar.
    """
    principal = client.principal()
    if not principal:
        raise RuntimeError("No principal found — check CalDAV credentials")
    calendars = principal.calendars()
    if not calendars:
        raise RuntimeError("No calendars found — check credentials and CalDAV access")

    if TEST_CAL:
        return _find_calendar_by_name(calendars, TEST_CAL_NAME)

    if CALENDAR_NAME:
        return _find_calendar_by_name(calendars, CALENDAR_NAME)

    return calendars[0]


def _find_calendar_by_name(calendars, name: str):
    """Find a calendar by display name, with a truncated-name fallback."""
    for cal in calendars:
        if html.unescape(cal.get_display_name() or "") == name:
            return cal
    # Fallback: truncated name match
    for cal in calendars:
        if name.startswith(html.unescape(cal.get_display_name() or "")):
            return cal
    raise RuntimeError(
        f"Calendar '{name}' not found. "
        f"Available: {[c.get_display_name() or '<unnamed>' for c in calendars]}"
    )


def fetch_event_by_uid(client, cal, uid: str):
    """Fetch one event by UID from a single calendar, or None.

    iCloud rejects a UID-only calendar-query REPORT with 412 Precondition
    Failed, so this issues a time-range REPORT (which iCloud accepts) and
    filters by UID in memory. The window is wide (±365 days) to cover any
    event a user references; matching is exact, so the window only bounds
    the payload, not correctness.
    """
    now = datetime.now(timezone.utc)
    start_dt = now - timedelta(days=365)
    end_dt = now + timedelta(days=365)
    for event in client.get_events(cal, start=start_dt, end=end_dt):
        if str(getattr(event, "id", "") or "") == uid:
            return event
    return None

# caldav raises typed 412 exceptions when a stale If-Match / If-Schedule-Tag-Match
# is sent: ETagMismatchError and ScheduleTagMismatchError. The UID-conflict 412s
# (UidConflictETagMismatchError, UidConflictScheduleTagMismatchError) inherit from
# them, so catching these two covers every 412 save. Older caldav may not define
# them; _is_412 then falls back to a status-code check.
_412_TYPES = tuple(
    t
    for t in (
        getattr(_caldav_error, "ETagMismatchError", None),
        getattr(_caldav_error, "ScheduleTagMismatchError", None),
    )
    if t is not None
)


def _is_412(exc: Exception) -> bool:
    """True if exc is a CalDAV 412 Precondition Failed.

    Primary: the typed 412 exceptions caldav raises for a stale If-Match /
    If-Schedule-Tag-Match. Fallback: the 412 status code in the error text,
    for caldav versions that surface a 412 as a bare PutError/ReportError.
    """
    if _412_TYPES and isinstance(exc, _412_TYPES):
        return True
    msg = str(exc)
    return "412" in msg or "Precondition" in msg


# ── MCP Tools ───────────────────────────────────────────────────────────────────


@mcp.tool()
def create_event(
    title: str,
    start: str,
    end: str | None = None,
    description: str = "",
    location: str = "",
    attendees: list[str] | None = None,
    all_day: bool = False,
    tz: str = "America/Los_Angeles",
) -> str:
    """Create a new calendar event.

    Args:
        title: Event title/summary.
        start: Start in ISO 8601. All-day: date only (e.g. 2025-08-01).
            Timed: a datetime with or without timezone (e.g. 2025-08-01T10:00:00
            or 2025-08-01T10:00:00-07:00). A datetime without a timezone is
            interpreted in the `tz` timezone, not UTC.
        end: End, same format as start. For all-day events this is the LAST
            day of the event (inclusive); the server stores the exclusive iCal
            end automatically. Omit `end` for a one-day all-day event. For
            timed events, omitting `end` defaults to start + 1 hour.
        description: Optional event description.
        location: Optional event location.
        attendees: Optional list of email addresses for attendees.
        all_day: If True, treat start/end as date-only (all-day event). If
            False, use heuristic detection (date-only strings → all-day).
        tz: IANA timezone used to interpret naive datetimes and to display
            times in the returned confirmation (e.g. "America/Los_Angeles",
            "America/New_York", "UTC"). Defaults to "America/Los_Angeles".

    Returns:
        A confirmation string: the stored day/time in `tz` plus the event UID.
        Use the UID to update or delete the event. If the day or time is wrong,
        call update_event with the UID instead of creating a new event.
    """
    from icalendar import Event
    from zoneinfo import ZoneInfo

    client = get_client()
    cal = get_calendar(client)

    parsed_start = datetime.fromisoformat(start)
    is_all_day = input_is_all_day(start, all_day)

    event = Event()
    event.add("uid", uuid.uuid4())
    event.add("dtstamp", datetime.now(timezone.utc))

    if is_all_day:
        # All-day: DATE type with an exclusive DTEND. `end` is the last
        # inclusive day. The VALUE=DATE parameter is required (iCloud shifts a
        # bare date back a day); set_all_day_dates handles that and the
        # exclusive end.
        set_all_day_dates(event, start, end)
        event.add("categories", "ALL-DAY-EVENT")
    else:
        # Timed: DATETIME, stored in UTC. Naive input is local time in `tz`.
        target_tz = ZoneInfo(tz)
        if parsed_start.tzinfo is None:
            parsed_start = parsed_start.replace(tzinfo=target_tz)
        if end:
            parsed_end = datetime.fromisoformat(end)
            if parsed_end.tzinfo is None:
                parsed_end = parsed_end.replace(tzinfo=target_tz)
        else:
            parsed_end = parsed_start + timedelta(hours=1)
        if parsed_end <= parsed_start:
            raise ValueError(f"end ({end}) must be after start ({start})")
        event.add("dtstart", parsed_start.astimezone(timezone.utc))
        event.add("dtend", parsed_end.astimezone(timezone.utc))

    event.add("summary", title)
    if description:
        event.add("description", description)
    if location:
        event.add("location", location)
    if attendees:
        for email in attendees:
            event.add("attendee", f"mailto:{email}")

    cal.add_event(event)
    uid = str(event.uid)
    logger.info(f"Created event {uid}: {title}")
    return build_event_feedback(vevent_to_dict(event, tz), tz)


@mcp.tool()
def list_events(start_date: str, end_date: str, tz: str = "America/Los_Angeles") -> list[dict]:
    """List events within a date range.

    Args:
        start_date: Start date (ISO 8601, e.g. 2025-08-01).
        end_date: End date (ISO 8601, e.g. 2025-08-31).
        tz: IANA timezone name to return times in (e.g. "America/Los_Angeles", "America/New_York", "UTC"). Defaults to "America/Los_Angeles".

    Returns:
        List of event dicts with uid, title, start, end, all_day, day_of_week,
        day_of_week_end, timezone, description, location. Timed events use ISO
        8601 in the specified timezone; all-day events use date-only start/end
        (end is the last inclusive day).
    """
    client = get_client()
    cal = get_calendar(client)

    # Parse dates using universal parser — half-open interval [start, end)
    start_parsed = parse_datetime(start_date)
    start_dt = start_parsed.datetime_utc
    end_parsed = parse_datetime(end_date)
    end_dt = end_parsed.datetime_end_of_day_utc
    events = client.get_events(cal, start=start_dt, end=end_dt)
    result = []
    for event in events:
        result.extend(expand_recurring(event, start_dt, end_dt, tz_name=tz))

    logger.info(f"Listed {len(result)} events from {start_date} to {end_date}")
    return result


@mcp.tool()
def update_event(
    uid: str,
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
    all_day: bool = False,
    tz: str = "America/Los_Angeles",
) -> str:
    """Update an existing calendar event by UID.

    Only the fields you provide are changed; the rest are left as-is.

    Args:
        uid: The event UID (from create_event or list/search results).
        title: New title/summary.
        start: New start in ISO 8601. All-day: date only. Timed: datetime with
            or without timezone; a naive datetime is interpreted in `tz`.
        end: New end, same rules as start. For all-day events this is the LAST
            day (inclusive). To move a one-day all-day event to a new day, set
            `start` to that day (omit `end`).
        description: New description.
        location: New location.
        all_day: If True, treat start/end as date-only. If False, use heuristic
            detection. Set True when converting a timed event to all-day.
        tz: IANA timezone to interpret naive datetimes and display times in
            the confirmation. Defaults to "America/Los_Angeles".

    Returns:
        A confirmation string: the updated day/time in `tz` plus the event UID.
    """
    from helpers import UpdateParams, apply_update

    client = get_client()
    cal = get_calendar(client)
    event = fetch_event_by_uid(client, cal, uid)
    if not event:
        raise RuntimeError(f"Event not found: {uid}")

    params = UpdateParams(
        title=title,
        start=start,
        end=end,
        description=description,
        location=location,
        all_day=all_day,
        tz=tz,
    )

    # Mutate the live VEVENT component that save() will PUT. caldav's save()
    # serializes event.icalendar_component, NOT event._data, so editing a
    # re-parsed copy (and reassigning _data) would silently never persist.
    vevent = event.icalendar_component
    if vevent is None:
        raise RuntimeError(f"Event has no VEVENT component: {uid}")
    apply_update(vevent, params)

    try:
        event.save()
    except Exception as e:
        error_msg = str(e)
        if _is_412(e):
            _retry_update(client, cal, uid, params)
        else:
            raise RuntimeError(f"Failed to update event {uid}: {error_msg}") from e

    logger.info(f"Updated event {uid}")
    return build_event_feedback(vevent_to_dict(vevent, tz), tz)


def _retry_update(client, cal, uid: str, params: "UpdateParams") -> None:
    """Retry an update after a 412 Precondition Failed.

    Re-reads the event from the server, re-applies the changes, and saves.
    Retries up to 3 times. Raises if all retries fail; returns None on success.
    """
    from helpers import apply_update

    for attempt in range(3):
        try:
            logger.info(f"412 retry {attempt + 1}/3 for {uid}")
            fresh_event = fetch_event_by_uid(client, cal, uid)
            if not fresh_event:
                raise RuntimeError(f"Event not found: {uid}")

            fvevent = fresh_event.icalendar_component
            if fvevent is None:
                raise RuntimeError(f"Event has no VEVENT component: {uid}")
            apply_update(fvevent, params)
            fresh_event.save()
            logger.info(f"Updated event {uid} (412 retry {attempt + 1} succeeded)")
            return
        except Exception as retry_err:
            if _is_412(retry_err) and attempt < 2:
                continue
            raise retry_err

    raise RuntimeError(f"Failed to update event {uid}: 412 Precondition Failed after 3 retries")


@mcp.tool()
def delete_event(uid: str) -> bool:
    """Delete a calendar event by UID.

    Args:
        uid: The event UID.

    Returns:
        True if the event was deleted successfully.
    """
    client = get_client()
    cal = get_calendar(client)
    event = fetch_event_by_uid(client, cal, uid)
    if not event:
        raise RuntimeError(f"Event not found: {uid}")
    try:
        event.delete()
        logger.info(f"Deleted event {uid}")
        return True
    except Exception as e:
        raise RuntimeError(f"Failed to delete event {uid}: {e}") from e


@mcp.tool()
def search_events(
    query: str,
    start_date: str | None = None,
    end_date: str | None = None,
    tz: str = "America/Los_Angeles",
) -> list[dict]:
    """Search for events matching a query string.

    Args:
        query: Search term to match against title, description, or location.
        start_date: Optional start date filter (ISO 8601).
        end_date: Optional end date filter (ISO 8601).
        tz: IANA timezone name to return times in (e.g. "America/Los_Angeles", "America/New_York", "UTC"). Defaults to "America/Los_Angeles".

    Returns:
        List of matching event dicts with uid, title, start, end, all_day,
        day_of_week, day_of_week_end, timezone, description, location. Timed
        events use ISO 8601 in the specified timezone; all-day events use
        date-only start/end (end is the last inclusive day).
    """
    client = get_client()
    cal = get_calendar(client)

    # Determine date range for search
    if start_date and end_date:
        start_parsed = parse_datetime(start_date)
        start_dt = start_parsed.datetime_utc
        end_parsed = parse_datetime(end_date)
        end_dt = end_parsed.datetime_end_of_day_utc
        events = client.get_events(cal, start=start_dt, end=end_dt)
    else:
        # Search a wide range if no dates given — last 365 days to next 365 days
        now = datetime.now(timezone.utc)
        from dateutil.relativedelta import relativedelta
        start_dt = now - relativedelta(years=1)
        end_dt = now + relativedelta(years=1)
        events = client.get_events(cal, start=start_dt, end=end_dt)

    query_lower = query.lower()
    results = []
    for event in events:
        for d in expand_recurring(event, start_dt, end_dt, tz_name=tz):
            if query_lower in (
                d.get("title", "").lower()
                + " "
                + d.get("description", "").lower()
                + " "
                + d.get("location", "").lower()
            ):
                results.append(d)

    logger.info(f"Search for '{query}' returned {len(results)} events")
    return results




if __name__ == "__main__":
    logger.info("Starting iCloud Calendar MCP server")
    logger.info(f"CalDAV URL: {CALDAV_URL}")
    logger.info(f"User: {CALDAV_USER}")
    logger.info(f"Calendar: {CALENDAR_NAME or '(first available)'}")
    # Verify connection on startup
    try:
        client = get_client()
        cal = get_calendar(client)
        logger.info(f"Connected to calendar: {cal.get_display_name()}")
    except Exception as e:
        logger.error(f"Failed to connect to CalDAV: {e}")
        raise SystemExit(1) from e
    mcp.settings.host = os.environ.get("MCP_HOST", "0.0.0.0")
    mcp.settings.port = int(os.environ.get("MCP_PORT", "13081"))
    mcp.run(transport="streamable-http")
