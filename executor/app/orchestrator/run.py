"""CLI: propose an intent from an advisor-signal JSON (dry-run, no broadcast).

    python -m app.orchestrator.run <signal.json>

Reads one ``AdvisedSignalView`` JSON (e.g. a line off the ``edge:signals`` Redis channel),
forms + persists the intent, runs the breakers, and — in manual mode — stops at
``pending_approval``. Prints the ``intent_id`` to approve next. Nothing reaches a network.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime

from app.config import get_settings
from app.db.engine import get_sessionmaker
from app.models.advised import AdvisedSignalView
from app.orchestrator.deps import signer_from_settings
from app.orchestrator.workflow import propose_signal


async def _run(path: str) -> None:
    settings = get_settings()
    signer = signer_from_settings(settings)
    if signer is None:
        print("no signer key configured (EDGE_EXEC_SIGNER_PRIVATE_KEY)", file=sys.stderr)
        raise SystemExit(3)
    advised = AdvisedSignalView.model_validate_json(open(path).read())
    sessionmaker = get_sessionmaker()
    intent_id = str(uuid.uuid4())
    async with sessionmaker() as session:
        result = await propose_signal(
            session, advised, signer=signer, settings=settings,
            now=datetime.now(tz=UTC), intent_id=intent_id,
        )
        await session.commit()
    print(json.dumps({"intent_id": result.intent_id, "status": result.status, "reasons": list(result.reasons)}))


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m app.orchestrator.run <signal.json>", file=sys.stderr)
        raise SystemExit(2)
    asyncio.run(_run(sys.argv[1]))


if __name__ == "__main__":
    main()
