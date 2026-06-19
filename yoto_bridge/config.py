"""Configuration: env vars and constants."""

import os
from pathlib import Path

# Yoto OAuth client id. Required — register your own app via Yoto's developer
# console and set this env var. There's no default: a shared default would
# (a) tie everyone's rate limits to one client and (b) leak the operator's
# client_id into a public repo.
CLIENT_ID = os.environ.get("YOTO_CLIENT_ID", "")

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

YOTO_AUTHORIZE_URL = "https://login.yotoplay.com/authorize"
YOTO_TOKEN_URL = "https://login.yotoplay.com/oauth/token"
YOTO_AUDIENCE = "https://api.yotoplay.com"

# Required. Must match an Allowed Callback URL registered on the Yoto OAuth
# client. The browser is redirected here with ?code=&state= after the user
# approves. For LAN deploys this is typically http://<pi-ip>:8765/auth/callback;
# for local dev, http://127.0.0.1:8765/auth/callback.
REDIRECT_URI = os.environ.get("YOTO_REDIRECT_URI", "")

# yoto_api's built-in device-code flow only requested offline_access. We now
# do our own PKCE Authorization-Code flow with the full scope list (device-code
# was deprecated by Yoto for new clients; see CLAUDE.md auth gotcha).
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
