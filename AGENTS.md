# AGENTS.md

Instructions for any coding agent (Codex, Cursor, Claude, Grok, anyone) working in this repo.
`CLAUDE.md` just points here. Agent session notes go in `docs/PM BOT READ ME.md`.

## What this repo is
- **`main` holds the old Python cue-point engine** (October 2026, A-H cue layout, Gemini assist,
  review UI). It is not the current app.
- **The current app is "Booth Buddy"**, a Mac setlist prep app (Crate Hackers-style) for
  Cullen's own DJ business: a SwiftUI shell (`desktop/`, Xcode project `AutoCueStudio`) around a
  local Python service (`web/server.py`) and engine (`engine/`). Its big difference from other
  prep tools: it reads each client's music choices from Cullen's booking site.
- The app lives on **`feat/prep-live-flow`**, which sits on top of a stack of open PRs:
  `v6` (#1, into `main`) <- `dev/personal-dj-foundation` (#10) <- `feat/setlist-engine` (#11)
  <- `fix/matcher-weak-match-and-version` (#12) <- `feat/prep-live-flow` (no PR yet).
  Commit `988e03f` on that branch is an unreviewed WIP backup snapshot.
- **Goal: get the app onto `main`.** Until Cullen decides how to land the stack, open new PRs
  against `feat/prep-live-flow`, one PR deep. Don't add new layers to the stack.

## Commands
- Engine tests: `python -m unittest discover -s engine -p 'test_*.py'` (pytest also works).
  Two keyring tests need the optional `keyring` package.
- Web service tests: `python -m unittest web.test_server`
- Desktop: build `desktop/` in Xcode on a Mac (see `docs/DESKTOP.md`). It can't build on Linux.

## Ground rules (Cullen, 2026-11-24)
The same rules are in the AGENTS.md of `cullenambrose/dj-av-booking` (booking site) and
`cullenambrose/sound-and-service-ios`.

1. **One operator.** The booking-site sync is for Cullen's single business. No multi-tenant,
   multi-DJ, marketplace or SaaS features, in this app or in the sync, unless Cullen asks for
   it in an issue. `docs/COMMERCIAL-PRODUCT-PLAN.md` is a plan, not a go-ahead.
2. **The booking site is the source of truth.** Everything this app reads from or sends to the
   site is defined by the event sync contract in dj-av-booking:
   `docs/contracts/event-sync-v1.md`. A pinned copy of the schema and fixtures is in
   [`contracts/`](contracts/README.md). Never invent endpoints or fields; if the app needs
   something the contract lacks, propose a contract change.
3. **Contract changes are versioned and paired.** A change bumps the version and lands as two
   linked PRs, one in each repo. Update `contracts/` here byte for byte. Major bumps need Cullen.
4. **Contract tests are required.** Tests must parse `contracts/fixtures/events-response.example.json`
   and validate every prep-status payload the app builds against
   `contracts/event-sync-v1.schema.json` (`#/$defs/PrepStatusRequest`).
5. **Errors are errors.** A non-2xx response or a feed that fails the schema stops crate writes
   and is shown to Cullen in the app. Never treat an error as "no songs", and never drop an
   event with only a `print` warning.
6. **One matcher definition.** Same-song rules are pinned by `contracts/fixtures/matcher-cases.json`.
   Change the cases first (in the site repo), then `engine/library/matcher.py` here and
   `lib/music/match.ts` on the site, in paired PRs.
7. **Crate safety.** Write Serato crates only when `planner_status` is `confirmed` or `locked`;
   everything else is a dry run. Library and Serato files stay read-only except the crate writer.
8. **Small PRs.** One task per PR. Don't leave PRs idle: if blocked, say why in the PR.
9. **Dates.** `event_date` is a local calendar date in the event's `timezone`
   (default `America/Los_Angeles`). Don't compare it to a UTC date.
10. **No secrets.** The device token lives in the macOS Keychain. Never commit, log or cache it.
    Never commit `.env*` files or API keys.

## App-side changes needed to match event sync v1
Status on 2026-11-24: none done yet. Code is in `engine/library/event_sync.py` and
`engine/library/event_sync_schema.py`.

1. Call `GET /api/autocue/events` and `POST /api/autocue/prep-status`. The current
   `/api/v1/events/upcoming` and `/api/v1/events/prep-status` don't exist on the site.
2. Gate crate writes on the top-level `planner_status` only. Drop the `locked` boolean lookup
   and the `planner_responses.status` / `planner.status` fallbacks.
3. Read `timeline_items` (not `event_timelines.items`). Use `title` and `kind` (not `name`) to
   pick subcrates; `song` is plain text; times are `time` (local text), not `start_time`.
4. Read `customer_first_name` (not `client_name`). The site sends no `venue`.
5. Take song cues from `music_lists.special_moments[]` (with `moment`) and from
   `timeline_items[].kind` + `song`. `planner` is display-only free text; don't parse it.
6. The site sends no `client_playlists`. Keep that for local paste intake only. Show
   `music_preferences.playlist_links` (`[{url, label}]`) as links.
7. Send `do_not_play_hits` as an array of `{title, artist?, reason?}`, not a count (the site
   rejects a number with 400). Every `missing_titles` entry needs a non-empty `title`.
8. Check `metadata.contract_version` starts with `1.`; refuse anything else with a visible error.
9. Make the desktop app or local web service call the sync. Today only the engine CLI
   (`engine/library/__main__.py`) and setlist inputs use it.
10. Matcher: `engine/library/matcher.py` fails one shared case today,
    `with-inside-title-is-not-featuring` ("Stay With Me" is treated as "Stay").

The site has its own list of changes in section 7 of the contract (planner status in the feed,
Pacific-date window, real errors instead of empty lists, special moments and playlist links).
