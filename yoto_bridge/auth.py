"""PKCE Authorization-Code OAuth flow against Yoto.

Why this is hand-rolled rather than yoto_api's built-in: the library still ships
a device-code helper, but Yoto deprecated device-code for new OAuth clients in
2024 — only legacy clients still get that grant. New "Public Clients" must use
Authorization Code + PKCE over a loopback / LAN redirect (Yoto's documented
replacement, see yoto.dev/authentication/headless-cli-auth/). We also need the
full scope list, which yoto_api's helper doesn't request.

Flow shape:
  1. start_flow() generates a code_verifier + state, returns an AuthFlow.
  2. UI redirects the user's browser to flow.authorize_url().
  3. Yoto bounces the browser back to YOTO_REDIRECT_URI with ?code=&state=.
  4. The /auth/callback handler verifies state, calls flow.exchange_code(),
     stores the token blob on the flow. _finalise_auth_flow then persists it.
"""

import base64
import hashlib
import logging
import secrets
from typing import Any
from urllib.parse import urlencode

import aiohttp

from . import config

log = logging.getLogger(__name__)


def _gen_code_verifier() -> str:
    """RFC 7636 §4.1: 43–128 URL-safe chars, ≥256 bits of entropy."""
    return base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")


def _code_challenge(verifier: str) -> str:
    """S256: SHA-256(verifier), base64url, no padding."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


class AuthFlow:
    """In-progress PKCE authorisation-code flow.

    States: 'pending' -> 'linked' | 'error'. There's no background task — the
    browser drives the flow and /auth/callback completes it synchronously.
    """

    def __init__(self) -> None:
        self.code_verifier: str = _gen_code_verifier()
        # OAuth `state` parameter — CSRF protection for the callback. Not to be
        # confused with `self.state` below (the flow's phase).
        self.oauth_state: str = secrets.token_urlsafe(32)
        self.token_blob: dict | None = None
        self.error: str | None = None

    @property
    def state(self) -> str:
        if self.token_blob is not None:
            return "linked"
        if self.error is not None:
            return "error"
        return "pending"

    def authorize_url(self) -> str:
        params = {
            "response_type": "code",
            "client_id": config.CLIENT_ID,
            "redirect_uri": config.REDIRECT_URI,
            "scope": config.SCOPES,
            "audience": config.YOTO_AUDIENCE,
            "code_challenge": _code_challenge(self.code_verifier),
            "code_challenge_method": "S256",
            "state": self.oauth_state,
        }
        return f"{config.YOTO_AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> dict:
        """Exchange the auth code for tokens. Stores the blob on self."""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                config.YOTO_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": config.CLIENT_ID,
                    "code_verifier": self.code_verifier,
                    "code": code,
                    "redirect_uri": config.REDIRECT_URI,
                },
            ) as r:
                body = await r.json(content_type=None)
                if not r.ok:
                    msg = f"token exchange failed: {r.status} {body}"
                    self.error = msg
                    raise RuntimeError(msg)
                self.token_blob = body
                log.info("Auth code exchanged; granted scopes: %s", body.get("scope"))
                return body

    async def cancel(self) -> None:
        """No-op — kept for API compatibility with the old device-code flow."""
        return None


def start_flow() -> AuthFlow:
    return AuthFlow()
