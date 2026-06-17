"""CLI: approve a pending intent, then sign + dry-run submit.

    python -m app.orchestrator.approve <intent_id>

The human step of the semi-auto flow: mints an approval token bound to the stored intent's exact
hash, records the approval (token HASH only), signs, and dry-run-submits. Nothing reaches a network
while ``EDGE_EXEC_DRY_RUN`` is true.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime

from app.config import get_settings
from app.db.engine import get_sessionmaker
from app.orchestrator.deps import signer_from_settings
from app.orchestrator.workflow import approve_and_sign


async def _run(intent_id: str) -> None:
    settings = get_settings()
    signer = signer_from_settings(settings)
    if signer is None:
        print("no signer key configured (EDGE_EXEC_SIGNER_PRIVATE_KEY)", file=sys.stderr)
        raise SystemExit(3)
    if not settings.approval_secret:
        print("no approval secret configured (EDGE_EXEC_APPROVAL_SECRET)", file=sys.stderr)
        raise SystemExit(3)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await approve_and_sign(
            session, intent_id, signer=signer, settings=settings, now=datetime.now(tz=UTC)
        )
        await session.commit()
    print(json.dumps({"intent_id": result.intent_id, "status": result.status, "reasons": list(result.reasons)}))


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m app.orchestrator.approve <intent_id>", file=sys.stderr)
        raise SystemExit(2)
    asyncio.run(_run(sys.argv[1]))


if __name__ == "__main__":
    main()
