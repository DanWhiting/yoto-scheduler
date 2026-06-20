"""In-memory activity log surfaced on the /ui/logs page.

A 500-entry ring buffer of user-facing events: scheduled events that fired,
playback the kids started, cards the enforcer blocked, routine transitions.

Lost on restart by design — this is for "what's been happening in the last
few hours", not an audit trail. If long-term history matters later, swap the
deque for a JSONL file with the same Entry shape.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Iterable, Literal, Optional

log = logging.getLogger(__name__)


Kind = Literal["event_fired", "blocked", "card_played", "transition"]


@dataclass
class Entry:
    ts: datetime
    kind: Kind
    summary: str
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    card_id: Optional[str] = None
    card_title: Optional[str] = None
    routine_name: Optional[str] = None
    event_id: Optional[str] = None
    event_name: Optional[str] = None
    action_type: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        return d


class ActivityLog:
    def __init__(self, max_entries: int = 500) -> None:
        self._buf: deque[Entry] = deque(maxlen=max_entries)

    def add(self, kind: Kind, summary: str, **fields: Optional[str]) -> None:
        entry = Entry(ts=datetime.now(), kind=kind, summary=summary, **fields)
        self._buf.append(entry)

    def recent(self, limit: int = 200) -> list[dict]:
        """Newest first; capped at `limit`."""
        items: Iterable[Entry] = list(self._buf)[-limit:]
        return [e.to_dict() for e in reversed(list(items))]
