"""Unit tests for helpers module.

Tests event_to_dict, is_all_day, and input_is_all_day without connecting to any server.

Usage:
    python helpers_test.py
"""
import sys
sys.path.insert(0, ".")

from datetime import date, datetime, timezone, timedelta
from icalendar import Calendar, Event, vDate
from helpers import event_to_dict, is_all_day, input_is_all_day, set_all_day_dates


def _make_event(uid, title, start, end, description="", location="", all_day=False):
    """Create a mock CalDAV event object with icalendar data."""
    cal = Calendar()
    ev = Event()
    ev.add("uid", uid)
    ev.add("summary", title)
    if description:
        ev.add("description", description)
    if location:
        ev.add("location", location)
    if all_day:
        ev.add("dtstart", vDate(start))
        ev.add("dtend", vDate(end))
    else:
        ev.add("dtstart", start)
        ev.add("dtend", end)
    cal.add_component(ev)

    class MockEvent:
        def __init__(self, data):
            self._data = data
    return MockEvent(cal.to_ical())


def run_tests():
    passed = 0
    failed = 0

    def assert_eq(label, actual, expected):
        nonlocal passed, failed
        if actual == expected:
            print(f"  ✓ {label}")
            passed += 1
        else:
            print(f"  ✗ {label}: got {actual!r}, expected {expected!r}")
            failed += 1

    # ── input_is_all_day ──
    print("input_is_all_day:")
    assert_eq("date-only string", input_is_all_day("2026-07-19"), True)
    assert_eq("datetime string", input_is_all_day("2026-07-19T12:00:00"), False)
    assert_eq("tz-aware string", input_is_all_day("2026-07-19T12:00:00-07:00"), False)
    assert_eq("override True", input_is_all_day("2026-07-19T12:00:00", all_day=True), True)
    assert_eq("override False", input_is_all_day("2026-07-19", all_day=False), False)

    # ── event_to_dict (timed event) ──
    print("\nevent_to_dict (timed):")
    start = datetime(2026, 7, 14, 10, 30, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 14, 11, 0, 0, tzinfo=timezone.utc)
    evt = _make_event("test-1", "Swim Lessons", start, end, location="Pool")
    d = event_to_dict(evt, tz_name="America/Los_Angeles")
    assert_eq("uid", d["uid"], "test-1")
    assert_eq("title", d["title"], "Swim Lessons")
    assert_eq("location", d["location"], "Pool")
    assert_eq("timezone", d["timezone"], "America/Los_Angeles")
    # Start should be in PDT (UTC-7)
    assert_eq("start hour", d["start"].split("T")[1][:2], "03")  # 10:30 UTC = 03:30 PDT
    assert_eq("day_of_week", d["day_of_week"], "Tuesday")

    # ── event_to_dict (all-day event) ──
    print("\nevent_to_dict (all-day):")
    start_d = date(2026, 7, 14)
    end_d = date(2026, 7, 15)  # exclusive DTEND per iCal
    evt = _make_event("allday-1", "Vacation", start_d, end_d, all_day=True)
    d = event_to_dict(evt)
    assert_eq("all-day start", d["start"], "2026-07-14")
    assert_eq("all-day end (last inclusive day)", d["end"], "2026-07-14")
    assert_eq("all-day flag", d["all_day"], True)
    assert_eq("all-day day_of_week", d["day_of_week"], "Tuesday")

    # Multi-day all-day: DTSTART 07-14, exclusive DTEND 07-17 -> last day 07-16
    evt2 = _make_event("allday-2", "Trip", date(2026, 7, 14), date(2026, 7, 17), all_day=True)
    d2 = event_to_dict(evt2)
    assert_eq("multi-day all-day start", d2["start"], "2026-07-14")
    assert_eq("multi-day all-day end", d2["end"], "2026-07-16")

    # Zero-length all-day (DTEND == DTSTART) -> single day, no shift back
    evt3 = _make_event("allday-3", "Picture Day", date(2026, 10, 26), date(2026, 10, 26), all_day=True)
    d3 = event_to_dict(evt3)
    assert_eq("zero-length all-day start", d3["start"], "2026-10-26")
    assert_eq("zero-length all-day end", d3["end"], "2026-10-26")

    # iCloud read-back quirk: all-day event returned as DATETIME at midnight UTC.
    # start 2026-10-26T00:00:00Z, exclusive end 2026-10-27T00:00:00Z.
    icl = Calendar()
    icl_ev = Event()
    icl_ev.add("uid", "allday-icloud")
    icl_ev.add("summary", "Picture Day")
    icl_ev.add("categories", "ALL-DAY-EVENT")
    icl_ev.add("dtstart", datetime(2026, 10, 26, 0, 0, 0, tzinfo=timezone.utc))
    icl_ev.add("dtend", datetime(2026, 10, 27, 0, 0, 0, tzinfo=timezone.utc))
    icl.add_component(icl_ev)
    class _M:
        _data = icl.to_ical()
    di = event_to_dict(_M(), tz_name="America/Los_Angeles")
    assert_eq("icloud all-day flag", di["all_day"], True)
    assert_eq("icloud all-day start", di["start"], "2026-10-26")
    assert_eq("icloud all-day end", di["end"], "2026-10-26")

    # ── set_all_day_dates (VALUE=DATE encoding) ──
    print("\nset_all_day_dates:")
    from icalendar import Event as _E

    def _serialized_dtlines(ev):
        return [l for l in ev.to_ical().decode().splitlines() if "DTSTART" in l or "DTEND" in l]

    # Single-day all-day: DTEND must be exclusive (next day) and carry VALUE=DATE.
    ev1 = _E()
    set_all_day_dates(ev1, "2026-11-05")
    lines1 = _serialized_dtlines(ev1)
    assert_eq("single-day DTSTART has VALUE=DATE", lines1[0], "DTSTART;VALUE=DATE:20261105")
    assert_eq("single-day DTEND is exclusive next day", lines1[1], "DTEND;VALUE=DATE:20261106")

    # Multi-day all-day: start 11-05, last inclusive 11-07 -> DTEND 11-08.
    ev2 = _E()
    set_all_day_dates(ev2, "2026-11-05", "2026-11-07")
    lines2 = _serialized_dtlines(ev2)
    assert_eq("multi-day DTSTART", lines2[0], "DTSTART;VALUE=DATE:20261105")
    assert_eq("multi-day DTEND exclusive", lines2[1], "DTEND;VALUE=DATE:20261108")

    # end before start -> ValueError.
    ev3 = _E()
    try:
        set_all_day_dates(ev3, "2026-11-05", "2026-11-04")
        assert_eq("end before start raises", "no error", "ValueError")
    except ValueError:
        assert_eq("end before start raises", "ValueError", "ValueError")

    # Re-setting replaces the prior value (no duplicate DTSTART).
    ev4 = _E()
    set_all_day_dates(ev4, "2026-11-05")
    set_all_day_dates(ev4, "2026-12-01")
    lines4 = _serialized_dtlines(ev4)
    assert_eq("re-set keeps one DTSTART", sum(1 for l in lines4 if l.startswith("DTSTART")), 1)
    assert_eq("re-set keeps one DTEND", sum(1 for l in lines4 if l.startswith("DTEND")), 1)
    assert_eq("re-set DTSTART updated", lines4[0], "DTSTART;VALUE=DATE:20261201")

    # ── is_all_day ──
    print("\nis_all_day:")
    # Create a vDate property
    from icalendar import vDate
    date_prop = vDate(date(2026, 7, 14))
    assert_eq("vDate is all-day", is_all_day(date_prop), True)

    # Create a datetime property
    dt_prop = type("MockProp", (), {"dt": datetime(2026, 7, 14, 10, 0), "to_ical": lambda self: b"20260714T100000"})()
    assert_eq("datetime is not all-day", is_all_day(dt_prop), False)

    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    if run_tests():
        sys.exit(0)
    else:
        sys.exit(1)
