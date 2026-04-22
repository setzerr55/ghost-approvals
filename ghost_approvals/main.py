"""Entry point: boot the Telegram bot and run forever."""

from __future__ import annotations

import asyncio
import logging
import signal

from .bot import build_application
from .config import get_settings
from .db import DB, init_db
from .rpc import AlchemyRPC


async def _amain() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("ghost_approvals")

    await init_db(settings.db_path)
    db = DB(settings.db_path)
    rpc = AlchemyRPC(settings.alchemy_api_key)

    app = build_application(settings, db, rpc)

    stop_event = asyncio.Event()

    def _stop(*_args: object) -> None:
        log.info("shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            # Windows — we don't target it, but be defensive
            pass

    await app.initialize()
    await app.start()
    assert app.updater is not None
    await app.updater.start_polling(drop_pending_updates=True)
    log.info("Ghost Approvals bot is live. Press Ctrl-C to stop.")

    try:
        await stop_event.wait()
    finally:
        log.info("stopping…")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await rpc.close()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
