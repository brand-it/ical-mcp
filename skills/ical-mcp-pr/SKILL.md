---
name: ical-mcp-pr
description: How to open and drive a pull request on THIS repo (brand-it/ical-mcp) to merge — the main-branch ruleset (approval + CI gates), the reviewer-bot approval, and the post-merge NAS deploy. Repo-specific; read before opening a PR here.
---

# ical-mcp PR process

How to get a change into `main` the way this repo is gated. `main` is protected by a **ruleset** (not classic branch protection), so you MUST go through a PR. The deep system explanation lives in `../AGENT.md`; this skill is the PR-specific procedure. The quick task reference is `../ical-mcp/SKILL.md`.

## Fixed facts (verify if stale)

| What | Value |
|---|---|
| Repo | `brand-it/ical-mcp` (public) |
| Protected branch | `main` (default branch) |
| Ruleset | id `25593046`, name `main`, `enforcement: active`, target `branch ~DEFAULT_BRANCH`. Get live: `gh api repos/brand-it/ical-mcp/rulesets` |
| CI workflow | `.github/workflows/build-and-publish.yml` |
| Reviewer bot | `alfred-bot-eng` (a 2nd GitHub account). Its token is the env var **`ALFRED_BOT_GITHUB_TOKEN`** in `~/.env` (1Password-generated). Do NOT commit the token; load it from the env. |
| Author identity | `brand-it` (the `GH_TOKEN` used for `git`/`gh`) |

## The main ruleset (the merge gates)

Current active rules:
- `deletion` — force-push / branch delete blocked.
- `non_fast_forward` — no non-fast-forward pushes.
- `required_linear_history` — no merge commits; rebase or squash.
- `required_signatures` — commits must be GPG/SSH-signed (see Signing below).
- `pull_request` — `required_approving_review_count: 1`, `require_last_push_approval: true`, squash-only merge. (Optional hardening: `required_review_thread_resolution: true` and `dismiss_stale_reviews_on_push: true` — see note below.)
- `required_status_checks` — the `test` check must pass (the pre-merge CI gate).

So a PR merges only when: **CI `test` is green** AND **≥1 approving review** AND the last push is approved. Merge method is **squash**.

## CI: what runs when

- `on: pull_request` → the `test` job runs the full suite (all 6 `*_test.py`). This is the pre-merge gate; it must be green.
- The `docker` job is gated to `push` on `main`/`v*` tags only — a PR **never** builds/pushes an image. It shows as `skipping` on a PR. The image is built only after merge.

So: PR → `test` runs → green; `docker` skips. After merge to `main` → `docker` builds + pushes `ghcr.io/brand-it/ical-mcp:latest` → deploy to the NAS (see `../ical-mcp/SKILL.md`).

## Signing (required_signatures)

Commits must be signed. This machine signs via the **1Password SSH agent**:
`~/.gitconfig` sets `commit.gpgsign=true`, `gpg.format=ssh`, `gpg.ssh.program=/opt/1Password/op-ssh-sign`, and the key routes through `~/.1password/agent.sock`.

If the agent socket is dead, `git commit` fails with `1Password: Could not connect to socket`. Revive it (unlock the 1Password app / enable its SSH agent) before committing. Do not disable signing to "work around" it — the ruleset requires signatures.

## The flow

1. **Branch + commit + push** (from `~/apps/ical-mcp`):
   ```bash
   git checkout -b <branch>
   git add -A && git commit -m "..."   # signed via 1Password agent
   git push -u origin <branch>
   ```
2. **Open the PR** to `main`. Use `--body-file` (a temp file), NOT an inline `--body` — an inline body with backticks/`$` gets mangled by shell interpolation:
   ```bash
   gh pr create --base main --head <branch> --title "..." --body-file /tmp/pr-body.md
   ```
3. **Poll CI** until `test` is green (`gh pr checks <n>`). On a PR, `docker` is `skipping` — that's expected, not a failure. If `test` fails, read `gh run view <id> --log-failed`, fix locally, commit, push, re-poll. Repeat until green.
4. **Review** — spawn a reviewer subagent on the PR diff. **First, the bot assigns itself to the PR** (see Self-assigning below), then **posts its findings as a PR review** so the comments live on the PR (see Posting review comments below). Then address every finding: fix the code → commit → push → re-run CI → reply to the comment. **Once a comment is addressed, the bot resolves that thread** (see Resolving review threads below) so the PR's open-thread count drops to zero.
5. **Approve** — the reviewer bot posts a counting approval (see Approving below).
6. **Hand off to the user to merge** (squash). The user is the merge authority.
7. **Deploy** — after merge, CI builds the image; pull + recreate on the NAS (see `../ical-mcp/SKILL.md`).

## The self-approval constraint (critical)

GitHub **does not let a PR author approve their own PR.** The PR is authored by `brand-it`, so a reviewer acting as `brand-it` cannot post a counting approval — it will be rejected (or not count). The approval MUST come from a **different** identity: the `alfred-bot-eng` account.

- The bot token needs **write** access to the repo to post a review/approval. A read-only token can read the diff (fine for the review subagent) but cannot approve — if the token is read-only, ask the user to grant `alfred-bot-eng` write collaborator access (or reissue with `contents: write`).
- Keep the bot token out of the repo and out of commits. Pass it to the subagent in-memory; never write it to a file in the repo.

## Self-assigning the bot to the PR

When the reviewer starts, the bot **assigns itself** to the PR so the PR shows who is reviewing it. PRs are issues, so use the issues assignees endpoint:

```bash
curl -s -X PUT -H "Authorization: Bearer $ALFRED_BOT_GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/brand-it/ical-mcp/issues/<N>/assignees \
  -d '{"assignee":"alfred-bot-eng"}'
```

`<N>` is the PR number (same as the issue number). This needs the bot to have write access (same as approving). Confirm: `gh pr view <N> --json assignees --jq '.assignees[].login'` should list `alfred-bot-eng`. Do this **before** posting the approval, so the PR reflects the reviewer from the start.

## Reviewing on the PR (the sub agent owns this)

The reviewer **sub agent** owns the whole review lifecycle on the PR — it posts the findings, and it resolves each thread once the main agent has addressed it. The main agent only fixes the code and pushes; it never resolves threads itself. Load the token first (see Approving).

The sub agent picks the review event from its verdict:

- **Nothing wrong** → post `APPROVE` directly (a counting approval; the PR is ready to merge). No threads to resolve.
- **Findings, all non-blocking (nits)** → post `COMMENT` with per-line comments, then resolve each thread as the main agent addresses it.
- **Blocking issues** → post `REQUEST_CHANGES`, then resolve each thread as the main agent addresses it.


```bash
# post findings as a review (COMMENT or REQUEST_CHANGES)
curl -s -X POST -H "Authorization: Bearer $ALFRED_BOT_GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/brand-it/ical-mcp/pulls/<N>/reviews \
  -d @/tmp/review.json
```

`/tmp/review.json` shape — an overall review plus optional per-line comments:

```json
{
  "event": "COMMENT",
  "body": "## Review\n\n- [nit] create_test.py:244 — test hard-references typed 412 classes; guard for the caldav floor.\n- [nit] create_test.py:247 — add the UidConflictScheduleTagMismatchError case.\n\nVerdict: APPROVE (nits non-blocking).",
  "comments": [
    {
      "path": "create_test.py",
      "line": 244,
      "body": "Guard these with getattr so the suite stays floor-safe (caldav 1.3.0 lacks the typed classes)."
    }
  ]
}
```

Notes:
- Per-line `comments[].line` is the **right-hand side** line number of the diff (the new file). For a comment on an added line, use the new line number.
- Keep the review body as the summary; put the actionable detail in the per-line `comments` so each becomes a resolvable thread.
- Confirm the review posted: `gh pr view <N> --json reviews --jq '.reviews[] | {author:.author.login,state:.state}'`.

## Resolving review threads (the sub agent)

After the main agent fixes a finding and pushes, the **sub agent** resolves that thread — not the main agent. Resolve a review-comment thread via the GraphQL API (the REST API has no thread-resolve endpoint):

```bash
curl -s -X POST -H "Authorization: Bearer $ALFRED_BOT_GITHUB_TOKEN" \
  -H "Content-Type: application/json" \
  https://api.github.com/graphql \
  -d '{"query":"mutation($id:ID!){ resolveReviewThread(input:{threadId:$id}){ thread { isResolved } } }","variables":{"id":"<THREAD_NODE_ID>"}}'
```

Get the open thread IDs first:

```bash
curl -s -X POST -H "Authorization: Bearer $ALFRED_BOT_GITHUB_TOKEN" \
  -H "Content-Type: application/json" \
  https://api.github.com/graphql \
  -d '{"query":"query($n:Int!){ repository(owner:\"brand-it\",name:\"ical-mcp\"){ pullRequest(number:$n){ reviewThreads(first:50){ nodes{ id isResolved } } } } }","variables":{"n":<N>}}'
```

Resolve each thread only after its finding is actually fixed and pushed. Confirm zero open threads before the final approval: the `isResolved` on every node should be `true`.

## Approving with the bot token

Once CI is green and all review threads are resolved, have the bot post an approval:
Load the token from the env file first (it is not exported by default):

```bash
set -a; . ~/.env; set +a   # exports ALFRED_BOT_GITHUB_TOKEN
```


```bash
# via the GitHub API (most reliable)
curl -s -X POST -H "Authorization: Bearer $ALFRED_BOT_GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/brand-it/ical-mcp/pulls/<N>/reviews \
  -d '{"event":"APPROVE","body":"LGTM — CI green, all review threads resolved."}'
```

Confirm it counted: `gh pr view <N> --json reviews --jq '.reviews[] | {author:.author.login,state:.state}'` and `gh pr checks <N>` (the approval gate should now be satisfied).

> If the bot is read-only, this 403s. Fix the token's permissions first (see constraint above), then retry.

## Ruleset edits (adding guards)

To change the `main` ruleset, PATCH the rules array. **Caution:** a PATCH that includes a malformed `required_status_checks` entry 404s the whole request, and the user may be editing the same ruleset in the GitHub UI concurrently (UI lock race). Safest pattern: GET the live ruleset, modify only the `pull_request` parameters in place, and PATCH that exact structure back:

```bash
gh api repos/brand-it/ical-mcp/rulesets/25593046 > /tmp/rs.json
# edit /tmp/rs.json: set pull_request.parameters.required_review_thread_resolution=true,
#   dismiss_stale_reviews_on_push=true (keep the required_status_checks rule intact)
gh api -X PATCH repos/brand-it/ical-mcp/rulesets/25593046 --input /tmp/rs.json
```

The two hardening flags (`required_review_thread_resolution`, `dismiss_stale_reviews_on_push`) are optional; the required gates are `required_approving_review_count: 1` + the `test` status-check. If a PATCH keeps 404-ing, re-GET and diff against what the UI shows before retrying.

## Deploy after merge

See `../ical-mcp/SKILL.md` for the NAS pull/recreate. CI builds `ghcr.io/brand-it/ical-mcp:latest` on merge; then on the NAS: `docker compose pull calendar-mcp && docker compose up -d --force-recreate calendar-mcp && docker compose restart mcp-gateway`.
