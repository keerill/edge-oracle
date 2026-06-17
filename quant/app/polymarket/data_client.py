"""Typed Data API client — trade prints (public, no auth).

The Data API (``data-api.polymarket.com``) serves executed trades. Prices/sizes come as JSON
numbers, so we decode with ``parse_float=str`` to keep the exact wire literal as a string
(the Decimal coercion happens once, downstream, in ``ingestion.trades_transform``).
"""

from __future__ import annotations

import httpx

from app.config import Settings
from app.polymarket.http import request_json
from app.polymarket.schemas import RawTrade


class DataClient:
    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def get_trades(
        self, *, condition_id: str | None = None, limit: int | None = None
    ) -> list[RawTrade]:
        """GET /trades — recent trade prints, optionally filtered to one ``market`` (condition id).

        GOTCHA: the query param is named ``market`` but it takes the **condition id**.
        """
        url = f"{self._settings.data_base_url}/trades"
        params: dict[str, object] = {}
        if condition_id is not None:
            params["market"] = condition_id
        if limit is not None:
            params["limit"] = limit
        data = await request_json(
            self._client, url, settings=self._settings, params=params, parse_float=str
        )
        if not isinstance(data, list):
            raise ValueError(f"expected a JSON array of trades, got {type(data).__name__}")
        return [RawTrade.model_validate(item) for item in data]
