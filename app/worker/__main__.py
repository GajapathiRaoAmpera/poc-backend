"""Run with: uv run python -m app.worker"""

import asyncio
import logging
import signal

from app.core.db import close_client, database, ensure_indexes
from app.llm.registry import get_provider
from app.modules.agents import load_all
from app.worker.runner import Worker


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    load_all()
    provider = get_provider()  # fail fast on bad provider config
    logging.getLogger(__name__).info("Using LLM provider %s/%s", provider.name, provider.model)
    db = database()
    await ensure_indexes(db)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await Worker(db, provider_factory=get_provider).run_forever(stop)
    finally:
        await close_client()


if __name__ == "__main__":
    asyncio.run(main())
