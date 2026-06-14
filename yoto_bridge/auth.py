"""Device-code OAuth flow with explicit scopes.

The yoto_api library only requests offline_access in its built-in device-code
flow, which yields tokens with no API scopes. Until that's fixed upstream, we
run the flow here with the full scope list and hand the resulting refresh token
to YotoClient via set_refresh_token.
"""

import asyncio
import logging
from typing import Any

import aiohttp

from . import config

log = logging.getLogger(__name__)


class AuthFlow:
    """In-progress device-code flow plus a background polling task.

    states: 'pending' -> 'linked' | 'error'
    """

    def __init__(self, auth_response: dict, session: aiohttp.ClientSession) -> None:
        self.verification_uri: str = (
            auth_response.get("verification_uri_complete") or auth_response["verification_uri"]
        )
        self.user_code: str = auth_response.get("user_code", "")
        self.expires_in: int = int(auth_response.get("expires_in", 300))
        self.token_blob: dict | None = None
        self.error: str | None = None
        self._session = session
        self._task: asyncio.Task[Any] = asyncio.create_task(self._run(auth_response))

    @property
    def state(self) -> str:
        if self.token_blob is not None:
            return "linked"
        if self.error is not None:
            return "error"
        return "pending"

    async def _run(self, auth_response: dict) -> None:
        try:
            self.token_blob = await _poll_for_token(self._session, auth_response)
            log.info("Device-code flow complete; granted scopes: %s", self.token_blob.get("scope"))
        except Exception as e:  # noqa: BLE001
            log.exception("Device-code flow failed")
            self.error = str(e)
        finally:
            if not self._session.closed:
                await self._session.close()

    async def cancel(self) -> None:
        if not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if not self._session.closed:
            await self._session.close()


async def start_flow() -> AuthFlow:
    """Request a device code and return an AuthFlow that polls for the token."""
    session = aiohttp.ClientSession()
    try:
        auth_response = await _request_device_code(session)
    except Exception:
        await session.close()
        raise
    return AuthFlow(auth_response, session)


async def _request_device_code(session: aiohttp.ClientSession) -> dict:
    async with session.post(
        config.YOTO_AUTH_URL,
        data={
            "client_id": config.CLIENT_ID,
            "scope": config.SCOPES,
            "audience": config.YOTO_AUDIENCE,
        },
    ) as r:
        body = await r.json(content_type=None)
        if not r.ok:
            raise RuntimeError(f"device code request failed: {r.status} {body}")
        return body


async def _poll_for_token(session: aiohttp.ClientSession, auth_response: dict) -> dict:
    device_code = auth_response["device_code"]
    interval = int(auth_response.get("interval", 5))
    expires_in = int(auth_response.get("expires_in", 300))
    waited = 0
    while waited < expires_in:
        async with session.post(
            config.YOTO_TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": config.CLIENT_ID,
                "audience": config.YOTO_AUDIENCE,
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
                raise RuntimeError("authorization code expired; retry")
            else:
                raise RuntimeError(f"token poll failed: {body}")
        await asyncio.sleep(interval)
        waited += interval
    raise RuntimeError("device-code flow timed out")
