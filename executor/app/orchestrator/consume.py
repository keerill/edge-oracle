"""CLI: run the signals consumer — auto-propose intents from edge:signals (dry-run).

    python -m app.orchestrator.consume

Subscribes to the advisor's Redis ``signals_channel`` and proposes a (pending-approval) intent for
each actionable directional signal. Approve them with ``python -m app.orchestrator.approve <id>``.
Nothing is signed or submitted here, and nothing reaches a network while ``EDGE_EXEC_DRY_RUN``.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime

from app.config import get_settings
from app.db.engine import get_sessionmaker
from app.orchestrator.consumer import redis_messages, run_consumer
from app.orchestrator.deps import signer_from_settings


async def _run() -> None:
    settings = get_settings()
    signer = signer_from_settings(settings)
    if signer is None:
        print("no signer key configured (EDGE_EXEC_SIGNER_PRIVATE_KEY)", file=sys.stderr)
        raise SystemExit(3)
    await run_consumer(
        redis_messages(settings),
        settings=settings,
        signer=signer,
        sessionmaker=get_sessionmaker(),
        now_fn=lambda: datetime.now(tz=UTC),
        id_fn=lambda: str(uuid.uuid4()),
    )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
