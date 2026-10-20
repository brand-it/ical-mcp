"""Unit tests for calendar MCP server.

Tests server-side logic without connecting to any calendar server.

Usage:
    python server_test.py
"""
import sys
sys.path.insert(0, ".")

from datetime_parser import parse_datetime, parse_datetime_for_ical, ParsedDateTime


def run_tests():
    """Run unit tests for server logic."""
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
    assert_eq("date-only", parse_datetime_for_ical("2026-07-19"), "20260719")
    assert_eq("date-only end", parse_datetime_for_ical("2026-07-24"), "20260724")
    assert_eq("naive datetime", parse_datetime_for_ical("2026-07-19T12:15:00"), "20260719T121500Z")
    assert_eq("naive midnight", parse_datetime_for_ical("2026-07-19T00:00:00"), "20260719T000000Z")
    assert_eq("PDT to UTC", parse_datetime_for_ical("2026-07-19T12:15:00-07:00"), "20260719T191500Z")
    assert_eq("MDT to UTC", parse_datetime_for_ical("2026-07-19T15:51:00-06:00"), "20260719T215100Z")
    assert_eq("EDT to UTC", parse_datetime_for_ical("2026-07-19T23:17:00-04:00"), "20260720T031700Z")
    assert_eq("already UTC", parse_datetime_for_ical("2026-07-19T12:15:00Z"), "20260719T121500Z")
    assert_eq("positive offset", parse_datetime_for_ical("2026-07-19T12:00:00+05:30"), "20260719T063000Z")

    # ── Date range parsing (list_events / search_events) ──
    print("\nDate range parsing (half-open interval):")
    # Same-day search
    start = parse_datetime("2026-07-14")
    end = parse_datetime("2026-07-14")
    assert_eq("same-day start", str(start.datetime_utc), "2026-07-14 00:00:00+00:00")
    assert_eq("same-day end (next day)", str(end.datetime_end_of_day_utc), "2026-07-15 00:00:00+00:00")

    # Multi-day search
    start = parse_datetime("2026-07-14")
    end = parse_datetime("2026-07-20")
    assert_eq("multi-day start", str(start.datetime_utc), "2026-07-14 00:00:00+00:00")
    assert_eq("multi-day end (next day)", str(end.datetime_end_of_day_utc), "2026-07-21 00:00:00+00:00")

    # ── Structured parser ──
    print("\nparse_datetime structured parser:")
    parsed = parse_datetime("2026-07-19")
    assert_eq("date-only is_date_only", parsed.is_date_only, True)
    assert_eq("date-only is_timezone_aware", parsed.is_timezone_aware, False)
    assert_eq("date-only date", str(parsed.date), "2026-07-19")
    assert_eq("date-only time", parsed.time, None)

    parsed = parse_datetime("2026-07-19T12:15:00-07:00")
    assert_eq("tz-aware is_date_only", parsed.is_date_only, False)
    assert_eq("tz-aware is_timezone_aware", parsed.is_timezone_aware, True)
    assert_eq("tz-aware datetime_utc", str(parsed.datetime_utc), "2026-07-19 19:15:00+00:00")

    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    if run_tests():
        sys.exit(0)
    else:
        sys.exit(1)
