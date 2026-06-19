# CLAUDE.md

Context for continuing work on yoto-scheduler. Keep this short — work from conversation context, not from documentation. Update when a rule changes.

## What this is

A LAN-only Python bridge for managing a family of Yoto players from a Raspberry Pi (or any LAN device). Wraps the async `yoto-api` library with:

- **Routines**: time-window volume caps per player.
- **Events**: scheduled playback actions per player.
- **Library / Groups**: read-only views.
- A mobile-friendly web UI at `/ui/*`. JSON API at the top level.

## Rules (must-follow)

1. **Dry-run guard.** Every mutating call to `YotoClient` goes through `WriteGuard` in [yoto_bridge/dry_run.py](yoto_bridge/dry_run.py). When `YOTO_BRIDGE_DRY_RUN=1`, those calls log and no-op. **When you add a new mutating client method anywhere, add its name to `WRITE_METHODS` in `dry_run.py`** — otherwise it'll silently hit Yoto in dry-run mode and the user will lose their safety net.

2. **Design language.** See [docs/STYLE.md](docs/STYLE.md). The four existing pages (Routines, Events, Library, Groups) are the working reference. Title Case for interactive elements; sentence case for body copy.

3. **Runtime state lives in `user_data/` and is gitignored.** Tokens, schedule, events — never commit them. The directory is created on first write.

4. **Don't create new .md files mid-task.** The user prefers conversation context over intermediate docs. This file + `docs/STYLE.md` are the exceptions, both explicitly requested.

5. **Restart after template / Python edits.** Starlette's Jinja2Templates caches templates by default. CSS changes are served fresh from disk; only the browser cache lies.

## What we mutate on Yoto

Only two fields, both via `scheduler.apply_cap`:
- `day_max_volume_limit`
- `night_max_volume_limit`

Both set to the same value (the routine's cap), effectively overriding Yoto's day/night volume distinction. Everything else (alarms, ambient colours, brightness, day/night times, Yoto Daily / Radio URIs) is left alone.

## Architecture

```
yoto_bridge/
  app.py            FastAPI app, lifespan, JSON API, State, serializers
  dry_run.py        WriteGuard proxy; WRITE_METHODS is the canonical list
  scheduler.py      Routines: per-player asyncio.Task per scheduled player
  events.py         Events: per-player asyncio.Task per enabled event
  auth.py           Manual OAuth device-code flow with explicit scopes
  storage.py        Token blob — atomic-write JSON
  config.py         Env-var-driven configuration
  ui/
    routes.py       HTML routes at /ui/*
    templates/      Jinja templates (base.html + pages/*.html + partials/*)
    static/         app.css + vendored deps (Pico, HTMX, Alpine, Shoelace)
docs/STYLE.md       UI design language (tokens, components, copy)
```

## Yoto API gotchas (already worked around)

- **Refresh tokens rotate** on every refresh. Always persist after `check_and_refresh_token()` — `_persist_rotated_refresh_token()` does this.
- **`device_code_flow_start` only requests `offline_access`.** Library bug. We run our own device-code POST in `auth.py` with the full scope list and hand the refresh token to `YotoClient` via `set_refresh_token`.
- **`PlayerStatus.is_online` moved to `YotoPlayer.is_online`** in yoto-api 4.x. Don't access it on the status object.
- **Rich device fields** (network, wifi, power source, temperature) are on `PlayerExtendedStatus`, not `PlayerStatus`.
- **`crypto.randomUUID()` requires HTTPS or localhost.** Over LAN HTTP it's undefined on mobile Safari. UI uses a local `uid()` helper (Math.random + Date.now).
- **Windows + aiomqtt**: must force `WindowsSelectorEventLoopPolicy` (Proactor doesn't implement `add_reader`). Handled in `__main__.py`. Doesn't affect Linux.
- **Yoto alarm `sound_id` values and the 3 Yoto Radio URIs** are not exposed by `yoto-api` or documented in Yoto's developer docs. The Events `radio` and `alarm_tone` action types are stubbed in [events.py](yoto_bridge/events.py) until we discover them — likely by reading them off a player whose Yoto-app has them configured.

## UI gotchas (already worked around)

- **Pico styles `[type=submit|button|reset]`** as native buttons. Those attribute selectors also match Shoelace `<sl-button>` hosts because Shoelace forwards `type` to the host for form integration. Result: stray Pico-primary-blue background painted *around* every Shoelace button. The `sl-button { background: transparent !important; … }` reset at the top of `app.css` neutralises it. If you see Pico-coloured rectangles surrounding a Shoelace control, this is the cause.
- **`<sl-select>` defaults**: prefer a literal HTML `value="…"` attribute over Alpine's reactive `:value=` for initial selection. Shoelace reads value during component upgrade; Alpine binding can land before the `<sl-option>` children are slotted, leaving the select empty.

## Configuration (env vars)

| Variable | Default | Purpose |
|---|---|---|
| `YOTO_CLIENT_ID` | dev app id | Yoto OAuth client ID |
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

`/` → redirects to `/ui/routines`. Authorise once via `/ui/settings` (device-code flow). MQTT event stream connects automatically.

## Things deliberately deferred

- **Containerise + deploy to Pi.** Plan: linux/arm64 Docker image, build on the Pi. Already discussed but not done.
- **Player selector on the Routines page.** Routines currently shows all players in one view; Events has a per-player segmented selector. Routines should follow.
- **Action types `radio` and `alarm_tone`.** UI shows them as "coming soon" disabled options. Needs URI / sound_id discovery first.
- **`uvicorn --reload` for dev.** Manual restarts are noisy. Worth wiring in alongside the Docker work.

## Patterns to reuse

- **Per-page Alpine component** with `init()` (fetch data), `autosave()` (debounced PUT), `buildPayload()`, `save()` with a monotonic sequence-number guard against stale responses.
- **Time-driven asyncio scheduler**: each item gets one task that `asyncio.sleep`s to its next slot, fires, then reschedules itself. Used by both `scheduler.py` and `events.py`.
- **Atomic JSON write**: `tmp = path.with_suffix(".json.tmp"); tmp.write_text(...); tmp.replace(path)` — used for tokens, schedule, events. New persistent state should follow this.
- **JSON endpoint enriches IDs to objects** where it cuts a UI round-trip (e.g. `/groups` resolves `card_ids` against the library).
- **Visible state for everything**: loading / empty / error / saving / saved each have explicit UI. A silent spinner is a bug.
