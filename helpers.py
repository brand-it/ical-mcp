from __future__ import annotations

"""Calendar event helpers.

Provides utilities for parsing CalDAV event objects into structured dicts,
detecting all-day events, and formatting datetime properties.

Usage:
    from helpers import event_to_dict, is_all_day, input_is_all_day

    d = event_to_dict(event, tz_name="America/Los_Angeles")
    print(d["title"], d["start"], d["day_of_week"])
"""
from datetime import date, datetime, timedelta, timezone


def set_all_day_dates(vevent, start: str, end: str | None = None) -> None:
    """Set DTSTART/DTEND on a VEVENT as an all-day (DATE) event.

    `start` is the first day; `end` is the last inclusive day (defaults to
    `start`). iCal all-day events use an exclusive DTEND, so the stored DTEND
    is the day after the last inclusive day. The VALUE=DATE parameter is
    required: iCloud reinterprets a bare date (DTSTART:20261105) as a floating
    datetime on the previous day at 23:00, shifting the event back a day.

    Raises ValueError if `end` is before `start`.
    """
    from icalendar import vDate

    start_day = datetime.fromisoformat(start).date()
    end_day = (
        datetime.fromisoformat(end).date() if end else start_day
    )
    if end_day < start_day:
        raise ValueError(f"end ({end}) is before start ({start})")
    _set_all_day(vevent, "DTSTART", start_day)
    _set_all_day(vevent, "DTEND", end_day + timedelta(days=1))


def _set_all_day(vevent, prop: str, day: date) -> None:
    """Set a DATE property with the VALUE=DATE parameter, replacing any prior value."""
    from icalendar import vDate

    if prop in vevent:
        del vevent[prop]
    vevent.add(prop, vDate(day), parameters={"VALUE": "DATE"})



def is_all_day(prop, vevent=None) -> bool:
    """Check if a property represents an all-day event.

    Checks:
    1. CATEGORIES contains ALL-DAY-EVENT (set by this server)
    2. Raw to_ical() has no 'T' (true DATE format)
    """
    if vevent:
        cats = vevent.get("categories")
        if cats and "ALL-DAY-EVENT" in cats:
            return True
    raw = prop.to_ical()
    if isinstance(raw, bytes) and b"T" not in raw:
        return True
    return False


def input_is_all_day(value: str, all_day: bool | None = None) -> bool:
    """Check if an input string represents an all-day event.

    Args:
        value: ISO 8601 date/time string.
        all_day: If True/False, overrides the heuristic.
    """
    if all_day is not None:
        return all_day
    return "T" not in value and "+" not in value and value.count("-") <= 2


def all_day_end_date(start: str) -> str:
    """Return the exclusive DTEND date for an all-day event.

    iCal all-day events use an exclusive DTEND: a one-day event on 2026-10-26
    stores DTSTART=20261026 and DTEND=20261027. Storing DTEND == DTSTART
    (zero-length) makes some clients render the event on the previous day.

    Args:
        start: ISO 8601 date (YYYY-MM-DD).

    Returns:
        The next day's date (YYYY-MM-DD).
    """
    d = date.fromisoformat(start)
    return (d + timedelta(days=1)).isoformat()


def build_event_feedback(d: dict, tz_name: str = "America/Los_Angeles") -> str:
    """Build a human/LLM-readable confirmation of a stored event.

    Echoes back exactly what was stored so the caller can confirm the event
    landed on the right day/time without a follow-up read.

    Args:
        d: An event dict from event_to_dict().
        tz_name: IANA timezone used for the timed-event local display.

    Returns:
        Multi-line confirmation string.
    """
    lines = [f"Event '{d.get('title', '')}' saved."]
    if d.get("all_day"):
        start = d.get("start", "")
        end = d.get("end", start)
        span = f"{start} to {end}" if end and end != start else start
        lines.append(f"All-day: {span} ({d.get('day_of_week', '')}).")
    else:
        lines.append(f"Start: {d.get('start', '')} ({d.get('day_of_week', '')}).")
        if d.get("end"):
            lines.append(f"End: {d.get('end', '')} ({d.get('day_of_week_end', '')}).")
        lines.append(f"Times shown in {tz_name} (stored in UTC).")
    if d.get("location"):
        lines.append(f"Location: {d.get('location')}.")
    if d.get("description"):
        lines.append(f"Description: {d.get('description')}.")
    lines.append(f"UID: {d.get('uid', '')}")
    lines.append(
        "Use the UID to update or delete this event. "
        "If the day or time is wrong, call update_event with the UID."
    )
    return "\n".join(lines)


def vevent_to_dict(vevent, tz_name: str = "America/Los_Angeles") -> dict:
    """Extract useful fields from an icalendar VEVENT component.

    Args:
        vevent: An icalendar VEVENT component.
        tz_name: IANA timezone name to return times in.
                 Defaults to "America/Los_Angeles".

    Returns:
        Dict with uid, title, start, end, all_day, day_of_week, day_of_week_end,
        timezone, description, location.
    """
    from zoneinfo import ZoneInfo

    uid = str(vevent.get("uid", ""))
    title = str(vevent.get("summary", ""))
    target_tz = ZoneInfo(tz_name)

    categories = vevent.get("categories")
    has_category = bool(categories and "ALL-DAY-EVENT" in categories)

    def _dt(prop):
        """Return the underlying date/datetime from a property or raw value."""
        if prop is None:
            return None
        return getattr(prop, "dt", prop)

    dtstart = _dt(vevent.get("dtstart"))
    dtend = _dt(vevent.get("dtend"))
    start_is_date = bool(
        dtstart and isinstance(dtstart, date) and not isinstance(dtstart, datetime)
    )
    is_all_day = has_category or start_is_date

    def _day(d):
        """Return a pure date from a date or datetime."""
        return d if isinstance(d, date) and not isinstance(d, datetime) else d.date()

    def _format_time(dt):
        """Format a date/datetime, returning (value, day_of_week).

        All-day events: value is the calendar day (ISO date). Timed events:
        value is ISO 8601 in the target timezone.
        """
        if not dt:
            return "", ""
        if is_all_day:
            day = _day(dt)
            return day.isoformat(), day.strftime("%A")
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_local = dt.astimezone(target_tz)
            return dt_local.isoformat(), dt_local.strftime("%A")
        day = _day(dt)
        return day.isoformat(), day.strftime("%A")

    start, day_of_week_start = _format_time(dtstart)

    if dtend:
        end, day_of_week_end = _format_time(dtend)
        if is_all_day:
            # DTEND is exclusive per iCal; show the last inclusive day. Works
            # for DATE type and for iCloud's DATETIME-midnight read-back. A
            # zero-length DTEND (== DTSTART) means a single day.
            start_day = _day(dtstart)
            last_day = _day(dtend) - timedelta(days=1)
            if last_day < start_day:
                last_day = start_day
            end = last_day.isoformat()
            day_of_week_end = last_day.strftime("%A")
    elif dtstart and vevent.get("duration"):
        dur_dt = vevent.get("duration").dt
        end_dt = dtstart + dur_dt
        if isinstance(end_dt, datetime) and end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        if isinstance(end_dt, datetime):
            end_local = end_dt.astimezone(target_tz)
            end = end_local.isoformat()
            day_of_week_end = end_local.strftime("%A")
        else:
            end = end_dt.isoformat()
            day_of_week_end = end_dt.strftime("%A")
    else:
        end, day_of_week_end = "", ""

    description = str(vevent.get("description", "") or "")
    location = str(vevent.get("location", "") or "")

    return {
        "uid": uid,
        "title": title,
        "start": start,
        "end": end,
        "all_day": is_all_day,
        "day_of_week": day_of_week_start,
        "day_of_week_end": day_of_week_end,
        "timezone": tz_name,
        "description": description,
        "location": location,
    }


def event_to_dict(event, tz_name: str = "America/Los_Angeles") -> dict | None:
    """Extract useful fields from a CalDAV event object (caldav 3.x).

    Args:
        event: The CalDAV event object.
        tz_name: IANA timezone name to return times in.
                 Defaults to "America/Los_Angeles".

    Returns:
        Dict with uid, title, start, end, all_day, day_of_week, day_of_week_end,
        timezone, description, location. Or None if event is invalid.
    """
    from icalendar import Calendar

    raw = getattr(event, "_data", None) or getattr(event, "data", None)
    if not raw:
        return None

    cal_obj = Calendar.from_ical(raw)
    vevent = None
    for component in cal_obj.walk():
        if component.name == "VEVENT":
            vevent = component
            break

    if not vevent:
        return None

    return vevent_to_dict(vevent, tz_name)


from dataclasses import dataclass, field


@dataclass
class UpdateParams:
    """Parameters for updating a calendar event.

    All fields are optional — only provided fields will be applied.
    """
    title: str | None = None
    start: str | None = None
    end: str | None = None
    description: str | None = None
    location: str | None = None
    all_day: bool = False
    tz: str = "America/Los_Angeles"

    def has_changes(self) -> bool:
        """Check if any fields are set."""
        return any(v is not None for v in [
            self.title, self.start, self.end, self.description, self.location
        ])


def apply_update(vevent, params: UpdateParams) -> bool:
    """Apply update parameters to a VEVENT component.

    Modifies the vevent in-place. Returns True if any changes were applied.

    Args:
        vevent: An icalendar VEVENT component.
        params: UpdateParams with the fields to change.

    Returns:
        True if any changes were made.

    Raises:
        ValueError: If start/end format is invalid or start is after end.
    """
    from icalendar import vDate

    if not params.has_changes():
        return False

    # Determine all-day status for this update
    new_all_day = _compute_all_day_status(params)

    if params.title is not None:
        vevent["SUMMARY"] = params.title

    if new_all_day:
        # All-day: DTEND is exclusive per iCal. A one-day event on 2026-10-26
        # stores DTEND=20261027. Storing DTEND == DTSTART makes some clients
        # render the event on the previous day.
        if params.start is not None:
            _set_datetime(vevent, "DTSTART", params.start, True, params.tz)
        if params.end is not None:
            _set_datetime(vevent, "DTEND", all_day_end_date(params.end), True, params.tz)
        elif params.start is not None:
            _set_datetime(vevent, "DTEND", all_day_end_date(params.start), True, params.tz)
    else:
        if params.start is not None:
            _set_datetime(vevent, "DTSTART", params.start, False, params.tz)
        if params.end is not None:
            _set_datetime(vevent, "DTEND", params.end, False, params.tz)

    # Update CATEGORIES based on all-day status
    # Only touch CATEGORIES if start/end was provided (we know the intent)
    if params.start is not None or params.end is not None:
        if new_all_day:
            vevent["CATEGORIES"] = "ALL-DAY-EVENT"
        elif "CATEGORIES" in vevent:
            del vevent["CATEGORIES"]

    if params.description is not None:
        vevent["DESCRIPTION"] = params.description

    if params.location is not None:
        vevent["LOCATION"] = params.location

    # Ensure DTSTAMP exists (required by iCloud). Use add(), not __setitem__,
    # so it serializes as valid iCal.
    if "DTSTAMP" not in vevent:
        vevent.add("DTSTAMP", datetime.now(timezone.utc))

    return True


def _compute_all_day_status(params: UpdateParams) -> bool:
    """Determine all-day status from update parameters.

    If both start and end are provided, use start. If only one is provided,
    use that one. Falls back to the explicit all_day flag.
    """
    if params.start is not None:
        return input_is_all_day(params.start, params.all_day)
    if params.end is not None:
        return input_is_all_day(params.end, params.all_day)
    return False


def _set_datetime(vevent, prop: str, value: str, is_all_day: bool, tz_name: str = "America/Los_Angeles"):
    """Set a datetime property on a VEVENT.

    Args:
        vevent: The VEVENT component.
        prop: Property name ("DTSTART" or "DTEND").
        value: ISO 8601 date or datetime string.
        is_all_day: If True, store as DATE; otherwise as DATETIME (UTC).
        tz_name: IANA timezone to interpret naive datetimes in.
    """
    from icalendar import vDate
    from zoneinfo import ZoneInfo

    if is_all_day:
        # VALUE=DATE is required: iCloud reinterprets a bare date as a
        # floating datetime on the previous day at 23:00, shifting the
        # event back a day.
        value = vDate(datetime.fromisoformat(value).date())
    else:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(tz_name))
        value = dt.astimezone(timezone.utc)
    # icalendar serializes a raw value set via __setitem__ as Python isoformat
    # (invalid iCal). add() serializes correctly; del first to avoid duplicates.
    if prop in vevent:
        del vevent[prop]
    vevent.add(prop, value, parameters={"VALUE": "DATE"} if is_all_day else None)


def serialize_event(vevent) -> bytes:
    """Serialize a VEVENT into a full VCALENDAR bytes payload.

    Wraps the VEVENT in a VCALENDAR component so it can be PUT to CalDAV
    without the caldav library re-serializing and modifying the data.

    Args:
        vevent: An icalendar VEVENT component.

    Returns:
        Bytes of the serialized VCALENDAR.
    """
    from icalendar import Calendar

    cal = Calendar()
    cal.add_component(vevent)
    return cal.to_ical()


def extract_vevent(event) -> object:
    """Extract the VEVENT component from a CalDAV event object.

    Args:
        event: A caldav Event object.

    Returns:
        The icalendar VEVENT component.

    Raises:
        RuntimeError: If no VEVENT component is found.
    """
    from icalendar import Calendar

    raw = getattr(event, "_data", None) or getattr(event, "data", None)
    if not raw:
        raise RuntimeError("No data in event")

    cal_obj = Calendar.from_ical(raw)
    for component in cal_obj.walk():
        if component.name == "VEVENT":
            return component

    raise RuntimeError("No VEVENT component found")
