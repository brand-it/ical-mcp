---
name: ical-mcp
description: How to work on the ical-mcp project — the CalDAV/iCloud Calendar MCP server. Covers the repo, the GitHub Actions build→GHCR pipeline, NAS deployment, testing, architecture, and known quirks. Read before editing source, changing the Dockerfile/workflow, or redeploying to the NAS.
---

# ical-mcp (CalDAV/iCloud Calendar MCP server)

The MCP server that manages calendar events over CalDAV (iCloud, or any CalDAV backend). Serves the **streamable-HTTP** MCP transport on port `13081`. This skill is for *maintaining* the project — for *using* the live tools, see the `calendar` skill.

## Locations

| What | Where |
|---|---|
| Source of truth (git) | `~/apps/ical-mcp` |
| GitHub repo | `https://github.com/brand-it/ical-mcp` (public) |
| Docker image | `ghcr.io/brand-it/ical-mcp:latest` (main) / `:vX.Y.Z` (tags) |
| NAS compose | `/mnt/Container/docker-compose.yml` (service `calendar-mcp`) |
| NAS GHCR auth | `/tmp/docker/config.json` on the NAS (see Deploy) |
| Live MCP tools | `xd://mcp__calendar_*` via the `mcp.lareaus.com` gateway |

## Modules

| File | Role |
|---|---|
| `server.py` | MCP tools (create/list/search/update/delete) + CalDAV client + startup connect-check. |
| `datetime_parser.py` | ISO 8601 parser; `ParsedDateTime` (UTC / end-of-day / iCal helpers). |
| `helpers.py` | `event_to_dict`, all-day detection, `apply_update`, serialization, `set_all_day_dates`. |
| `recurrence.py` | `expand_recurring` — RRULE/RDATE/EXDATE expansion for reads. |
| `Dockerfile` | `python:3.13-slim`, copies the 4 modules, `CMD ["python","server.py"]`. |
| `*_test.py` | Unit tests (no network; the CalDAV layer is mocked). Each exits non-zero on failure. |

## Deploy flow (the normal path)

Push to `main` and the GitHub Actions workflow (`.github/workflows/build-and-publish.yml`) does the rest:

1. `test` job runs the full suite (all 6 `*_test.py`). It must pass.
2. `docker` job (needs `test`) builds and pushes the image to GHCR:
   - `latest` — only on `main` (`enable={{is_default_branch}}`), so a tag push can't clobber it.
   - `vX.Y.Z` — from a `v*` tag.

Then pull on the NAS (no local build anymore):

```bash
ssh -p 42 -o BatchMode=yes newdark@192.168.50.123 \
  'export PATH=/share/ZFS530_DATA/.qpkg/container-station/bin:$PATH; \
   export DOCKER_CONFIG=/tmp/docker; \
   cd /share/Container/home/ && \
   docker compose pull calendar-mcp && \
   docker compose up -d --force-recreate calendar-mcp && \
   docker compose restart mcp-gateway'
```

The compose service is `image: ghcr.io/brand-it/ical-mcp:latest` — there is **no** `build:` block. To change the code: edit in `~/apps/ical-mcp`, commit, push to `main`, wait for CI green, then run the pull/recreate above.

### GHCR auth on the NAS

The NAS docker authenticates to GHCR via `/tmp/docker/config.json` (the `DOCKER_CONFIG` override). It holds a `ghcr.io` entry with `auth` = base64(`brand-it:<github-token>`). If a pull fails with 401/403, regenerate it:

```bash
TOKEN=$(gh auth token); AUTH=$(echo -n "brand-it:$TOKEN" | base64 -w0)
ssh -p 42 -o BatchMode=yes newdark@192.168.50.123 \
  "export DOCKER_CONFIG=/tmp/docker; printf '%s' '{\"auths\":{\"ghcr.io\":{\"auth\":\"$AUTH\"}}}' > /tmp/docker/config.json"
```

The `gh` token needs `packages: read` (or the package must be public) to pull. See the note on package visibility below.

## Testing

No CalDAV connection needed — the network layer is mocked. Run from the repo (needs the deps in `requirements.txt`):

```bash
pip install -r requirements.txt
for f in datetime_parser_test.py helpers_test.py recurrence_test.py server_test.py update_test.py create_test.py; do python "$f"; done
```

Each prints `Results: N passed, M failed` and exits non-zero on failure. The same loop is what CI runs. `create_test.py` and `update_test.py` mock `server.get_client` / `server.get_calendar` (and `FakeCalendar.event_by_uid`) — keep those mocks in sync if you change how `server.py` looks up events.

## Architecture notes & quirks

- **Event lookup is scoped to the target calendar.** `update_event` uses `get_calendar(client).event_by_uid(uid)` — NOT an all-calendars scan. This is deliberate: in test mode a UID collision must never touch a real-calendar event. `delete_event` and the 412 retry path use the same scoped lookup. If you reintroduce an all-calendars search, you break test-mode isolation.
- **Test mode** (`TEST_CAL`, default **on**) routes every read/write to the calendar named by `TEST_CAL_NAME` (default `Test Calendar`). Set `TEST_CAL=false` to hit the real calendar (chosen by `CALENDAR_NAME`, else the first).
- **All-day events** are stored with `VALUE=DATE` on DTSTART/DTEND and tagged `CATEGORIES: ALL-DAY-EVENT`. The `VALUE=DATE` param is required — iCloud otherwise shifts a bare date back a day. `set_all_day_dates` handles the exclusive end.
- **Recurring events are read-only.** `list_events`/`search_events` expand RRULE/RDATE/EXDATE via `expand_recurring`. `create_event` takes no recurrence rule, and iCloud rejects creating new RRULE events via CalDAV (PUT → 403).
- **Naive timed input** is interpreted in `tz` (default `America/Los_Angeles`), not UTC. A value with an offset is stored as-is. Omitted `end` = start + 1 hour.
- **412 Precondition Failed** on update: `update_event` re-reads, re-applies, and saves, retrying up to 3 times.
- **Startup connect-check:** `server.py` verifies the CalDAV connection and target calendar on boot and exits 1 on failure (so the container's healthcheck catches bad creds). A transient `ConnectionResetError` on first connect is retried internally; the gateway may need a restart if it dialed the upstream before the server finished coming up.

## Decision rules

- **Never commit secrets.** Credentials are runtime env vars (`CALDAV_USER`, `CALDAV_PASSWORD`, …). Scan before pushing: `grep -rniE 'ghp_|xoxb-|password=|token' .`.
- **Change code → push to main → CI → NAS pull.** Don't build on the NAS; the image comes from GHCR.
- **Tag a release** with `git tag vX.Y.Z && git push origin vX.Y.Z` to publish a versioned image; `latest` stays on main.
- **Gateway "not connected" for calendar?** The upstream likely wasn't ready when the gateway dialed it — `docker compose restart mcp-gateway` after the container is healthy.
- **Package visibility:** the GHCR package defaults to **private**. Making it public needs the `packages` scope (or the GitHub UI: repo → Packages → ical-mcp → Settings → Visibility → Public). The NAS pulls fine with its own token either way.
