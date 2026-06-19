"""Scheduled play events.

A user-defined event fires at a chosen time on chosen days for one player. It
sets the player's volume and triggers a playback action — currently only
`type: "card"` (play a card from the library). The data model scaffolds the
other action types Yoto supports so adding them later is fill-in-the-blanks.

Storage: events.json next to schedule.json. Loaded once at startup; each event
gets its own asyncio task that sleeps until the next fire time, fires, and
reschedules itself.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, time, timedelta
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from . import config

log = logging.getLogger(__name__)


_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_DAYS_RE = re.compile(r"^[01]{7}$")


def raw_volume_to_percent(raw: int) -> int:
    """Yoto's hardware volume is a 0-16 raw scale (what the device displays and
    what set_player_config(day_max_volume_limit=…) accepts). But yoto_api's
    `set_volume(player_id, v)` interprets `v` as a 0-100 percentage and snaps
    it to the nearest raw step via VOLUME_MAPPING_INVERTED. So if the bridge
    passes the raw value 8 (intending "half"), yoto_api reads "8%" and lands
    on raw 1 (≈7%). Every call site that takes a raw 0-16 number from the UI
    and hands it to set_volume must convert through this helper.
    """
    raw = max(0, min(16, int(raw)))
    return round(raw / 16 * 100)


class EventAction(BaseModel):
    """What to play when the event fires.

    Only `card` is fully wired today. `radio` and `alarm_tone` are accepted in
    storage and stubbed in the runner — the UI hides them as 'coming soon'
    until we have Yoto's station / tone IDs.
    """

    type: Literal["alarm_tone", "radio", "card"]
    # type=card: card_id required; chapter_key + track_key optional (start
    # from a specific point in the card instead of the beginning).
    card_id: Optional[str] = None
    chapter_key: Optional[str] = None
    track_key: Optional[str] = None
    # type=radio: a station id (TBD)
    radio_station: Optional[str] = None
    # type=alarm_tone: a Yoto alarm sound id (TBD)
    tone_id: Optional[str] = None

    # Accept extra keys from earlier saves (e.g. `group_id`) without erroring.
    model_config = {"extra": "ignore"}


class Event(BaseModel):
    id: str
    player_id: str
    name: Optional[str] = None
    time: str  # "HH:MM" 24h
    # 7-char bitmap, Monday→Sunday. Default: every day.
    days_enabled: str = "1111111"
    enabled: bool = True
    volume: int = Field(default=8, ge=0, le=16)
    action: EventAction

    @field_validator("time")
    @classmethod
    def _check_time(cls, v: str) -> str:
        if not _TIME_RE.match(v):
            raise ValueError("time must be HH:MM (24h)")
        return v

    @field_validator("days_enabled")
    @classmethod
    def _check_days(cls, v: str) -> str:
        if not _DAYS_RE.match(v):
            raise ValueError("days_enabled must be 7 chars of 0/1")
        return v


class EventsConfig(BaseModel):
    events: list[Event] = Field(default_factory=list)


# --- file I/O --------------------------------------------------------------


def load() -> EventsConfig:
    if not config.EVENTS_FILE.exists():
        save(EventsConfig())
        return EventsConfig()
    raw = json.loads(config.EVENTS_FILE.read_text())
    return EventsConfig(**raw)


def save(cfg: EventsConfig) -> None:
    config.EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.EVENTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(cfg.model_dump_json(indent=2))
    tmp.replace(config.EVENTS_FILE)


# --- pure helpers ----------------------------------------------------------


def seconds_until_next_fire(event: Event, now: Optional[datetime] = None) -> float:
    """Seconds from `now` to the next time this event should fire.

    Honors the time-of-day and `days_enabled` bitmap. Returns +inf if no day
    is enabled (event never fires).
    """
    if not event.enabled or "1" not in event.days_enabled:
        return float("inf")

    now = now or datetime.now()
    h, m = (int(x) for x in event.time.split(":"))
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Monday = 0
    today_dow = now.weekday()

    for delta in range(8):  # today + next 7 days covers any next-fire case
        dow = (today_dow + delta) % 7
        if event.days_enabled[dow] != "1":
            continue
        candidate = (midnight + timedelta(days=delta)).replace(hour=h, minute=m)
        if candidate > now:
            return (candidate - now).total_seconds()
    return float("inf")


# --- runtime ---------------------------------------------------------------


class EventsRunner:
    """Owns one asyncio.Task per enabled event."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.cfg: EventsConfig = load()
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    async def start(self) -> None:
        count = sum(1 for e in self.cfg.events if e.enabled)
        log.info("EventsRunner starting; %d enabled event(s)", count)
        for event in self.cfg.events:
            if event.enabled:
                self._schedule(event)

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

    async def reload(self, new_cfg: EventsConfig) -> None:
        await self.stop()
        self.cfg = new_cfg
        save(self.cfg)
        await self.start()

    # ------ internals ------

    def _schedule(self, event: Event) -> None:
        delay = seconds_until_next_fire(event)
        if delay == float("inf"):
            log.info(
                "Event %s (%s) has no enabled days; not scheduled",
                event.id, event.name or event.action.type,
            )
            return
        existing = self._tasks.get(event.id)
        if existing is not None and not existing.done():
            existing.cancel()
        log.info(
            "Event %s (%s, %s) scheduled to fire in %.0fs",
            event.id, event.name or event.action.type, event.time, delay,
        )
        self._tasks[event.id] = asyncio.create_task(self._wait_and_fire(event, delay))

    async def _wait_and_fire(self, event: Event, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        try:
            await self._fire(event)
        except Exception:
            log.exception("Event %s fire crashed", event.id)
        # Reschedule for the next matching slot.
        self._schedule(event)

    async def _fire(self, event: Event) -> None:
        log.info(
            "Firing event '%s' on %s at %s (action=%s)",
            event.name or event.id, event.player_id, event.time, event.action.type,
        )
        # Set playback volume. event.volume is on the raw 0-16 scale (UI slider);
        # set_volume expects a percentage. The device's volume_max still clamps.
        try:
            await self.client.set_volume(event.player_id, raw_volume_to_percent(event.volume))
        except Exception:
            log.exception("set_volume failed for event %s", event.id)

        # Radios + alarm tones are also just cards on Yoto's side (Yoto Radio is
        # b0Teo; "Wake with Jake" is 4OD25). The action type is only kept around
        # so the events picker can filter the library — playback is identical.
        a = event.action
        if a.type in ("card", "radio", "alarm_tone"):
            if not a.card_id:
                log.warning("Event %s has type=%s but no card_id; skipping",
                            event.id, a.type)
                return
            kwargs: dict[str, Any] = {}
            if a.chapter_key:
                kwargs["chapter_key"] = a.chapter_key
            if a.track_key:
                kwargs["track_key"] = a.track_key
            await self.client.play_card(event.player_id, a.card_id, **kwargs)
