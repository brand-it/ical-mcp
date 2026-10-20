from __future__ import annotations
"""Universal ISO 8601 date/time parser.

Parses date-only, naive datetime, and timezone-aware datetime strings
into a structured ParsedDateTime object with conversion helpers.

Usage:
    from datetime_parser import parse_datetime, parse_datetime_for_ical

    parsed = parse_datetime("2026-07-19")
    print(parsed.is_date_only)  # True
    print(parsed.datetime_utc)  # 2026-07-19 00:00:00+00:00
    print(parsed.datetime_end_of_day_utc)  # 2026-07-20 00:00:00+00:00
    print(parsed.to_ical())  # "20260719"

    # Backward-compatible thin wrapper
    print(parse_datetime_for_ical("2026-07-19T12:15:00-07:00"))  # "20260719T191500Z"
"""
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass


@dataclass
class ParsedDateTime:
    """Structured result from parsing an ISO 8601 date/time string.

    Attributes:
        is_date_only: True if input was a date-only string (e.g. "2026-07-19").
        is_timezone_aware: True if input had timezone info.
        date: The parsed date component.
        time: The parsed time component (None for date-only).
        tz: The timezone (None for date-only or naive datetime).
        raw: The original input string.
    """
    is_date_only: bool
    is_timezone_aware: bool
    date: date | None = None
    time: datetime | None = None
    tz: timezone | None = None
    raw: str = ""

    @property
    def datetime_utc(self) -> datetime:
        """Return as a timezone-aware UTC datetime.

        For date-only, returns midnight UTC of that date.
        For naive datetime, treats as UTC.
        For timezone-aware, converts to UTC.
        """
        if self.is_date_only:
            return datetime(self.date.year, self.date.month, self.date.day, tzinfo=timezone.utc)
        dt = self.time
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @property
    def datetime_end_of_day_utc(self) -> datetime:
        """Return midnight UTC of the day AFTER this date.

        Use for half-open interval [start, end) end boundaries.
        """
        if self.is_date_only:
            return datetime(self.date.year, self.date.month, self.date.day,
                           tzinfo=timezone.utc) + timedelta(days=1)
        dt = self.datetime_utc
        return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc) + timedelta(days=1)

    def to_ical(self) -> str:
        """Convert to iCal-formatted string.

        Date-only → "20260719" (all-day event)
        Datetime → "20260719T191500Z" (UTC)
        """
        if self.is_date_only:
            return self.date.strftime('%Y%m%d')
        utc = self.datetime_utc
        return utc.strftime('%Y%m%dT%H%M%SZ')


def parse_datetime(value: str) -> ParsedDateTime:
    """Parse an ISO 8601 date/time string into a structured ParsedDateTime.

    Handles three cases:
    - Date-only (e.g. "2026-07-19") → is_date_only=True
    - Naive datetime (e.g. "2026-07-19T12:15:00") → is_timezone_aware=False
    - Timezone-aware (e.g. "2026-07-19T12:15:00-07:00") → is_timezone_aware=True

    Args:
        value: ISO 8601 date or datetime string.

    Returns:
        ParsedDateTime with format detection and conversion helpers.
    """
    # Date-only detection: no 'T', no '+', at most 2 hyphens
    if "T" not in value and "+" not in value and value.count("-") <= 2:
        d = date.fromisoformat(value)
        return ParsedDateTime(
            is_date_only=True,
            is_timezone_aware=False,
            date=d,
            raw=value,
        )
    parsed = datetime.fromisoformat(value)
    return ParsedDateTime(
        is_date_only=False,
        is_timezone_aware=parsed.tzinfo is not None,
        date=parsed.date(),
        time=parsed,
        tz=parsed.tzinfo,
        raw=value,
    )


def parse_datetime_for_ical(value: str) -> str:
    """Parse an ISO 8601 date/time string and return an iCal-formatted value.

    Backward-compatible wrapper around parse_datetime().to_ical().

    Handles three cases:
    - Date-only (e.g. "2026-07-19") → iCal DATE format (all-day event)
    - Naive datetime (e.g. "2026-07-19T12:15:00") → treated as UTC, converted to iCal UTC format
    - Timezone-aware datetime (e.g. "2026-07-19T12:15:00-07:00") → converted to UTC, iCal UTC format

    Args:
        value: ISO 8601 date or datetime string.

    Returns:
        iCal-formatted string (e.g. "20260719" for date-only, "20260719T191500Z" for datetime).
    """
    return parse_datetime(value).to_ical()
