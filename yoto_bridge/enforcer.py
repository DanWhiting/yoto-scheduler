"""Routine-whitelist enforcement.

Reacts to player state changes from two sources:
1. MQTT on_update events (someone inserts / changes a card).
2. Scheduler-triggered rechecks after a routine transition or schedule edit
   (the active whitelist just changed, so any in-progress playback needs to
   be re-evaluated even though the player itself didn't change state).

In both cases, if the now-playing card isn't in the resolved whitelist, the
enforcer calls `stop` on the player.

Hard ceiling: there's no Yoto API to make the device refuse a card outright.
Expect 1-3 seconds of audio before the stop lands on MQTT-triggered checks.
If the bridge is offline, nothing is enforced.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import scheduler

log = logging.getLogger(__name__)


class Enforcer:
    def __init__(
        self,
        client: Any,
        sched: scheduler.Scheduler,
        activity: Any = None,
        known_tone_ids: set[str] | None = None,
    ) -> None:
        self.client = client
        self.sched = sched
        self.activity = activity
        # Live reference to app.py's _known_tone_ids set — populated by
        # _discover_alarm_tones AFTER this constructor runs, so mutations
        # via the shared set are visible here without re-wiring.
        self.known_tone_ids: set[str] = (
            known_tone_ids if known_tone_ids is not None else set()
        )
        # device_id -> card_id we most recently stopped. Cleared when the
        # player's card_id changes (so re-inserting the same forbidden card
        # is re-evaluated, but a single insert isn't stopped repeatedly).
        self._blocked: dict[str, str] = {}
        # device_id -> card_id we most recently logged as "played" — so a
        # single insertion doesn't spam the activity log on every MQTT tick.
        self._last_played: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def recheck(self, device_id: str) -> None:
        """Re-evaluate current playback for a player without waiting for the
        next MQTT update. Called by the scheduler after each routine transition
        and schedule reload — events that change the active whitelist but
        don't otherwise prompt a player state push.
        """
        player = self.client.players.get(device_id)
        if player is None:
            return
        await self.check(player)

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
            self._last_played.pop(device_id, None)
            return

        # Only enforce on active playback; pause/stop events shouldn't trigger.
        playback = getattr(last_event, "playback_status", None)
        playback_value = getattr(playback, "value", None) if playback is not None else None
        if playback_value != "playing":
            return

        # Activity log: a card just started playing (different from whatever
        # was playing last). Dedup so resuming/seeking the same card doesn't
        # spam the log.
        if self.activity is not None and self._last_played.get(device_id) != card_id:
            self._last_played[device_id] = card_id
            device_name = _device_name(self.client, device_id)
            card_title = _card_title(self.client, card_id)
            self.activity.add(
                kind="card_played",
                summary=f"Played '{card_title or card_id}' on {device_name}",
                device_id=device_id, device_name=device_name,
                card_id=card_id, card_title=card_title,
            )

        # Alarm tones bypass the whitelist. They're system cards the bridge
        # fires deliberately (scheduled events, Yoto's own alarms) — we
        # should never be the thing stopping them.
        if card_id in self.known_tone_ids:
            self._blocked.pop(device_id, None)
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
            if self.activity is not None:
                device_name = _device_name(self.client, device_id)
                card_title = _card_title(self.client, card_id)
                self.activity.add(
                    kind="blocked",
                    summary=f"Blocked '{card_title or card_id}' on {device_name} during '{routine.name}'",
                    device_id=device_id, device_name=device_name,
                    card_id=card_id, card_title=card_title,
                    routine_name=routine.name,
                )
            try:
                await self.client.stop(device_id)
            except Exception:
                log.exception("Failed to stop playback for whitelist block")


def _device_name(client: Any, device_id: str) -> str:
    player = (getattr(client, "players", None) or {}).get(device_id)
    return getattr(player, "name", None) or device_id


def _card_title(client: Any, card_id: str) -> str | None:
    card = (getattr(client, "library", None) or {}).get(card_id)
    return getattr(card, "title", None) if card is not None else None
