# ical-mcp

An MCP (Model Context Protocol) server that manages calendar events over CalDAV. Built for iCloud, but works with any CalDAV server. Exposes tools for creating, listing, searching, updating, and deleting events.

Serves the **streamable-HTTP** MCP transport (default port `13081`).

## Tools

| Tool | Description |
|---|---|
| `create_event` | Create a new event. Pass `all_day: true` and date-only values for all-day events. |
| `list_events` | List events in a half-open `[start, end)` date range. |
| `search_events` | Search events by a query string (title, description, or location). |
| `update_event` | Update an event by UID. Only the fields you pass change. Pass `all_day` to convert between timed and all-day. |
| `delete_event` | Delete an event by UID. |

## Quick start (Docker)

```bash
docker run -d \
  --name ical-mcp \
  -e CALDAV_URL=https://caldav.icloud.com/ \
  -e CALDAV_USER=you@icloud.com \
  -e CALDAV_PASSWORD=xxxx-xxxx-xxxx-xxxx \
  -p 13081:13081 \
  ghcr.io/brand-it/ical-mcp:latest
```

The server logs `Connected to calendar: <name>` on a successful CalDAV connection and exits with status 1 otherwise.

## Configuration

All settings are environment variables:

| Variable | Required | Default | Description |
|---|---|---|---|
| `CALDAV_URL` | yes | `https://caldav.icloud.com/` | CalDAV server URL. |
| `CALDAV_USER` | yes | — | CalDAV username (e.g. your iCloud email). |
| `CALDAV_PASSWORD` | yes | — | Password or app-specific password. |
| `CALENDAR_NAME` | no | first available | Calendar to use. Unset = the first calendar on the account. |
| `MCP_HOST` | no | `0.0.0.0` | MCP server bind address. |
| `MCP_PORT` | no | `13081` | MCP server port. |
| `TEST_CAL` | no | `true` | When `true`, all reads/writes route to the test calendar instead of the real one. Set `false` to operate on the real calendar. |
| `TEST_CAL_NAME` | no | `Test Calendar` | Name of the test calendar used when `TEST_CAL` is on. |

### Test mode

`TEST_CAL` defaults to **on** so the server never touches your real calendar by accident. When on, it routes every read and write to the calendar named by `TEST_CAL_NAME`. Set `TEST_CAL=false` to work against your real calendar (selected by `CALENDAR_NAME`, or the first one if unset).

## Timezone handling

- **Timed events** — pass ISO 8601. A value with an offset (e.g. `2026-07-19T12:15:00-07:00`) is stored as-is. A value **without** an offset is interpreted in the `tz` parameter (default `America/Los_Angeles`), not UTC. Omitting `end` defaults to start + 1 hour.
- **All-day events** — pass `all_day: true` with date-only values (e.g. `start: "2026-07-19"`, `end: "2026-07-24"`). `end` is the **last** day of the event; the server stores the exclusive iCal end automatically. The event is tagged `CATEGORIES: ALL-DAY-EVENT` so it displays as date-only on read-back.
- **Output** — times are returned in the `tz` you pass (default `America/Los_Angeles`). Each result includes `day_of_week`, `day_of_week_end`, and `timezone`.

## Recurring events

Recurring events are **read-only** through this server: `list_events` and `search_events` expand RRULE/RDATE/EXDATE so each occurrence appears on its actual date. `create_event` does not accept a recurrence rule. (iCloud also rejects creating new RRULE events via CalDAV.)

## Connecting an MCP client

The server speaks streamable-HTTP. Point your client at the base URL:

```
http://<host>:13081/
```

For example, an MCP client config entry:

```json
{
  "mcpServers": {
    "calendar": {
      "url": "http://localhost:13081/"
    }
  }
}
```

## Build & deploy

### From the prebuilt image (recommended)

Pushing to `main` builds and publishes the image to GitHub Container Registry. Pull it:

```bash
docker pull ghcr.io/brand-it/ical-mcp:latest
```

`latest` tracks the newest `main` build. Tagged releases are also published (e.g. `ghcr.io/brand-it/ical-mcp:v1.0.0`).

### Build locally

```bash
docker build -t ical-mcp .
```

## Testing

The test suite runs without any CalDAV connection (the network layer is mocked):

```bash
python3 datetime_parser_test.py   # parser module
python3 helpers_test.py           # event formatting
python3 recurrence_test.py        # recurring expansion
python3 server_test.py            # server-level logic
python3 update_test.py            # update path
python3 create_test.py            # create + update tool logic
```

Each script prints `Results: N passed, M failed` and exits non-zero on failure.

## Architecture

| Module | Purpose |
|---|---|
| `server.py` | MCP tools (create, list, search, update, delete) + CalDAV client. |
| `datetime_parser.py` | Universal ISO 8601 parser; `ParsedDateTime` struct with UTC / end-of-day / iCal helpers. |
| `helpers.py` | Event formatting (`event_to_dict`), all-day detection, update application, serialization. |
| `recurrence.py` | Recurring event expansion (RRULE/RDATE/EXDATE). |

### `parse_datetime(value: str) -> ParsedDateTime`

Returns a structured object with format detection:
- `is_date_only` — True for date-only strings
- `is_timezone_aware` — True if the input had timezone info
- `datetime_utc` — UTC datetime (midnight for date-only)
- `datetime_end_of_day_utc` — midnight of the next day (for half-open intervals)
- `to_ical()` — iCal-formatted string

### `expand_recurring(event, start_dt, end_dt, tz_name)`

Expands a recurring event into individual occurrences within `[start_dt, end_dt)`. Handles RRULE, RDATE, and EXDATE; non-recurring events return as a single item. Used by `list_events` and `search_events`.

## Known quirks

### iCloud DATE → DATETIME read-back

iCloud returns all-day events as a DATETIME at midnight UTC on read-back. The server detects the `ALL-DAY-EVENT` category and displays these as date-only. All-day events are also stored with the `VALUE=DATE` parameter on DTSTART/DTEND, which is required — iCloud otherwise shifts a bare date back by one day.

### 412 Precondition Failed

iCloud returns 412 when an event's etag changed since it was read (another client modified it). `update_event` re-reads the event, re-applies the changes, and saves, retrying up to 3 times. Most updates succeed on the first save.

## File structure

```
ical-mcp/
├── Dockerfile               # Container build
├── README.md                # This file
├── requirements.txt         # Python dependencies
├── server.py                # MCP tools (main entry point)
├── datetime_parser.py       # Universal ISO 8601 parser
├── helpers.py               # Event formatting utilities
├── recurrence.py            # Recurring event expansion
├── datetime_parser_test.py  # Parser tests
├── helpers_test.py          # Helpers tests
├── recurrence_test.py       # Recurrence tests
├── server_test.py           # Server-level tests
├── update_test.py           # Update-path tests
└── create_test.py           # Create + update tool tests
```
