"""Unit tests for datetime_parser module.

Tests parse_datetime and parse_datetime_for_ical without connecting to any server.

Usage:
    python test_datetime_parser.py
"""
import sys
sys.path.insert(0, ".")

from datetime_parser import parse_datetime, parse_datetime_for_ical, ParsedDateTime


def run_tests():
    """Run unit tests for the datetime parser."""
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

    # ── parse_datetime_for_ical (backward-compatible wrapper) ──
    print("parse_datetime_for_ical:")
    # Date-only → all-day event (no time, no Z)
    assert_eq("date-only", parse_datetime_for_ical("2026-07-19"), "20260719")
    assert_eq("date-only end", parse_datetime_for_ical("2026-07-24"), "20260724")
    # Naive datetime → treated as UTC
    assert_eq("naive datetime", parse_datetime_for_ical("2026-07-19T12:15:00"), "20260719T121500Z")
    assert_eq("naive midnight", parse_datetime_for_ical("2026-07-19T00:00:00"), "20260719T000000Z")
    # Timezone-aware → converted to UTC
    assert_eq("PDT to UTC", parse_datetime_for_ical("2026-07-19T12:15:00-07:00"), "20260719T191500Z")
    assert_eq("MDT to UTC", parse_datetime_for_ical("2026-07-19T15:51:00-06:00"), "20260719T215100Z")
    assert_eq("EDT to UTC", parse_datetime_for_ical("2026-07-19T23:17:00-04:00"), "20260720T031700Z")
    assert_eq("already UTC", parse_datetime_for_ical("2026-07-19T12:15:00Z"), "20260719T121500Z")
    assert_eq("positive offset", parse_datetime_for_ical("2026-07-19T12:00:00+05:30"), "20260719T063000Z")

    # ── parse_datetime (structured parser) ──
    print("\nparse_datetime:")
    # Date-only detection
    parsed = parse_datetime("2026-07-19")
    assert_eq("date-only is_date_only", parsed.is_date_only, True)
    assert_eq("date-only is_timezone_aware", parsed.is_timezone_aware, False)
    assert_eq("date-only date", str(parsed.date), "2026-07-19")
    assert_eq("date-only time", parsed.time, None)
    assert_eq("date-only datetime_utc", str(parsed.datetime_utc), "2026-07-19 00:00:00+00:00")
    assert_eq("date-only end_of_day", str(parsed.datetime_end_of_day_utc), "2026-07-20 00:00:00+00:00")

    # Naive datetime
    parsed = parse_datetime("2026-07-19T12:15:00")
    assert_eq("naive is_date_only", parsed.is_date_only, False)
    assert_eq("naive is_timezone_aware", parsed.is_timezone_aware, False)
    assert_eq("naive datetime_utc", str(parsed.datetime_utc), "2026-07-19 12:15:00+00:00")

    # Timezone-aware datetime
    parsed = parse_datetime("2026-07-19T12:15:00-07:00")
    assert_eq("tz-aware is_date_only", parsed.is_date_only, False)
    assert_eq("tz-aware is_timezone_aware", parsed.is_timezone_aware, True)
    assert_eq("tz-aware datetime_utc", str(parsed.datetime_utc), "2026-07-19 19:15:00+00:00")

    # Half-open interval for same-day search
    start = parse_datetime("2026-07-14")
    end = parse_datetime("2026-07-14")
    assert_eq("same-day start", str(start.datetime_utc), "2026-07-14 00:00:00+00:00")
    assert_eq("same-day end (next day)", str(end.datetime_end_of_day_utc), "2026-07-15 00:00:00+00:00")

    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    if run_tests():
        sys.exit(0)
    else:
        sys.exit(1)
