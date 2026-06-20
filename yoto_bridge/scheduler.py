"""Time-routine volume scheduler.

Routines (name + volume_max) are shared across all players; transition times are
per-player. Each routine fires on its scheduled time for that player, writing
the cap to the device's `day_max_volume_limit` AND `night_max_volume_limit` via
set_player_config, so the cap is device-enforced regardless of Yoto's own
day/night split.
"""

import asyncio
import json
import logging
from datetime import datetime, time, timedelta
from typing import Any

from pydantic import BaseModel, Field, field_validator

from . import config

log = logging.getLogger(__name__)


class Routine(BaseModel):
    name: str
    volume_max: int = Field(ge=0, le=16)
    # Three-mode whitelist:
    # - allow_nothing=True              → block every card (e.g. "Sleeping")
    # - both lists empty + allow_nothing=False → no restriction (default)
    # - either list non-empty           → only those cards/groups allowed
    # allow_nothing takes precedence over the lists if both are set.
    allowed_card_ids: list[str] = Field(default_factory=list)
    allowed_group_ids: list[str] = Field(default_factory=list)
    allow_nothing: bool = False

    model_config = {"extra": "ignore"}


class ScheduleConfig(BaseModel):
    routines: list[Routine] = Field(default_factory=list)
    # device_id -> {routine_name: "HH:MM"}
    schedules: dict[str, dict[str, str]] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}

    @field_validator("routines")
    @classmethod
    def _unique_routine_names(cls, routines: list[Routine]) -> list[Routine]:
        names = [r.name for r in routines]
        if len(names) != len(set(names)):
            raise ValueError("routine names must be unique")
        return routines


DEFAULT_CONFIG = ScheduleConfig(
    routines=[
        Routine(name="morning", volume_max=8),
        Routine(name="daytime", volume_max=12),
        Routine(name="evening", volume_max=10),
        Routine(name="bedtime", volume_max=6),
        Routine(name="nighttime", volume_max=3),
    ],
)


# --- file I/O --------------------------------------------------------------


def load() -> ScheduleConfig:
    if not config.SCHEDULE_FILE.exists():
        save(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    raw = json.loads(config.SCHEDULE_FILE.read_text())
    return ScheduleConfig(**raw)


def save(cfg: ScheduleConfig) -> None:
    config.SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.SCHEDULE_FILE.with_suffix(".json.tmp")
    tmp.write_text(cfg.model_dump_json(indent=2))
    tmp.replace(config.SCHEDULE_FILE)


# --- pure helpers ----------------------------------------------------------


def _parse_hhmm(s: str) -> time:
    return datetime.strptime(s, "%H:%M").time()


def current_routine(transitions: dict[str, str], now: datetime | None = None) -> str | None:
    """Which routine is active for a player at `now`. Wraps around midnight."""
    if not transitions:
        return None
    now_t = (now or datetime.now()).time()
    parsed = sorted(
        ((_parse_hhmm(t), name) for name, t in transitions.items()),
        key=lambda x: x[0],
    )
    active = None
    for t, name in parsed:
        if t <= now_t:
            active = name
        else:
            break
    # Before today's earliest transition? The active period is the one that
    # started latest yesterday — i.e. the last transition in the day.
    return active if active is not None else parsed[-1][1]


def seconds_until_next_transition(
    transitions: dict[str, str], now: datetime | None = None
) -> tuple[float, str]:
    """Returns (seconds_to_wait, next_routine_name)."""
    now = now or datetime.now()
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    parsed = sorted(
        ((_parse_hhmm(t), name) for name, t in transitions.items()),
        key=lambda x: x[0],
    )
    for t, name in parsed:
        tx = today_midnight.replace(hour=t.hour, minute=t.minute)
        if tx > now:
            return (tx - now).total_seconds(), name
    # All transitions today are in the past — first one tomorrow.
    t, name = parsed[0]
    tx = today_midnight.replace(hour=t.hour, minute=t.minute) + timedelta(days=1)
    return (tx - now).total_seconds(), name


# --- runtime ---------------------------------------------------------------


async def apply_cap(client: Any, device_id: str, volume_max: int) -> None:
    """Write the cap to both day and night settings so it applies all day."""
    await client.set_player_config(
        device_id,
        day_max_volume_limit=volume_max,
        night_max_volume_limit=volume_max,
    )


class Scheduler:
    """Owns one asyncio.Task per scheduled player."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.cfg: ScheduleConfig = load()
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def routine_cap(self, name: str) -> int | None:
        for r in self.cfg.routines:
            if r.name == name:
                return r.volume_max
        return None

    def active_routine(self, device_id: str, now: datetime | None = None) -> Routine | None:
        """The Routine object currently active for a player, or None."""
        transitions = self.cfg.schedules.get(device_id)
        if not transitions:
            return None
        name = current_routine(transitions, now)
        if name is None:
            return None
        for r in self.cfg.routines:
            if r.name == name:
                return r
        return None

    def resolved_allowed_cards(self, routine: Routine) -> set[str] | None:
        """Returns the set of allowed card_ids, or None when unrestricted.

        - allow_nothing=True              → empty set (= every card is blocked)
        - both lists empty                → None (= allow everything)
        - either list non-empty           → union of card_ids + group members
        """
        if routine.allow_nothing:
            return set()
        if not routine.allowed_card_ids and not routine.allowed_group_ids:
            return None
        allowed = set(routine.allowed_card_ids)
        groups = getattr(self.client, "groups", {}) or {}
        for gid in routine.allowed_group_ids:
            group = groups.get(gid)
            if group is not None:
                allowed.update(getattr(group, "card_ids", []) or [])
        return allowed

    def status_for(self, device_id: str) -> dict | None:
        transitions = self.cfg.schedules.get(device_id)
        if not transitions:
            return None
        now = datetime.now()
        current = current_routine(transitions, now)
        secs, nxt = seconds_until_next_transition(transitions, now)
        return {
            "current_routine": current,
            "current_cap": self.routine_cap(current) if current else None,
            "next_routine": nxt,
            "next_in_seconds": int(secs),
        }

    async def start(self) -> None:
        for device_id in self.cfg.schedules:
            await self._apply_and_schedule(device_id)

    async def reload(self, new_cfg: ScheduleConfig) -> None:
        await self.stop()
        self.cfg = new_cfg
        save(self.cfg)
        await self.start()

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        # Allow tasks to actually unwind so we don't leak.
        for task in self._tasks.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

    async def _apply_and_schedule(self, device_id: str) -> None:
        transitions = self.cfg.schedules.get(device_id)
        if not transitions:
            return
        routine = current_routine(transitions)
        if routine is None:
            return
        cap = self.routine_cap(routine)
        if cap is None:
            log.warning(
                "Player %s schedule references unknown routine %r — skipping cap apply.",
                device_id,
                routine,
            )
        else:
            try:
                await apply_cap(self.client, device_id, cap)
                log.info("Applied routine '%s' (cap %d) to %s", routine, cap, device_id)
            except Exception:
                log.exception("Failed to apply cap %d to %s", cap, device_id)

        existing = self._tasks.get(device_id)
        if existing is not None and not existing.done():
            existing.cancel()
        delay, next_routine = seconds_until_next_transition(transitions)
        self._tasks[device_id] = asyncio.create_task(
            self._sleep_then_transition(device_id, delay, next_routine)
        )

    async def _sleep_then_transition(
        self, device_id: str, delay: float, routine: str
    ) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        log.info("Transition: %s -> %s", device_id, routine)
        await self._apply_and_schedule(device_id)
