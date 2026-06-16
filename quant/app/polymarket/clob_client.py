"""Typed CLOB client — order book + pricing reads (public, no auth).

``get_book`` is the only method on the poller's hot path. ``get_midpoint``,
``get_spread`` and ``get_prices_history`` are deliverables (fixture-tested) but are
not wired into the scan loop. Every response is boundary-validated.
"""

from __future__ import annotations

import httpx

from app.config import Settings
from app.polymarket.http import request_json
from app.polymarket.schemas import (
    RawMidpoint,
    RawOrderBook,
    RawPricesHistory,
    RawSpread,
)


class ClobClient:
    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def get_book(self, token_id: str) -> RawOrderBook:
        """GET /book?token_id= — full order-book depth for a token."""
        url = f"{self._settings.clob_base_url}/book"
        data = await request_json(
            self._client, url, settings=self._settings, params={"token_id": token_id}
        )
        return RawOrderBook.model_validate(data)

    async def get_midpoint(self, token_id: str) -> RawMidpoint:
        """GET /midpoint?token_id=."""
        url = f"{self._settings.clob_base_url}/midpoint"
        data = await request_json(
            self._client, url, settings=self._settings, params={"token_id": token_id}
        )
        return RawMidpoint.model_validate(data)

    async def get_spread(self, token_id: str) -> RawSpread:
        """GET /spread?token_id=."""
        url = f"{self._settings.clob_base_url}/spread"
        data = await request_json(
            self._client, url, settings=self._settings, params={"token_id": token_id}
        )
        return RawSpread.model_validate(data)

    async def get_prices_history(
        self,
        token_id: str,
        *,
        interval: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
        fidelity: int | None = None,
    ) -> RawPricesHistory:
        """GET /prices-history.

        GOTCHA: the query param is named ``market`` but it takes the **token id**
        (not the condition id).
        """
        url = f"{self._settings.clob_base_url}/prices-history"
        params: dict[str, object] = {"market": token_id}
        if interval is not None:
            params["interval"] = interval
        if start_ts is not None:
            params["startTs"] = start_ts
        if end_ts is not None:
            params["endTs"] = end_ts
        if fidelity is not None:
            params["fidelity"] = fidelity
        data = await request_json(self._client, url, settings=self._settings, params=params)
        return RawPricesHistory.model_validate(data)
