from __future__ import annotations

"""Recurring event expansion.

Expands recurring calendar events (RRULE/RDATE) into individual
occurrences within a date range.

Usage:
    from recurrence import expand_recurring

    occurrences = expand_recurring(event, start_dt, end_dt, tz_name="America/Los_Angeles")
"""
import logging
from datetime import date, datetime, timedelta, timezone

from icalendar import Calendar

logger = logging.getLogger("calendar-mcp")


def expand_recurring(event, start_dt: datetime, end_dt: datetime, tz_name: str = "America/Los_Angeles") -> list[dict]:
    """Expand a recurring event into individual occurrences within [start_dt, end_dt).

    For non-recurring events, returns a single-item list.

    Args:
        event: CalDAV event object.
        start_dt: Start of query range (UTC).
        end_dt: End of query range (UTC, exclusive).
        tz_name: IANA timezone name for output formatting.

    Returns:
        List of event dicts with occurrence dates.
    """
    from zoneinfo import ZoneInfo

    raw = getattr(event, "_data", None) or getattr(event, "data", None)
    if not raw:
        return []

    cal_obj = Calendar.from_ical(raw)
    vevent = None
    for component in cal_obj.walk():
        if component.name == "VEVENT":
            vevent = component
            break
    if not vevent:
        return []

    rrule = vevent.get("rrule")
    rdate = vevent.get("rdate")
    exdate = vevent.get("exdate")
    dtstart_prop = vevent.get("dtstart")
    dtend_prop = vevent.get("dtend")
    duration_prop = vevent.get("duration")

    # Not recurring — return single event
    if not rrule and not rdate:
        d = _event_to_dict(event, tz_name=tz_name)
        return [d] if d else []

    dtstart = dtstart_prop.dt if dtstart_prop else None
    if not dtstart:
        d = _event_to_dict(event, tz_name=tz_name)
        return [d] if d else []

    is_all_day_event = _check_all_day(dtstart_prop, vevent)

    # Compute event duration
    if dtend_prop:
        dtend = dtend_prop.dt
        duration = dtend - dtstart
    elif duration_prop:
        duration = duration_prop.dt
    else:
        duration = timedelta(hours=1) if not is_all_day_event else timedelta(days=1)

    # Build rruleset
    from dateutil.rrule import rruleset, rrulestr

    rrs = rruleset()

    if rrule:
        try:
            rule_str = rrule.to_ical().decode("utf-8")
            rule = rrulestr(rule_str, dtstart=dtstart)
            rrs.rrule(rule)
        except Exception as e:
            logger.warning(f"Failed to parse RRULE: {e}")

    if rdate:
        for r in (rdate if isinstance(rdate, list) else [rdate]):
            rrs.rdate(getattr(r, "dt", r))

    if exdate:
        ex_list = exdate if isinstance(exdate, list) else [exdate]
        for ex in ex_list:
            # Handle vDDDLists which wraps multiple dates
            ex_dates = getattr(ex, "dts", [ex])
            for ed in ex_dates:
                rrs.exdate(getattr(ed, "dt", ed))

    target_tz = ZoneInfo(tz_name)
    title = str(vevent.get("summary", ""))
    description = str(vevent.get("description", "") or "")
    location = str(vevent.get("location", "") or "")
    uid = str(vevent.get("uid", ""))
    # Match timezone awareness of dtstart for rruleset.between()
    is_naive = (isinstance(dtstart, datetime) and dtstart.tzinfo is None) or isinstance(dtstart, date) and not isinstance(dtstart, datetime)
    results = []

    b_start = start_dt.replace(tzinfo=None) if is_naive else start_dt
    b_end = end_dt.replace(tzinfo=None) if is_naive else end_dt
    for occ in rrs.between(b_start, b_end, inc=True):
        occ_end = occ + duration

        # Format start
        if is_all_day_event:
            start_str = occ.date().isoformat()
            dow_start = occ.strftime("%A")
        else:
            if occ.tzinfo is None:
                occ = occ.replace(tzinfo=timezone.utc)
            occ_local = occ.astimezone(target_tz)
            start_str = occ_local.isoformat()
            dow_start = occ_local.strftime("%A")

        # Format end
        if is_all_day_event:
            # DTEND is exclusive; show the last inclusive day.
            last_day = occ_end - timedelta(days=1)
            last_day = last_day if isinstance(last_day, date) else last_day.date()
            end_str = last_day.isoformat()
            dow_end = last_day.strftime("%A")
        else:
            if occ_end.tzinfo is None:
                occ_end = occ_end.replace(tzinfo=timezone.utc)
            end_local = occ_end.astimezone(target_tz)
            end_str = end_local.isoformat()
            dow_end = end_local.strftime("%A")

        results.append({
            "uid": uid,
            "title": title,
            "start": start_str,
            "end": end_str,
            "all_day": is_all_day_event,
            "day_of_week": dow_start,
            "day_of_week_end": dow_end,
            "timezone": tz_name,
            "description": description,
            "location": location,
        })

    return results


def _check_all_day(prop, vevent=None) -> bool:
    """Check if a property represents an all-day event."""
    if vevent:
        cats = vevent.get("categories")
        if cats and "ALL-DAY-EVENT" in cats:
            return True
    raw = prop.to_ical()
    if isinstance(raw, bytes) and b"T" not in raw:
        return True
    return False


def _event_to_dict(event, tz_name: str = "America/Los_Angeles") -> dict | None:
    """Minimal event-to-dict for the non-recurring fallback.

    Delegates to the shared helper so all-day DATE-type events and the
    exclusive-end display stay consistent with list/search output.
    """
    from helpers import event_to_dict
    return event_to_dict(event, tz_name=tz_name)
