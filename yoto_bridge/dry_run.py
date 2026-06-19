"""Write-guard wrapper around YotoClient.

When dry-run is on (env var `YOTO_BRIDGE_DRY_RUN=1`), every method that would
mutate device or family state on Yoto's side becomes a logging no-op. Reads
(update_*, attribute access for players/library/groups, properties like
is_mqtt_connected) pass through untouched, so the UI behaves normally — you
just don't actually change any Yoto state.

Adding a new mutating method? Add its name to `WRITE_METHODS` below.
"""

import logging
from typing import Any

log = logging.getLogger(__name__)


# Methods on YotoClient that send commands to Yoto / the device.
# Anything in this set returns a logging no-op when dry_run=True.
WRITE_METHODS: frozenset[str] = frozenset({
    # PlayerConfig (volume caps, ambients, brightness, alarms…)
    "set_player_config",
    "set_alarms",
    "set_alarm_enabled",
    # Playback commands
    "set_volume",
    "pause",
    "resume",
    "stop",
    "next_track",
    "previous_track",
    "seek",
    "play_card",
    "set_sleep_timer",
    "set_ambients",
    "restart",
})


class WriteGuard:
    """Transparent proxy around YotoClient.

    Read access falls through to the wrapped client; calls to any name in
    `WRITE_METHODS` are intercepted and logged instead of being dispatched.
    """

    def __init__(self, client: Any, dry_run: bool) -> None:
        # Bypass our own __setattr__ — these are proxy internals, not forwarded.
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_dry_run", bool(dry_run))

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @property
    def wrapped(self) -> Any:
        """Escape hatch: the unwrapped client, if you need it deliberately."""
        return self._client

    def __getattr__(self, name: str) -> Any:
        # __getattr__ is called only when `name` isn't found on the proxy itself.
        attr = getattr(self._client, name)
        if not self._dry_run or name not in WRITE_METHODS:
            return attr

        async def _blocked(*args: Any, **kwargs: Any) -> None:
            log.info(
                "DRY-RUN: skipped client.%s(args=%r, kwargs=%r)",
                name, args, kwargs,
            )
            return None

        _blocked.__name__ = f"dry_{name}"
        return _blocked

    # Dunder methods bypass __getattr__ on instances — forward the ones we use.

    async def __aenter__(self) -> "WriteGuard":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc_info: Any) -> Any:
        return await self._client.__aexit__(*exc_info)
