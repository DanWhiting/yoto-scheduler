"""Reactive routine-whitelist enforcement.

Watches MQTT-driven player state updates. When a player starts playing a card
that's not in the active routine's whitelist, calls `stop` on the player.

Hard ceiling: enforcement is reactive — there's no Yoto API to make the device
refuse a card outright. Expect 1-3 seconds of audio before the stop lands. If
the bridge is offline, nothing is enforced at all.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import scheduler

log = logging.getLogger(__name__)


class Enforcer:
    def __init__(self, client: Any, sched: scheduler.Scheduler) -> None:
        self.client = client
        self.sched = sched
        # device_id -> card_id we most recently stopped. Cleared when the
        # player's card_id changes (so re-inserting the same forbidden card
        # is re-evaluated, but a single insert isn't stopped repeatedly).
        self._blocked: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def check(self, player: Any) -> None:
        last_event = getattr(player, "last_event", None)
        if last_event is None:
            return
        device = getattr(player, "device", None)
        device_id = getattr(device, "device_id", None) or getattr(player, "device_id", None)
        if not device_id:
            return

        card_id = getattr(last_event, "card_id", None)
        # Yoto reports "no card inserted" as the literal string "none" or null.
        if not card_id or card_id == "none":
            self._blocked.pop(device_id, None)
            return

        # Only enforce on active playback; pause/stop events shouldn't trigger.
        playback = getattr(last_event, "playback_status", None)
        playback_value = getattr(playback, "value", None) if playback is not None else None
        if playback_value != "playing":
            return

        routine = self.sched.active_routine(device_id)
        if routine is None:
            return
        allowed = self.sched.resolved_allowed_cards(routine)
        if allowed is None:
            # Routine has no whitelist — anything goes.
            self._blocked.pop(device_id, None)
            return
        if card_id in allowed:
            self._blocked.pop(device_id, None)
            return

        # Already blocked this card on this player and the card hasn't changed
        # since — don't re-fire stop in a loop on subsequent events.
        if self._blocked.get(device_id) == card_id:
            return

        async with self._lock:
            if self._blocked.get(device_id) == card_id:
                return
            self._blocked[device_id] = card_id
            log.info(
                "Whitelist block: card=%s on device=%s during routine='%s'",
                card_id, device_id, routine.name,
            )
            try:
                await self.client.stop(device_id)
            except Exception:
                log.exception("Failed to stop playback for whitelist block")
