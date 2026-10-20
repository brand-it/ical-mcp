#!/usr/bin/env python3
"""Unit tests for UpdateParams and apply_update in helpers.py.

Run without connecting to CalDAV:
    PYTHONPATH=/home/linuxbrew/.linuxbrew/lib/python3.14/site-packages python3 update_test.py
"""
import time
import sys
from datetime import date, datetime, timezone
from icalendar import Calendar, Event, vDate
from helpers import UpdateParams, apply_update, serialize_event, extract_vevent


def _make_vevent(title="Test Event", start=None, end=None, all_day=False):
    """Create a VEVENT for testing."""
    evt = Event()
    evt.add("summary", title)
    evt.add("uid", "test-uid-123")
    if all_day:
        evt.add("dtstart", vDate(start) if isinstance(start, date) else vDate(start))
        evt.add("dtend", vDate(end) if isinstance(end, date) else vDate(end))
        evt.add("categories", "ALL-DAY-EVENT")
    else:
        evt.add("dtstart", start if start else datetime(2026, 7, 14, 10, 0, 0, tzinfo=timezone.utc))
        evt.add("dtend", end if end else datetime(2026, 7, 14, 11, 0, 0, tzinfo=timezone.utc))
    return evt


def _wrap(vevent):
    """Wrap a VEVENT in a VCALENDAR for serialization testing."""
    cal = Calendar()
    cal.add_component(vevent)
    return cal


def assert_eq(label, got, expected):
    if got != expected:
        print(f"  ✗ {label}: got {got!r}, expected {expected!r}")
        return False
    print(f"  ✓ {label}")
    return True


def run_tests():
    passed = 0
    failed = 0

    def check(label, got, expected):
        nonlocal passed, failed
        if assert_eq(label, got, expected):
            passed += 1
        else:
            failed += 1

    # ── UpdateParams ──
    print("\nUpdateParams:")
    params = UpdateParams()
    check("empty params has no changes", params.has_changes(), False)

    params = UpdateParams(location="Test")
    check("location-only has changes", params.has_changes(), True)

    params = UpdateParams(title="New Title")
    check("title-only has changes", params.has_changes(), True)

    # ── apply_update: title ──
    print("\napply_update - title:")
    evt = _make_vevent("Old Title")
    params = UpdateParams(title="New Title")
    apply_update(evt, params)
    check("title updated", str(evt.get("summary")), "New Title")

    # ── apply_update: location ──
    print("\napply_update - location:")
    evt = _make_vevent()
    params = UpdateParams(location="New Location")
    apply_update(evt, params)
    check("location updated", str(evt.get("location", "")), "New Location")

    # ── apply_update: description ──
    print("\napply_update - description:")
    evt = _make_vevent()
    params = UpdateParams(description="New Description")
    apply_update(evt, params)
    check("description updated", str(evt.get("description", "")), "New Description")

    # ── apply_update: timed start/end ──
    print("\napply_update - timed start/end:")
    evt = _make_vevent()
    params = UpdateParams(start="2026-08-01T14:00:00-07:00", end="2026-08-01T15:00:00-07:00")
    apply_update(evt, params)
    new_start = evt.get("dtstart")
    if hasattr(new_start, "dt"): new_start = new_start.dt
    check("timed start updated", new_start.hour, 21)  # 14:00 PDT = 21:00 UTC
    new_end = evt.get("dtend")
    if hasattr(new_end, "dt"): new_end = new_end.dt
    check("timed end updated", new_end.hour, 22)  # 15:00 PDT = 22:00 UTC

    # ── apply_update: all-day start/end ──
    print("\napply_update - all-day start/end:")
    evt = _make_vevent()
    params = UpdateParams(start="2026-08-01", end="2026-08-02", all_day=True)
    apply_update(evt, params)
    check("all-day start is vDate", isinstance(evt.get("dtstart").dt, date), True)
    check("all-day start date", evt.get("dtstart").dt.year, 2026)
    check("CATEGORIES set", str(evt.get("categories", "")), "ALL-DAY-EVENT")

    # ── apply_update: all-day serializes with VALUE=DATE ──
    print("\napply_update - all-day VALUE=DATE serialization:")
    evt = _make_vevent()
    params = UpdateParams(start="2026-08-01", end="2026-08-02", all_day=True)
    apply_update(evt, params)
    dtlines = [l for l in evt.to_ical().decode().splitlines() if "DTSTART" in l or "DTEND" in l]
    check("all-day DTSTART has VALUE=DATE", dtlines[0], "DTSTART;VALUE=DATE:20260801")
    # end 08-02 is last inclusive day -> exclusive DTEND 08-03
    check("all-day DTEND exclusive + VALUE=DATE", dtlines[1], "DTEND;VALUE=DATE:20260803")

    # ── apply_update: no changes ──
    print("\napply_update - no changes:")
    evt = _make_vevent()
    params = UpdateParams()
    result = apply_update(evt, params)
    check("no changes returns False", result, False)

    # ── apply_update: DTSTAMP added ──
    print("\napply_update - DTSTAMP:")
    evt = _make_vevent()
    if "DTSTAMP" in evt:
        del evt["DTSTAMP"]
    params = UpdateParams(location="Test")
    apply_update(evt, params)
    check("DTSTAMP added", "DTSTAMP" in evt, True)

    # ── serialize_event ──
    print("\nserialize_event:")
    evt = _make_vevent()
    params = UpdateParams(location="Serialized Test")
    apply_update(evt, params)
    data = serialize_event(evt)
    check("serialize returns bytes", isinstance(data, bytes), True)
    check("serialize contains LOCATION", b"LOCATION" in data, True)
    check("serialize contains VCALENDAR", b"VCALENDAR" in data, True)
    check("serialize contains VEVENT", b"VEVENT" in data, True)

    # ── Round-trip: serialize → deserialize → verify ──
    print("\nRound-trip serialize/deserialize:")
    evt = _make_vevent("Round Trip Event")
    params = UpdateParams(title="Updated Round Trip", location="Round Trip Loc")
    apply_update(evt, params)
    data = serialize_event(evt)
    cal = Calendar.from_ical(data)
    for comp in cal.walk():
        if comp.name == "VEVENT":
            check("round-trip title", str(comp.get("summary")), "Updated Round Trip")
            check("round-trip location", str(comp.get("location", "")), "Round Trip Loc")

    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    start = time.time()
    if run_tests():
        sys.exit(0)
    else:
        sys.exit(1)
