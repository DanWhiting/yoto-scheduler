"""Token blob persistence with atomic write."""

import json

from . import config


def load() -> dict | None:
    if not config.TOKEN_FILE.exists():
        return None
    try:
        return json.loads(config.TOKEN_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def save(blob: dict) -> None:
    config.TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.TOKEN_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(blob, indent=2))
    tmp.replace(config.TOKEN_FILE)


def clear() -> None:
    config.TOKEN_FILE.unlink(missing_ok=True)
