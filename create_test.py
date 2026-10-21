"""Unit tests for create_event / update_event tool logic (no network).

Monkeypatches the CalDAV layer so the real tool code runs against fakes that
capture exactly what would be stored. Run:
    python create_test.py
"""
import sys
sys.path.insert(0, ".")

from datetime import date, datetime, timezone
from icalendar import Calendar
from caldav.lib import error as _caldav_error

import server
from helpers import event_to_dict


class FakeCalendar:
    """Captures add_event calls; supports the time-range lookup the
    update/delete paths use (client.get_events -> match on event.id).
    The fake ignores the date window and returns all stored events; the
    server filters by UID in memory, so the window is irrelevant here."""

    def __init__(self, by_uid=None):
        self.stored = []
        self._by_uid = by_uid or {}

    def add_event(self, event):
        self.stored.append(event)
        return event

    def get_events(self, calendar, start=None, end=None):
        return list(self._by_uid.values())


class FakeEvent:
    """Mimics a caldav Event: settable _data, save() records the payload,
    and icalendar_component exposes the parsed VEVENT (as the real caldav
    Event does, which update_event reads to apply in-place edits)."""

    def __init__(self, data):
        self._data = data
        self.saved = []
        self._comp = None
        for c in Calendar.from_ical(data).walk():
            if c.name == "VEVENT":
                self._comp = c
                break
        self.id = str(self._comp.get("uid", ""))

    @property
    def icalendar_component(self):
        # Real caldav caches the in-memory component; save() serializes this
        # same object, so in-place edits from apply_update() persist.
        return self._comp

    def save(self):
        cal = Calendar()
        cal.add_component(self._comp)
        self._data = cal.to_ical()
        self.saved.append(self._data)


def _make_fake_event(ical_bytes):
    return FakeEvent(ical_bytes)


def _vevent_from_event(event):
    """Extract the VEVENT component from a captured icalendar Event."""
    cal = Calendar.from_ical(event.to_ical())
    for c in cal.walk():
        if c.name == "VEVENT":
            return c
    raise RuntimeError("no VEVENT")


passed = 0
failed = 0


def check(label, got, expected):
    global passed, failed
    if got == expected:
        print(f"  ✓ {label}")
        passed += 1
    else:
        print(f"  ✗ {label}: got {got!r}, expected {expected!r}")
        failed += 1


def check_true(label, cond):
    check(label, bool(cond), True)


# ── create_event ──────────────────────────────────────────────────────────────


def test_create_all_day():
    print("\ncreate_event (all-day, one day):")
    cal = FakeCalendar()
    server.get_client = lambda: object()
    server.get_calendar = lambda client: cal

    result = server.create_event("Picture Day", "2026-10-26", all_day=True)
    check_true("one event stored", len(cal.stored) == 1)
    ev = _vevent_from_event(cal.stored[0])
    check("DTSTART", str(ev.get("dtstart").dt), "2026-10-26")
    check("DTEND exclusive", str(ev.get("dtend").dt), "2026-10-27")
    check("UID present", bool(str(ev.get("uid", ""))), True)
    check("categories", "ALL-DAY-EVENT" in str(ev.get("categories")), True)
    check(
        "DTSTART has VALUE=DATE param",
        "DATE" in str(ev.get("dtstart").params),
        True,
    )
    check("feedback has title", "Picture Day" in result, True)
    check("feedback has day", "2026-10-26" in result, True)
    check("feedback has weekday", "Monday" in result, True)
    check("feedback has UID", str(ev.get("uid")) in result, True)


def test_create_all_day_multiday():
    print("\ncreate_event (all-day, multi-day):")
    cal = FakeCalendar()
    server.get_calendar = lambda client: cal
    server.create_event("Fall Break", "2026-10-26", end="2026-10-28", all_day=True)
    ev = _vevent_from_event(cal.stored[0])
    check("DTSTART", str(ev.get("dtstart").dt), "2026-10-26")
    check("DTEND exclusive (last day + 1)", str(ev.get("dtend").dt), "2026-10-29")


def test_create_timed_naive_local():
    print("\ncreate_event (timed, naive -> local tz):")
    cal = FakeCalendar()
    server.get_calendar = lambda client: cal
    result = server.create_event("Standup", "2026-10-26T10:00:00", tz="America/Los_Angeles")
    ev = _vevent_from_event(cal.stored[0])
    start = ev.get("dtstart").dt
    # 10:00 PDT (UTC-7) = 17:00 UTC
    check("stored UTC hour", start.hour, 17)
    check("stored UTC date", start.date().isoformat(), "2026-10-26")
    end = ev.get("dtend").dt
    check("default end +1h UTC", end.hour, 18)
    check_true("UID present", bool(str(ev.get("uid", ""))))
    check("feedback shows local time", "10:00" in result, True)
    check("feedback shows tz label", "America/Los_Angeles" in result, True)


def test_create_timed_aware():
    print("\ncreate_event (timed, tz-aware):")
    cal = FakeCalendar()
    server.get_calendar = lambda client: cal
    server.create_event("Call", "2026-10-26T10:00:00-07:00", end="2026-10-26T11:00:00-07:00")
    ev = _vevent_from_event(cal.stored[0])
    check("start UTC hour", ev.get("dtstart").dt.hour, 17)
    check("end UTC hour", ev.get("dtend").dt.hour, 18)


def test_create_rejects_bad_end():
    print("\ncreate_event (end before start):")
    cal = FakeCalendar()
    server.get_calendar = lambda client: cal
    try:
        server.create_event("Bad", "2026-10-26T10:00:00", end="2026-10-26T09:00:00")
        check("raised ValueError", False, True)
    except ValueError:
        check("raised ValueError", True, True)
    check("nothing stored", len(cal.stored), 0)


# ── update_event ──────────────────────────────────────────────────────────────


def _existing_event():
    """A timed event as it would exist on the server."""
    from icalendar import Event

    ev = Event()
    ev.add("uid", "uid-123")
    ev.add("summary", "Old")
    ev.add("dtstart", datetime(2026, 10, 26, 17, 0, 0, tzinfo=timezone.utc))
    ev.add("dtend", datetime(2026, 10, 26, 18, 0, 0, tzinfo=timezone.utc))
    cal = Calendar()
    cal.add_component(ev)
    return _make_fake_event(cal.to_ical())


def test_update_move_timed():
    print("\nupdate_event (move timed, naive -> local):")
    fake = _existing_event()
    cal = FakeCalendar(by_uid={"uid-123": fake})
    server.get_client = lambda: cal
    server.get_calendar = lambda client: cal
    result = server.update_event("uid-123", start="2026-10-27T09:00:00", tz="America/Los_Angeles")
    check_true("saved", len(fake.saved) == 1)
    d = event_to_dict(fake, tz_name="America/Los_Angeles")
    check("new start local", d["start"], "2026-10-27T09:00:00-07:00")
    check("title unchanged", d["title"], "Old")
    check("feedback has new day", "2026-10-27" in result, True)


def test_update_to_all_day():
    print("\nupdate_event (convert to all-day, start only):")
    fake = _existing_event()
    cal = FakeCalendar(by_uid={"uid-123": fake})
    server.get_client = lambda: cal
    server.get_calendar = lambda client: cal
    result = server.update_event("uid-123", start="2026-10-26", all_day=True)
    d = event_to_dict(fake, tz_name="America/Los_Angeles")
    check("all_day flag", d["all_day"], True)
    check("start day", d["start"], "2026-10-26")
    check("end = last inclusive day", d["end"], "2026-10-26")
    check("feedback says all-day", "All-day" in result, True)


def test_update_all_day_multiday():
    print("\nupdate_event (all-day multi-day):")
    fake = _existing_event()
    cal = FakeCalendar(by_uid={"uid-123": fake})
    server.get_client = lambda: cal
    server.get_calendar = lambda client: cal
    server.update_event("uid-123", start="2026-10-26", end="2026-10-28", all_day=True)
    d = event_to_dict(fake, tz_name="America/Los_Angeles")
    check("start", d["start"], "2026-10-26")
    check("end last inclusive", d["end"], "2026-10-28")


def test_update_not_found():
    print("\nupdate_event (missing UID):")
    cal = FakeCalendar()
    server.get_client = lambda: cal
    server.get_calendar = lambda client: cal
    try:
        server.update_event("nope", title="x")
        check("raised RuntimeError", False, True)
    except RuntimeError:
        check("raised RuntimeError", True, True)


def test_is_412_detection():
    print("\n_is_412 (typed 412 detection):")
    # The typed 412 exceptions caldav raises for a stale If-Match /
    # If-Schedule-Tag-Match must be detected by type, not by string-matching.
    # These attributes only exist on caldav that ships the typed 412 classes
    # (absent at the requirements.txt floor of 1.3.0). Guard each so the suite
    # stays floor-safe, mirroring server.py's own getattr-based degradation.
    typed_412 = [
        "ETagMismatchError",
        "ScheduleTagMismatchError",
        "UidConflictETagMismatchError",
        "UidConflictScheduleTagMismatchError",
    ]
    for name in typed_412:
        cls = getattr(_caldav_error, name, None)
        if cls is None:
            print(f"  (skip {name}: not present in this caldav version)")
            continue
        check_true(f"{name} is 412", server._is_412(cls(url="x", reason="no reason")))
    # A bare PutError whose message carries the 412 status (older caldav) ->
    # detected via the fallback.
    check_true("bare PutError w/ 412 in msg", server._is_412(_caldav_error.PutError(url="412 Precondition Failed", reason="no reason")))
    # A non-412 error must NOT be treated as 412.
    check("NotFoundError is not 412", server._is_412(_caldav_error.NotFoundError(url="x", reason="no reason")), False)
    check("generic RuntimeError is not 412", server._is_412(RuntimeError("boom")), False)


def run():
    test_create_all_day()
    test_create_all_day_multiday()
    test_create_timed_naive_local()
    test_create_timed_aware()
    test_create_rejects_bad_end()
    test_update_move_timed()
    test_update_to_all_day()
    test_update_all_day_multiday()
    test_update_not_found()
    test_is_412_detection()
    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    import time

    start = time.time()
    ok = run()
    print(f"Elapsed: {time.time() - start:.2f}s")
    sys.exit(0 if ok else 1)
