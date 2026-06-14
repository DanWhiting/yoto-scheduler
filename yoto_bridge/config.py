"""Configuration: env vars and constants."""

import os
from pathlib import Path

CLIENT_ID = os.environ.get("YOTO_CLIENT_ID", "HsUxcNdA8VF7EqNeq2ZvH1t3GQAuO7IQ")

HOST = os.environ.get("YOTO_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("YOTO_BRIDGE_PORT", "8765"))
LOG_LEVEL = os.environ.get("YOTO_BRIDGE_LOG_LEVEL", "info")

# Default to the project-root token file so the existing smoke-test token is
# reused. Override with YOTO_TOKEN_FILE in production (e.g. ~/.yoto/tokens.json).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = Path(os.environ.get("YOTO_TOKEN_FILE", str(_PROJECT_ROOT / "yoto_tokens.json")))
SCHEDULE_FILE = Path(os.environ.get("YOTO_SCHEDULE_FILE", str(_PROJECT_ROOT / "schedule.json")))

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
