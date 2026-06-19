"""One-shot probe to discover a Yoto API endpoint listing all alarm tones.

The bridge currently only finds tones that are *already assigned* as alarms
on a player. The user has access to more tones in the Yoto app (e.g. surf's
up, fanfare, harp rise) that aren't currently scheduled. The official app
must fetch this list from somewhere — we just need to find the endpoint.

Run with the bridge stopped (uses the same token file).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from yoto_bridge import config  # noqa: E402

CANDIDATES = [
    "/card/system/alarm-sounds",
    "/card/system/alarms",
    "/card/system/sounds",
    "/card/system/alarm-tones",
    "/card/system/tones",
    "/card/system/library",
    "/card/system/preset",
    "/card/system/presets",
    "/card/family/alarm-sounds",
    "/card/family/sounds",
    "/card/family/tones",
    "/card/family/system",
    "/card/family/library/alarms",
    "/card/family/library/sounds",
    "/card/family/library/tones",
    "/card/preset/alarms",
    "/card/presets",
    "/card/club",
    "/card/clubLibrary",
    "/card/clublibrary",
    "/club/library",
    "/club/alarms",
    "/club",
    "/preset",
    "/presets",
    "/system",
    "/system/alarms",
    "/system/alarm-sounds",
    "/system/sounds",
    "/system/library",
    "/yoto-club",
    "/account/preferences",
    "/account/sounds",
    "/me/alarms",
    "/me/sounds",
]


async def main() -> None:
    blob = json.loads(config.TOKEN_FILE.read_text())
    access_token = blob.get("access_token") or blob.get("token")
    if not access_token:
        print("No access_token in token file; refresh first.")
        return

    headers = {"Authorization": f"Bearer {access_token}"}
    timeout = aiohttp.ClientTimeout(total=10.0)
    base = "https://api.yotoplay.com"
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        for path in CANDIDATES:
            try:
                async with session.get(base + path, allow_redirects=False) as r:
                    body = await r.text()
                    ctype = r.headers.get("content-type", "")
                    snip = body[:200].replace("\n", " ")
                    print(f"{path:35} {r.status:3}  ct={ctype.split(';')[0]:30}  {snip}")
            except Exception as e:
                print(f"{path:35} ERR  {e}")


if __name__ == "__main__":
    asyncio.run(main())
