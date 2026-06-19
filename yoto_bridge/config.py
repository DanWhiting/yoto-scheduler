"""Configuration: env vars and constants."""

import os
from pathlib import Path

CLIENT_ID = os.environ.get("YOTO_CLIENT_ID", "HsUxcNdA8VF7EqNeq2ZvH1t3GQAuO7IQ")

# Dev / dry-run mode. When on, any mutating client call (set_volume, play_card,
# set_player_config, etc.) becomes a logging no-op — read paths still work, so
# the UI behaves normally but no Yoto state is changed.
DRY_RUN = os.environ.get("YOTO_BRIDGE_DRY_RUN", "").lower() in ("1", "true", "yes", "on")

HOST = os.environ.get("YOTO_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("YOTO_BRIDGE_PORT", "8765"))
LOG_LEVEL = os.environ.get("YOTO_BRIDGE_LOG_LEVEL", "info")

# Per-deployment runtime state lives under ./user_data/ to keep the project
# root clean. The dir is created on first write by storage / scheduler / events.
# Override individual files with env vars or move the whole dir with YOTO_USER_DATA_DIR.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
USER_DATA_DIR = Path(os.environ.get("YOTO_USER_DATA_DIR", str(_PROJECT_ROOT / "user_data")))
TOKEN_FILE    = Path(os.environ.get("YOTO_TOKEN_FILE",    str(USER_DATA_DIR / "yoto_tokens.json")))
SCHEDULE_FILE = Path(os.environ.get("YOTO_SCHEDULE_FILE", str(USER_DATA_DIR / "schedule.json")))
EVENTS_FILE   = Path(os.environ.get("YOTO_EVENTS_FILE",   str(USER_DATA_DIR / "events.json")))

YOTO_AUTH_URL = "https://login.yotoplay.com/oauth/device/code"
YOTO_TOKEN_URL = "https://login.yotoplay.com/oauth/token"
YOTO_AUDIENCE = "https://api.yotoplay.com"

# Library only requests offline_access; we run our own device-code flow with the
# full list. See memory: project_yoto_api_scope_workaround.
SCOPES = " ".join([
    "offline_access",
    "family:view",
    "family:devices:view",
    "family:devices:control",
    "family:devices:manage",
    "family:library:view",
    "family:library:manage",
    "user:content:view",
    "user:content:manage",
    "user:icons:manage",
    "profile",
])
