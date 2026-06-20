# CLAUDE.md

Context for continuing work on yoto-scheduler. Keep this short — work from conversation context, not from documentation. Update when a rule changes.

## What this is

A LAN-only Python bridge for managing a family of Yoto players from a Raspberry Pi (or any LAN device). Wraps the async `yoto-api` library with:

- **Routines**: time-window volume caps per player, with an optional card/group whitelist that reactively stops disallowed cards.
- **Events**: scheduled playback actions per player.
- **Logs**: in-memory ring buffer of fires / blocks / card plays / transitions.
- A mobile-friendly web UI at `/ui/*`. JSON API at the top level (`/library` and `/groups` JSON endpoints still exist — they back the pickers in the Routines and Events pages).

## Rules (must-follow)

1. **Dry-run guard.** Every mutating call to `YotoClient` goes through `WriteGuard` in [yoto_bridge/dry_run.py](yoto_bridge/dry_run.py). When `YOTO_BRIDGE_DRY_RUN=1`, those calls log and no-op. **When you add a new mutating client method anywhere, add its name to `WRITE_METHODS` in `dry_run.py`** — otherwise it'll silently hit Yoto in dry-run mode and the user will lose their safety net.

2. **Design language.** See [docs/STYLE.md](docs/STYLE.md). The four pages (Routines, Events, Logs, Settings) are the working reference. Title Case for interactive elements; sentence case for body copy.

3. **Runtime state lives in `user_data/` and is gitignored.** Tokens, schedule, events — never commit them. The directory is created on first write.

4. **Don't create new .md files mid-task.** The user prefers conversation context over intermediate docs. This file + `docs/STYLE.md` are the exceptions, both explicitly requested.

5. **Restart after template / Python edits** (Starlette's Jinja2Templates caches by default). In dev (`uv run`), **CSS** changes are served fresh from disk — only the browser cache lies. In Docker, static files are `COPY`d into the image at build time, so CSS edits also need `docker compose up -d --build` to land.

## What we mutate on Yoto

Only two fields, both via `scheduler.apply_cap`:
- `day_max_volume_limit`
- `night_max_volume_limit`

Both set to the same value (the routine's cap), effectively overriding Yoto's day/night volume distinction. Everything else (alarms, ambient colours, brightness, day/night times, Yoto Daily / Radio URIs) is left alone.

Plus reactive `stop` calls from the whitelist [Enforcer](yoto_bridge/enforcer.py) when a player starts playing a card outside the active routine's whitelist.

## Whitelist enforcement (Routines)

Three whitelist modes per routine: empty lists + `allow_nothing=False` = unrestricted (default); non-empty lists = only those cards/groups; `allow_nothing=True` = block every card (sleeping mode). `allow_nothing` wins if both are set.

**Alarm tones always bypass the whitelist** regardless of mode. Tones are scheduled by the bridge itself (event actions) or fired by the player's own alarms — we should never be the thing stopping them. Enforcement checks `card_id in known_tone_ids` (passed live by reference from `app._known_tone_ids`) and short-circuits before any routine lookup.

The [Enforcer](yoto_bridge/enforcer.py) calls `client.stop(device_id)` when the now-playing card isn't a tone AND isn't in the resolved set (groups expanded against `client.groups`). It fires on three triggers:
- Every MQTT `on_update` (kid inserts / changes a card).
- Each routine transition — `Scheduler._apply_and_schedule` calls `enforcer.recheck(device_id)` after writing the cap (catches transition-mid-play).
- Each schedule edit — `sched.reload()` runs `_apply_and_schedule` for every player (catches edit-mid-play).

The Scheduler ↔ Enforcer link is back-wired in `_bring_online` (Enforcer depends on Scheduler, so the dep can't go through Scheduler's constructor). Enforcer dedups on `(device_id, card_id)` so it issues one `stop` per insertion, not one per event.

Hard ceiling: there's **no Yoto API to refuse a card outright** — enforcement remains reactive. Expect 1–3s of audio before MQTT-triggered stops land; transition/edit-triggered stops land essentially immediately. If the bridge process is offline, nothing is enforced.

## Architecture

```
yoto_bridge/
  app.py            FastAPI app, lifespan, JSON API, State, serializers
  dry_run.py        WriteGuard proxy; WRITE_METHODS is the canonical list
  scheduler.py      Routines: per-player asyncio.Task per scheduled player
  enforcer.py       Whitelist enforcement (MQTT + scheduler-triggered rechecks)
  events.py         Events: one asyncio.Task per enabled event
  activity.py       In-memory ring buffer behind /logs and /ui/logs
  auth.py           PKCE Authorization-Code flow against Yoto (Auth0)
  storage.py        Token blob — atomic-write JSON
  config.py         Env-var-driven configuration
  ui/
    routes.py       HTML routes at /ui/*
    templates/      Jinja templates (base.html + pages/*.html + partials/*)
    static/         app.css + vendored deps (Pico, HTMX, Alpine, Shoelace)
scripts/            One-off investigative scripts (e.g. probe_tones.py)
docs/STYLE.md       UI design language (tokens, components, copy)
```

## Yoto API gotchas (already worked around)

- **Refresh tokens rotate** on every refresh. Always persist after `check_and_refresh_token()` — `_persist_rotated_refresh_token()` does this.
- **Device-code grant is deprecated by Yoto for new OAuth clients** (since 2024). Legacy clients still work; freshly-registered Public Clients get `403 unauthorized_client` on the device-code endpoint. We use **PKCE Authorization Code** flow instead — see `auth.py` (PKCE verifier + challenge, browser redirect to `/authorize`, `/auth/callback` exchange). yoto.dev's [headless-cli-auth](https://yoto.dev/authentication/headless-cli-auth/) page is the canonical recipe.
- **`device_code_flow_start` only requests `offline_access`.** Library bug — irrelevant now since we don't use device-code, but yoto_api's PKCE-equivalent has the same shape of bug, hence our own auth.py.
- **`PlayerStatus.is_online` moved to `YotoPlayer.is_online`** in yoto-api 4.x. Don't access it on the status object.
- **Rich device fields** (network, wifi, power source, temperature) are on `PlayerExtendedStatus`, not `PlayerStatus`.
- **`crypto.randomUUID()` requires HTTPS or localhost.** Over LAN HTTP it's undefined on mobile Safari. UI uses a local `uid()` helper (Math.random + Date.now).
- **Windows + aiomqtt**: must force `WindowsSelectorEventLoopPolicy` (Proactor doesn't implement `add_reader`). Handled in `__main__.py`. Doesn't affect Linux.
- **Timezone**: routines + events use naive `datetime.now()` (wall-clock semantics — "21:00" means 21:00 *here*). Slim Docker images default to UTC, which would fire everything an hour off during BST. We install `tzdata` in the runtime image and default `TZ=Europe/London` in `docker-compose.yml`; both are required (TZ alone with no tzdata = silent UTC fallback). Override via the host's `.env` if you're not on UK time.
- **Radios and alarm tones are normal library cards.** Yoto's "Yoto Radio / Yoto Daily / Sleep Radio / Classical Radio" cards live in the family library and are recognised by a title heuristic (`/\bradio\b|^yoto daily$/i`). Alarm-tone cards (e.g. `4OD25` "Wake with Jake") are *not* in `/card/family/library` but are fetchable via `update_card_detail`. The bridge auto-discovers them on startup from two sources: a hardcoded `SEED_TONE_IDS` tuple in [app.py](yoto_bridge/app.py) (the six tones discovered during development; any that fail to fetch on a given account are silently skipped), plus anything currently in `PlayerConfig.alarms[].sound_id`. Both feed `_known_tone_ids`; `/library` tags them with `category: "tone"`. There's no Yoto endpoint that lists all tones — see [scripts/probe_tones.py](scripts/probe_tones.py) for the dead-end probes. To add a new tone not in the seed list: assign it as an alarm in the Yoto app, refresh the bridge, then optionally unset it; or add the ID to `SEED_TONE_IDS` if it should be permanent.
- **Library load order**: `update_library()` must run *before* `_discover_alarm_tones()` in `_bring_online`. The tone discovery populates `client.library` with individual tone cards via `update_card_detail`, after which `/library`'s lazy-load check (`not s.client.library`) is False and the full library fetch is skipped — leaving the user with only the tones visible.

## UI gotchas (already worked around)

- **Pico styles `[type=submit|button|reset]`** as native buttons. Those attribute selectors also match Shoelace `<sl-button>` hosts because Shoelace forwards `type` to the host for form integration. Result: stray Pico-primary-blue background painted *around* every Shoelace button. The `sl-button { background: transparent !important; … }` reset at the top of `app.css` neutralises it. If you see Pico-coloured rectangles surrounding a Shoelace control, this is the cause.
- **`<sl-select>` defaults**: prefer a literal HTML `value="…"` attribute over Alpine's reactive `:value=` for initial selection. Shoelace reads value during component upgrade; Alpine binding can land before the `<sl-option>` children are slotted, leaving the select empty.
- **`<p>` and `<div>` inside `<button>`** is invalid HTML — `<button>` only allows phrasing content. Browsers auto-correct during parse by closing the button early and hoisting the block elements out, which renders the inner text *outside* the visible button. Symptom: card images appear but their titles don't. Fix: use `<span>` children with `display: block` in CSS (the picker grid in `events.html` does this).
- **Shoelace events bubble + share names.** `sl-hide` / `sl-after-hide` / `sl-show` / `sl-after-show` are emitted by `sl-dialog`, `sl-alert`, `sl-details`, `sl-drawer`, etc. — and they bubble. A handler on an outer component catches inner components' identically-named events. Symptom: closing one `sl-alert` inside an `sl-dialog` makes the dialog disappear. Fix: Alpine `@sl-after-hide.self="..."` (or explicit `if ($event.target === $el)` for plain JS listeners).

## Configuration (env vars)

| Variable | Default | Purpose |
|---|---|---|
| `YOTO_CLIENT_ID` | _(required)_ | Yoto OAuth client ID. Register a Public Client at Yoto's developer console; never commit. |
| `YOTO_REDIRECT_URI` | _(required)_ | Full URL Yoto redirects to after auth. Must match an Allowed Callback URL on the OAuth client (e.g. `http://192.168.1.94:8765/auth/callback`). |
| `YOTO_BRIDGE_HOST` | `127.0.0.1` | Bind host. Use `0.0.0.0` for LAN access. |
| `YOTO_BRIDGE_PORT` | `8765` | Bind port |
| `YOTO_BRIDGE_DRY_RUN` | unset | `1` to log-and-skip mutating calls |
| `YOTO_USER_DATA_DIR` | `./user_data` | Parent dir for all per-deployment state |
| `YOTO_TOKEN_FILE` | `./user_data/yoto_tokens.json` | Refresh-token blob path |
| `YOTO_SCHEDULE_FILE` | `./user_data/schedule.json` | Routines storage path |
| `YOTO_EVENTS_FILE` | `./user_data/events.json` | Events storage path |

## Running (development)

```
# LAN-accessible + dry-run (current dev mode):
YOTO_BRIDGE_HOST=0.0.0.0 YOTO_BRIDGE_DRY_RUN=1 uv run python -m yoto_bridge
```

`/` → redirects to `/ui/routines`. Authorise once via `/ui/settings` (PKCE Authorization Code flow — browser redirect to Yoto, back to `/auth/callback`). MQTT event stream connects automatically.

## Deployment (Pi / Docker)

Multi-stage [Dockerfile](Dockerfile) + [docker-compose.yml](docker-compose.yml). Designed for `linux/arm64` (Pi 4/5) but builds on amd64 too — Bookworm slim base.

```
# On the Pi:
git clone <repo> /opt/yoto-bridge
cd /opt/yoto-bridge
docker compose up -d --build
# Then visit http://<pi-host>:8765/ui/settings to authorise.
```

State persists to `./user_data/` on the host (bind mount → `/data` in the container). The container runs as uid 1000 — chown the host directory if you hit permission errors on first run. Port 8765 is mapped. `DRY_RUN` is blank by default (real mode); flip it to `"1"` in `docker-compose.yml` for a no-mutation first pass.

The project has no `[build-system]` in `pyproject.toml`, so uv only installs dependencies — the runtime image puts `yoto_bridge/` on `PYTHONPATH` directly. Don't add a build-system unless you also restructure the package; the current shape relies on the path trick.

## Things deliberately deferred

- **Player selector on the Routines page.** Routines currently shows all players in one view; Events has a per-player segmented selector. Routines should follow.
- **"+ Add tone by ID" picker affordance.** The seed list covers the six standard Yoto tones; an explicit add-by-ID input (persisted in a separate JSON file) would let users surface premium / custom tones without round-tripping through the Yoto app.
- **`uvicorn --reload` for dev.** Manual restarts are noisy. Worth wiring in alongside the Docker work.
- **Whitelist UX polish.** "Allow Everything" button could become a primary action when something is selected; the dialog could show a count of total allowed cards (groups expanded). Skipped until the feature gets real-world use.

## Patterns to reuse

- **Per-page Alpine component** with `init()` (fetch data), `autosave()` (debounced PUT), `buildPayload()`, `save()` with a monotonic sequence-number guard against stale responses.
- **Time-driven asyncio scheduler**: each item gets one task that `asyncio.sleep`s to its next slot, fires, then reschedules itself. Used by both `scheduler.py` and `events.py`.
- **Atomic JSON write**: `tmp = path.with_suffix(".json.tmp"); tmp.write_text(...); tmp.replace(path)` — used for tokens, schedule, events. New persistent state should follow this.
- **JSON endpoint enriches IDs to objects** where it cuts a UI round-trip (e.g. `/groups` resolves `card_ids` against the library).
- **Activity log via shared `ActivityLog` ring buffer**, passed by reference into anything that emits user-facing events (`EventsRunner`, `Enforcer`, `Scheduler`). Lost on restart by design.
- **Visible state for everything**: loading / empty / error / saving / saved each have explicit UI. A silent spinner is a bug.
