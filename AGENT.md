# AGENT.md — ical-mcp

Read this before working on the project. It explains how the system works end to end, the invariants that must hold, and how to verify your changes. The quick task reference is `skills/ical-mcp/SKILL.md`; this file is the deeper explanation.

## What this is

`ical-mcp` is an **MCP (Model Context Protocol) server** that manages calendar events over **CalDAV**. It is built for iCloud (`https://caldav.icloud.com/`) but works with any CalDAV backend. It exposes five tools — `create_event`, `list_events`, `search_events`, `update_event`, `delete_event` — and serves the **streamable-HTTP** MCP transport on port `13081`.

An MCP *client* (an LLM harness, an MCP gateway, LibreChat, etc.) connects to the server's URL and calls the tools. The server talks to the CalDAV server using the `caldav` Python library. This repo is the *server*; it is not a client.

## Data flow

```
MCP client (gateway / LLM)
   │  streamable-HTTP, POST /mcp
   ▼
server.py  ── FastMCP("icloud-calendar") ── @mcp.tool() functions
   │  caldav.DAVClient (CALDAV_URL, CALDAV_USER, CALDAV_PASSWORD)
   ▼
CalDAV server (iCloud)  ──  a named calendar (or the test calendar)
```

- On startup `server.py` authenticates, resolves the target calendar, logs `Connected to calendar: <name>`, and **exits 1** if that fails. This is what makes the container's healthcheck meaningful — bad credentials = unhealthy container, not a silent running process.
- Each tool call is a fresh `get_client()` (a new `DAVClient`). There is no long-lived connection pool.
- Reads (`list_events`, `search_events`) fetch events in a half-open `[start, end)` window and run each through `expand_recurring`, so recurring events appear on their actual occurrence dates.
- `update_event` mutates the live `icalendar_component` in place and calls `save()`. (caldav serializes the in-memory component, not a re-parsed copy — see the comment at the `apply_update` call site. Editing a re-parsed copy and reassigning `_data` would silently never persist.)

## The five tools (behavior contract)

| Tool | Required | Notes |
|---|---|---|
| `create_event` | `title`, `start` | `end` optional (timed defaults to start+1h). `all_day: true` + date-only values for all-day. `attendees` is a list of emails, stored as `mailto:`. Returns a confirmation string containing the new **uid**. |
| `update_event` | `uid` | Only the fields you pass change. Pass `all_day` to convert timed↔all-day. **`attendees` is not updatable.** |
| `delete_event` | `uid` | Irreversible. |
| `list_events` | `start_date`, `end_date` | Half-open `[start, end)`; `end_date` is midnight of the next day. Optional `tz`. |
| `search_events` | `query` | Matches title, description, or location (case-insensitive). Optional date range + `tz`. |

Every event dict returned to the client has: `uid`, `title`, `start`, `end`, `all_day`, `day_of_week`, `day_of_week_end`, `timezone`, `description`, `location`. **`uid` is the only stable handle** — always resolve it via a read before update/delete.

## Invariants that must hold

These are the load-bearing constraints. Breaking any of them is a bug, not a style choice.

1. **Test-mode isolation.** `TEST_CAL` defaults to **on**. When on, *every* read and write is routed to the calendar named by `TEST_CAL_NAME` (default `Test Calendar`), never the real calendar. `update_event` and `delete_event` look the event up **only in the target calendar** (`get_calendar(client).event_by_uid(uid)`), never by scanning all calendars. A UID collision must never let a test-mode call touch a real-calendar event. If you add an all-calendars search, you break this.
2. **All-day storage.** All-day events are stored with `VALUE=DATE` on DTSTART/DTEND and tagged `CATEGORIES: ALL-DAY-EVENT`. The `VALUE=DATE` parameter is *required*: iCloud reinterprets a bare date as a floating datetime on the *previous* day at 23:00, shifting the event back a day. `set_all_day_dates` in `helpers.py` owns this encoding — do not hand-roll DATE construction elsewhere.
3. **Naive-time interpretation.** A timed input with no offset is interpreted in `tz` (default `America/Los_Angeles`), **not** UTC. A value with an offset is stored as-is. This is the single most common way to get a wrong time.
4. **No secrets in the repo.** Credentials are runtime env vars only (`CALDAV_USER`, `CALDAV_PASSWORD`, `CALDAV_URL`, `CALENDAR_NAME`, `MCP_HOST`, `MCP_PORT`, `TEST_CAL`, `TEST_CAL_NAME`). The source must build and run without any credential baked in.
5. **Recurring is read-only.** `create_event` takes no recurrence rule, and iCloud rejects creating new RRULE events via CalDAV (PUT → 403). Recurring events are only *expanded on read* by `expand_recurring`. Don't add a recurrence param to create.

## The build → image → deploy pipeline

The whole point of this setup: **the NAS never builds the image.** It pulls from GitHub Container Registry (GHCR), which GitHub Actions builds on every push to `main`.

```
edit in ~/apps/ical-mcp
   │  git commit + push origin main
   ▼
GitHub Actions (.github/workflows/build-and-publish.yml)
   ├─ test job: runs all 6 *_test.py (must pass)
   └─ docker job (needs: test): builds + pushes to GHCR
          ├─ ghcr.io/brand-it/ical-mcp:latest   (main only)
          └─ ghcr.io/brand-it/ical-mcp:vX.Y.Z   (from a v* tag)
   ▼
NAS: docker compose pull calendar-mcp + up -d --force-recreate + restart mcp-gateway
```

Key mechanics:
- **`latest` only updates on `main`.** The workflow's metadata tag uses `enable={{is_default_branch}}`, so pushing a `v*` tag publishes a versioned image *without* clobbering `latest`. If you tag older code, `latest` is safe.
- **The compose service is `image:`-only.** `calendar-mcp` in `docker-compose.yml` has `image: ghcr.io/brand-it/ical-mcp:latest` and **no `build:` block**. If you see a `build:` block there, it's a regression to the old local-build flow.
- **NAS GHCR auth** lives in `/tmp/docker/config.json` on the NAS (the `DOCKER_CONFIG` override), a `ghcr.io` entry with `auth` = base64(`brand-it:<github-token>`). The token needs `packages: read`, or the package must be public. If pulls 401/403, regenerate that file (see the skill).
- **Tag a release** with `git tag vX.Y.Z && git push origin vX.Y.Z` to publish a versioned image.

## Testing

The test suite runs **without any CalDAV connection** — the network layer is mocked. This is deliberate: it makes the suite fast, deterministic, and safe to run anywhere (including CI). Six scripts, each exits non-zero on failure and prints `Results: N passed, M failed`:

- `datetime_parser_test.py` — the parser (date-only / naive / aware, UTC + end-of-day + iCal conversion).
- `helpers_test.py` — `event_to_dict`, all-day detection, `set_all_day_dates`, `apply_update`.
- `recurrence_test.py` — `expand_recurring` (RRULE/RDATE/EXDATE).
- `server_test.py` — server-level logic.
- `update_test.py` — the update path.
- `create_test.py` — create + update tool logic.

`create_test.py` and `update_test.py` monkeypatch `server.get_client` and `server.get_calendar` (the latter returns a `FakeCalendar` whose `event_by_uid` returns a captured `FakeEvent`). **If you change how `server.py` resolves the calendar or looks up an event, update these mocks in the same change** — they encode the current lookup contract (scoped `event_by_uid`, not an all-calendars scan).

Run locally: `pip install -r requirements.txt` then run each script. CI runs the same loop in the `test` job; it is a hard gate on the `docker` job, so a broken test blocks the image build.

## Known quirks (iCloud-specific)

- **DATE → DATETIME read-back.** iCloud returns all-day events as a DATETIME at midnight UTC on read-back. The server detects the `ALL-DAY-EVENT` category and displays these as date-only.
- **412 Precondition Failed on update.** Happens when the event's etag changed since it was read (another client modified it). `update_event` re-reads, re-applies, and saves, retrying up to 3 times. Most updates succeed on the first save.
- **Recurring create → 403.** Covered above; expected, not a bug.
- **First-connect reset.** A transient `ConnectionResetError` on the very first CalDAV connect is retried internally. If the MCP gateway dialed the upstream before the server finished coming up, the gateway's calendar client stays down until `docker compose restart mcp-gateway`.

## How to verify a change

1. **Unit level:** run the affected `*_test.py` (or the whole loop). Green is required.
2. **Image level:** push to `main`, watch the Actions run (`gh run watch`). Both `test` and `docker` must be green; confirm the image landed in GHCR.
3. **Deploy level:** on the NAS, `docker compose pull calendar-mcp && docker compose up -d --force-recreate calendar-mcp && docker compose restart mcp-gateway`. Confirm the container is `healthy` and the log shows `Connected to calendar: <name>`.
4. **End-to-end:** through the live MCP tools (`xd://mcp__calendar_*`), do a create → read → update → delete cycle against the **Test Calendar** (test mode is on by default, so the real calendar is untouched). Confirm the read-back matches what you created, then delete the test event so the calendar is left clean.

## File layout

```
ical-mcp/
├── AGENT.md                 # this file
├── README.md                # public user docs (usage, config, build/deploy)
├── Dockerfile
├── requirements.txt
├── .gitignore
├── .github/workflows/build-and-publish.yml   # CI: test + build/push to GHCR
├── skills/ical-mcp/SKILL.md # quick task reference (mirrors the installed OMP skill)
├── server.py                # MCP tools + CalDAV client (entry point)
├── datetime_parser.py       # ISO 8601 parser
├── helpers.py               # event formatting / update application
├── recurrence.py            # recurring expansion
└── *_test.py                # unit tests (no network)
```
