# CLAUDE.md

Context for continuing work on yoto-scheduler. Keep this short — work from conversation context, not from documentation. Update when a rule changes.

## What this is

A LAN-only Python bridge for managing a family of Yoto players from a Raspberry Pi (or any LAN device). Wraps the async `yoto-api` library with:

- **Routines**: time-window volume caps per player, with an optional card/group whitelist that reactively stops disallowed cards.
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

Plus reactive `stop` calls from the whitelist [Enforcer](yoto_bridge/enforcer.py) when a player starts playing a card outside the active routine's whitelist.

## Whitelist enforcement (Routines)

A routine can carry `allowed_card_ids` + `allowed_group_ids`. Empty both = unrestricted (default). Otherwise the [Enforcer](yoto_bridge/enforcer.py) watches every MQTT `on_update` and calls `client.stop(device_id)` when the now-playing card_id isn't in the resolved union (groups expanded against `client.groups`).

Hard ceiling: there's **no Yoto API to refuse a card outright** — enforcement is reactive only. Expect 1–3s of audio before the stop lands. If the bridge process is offline, nothing is enforced. The Enforcer dedups on `(device_id, card_id)` so it issues one `stop` per insertion, not one per MQTT message.

## Architecture

```
yoto_bridge/
  app.py            FastAPI app, lifespan, JSON API, State, serializers
  dry_run.py        WriteGuard proxy; WRITE_METHODS is the canonical list
  scheduler.py      Routines: per-player asyncio.Task per scheduled player
  enforcer.py       Reactive whitelist enforcement via the MQTT on_update hook
  events.py         Events: per-player asyncio.Task per enabled event
  auth.py           Manual OAuth device-code flow with explicit scopes
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
- **`device_code_flow_start` only requests `offline_access`.** Library bug. We run our own device-code POST in `auth.py` with the full scope list and hand the refresh token to `YotoClient` via `set_refresh_token`.
- **`PlayerStatus.is_online` moved to `YotoPlayer.is_online`** in yoto-api 4.x. Don't access it on the status object.
- **Rich device fields** (network, wifi, power source, temperature) are on `PlayerExtendedStatus`, not `PlayerStatus`.
- **`crypto.randomUUID()` requires HTTPS or localhost.** Over LAN HTTP it's undefined on mobile Safari. UI uses a local `uid()` helper (Math.random + Date.now).
- **Windows + aiomqtt**: must force `WindowsSelectorEventLoopPolicy` (Proactor doesn't implement `add_reader`). Handled in `__main__.py`. Doesn't affect Linux.
- **Radios and alarm tones are normal library cards.** Yoto's "Yoto Radio / Yoto Daily / Sleep Radio / Classical Radio" cards live in the family library and are recognised by a title heuristic (`/\bradio\b|^yoto daily$/i`). Alarm-tone cards (e.g. `4OD25` "Wake with Jake") are *not* in `/card/family/library` but are fetchable via `update_card_detail`. The bridge auto-discovers them on startup by reading every player's `PlayerConfig.alarms[].sound_id` and fetching each card detail — see `_discover_alarm_tones` in [app.py](yoto_bridge/app.py). Library response tags them `category: "tone"`. **Limitation**: only tones currently assigned as alarms are discovered — there is no Yoto endpoint listing all available tones (see [scripts/probe_tones.py](scripts/probe_tones.py) for the dead-end probes). Workaround: temporarily set an unfamiliar tone as an alarm in the Yoto app, refresh, then unset.
- **Library load order**: `update_library()` must run *before* `_discover_alarm_tones()` in `_bring_online`. The tone discovery populates `client.library` with individual tone cards via `update_card_detail`, after which `/library`'s lazy-load check (`not s.client.library`) is False and the full library fetch is skipped — leaving the user with only the tones visible.

## UI gotchas (already worked around)

- **Pico styles `[type=submit|button|reset]`** as native buttons. Those attribute selectors also match Shoelace `<sl-button>` hosts because Shoelace forwards `type` to the host for form integration. Result: stray Pico-primary-blue background painted *around* every Shoelace button. The `sl-button { background: transparent !important; … }` reset at the top of `app.css` neutralises it. If you see Pico-coloured rectangles surrounding a Shoelace control, this is the cause.
- **`<sl-select>` defaults**: prefer a literal HTML `value="…"` attribute over Alpine's reactive `:value=` for initial selection. Shoelace reads value during component upgrade; Alpine binding can land before the `<sl-option>` children are slotted, leaving the select empty.
- **`<p>` and `<div>` inside `<button>`** is invalid HTML — `<button>` only allows phrasing content. Browsers auto-correct during parse by closing the button early and hoisting the block elements out, which renders the inner text *outside* the visible button. Symptom: card images appear but their titles don't. Fix: use `<span>` children with `display: block` in CSS (the picker grid in `events.html` does this).

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
- **Tone discovery for tones the user hasn't assigned as alarms.** Workaround documented in the gotchas section. Real fix would be an "+ Add tone by ID" affordance in the picker, persisted in a separate JSON file.
- **`uvicorn --reload` for dev.** Manual restarts are noisy. Worth wiring in alongside the Docker work.
- **Whitelist UX polish.** "Allow Everything" button could become a primary action when something is selected; the dialog could show a count of total allowed cards (groups expanded). Skipped until the feature gets real-world use.

## Patterns to reuse

- **Per-page Alpine component** with `init()` (fetch data), `autosave()` (debounced PUT), `buildPayload()`, `save()` with a monotonic sequence-number guard against stale responses.
- **Time-driven asyncio scheduler**: each item gets one task that `asyncio.sleep`s to its next slot, fires, then reschedules itself. Used by both `scheduler.py` and `events.py`.
- **Atomic JSON write**: `tmp = path.with_suffix(".json.tmp"); tmp.write_text(...); tmp.replace(path)` — used for tokens, schedule, events. New persistent state should follow this.
- **JSON endpoint enriches IDs to objects** where it cuts a UI round-trip (e.g. `/groups` resolves `card_ids` against the library).
- **Visible state for everything**: loading / empty / error / saving / saved each have explicit UI. A silent spinner is a bug.
