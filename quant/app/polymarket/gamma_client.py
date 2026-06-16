"""Typed Gamma client — market discovery. Thin adapter: fetch + boundary-validate.

Ranking/transform happen downstream (ingestion.transform) so they stay pure and
unit-testable. A single malformed market in a large response is skipped (logged),
not allowed to fail discovery of the rest.
"""

from __future__ import annotations

import logging

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.polymarket.http import request_json
from app.polymarket.schemas import RawGammaMarket

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
