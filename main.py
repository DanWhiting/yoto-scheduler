"""Smoke test for yoto_api v2.5+ on this machine.

The library's built-in device_code_flow only requests `offline_access`, which
yields tokens with no API scopes (403 on every /device-v2/* call). Until that
is fixed upstream, we run the device-code flow ourselves with explicit scopes
and then hand the refresh token to YotoClient via set_refresh_token.

First run: prints a verification URL; open it on any device and authorise.
Subsequent runs: reuses the saved refresh token in yoto_tokens.json.

Run with:   uv run python main.py
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import aiohttp
from yoto_api import YotoClient

CLIENT_ID = os.environ.get("YOTO_CLIENT_ID", "HsUxcNdA8VF7EqNeq2ZvH1t3GQAuO7IQ")
TOKEN_FILE = Path(__file__).parent / "yoto_tokens.json"

AUTH_URL = "https://login.yotoplay.com/oauth/device/code"
TOKEN_URL = "https://login.yotoplay.com/oauth/token"
API_AUDIENCE = "https://api.yotoplay.com"

# Scopes we want on every token. Keep in sync with what's enabled in the
# Yoto developer dashboard for this client_id. See yoto.dev/authentication/scopes.
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

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
)
log = logging.getLogger("yoto-smoke")


def load_token_blob() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def save_token_blob(blob: dict) -> None:
    tmp = TOKEN_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(blob, indent=2))
    tmp.replace(TOKEN_FILE)
    log.info("Saved token to %s", TOKEN_FILE)


async def request_device_code(session: aiohttp.ClientSession) -> dict:
    async with session.post(
        AUTH_URL,
        data={"client_id": CLIENT_ID, "scope": SCOPES, "audience": API_AUDIENCE},
    ) as r:
        body = await r.json(content_type=None)
        if not r.ok:
            raise RuntimeError(f"device code request failed: {r.status} {body}")
        return body


async def poll_for_token(session: aiohttp.ClientSession, auth: dict) -> dict:
    device_code = auth["device_code"]
    interval = int(auth.get("interval", 5))
    expires_in = int(auth.get("expires_in", 300))
    waited = 0
    while waited < expires_in:
        async with session.post(
            TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": CLIENT_ID,
                "audience": API_AUDIENCE,
            },
        ) as r:
            body = await r.json(content_type=None)
            if r.ok:
                return body
            err = body.get("error")
            if err == "authorization_pending":
                pass
            elif err == "slow_down":
                interval += 5
            elif err == "expired_token":
                raise RuntimeError("authorization code expired; rerun")
            else:
                raise RuntimeError(f"token poll failed: {body}")
        await asyncio.sleep(interval)
        waited += interval
    raise RuntimeError("device-code flow timed out")


async def do_full_auth() -> dict:
    """Run a device-code flow with explicit scopes. Returns the raw token body."""
    async with aiohttp.ClientSession() as session:
        auth = await request_device_code(session)
        print()
        print("=" * 60)
        print("Open this URL on any device and authorise:")
        print(f"  {auth.get('verification_uri_complete') or auth.get('verification_uri')}")
        if "user_code" in auth:
            print(f"  (user code: {auth['user_code']})")
        print("=" * 60)
        print()
        token = await poll_for_token(session, auth)
        return token


async def main() -> None:
    blob = load_token_blob()
    needs_auth = (
        blob is None
        or "refresh_token" not in blob
        or "family:devices:view" not in (blob.get("scope") or "")
    )

    if needs_auth:
        log.info("Running fresh device-code flow with explicit scopes.")
        blob = await do_full_auth()
        save_token_blob(blob)
        log.info("Granted scopes: %s", blob.get("scope"))
    else:
        assert blob is not None
        log.info("Reusing saved token. Granted scopes: %s", blob.get("scope"))

    async with YotoClient(client_id=CLIENT_ID) as client:
        client.set_refresh_token(blob["refresh_token"])
        token = await client.check_and_refresh_token()
        # check_and_refresh_token returns a new refresh token sometimes; keep ours fresh.
        if token.refresh_token and token.refresh_token != blob["refresh_token"]:
            blob["refresh_token"] = token.refresh_token
            save_token_blob(blob)

        await client.update_player_list()
        await client.update_all_player_info()

        if not client.players:
            log.warning("No players found on this account.")
            return

        print()
        print(f"Found {len(client.players)} player(s):")
        for device_id, player in client.players.items():
            name = getattr(getattr(player, "device", None), "name", "<unknown>")
            print(f"  - {device_id}  {name}")


if __name__ == "__main__":
    asyncio.run(main())
