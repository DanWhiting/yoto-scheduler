"""FastAPI app for yoto-bridge.

Owns one YotoClient for the app lifetime. Routes are thin wrappers around the
client; on_update via connect_events keeps client.players in sync with the
device. Token loaded from storage on startup; if absent, /auth/start kicks off
a device-code flow.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from yoto_api import YotoClient, YotoError

from . import auth, config, enforcer as enforcer_mod, events, scheduler, storage
from .dry_run import WriteGuard
from .ui import routes as ui_routes

log = logging.getLogger(__name__)


class State:
    client: YotoClient
    authorized: bool = False
    auth_flow: auth.AuthFlow | None = None
    events_task: asyncio.Task[Any] | None = None
    sched: scheduler.Scheduler | None = None
    events_runner: events.EventsRunner | None = None
    enforcer: enforcer_mod.Enforcer | None = None


async def _bring_online(state: State, blob: dict) -> None:
    """Refresh token, persist any rotated refresh_token, populate players, start events + scheduler."""
    token = await state.client.check_and_refresh_token()
    _persist_rotated_refresh_token(blob, token.refresh_token)
    await state.client.update_player_list()
    await state.client.update_all_player_info()
    state.authorized = True
    log.info("Linked; %d player(s) loaded.", len(state.client.players))
    state.sched = scheduler.Scheduler(state.client)
    await state.sched.start()
    state.enforcer = enforcer_mod.Enforcer(state.client, state.sched)
    # Start MQTT subscription AFTER scheduler+enforcer exist so the on_update
    # closure has something to dispatch to from the first event onward.
    _start_events(state)
    # Load the full library BEFORE discovering tones — otherwise the tone
    # discovery adds individual cards via update_card_detail, the library
    # becomes "non-empty but partial", and /library's lazy-load check then
    # skips the full fetch (it only checks for emptiness).
    try:
        await state.client.update_library()
    except Exception:
        log.exception("Library update at startup failed; will retry on first /library hit.")
    try:
        await _discover_alarm_tones(state.client)
    except Exception:
        log.exception("Alarm-tone discovery failed")
    state.events_runner = events.EventsRunner(state.client)
    await state.events_runner.start()


def _persist_rotated_refresh_token(blob: dict, refresh_token: str | None) -> None:
    """Save the blob if the refresh token has changed.

    Yoto's Auth0 rotates refresh tokens on every refresh, so we must persist
    each new one or the next startup will use an already-invalidated token.
    """
    if refresh_token and refresh_token != blob.get("refresh_token"):
        blob["refresh_token"] = refresh_token
        storage.save(blob)


def _start_events(state: State) -> None:
    if state.events_task is not None and not state.events_task.done():
        return
    device_ids = list(state.client.players.keys())
    if not device_ids:
        log.warning("No devices to subscribe to; skipping connect_events.")
        return

    async def on_update(player: Any) -> None:
        # connect_events mutates client.players in place; this hook is the
        # extension point for future SSE / MQTT fan-out.
        log.debug("event: %s", getattr(getattr(player, "device", None), "name", "?"))
        if state.enforcer is not None:
            try:
                await state.enforcer.check(player)
            except Exception:
                log.exception("Enforcer check crashed")

    async def runner() -> None:
        try:
            await state.client.connect_events(device_ids, on_update=on_update)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("connect_events task crashed")

    state.events_task = asyncio.create_task(runner())


@asynccontextmanager
async def lifespan(app: FastAPI):
    state = State()
    # Wrap the client in a write-guard so dry-run mode intercepts mutating calls.
    # Proxy is duck-type-compatible with YotoClient; type checkers can't see
    # through __getattr__ so the assignment needs an explicit ignore.
    state.client = WriteGuard(YotoClient(client_id=config.CLIENT_ID), dry_run=config.DRY_RUN)  # type: ignore[assignment]
    await state.client.__aenter__()
    app.state.yoto = state

    if config.DRY_RUN:
        log.warning("DRY-RUN MODE: mutating Yoto API calls will be logged and skipped.")

    blob = storage.load()
    if blob and "refresh_token" in blob:
        try:
            state.client.set_refresh_token(blob["refresh_token"])
            await _bring_online(state, blob)
        except Exception:  # noqa: BLE001
            log.exception("Saved token failed; awaiting /auth/start.")
            state.authorized = False
    else:
        log.info("No saved token; awaiting /auth/start.")

    try:
        yield
    finally:
        # Catch any refresh-token rotations that happened during the run.
        if state.authorized:
            try:
                current_blob = storage.load() or {}
                current_token = getattr(state.client, "token", None)
                if current_token is not None:
                    _persist_rotated_refresh_token(current_blob, current_token.refresh_token)
            except Exception:  # noqa: BLE001
                log.exception("Failed to persist refresh token on shutdown.")
        if state.events_runner is not None:
            await state.events_runner.stop()
        if state.sched is not None:
            await state.sched.stop()
        if state.auth_flow is not None:
            await state.auth_flow.cancel()
        if state.events_task is not None and not state.events_task.done():
            state.events_task.cancel()
            try:
                await state.events_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await state.client.disconnect_events()
        except Exception:  # noqa: BLE001
            pass
        await state.client.__aexit__(None, None, None)


app = FastAPI(title="yoto-bridge", lifespan=lifespan)

_UI_STATIC = Path(__file__).resolve().parent / "ui" / "static"
app.mount("/static", StaticFiles(directory=str(_UI_STATIC)), name="static")
app.include_router(ui_routes.router)


def _state() -> State:
    return app.state.yoto  # type: ignore[no-any-return]


def _require_authorized(state: State) -> None:
    if not state.authorized:
        raise HTTPException(status_code=401, detail="Not linked; POST /auth/start")


async def _finalise_auth_flow(state: State) -> None:
    """Persist tokens, bring client online. Caller must catch exceptions."""
    if state.auth_flow is None or state.auth_flow.token_blob is None:
        raise RuntimeError("No completed auth flow to finalise")
    blob = state.auth_flow.token_blob
    storage.save(blob)
    try:
        state.client.set_refresh_token(blob["refresh_token"])
        await _bring_online(state, blob)
    except Exception:
        log.exception("Failed to bring client online after auth")
        state.auth_flow = None
        raise
    state.auth_flow = None


# --- health -----------------------------------------------------------------


@app.get("/healthz")
async def healthz() -> dict:
    s = _state()
    return {
        "ok": True,
        "authorized": s.authorized,
        "player_count": len(s.client.players) if s.authorized else 0,
        "auth_flow_state": s.auth_flow.state if s.auth_flow else None,
        "mqtt_connected": s.client.is_mqtt_connected if s.authorized else False,
        "dry_run": getattr(s.client, "dry_run", False),
    }


# --- auth -------------------------------------------------------------------


def _ensure_pkce_configured() -> None:
    if not config.CLIENT_ID:
        raise HTTPException(500, "YOTO_CLIENT_ID is not set")
    if not config.REDIRECT_URI:
        raise HTTPException(500, "YOTO_REDIRECT_URI is not set (must match an Allowed Callback URL at Yoto)")


@app.post("/auth/start")
async def auth_start() -> dict:
    """Begin a PKCE flow. Returns the authorize URL — the caller redirects the
    user's browser there. UI form-post variant lives in ui/routes.py.
    """
    s = _state()
    if s.authorized:
        return {"state": "linked", "message": "Already linked."}
    _ensure_pkce_configured()
    if s.auth_flow is None or s.auth_flow.state != "pending":
        s.auth_flow = auth.start_flow()
    return {"state": "pending", "authorize_url": s.auth_flow.authorize_url()}


@app.get("/auth/status")
async def auth_status() -> dict:
    s = _state()
    if s.authorized:
        return {"state": "linked"}
    if s.auth_flow is None:
        return {"state": "idle"}
    if s.auth_flow.state == "error":
        return {"state": "error", "message": s.auth_flow.error}
    return {"state": s.auth_flow.state}


@app.get("/auth/callback")
async def auth_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
) -> RedirectResponse:
    """Yoto redirects the browser here after the user approves. We exchange the
    code for tokens, persist them, bring the client online, then bounce back
    to the settings page so the UI reflects the new state.
    """
    s = _state()
    if error:
        msg = f"{error}: {error_description or ''}".strip(": ")
        if s.auth_flow is not None:
            s.auth_flow.error = msg
        log.warning("Yoto returned auth error: %s", msg)
        return RedirectResponse("/ui/settings", status_code=303)
    if not code or not state:
        raise HTTPException(400, "callback missing required ?code= or ?state=")
    if s.auth_flow is None or s.auth_flow.state != "pending":
        raise HTTPException(400, "no pending auth flow — start one from /ui/settings")
    if state != s.auth_flow.oauth_state:
        raise HTTPException(400, "state mismatch — possible CSRF; re-start the auth flow")
    try:
        await s.auth_flow.exchange_code(code)
        await _finalise_auth_flow(s)
    except Exception as e:  # noqa: BLE001
        log.exception("PKCE token exchange failed")
        # auth_flow.error already populated by exchange_code on token errors;
        # surface any other failure to the UI via a side channel state error.
        if s.auth_flow is not None and not s.auth_flow.error:
            s.auth_flow.error = str(e)
    return RedirectResponse("/ui/settings", status_code=303)


@app.post("/auth/logout")
async def auth_logout() -> dict:
    s = _state()
    s.authorized = False
    if s.events_task is not None and not s.events_task.done():
        s.events_task.cancel()
    storage.clear()
    return {"ok": True}


# --- players ----------------------------------------------------------------


def _enum_name(value: Any) -> Any:
    return value.name if value is not None and hasattr(value, "name") else value


def _serialize_status(status: Any) -> dict:
    # yoto-api 4.x: PlayerStatus has the minimal MQTT fields only; the extras
    # (power_source, network, wifi) moved to PlayerExtendedStatus. is_online
    # moved to YotoPlayer itself. We only surface fields we can rely on.
    return {
        "battery_level_percentage": status.battery_level_percentage,
        "is_charging": status.is_charging,
        "active_card": status.active_card,
        "card_insertion_state": _enum_name(status.card_insertion_state),
        "system_volume_percentage": status.system_volume_percentage,
        "user_volume_percentage": status.user_volume_percentage,
        "day_mode": _enum_name(status.day_mode),
    }


def _serialize_event(event: Any) -> dict:
    return {
        "playback_status": event.playback_status.value if event.playback_status else None,
        "card_id": event.card_id,
        "chapter_title": event.chapter_title,
        "track_title": event.track_title,
        "track_length": event.track_length,
        "position": event.position,
        "volume": event.volume,
        "volume_max": event.volume_max,
        "sleep_timer_seconds": event.sleep_timer_seconds,
        "sleep_timer_active": event.sleep_timer_active,
    }


def _serialize_player(device_id: str, player: Any) -> dict:
    return {
        "device_id": device_id,
        "name": player.name,
        "model": player.model,
        "is_online": getattr(player, "is_online", None),
        "status": _serialize_status(player.status),
        "last_event": _serialize_event(player.last_event),
    }


@app.get("/players")
async def list_players() -> list[dict]:
    s = _state()
    _require_authorized(s)
    return [_serialize_player(d, p) for d, p in s.client.players.items()]


def _serialize_card(card: Any) -> dict:
    return {
        "id": card.id,
        "title": card.title,
        "author": card.author,
        "category": card.category,
        "cover_image_large": card.cover_image_large,
        "series_title": card.series_title,
        "series_order": card.series_order,
    }


# Card IDs discovered to be alarm tones (each player's `alarms[].sound_id`).
# These cards are not in /card/family/library but are fetchable by ID via
# update_card_detail. We collect them on startup and tag them so the picker
# can filter them out of the regular card view.
_known_tone_ids: set[str] = set()


async def _discover_alarm_tones(client: Any) -> None:
    """Read alarm sound_ids from every player and fetch their card details.

    Adds the cards to client.library and records the ids in _known_tone_ids so
    /library can tag them with category='tone'.
    """
    seen: set[str] = set()
    for player in client.players.values():
        config = getattr(getattr(player, "info", None), "config", None)
        if config is None:
            continue
        for alarm in (getattr(config, "alarms", None) or []):
            sid = getattr(alarm, "sound_id", None)
            if sid and sid not in seen:
                seen.add(sid)
    for sid in seen:
        if sid in client.library:
            _known_tone_ids.add(sid)
            continue
        try:
            await client.update_card_detail(sid)
            _known_tone_ids.add(sid)
            log.info("Discovered alarm tone: %s", sid)
        except Exception:
            log.warning("Couldn't fetch alarm-tone card %s", sid, exc_info=True)


def _tone_title_from_chapters(card: Any) -> str | None:
    """Tone cards have title=null; the actual name lives in the first chapter."""
    chapters = getattr(card, "chapters", None) or {}
    iterable = chapters.values() if isinstance(chapters, dict) else iter(chapters)
    for chapter in iterable:
        title = getattr(chapter, "title", None)
        if title:
            return title
    return None


@app.get("/library")
async def list_library(refresh: bool = False) -> list[dict]:
    """Return the family's card library. Pass ?refresh=1 to re-fetch from Yoto."""
    s = _state()
    _require_authorized(s)
    if refresh or not s.client.library:
        try:
            await s.client.update_library()
        except YotoError as e:
            raise HTTPException(status_code=502, detail=str(e))
    if refresh:
        # On explicit refresh, also re-discover tones from current alarm configs.
        await _discover_alarm_tones(s.client)

    out: list[dict] = []
    for c in s.client.library.values():
        item = _serialize_card(c)
        if c.id in _known_tone_ids:
            item["category"] = "tone"
            if not item["title"]:
                item["title"] = _tone_title_from_chapters(c)
        out.append(item)
    return out


@app.get("/players/{device_id}/config")
async def get_player_config(device_id: str) -> dict:
    """Dump the full PlayerConfig for inspection. Useful for discovering
    Yoto Daily / Yoto Radio / Sleep Radio URIs and existing alarm sound_ids.
    """
    from dataclasses import asdict
    s = _state()
    _require_authorized(s)
    if device_id not in s.client.players:
        raise HTTPException(status_code=404, detail=f"Unknown device {device_id}")
    player = s.client.players[device_id]
    config = getattr(getattr(player, "info", None), "config", None)
    if config is None:
        return {"device_id": device_id, "name": player.name, "config": None,
                "note": "player.info.config not populated yet"}
    try:
        cfg_dict = asdict(config)
    except TypeError:
        cfg_dict = {"raw": repr(config)}
    return {"device_id": device_id, "name": player.name, "config": cfg_dict}


@app.get("/library/{card_id}/tracks")
async def get_card_tracks(card_id: str) -> dict:
    """Return the chapters + tracks of a card, fetching detail if needed.

    Doesn't require the card to be in the family library — alarm tones and
    other system cards are fetchable by ID but not "owned".
    """
    s = _state()
    _require_authorized(s)
    try:
        await s.client.update_card_detail(card_id)
    except YotoError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if card_id not in s.client.library:
        raise HTTPException(status_code=404, detail=f"Card {card_id} not found at Yoto")

    card = s.client.library[card_id]
    chapters_out: list[dict] = []
    chapters = card.chapters or {}
    # The library returns chapters either as a dict keyed by chapter_key or as
    # a list — handle both gracefully.
    iterable = chapters.items() if isinstance(chapters, dict) else enumerate(chapters)
    for ck, chapter in iterable:
        chapter_key = getattr(chapter, "key", None) or str(ck)
        tracks_out: list[dict] = []
        tracks = getattr(chapter, "tracks", None) or {}
        track_iter = tracks.items() if isinstance(tracks, dict) else enumerate(tracks)
        for tk, track in track_iter:
            tracks_out.append({
                "key": getattr(track, "key", None) or str(tk),
                "title": getattr(track, "title", None),
                "duration": getattr(track, "duration", None),
            })
        chapters_out.append({
            "key": chapter_key,
            "title": getattr(chapter, "title", None),
            "duration": getattr(chapter, "duration", None),
            "tracks": tracks_out,
        })
    return {"card_id": card_id, "title": card.title, "chapters": chapters_out}


@app.get("/groups")
async def list_groups(refresh: bool = False) -> list[dict]:
    """Return the family's library groups, with each group's cards enriched
    from `client.library` so the UI doesn't need a second fetch.
    """
    s = _state()
    _require_authorized(s)
    if refresh or not s.client.groups:
        try:
            await s.client.update_groups()
        except YotoError as e:
            raise HTTPException(status_code=502, detail=str(e))
    # Ensure library is loaded so we can resolve card_ids to titles + covers.
    if not s.client.library:
        try:
            await s.client.update_library()
        except YotoError as e:
            log.warning("Couldn't load library for group enrichment: %s", e)

    out: list[dict] = []
    for group in s.client.groups.values():
        cards: list[dict] = []
        for card_id in group.card_ids:
            card = s.client.library.get(card_id)
            if card is not None:
                cards.append(_serialize_card(card))
            else:
                # Card is referenced in the group but missing from the library
                # response — still surface the id so the UI can show a placeholder.
                cards.append({
                    "id": card_id, "title": None, "author": None,
                    "category": None, "cover_image_large": None,
                    "series_title": None, "series_order": None,
                })
        out.append({
            "id": group.id,
            "name": group.name,
            "image_url": group.image_url,
            "card_count": len(group.card_ids),
            "cards": cards,
        })
    out.sort(key=lambda g: (g.get("name") or "").lower())
    return out


@app.post("/players/{device_id}/refresh")
async def refresh_status(device_id: str) -> dict:
    """Ask the device to push its current state via MQTT.

    Useful when the device has been idle: status fields stay null until an event
    arrives, and devices only push on state change.
    """
    s = _state()
    _require_authorized(s)
    try:
        await s.client.request_status_push(device_id)
    except YotoError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True}


@app.post("/players/{device_id}/pause")
async def pause(device_id: str) -> dict:
    s = _state()
    _require_authorized(s)
    try:
        await s.client.pause(device_id)
    except YotoError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True}


@app.post("/players/{device_id}/resume")
async def resume(device_id: str) -> dict:
    s = _state()
    _require_authorized(s)
    try:
        await s.client.resume(device_id)
    except YotoError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True}


@app.post("/players/{device_id}/stop")
async def stop(device_id: str) -> dict:
    s = _state()
    _require_authorized(s)
    try:
        await s.client.stop(device_id)
    except YotoError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True}


class VolumeBody(BaseModel):
    volume: int


@app.post("/players/{device_id}/volume")
async def set_volume(device_id: str, body: VolumeBody) -> dict:
    s = _state()
    _require_authorized(s)
    if not 0 <= body.volume <= 16:
        raise HTTPException(status_code=400, detail="volume must be 0..16")
    try:
        # body.volume is raw 0-16; set_volume expects 0-100 percentage.
        await s.client.set_volume(device_id, events.raw_volume_to_percent(body.volume))
    except YotoError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True}


class VolumeMaxBody(BaseModel):
    volume_max: int


@app.post("/players/{device_id}/volume_max")
async def set_volume_max(device_id: str, body: VolumeMaxBody) -> dict:
    """One-off cap override. The scheduler will overwrite this at the next transition."""
    s = _state()
    _require_authorized(s)
    if not 0 <= body.volume_max <= 16:
        raise HTTPException(status_code=400, detail="volume_max must be 0..16")
    try:
        await scheduler.apply_cap(s.client, device_id, body.volume_max)
    except YotoError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True}


@app.post("/players/{device_id}/next")
async def next_track(device_id: str) -> dict:
    s = _state()
    _require_authorized(s)
    try:
        await s.client.next_track(device_id)
    except YotoError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True}


@app.post("/players/{device_id}/previous")
async def previous_track(device_id: str) -> dict:
    s = _state()
    _require_authorized(s)
    try:
        await s.client.previous_track(device_id)
    except YotoError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True}


class PlayCardBody(BaseModel):
    card_id: str
    seconds_in: int | None = None
    cutoff: int | None = None
    chapter_key: str | None = None
    track_key: str | None = None


@app.post("/players/{device_id}/play_card")
async def play_card(device_id: str, body: PlayCardBody) -> dict:
    s = _state()
    _require_authorized(s)
    try:
        await s.client.play_card(
            device_id,
            body.card_id,
            seconds_in=body.seconds_in,
            cutoff=body.cutoff,
            chapter_key=body.chapter_key,
            track_key=body.track_key,
        )
    except YotoError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True}


# --- schedule ---------------------------------------------------------------


def _require_scheduler(s: State) -> scheduler.Scheduler:
    _require_authorized(s)
    if s.sched is None:
        raise HTTPException(status_code=503, detail="Scheduler not initialised")
    return s.sched


@app.get("/schedule")
async def get_schedule() -> dict:
    s = _state()
    sch = _require_scheduler(s)
    return {
        "config": sch.cfg.model_dump(),
        "live": {device_id: sch.status_for(device_id) for device_id in s.client.players},
        "known_devices": [
            {"device_id": d, "name": p.name} for d, p in s.client.players.items()
        ],
    }


@app.put("/schedule")
async def put_schedule(body: scheduler.ScheduleConfig) -> dict:
    s = _state()
    sch = _require_scheduler(s)
    await sch.reload(body)
    return {"ok": True, "config": sch.cfg.model_dump()}


# --- events ----------------------------------------------------------------


def _require_events(s: State) -> events.EventsRunner:
    _require_authorized(s)
    if s.events_runner is None:
        raise HTTPException(status_code=503, detail="Events runner not initialised")
    return s.events_runner


@app.get("/events")
async def get_events() -> dict:
    s = _state()
    runner = _require_events(s)
    return {
        "config": runner.cfg.model_dump(),
        "known_devices": [
            {"device_id": d, "name": p.name} for d, p in s.client.players.items()
        ],
    }


@app.put("/events")
async def put_events(body: events.EventsConfig) -> dict:
    s = _state()
    runner = _require_events(s)
    await runner.reload(body)
    return {"ok": True, "config": runner.cfg.model_dump()}
