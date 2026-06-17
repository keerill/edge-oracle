"""Typed Gamma client — market discovery. Thin adapter: fetch + boundary-validate.

Ranking/transform happen downstream (ingestion.transform) so they stay pure and
unit-testable. A single malformed market in a large response is skipped (logged),
not allowed to fail discovery of the rest.
"""

from __future__ import annotations

import logging

import httpx
from pydantic import ValidationError

from collections.abc import Sequence

from app.config import Settings
from app.polymarket.http import request_json
from app.polymarket.schemas import RawGammaEventRef, RawGammaMarket, RawGammaTag

logger = logging.getLogger(__name__)


class GammaClient:
    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def list_active_markets(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        order: str = "liquidity",
        ascending: bool = False,
    ) -> list[RawGammaMarket]:
        """GET /markets filtered to active, open, order-book-enabled markets,
        ranked by ``order`` (default liquidity desc)."""
        url = f"{self._settings.gamma_base_url}/markets"
        params = {
            "active": "true",
            "closed": "false",
            "enableOrderBook": "true",
            "order": order,
            "ascending": "true" if ascending else "false",
            "limit": min(limit or self._settings.gamma_page_limit, 500),
            "offset": offset,
        }
        data = await request_json(self._client, url, settings=self._settings, params=params)
        if not isinstance(data, list):
            raise ValueError(
                f"Gamma /markets returned {type(data).__name__}, expected a list"
            )
        markets: list[RawGammaMarket] = []
        for item in data:
            try:
                markets.append(RawGammaMarket.model_validate(item))
            except ValidationError as exc:
                logger.warning("skipping malformed Gamma market: %s", exc)
        return markets

    async def fetch_resolutions(
        self, condition_ids: Sequence[str]
    ) -> list[RawGammaMarket]:
        """GET /markets?closed=true&condition_ids=... — the resolved markets among the given
        ids (pending ones are simply absent). Used by the resolution-watcher; a malformed
        market is skipped, not fatal."""
        ids = [c for c in dict.fromkeys(condition_ids) if c]
        if not ids:
            return []
        url = f"{self._settings.gamma_base_url}/markets"
        params = {"closed": "true", "condition_ids": ids, "limit": 500}
        data = await request_json(self._client, url, settings=self._settings, params=params)
        if not isinstance(data, list):
            raise ValueError(f"Gamma /markets returned {type(data).__name__}, expected a list")
        out: list[RawGammaMarket] = []
        for item in data:
            try:
                out.append(RawGammaMarket.model_validate(item))
            except ValidationError as exc:
                logger.warning("skipping malformed resolved market: %s", exc)
        return out

    async def fetch_event_tags(
        self, event_ids: Sequence[str]
    ) -> dict[str, list[RawGammaTag]]:
        """GET /events?id=...&id=... — event tags (NOT returned by /markets), batched.

        Returns ``event_id -> tags``. Used during discovery to derive a fee category for
        markets Gamma left uncategorized. A malformed event is skipped, not fatal."""
        ids = [e for e in dict.fromkeys(event_ids) if e]  # dedupe, drop empties, keep order
        if not ids:
            return {}
        url = f"{self._settings.gamma_base_url}/events"
        data = await request_json(
            self._client, url, settings=self._settings, params={"id": ids}
        )
        if not isinstance(data, list):
            raise ValueError(f"Gamma /events returned {type(data).__name__}, expected a list")
        out: dict[str, list[RawGammaTag]] = {}
        for item in data:
            try:
                event = RawGammaEventRef.model_validate(item)
            except ValidationError as exc:
                logger.warning("skipping malformed Gamma event: %s", exc)
                continue
            if event.id:
                out[event.id] = event.tags
        return out
