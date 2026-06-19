"""Entry point: `uv run python -m yoto_bridge`.

We drive uvicorn's Server.serve() inside our own asyncio.run() so we control the
event loop. uvicorn.run / Server.run on Windows hard-codes ProactorEventLoop,
which aiomqtt (used by yoto_api) cannot use (it needs add_reader/add_writer).
"""

import asyncio
import logging
import sys

import uvicorn

from . import config


def main() -> None:
    # Make our INFO logs reach stdout. uvicorn only configures its own loggers.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        stream=sys.stdout,
    )

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    if not config.CLIENT_ID:
        logging.getLogger(__name__).warning(
            "YOTO_CLIENT_ID is not set; the device-code auth flow will fail "
            "until it's configured (env var or docker-compose.yml).",
        )

    server = uvicorn.Server(
        uvicorn.Config(
            "yoto_bridge.app:app",
            host=config.HOST,
            port=config.PORT,
            log_level=config.LOG_LEVEL,
            loop="asyncio",
        )
    )
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
