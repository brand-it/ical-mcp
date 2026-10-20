"""Unit tests for recurrence module.

Tests expand_recurring without connecting to any calendar server.
Uses mock event objects with icalendar data.

Usage:
    python recurrence_test.py
"""
import sys
sys.path.insert(0, ".")

from datetime import datetime, timedelta, timezone
from icalendar import Calendar, Event
from recurrence import expand_recurring


def _make_event(uid, title, start, end, rrule=None, exdate=None, rdate=None, all_day=False):
    """Create a mock CalDAV event object with icalendar data."""
    cal = Calendar()
    ev = Event()
    ev.add("uid", uid)
    ev.add("summary", title)

    if all_day:
        from icalendar import vDate
        ev.add("dtstart", vDate(start))
        ev.add("dtend", vDate(end))
    else:
        ev.add("dtstart", start)
        ev.add("dtend", end)

    if rrule:
        ev.add("rrule", rrule)
    if exdate:
        ev.add("exdate", exdate)
    if rdate:
        ev.add("rdate", rdate)

    cal.add_component(ev)

    # Create a mock event object with _data attribute
    class MockEvent:
        def __init__(self, data):
            self._data = cal.to_ical()
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

    # ── Non-recurring event ──
    print("Non-recurring event:")
    start = datetime(2026, 7, 14, 10, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 14, 11, 0, 0, tzinfo=timezone.utc)
    evt = _make_event("non-recurring-1", "Lunch", start, end)
    results = expand_recurring(evt, start, end + timedelta(days=1))
    assert_eq("non-recurring returns 1 result", len(results), 1)
    assert_eq("non-recurring title", results[0]["title"], "Lunch")

    # ── Weekly recurring event ──
    print("\nWeekly recurring event:")
    from dateutil.rrule import WEEKLY
    start = datetime(2026, 7, 7, 10, 30, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 7, 11, 0, 0, tzinfo=timezone.utc)
    query_start = datetime(2026, 7, 13, 0, 0, 0, tzinfo=timezone.utc)
    query_end = datetime(2026, 7, 22, 0, 0, 0, tzinfo=timezone.utc)
    evt = _make_event("weekly-1", "Weekly Meeting", start, end, {"FREQ": "WEEKLY"})
    results = expand_recurring(evt, query_start, query_end)
    assert_eq("weekly returns 2 occurrences", len(results), 2)
    dates = sorted([r["start"][:10] for r in results])
    assert_eq("weekly occurrence 1", dates[0], "2026-07-14")
    assert_eq("weekly occurrence 2", dates[1], "2026-07-21")

    # ── Daily recurring event ──
    print("\nDaily recurring event:")
    start = datetime(2026, 7, 14, 9, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 14, 9, 30, 0, tzinfo=timezone.utc)
    query_start = datetime(2026, 7, 14, 0, 0, 0, tzinfo=timezone.utc)
    query_end = datetime(2026, 7, 17, 0, 0, 0, tzinfo=timezone.utc)
    evt = _make_event("daily-1", "Standup", start, end, {"FREQ": "DAILY", "COUNT": 10})
    results = expand_recurring(evt, query_start, query_end)
    assert_eq("daily returns 3 occurrences", len(results), 3)

    # ── Same-day search for recurring event ──
    print("\nSame-day search for recurring event:")
    start = datetime(2026, 7, 7, 10, 30, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 7, 11, 0, 0, tzinfo=timezone.utc)
    query_start = datetime(2026, 7, 14, 0, 0, 0, tzinfo=timezone.utc)
    query_end = datetime(2026, 7, 15, 0, 0, 0, tzinfo=timezone.utc)
    evt = _make_event("weekly-2", "Swim Lessons", start, end, {"FREQ": "WEEKLY"})
    results = expand_recurring(evt, query_start, query_end)
    assert_eq("same-day recurring returns 1 occurrence", len(results), 1)
    assert_eq("same-day occurrence date", results[0]["start"][:10], "2026-07-14")
    assert_eq("same-day occurrence title", results[0]["title"], "Swim Lessons")

    # ── Recurring event with EXDATE ──
    print("\nRecurring event with EXDATE:")
    start = datetime(2026, 7, 14, 9, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 14, 9, 30, 0, tzinfo=timezone.utc)
    query_start = datetime(2026, 7, 14, 0, 0, 0, tzinfo=timezone.utc)
    query_end = datetime(2026, 7, 18, 0, 0, 0, tzinfo=timezone.utc)
    exdate = [datetime(2026, 7, 15, 9, 0, 0, tzinfo=timezone.utc)]
    evt = _make_event("daily-ex-1", "Daily Task", start, end, {"FREQ": "DAILY"}, exdate=exdate)
    results = expand_recurring(evt, query_start, query_end)
    # Daily from July 14, query 14-18 UTC midnight. Occurrences: 14, 15(excluded), 16, 17 = 3
    assert_eq("daily with exdate skips excluded day", len(results), 3)

    # ── All-day recurring event ──
    print("\nAll-day recurring event:")
    from datetime import date as ddate
    start_d = ddate(2026, 7, 14)
    end_d = ddate(2026, 7, 15)
    query_start = datetime(2026, 7, 14, 0, 0, 0, tzinfo=timezone.utc)
    query_end = datetime(2026, 7, 17, 0, 0, 0, tzinfo=timezone.utc)
    evt = _make_event("allday-1", "Vacation", start_d, end_d, {"FREQ": "DAILY"}, all_day=True)
    results = expand_recurring(evt, query_start, query_end)
    assert_eq("all-day recurring returns occurrences", len(results), 4)  # 14, 15, 16, 17

    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0

if __name__ == "__main__":
    if run_tests():
        sys.exit(0)
    else:
        sys.exit(1)
