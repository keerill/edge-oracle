"""Data API client tests — request construction + parsing, incl. the ``market=<condition_id>``
param and the parse_float=str money-preservation path."""

from __future__ import annotations

from app.config import Settings
from app.polymarket.data_client import DataClient

COND = "0x5ed1ac33d62753202213323b9fd4acb5fb5ea5ced7a187784cc697fbec296f54"


async def test_get_trades_request_and_parse(load_fixture, capturing_client):
    client, captured = capturing_client(load_fixture("data_trades.json"))
    async with client:
        trades = await DataClient(client, Settings()).get_trades(condition_id=COND, limit=2)

    req = captured["request"]
    assert req.url.host == "data-api.polymarket.com"
    assert req.url.path == "/trades"
    assert req.url.params["market"] == COND  # gotcha: param named "market" takes the condition id
    assert req.url.params["limit"] == "2"
    assert len(trades) == 2
    assert trades[0].asset.endswith("265187")
    assert trades[0].side == "BUY"
    # money kept as the exact wire literal string at the boundary (no float)
    assert trades[0].price == "0.8099999954639995"
    assert trades[1].price == "0.32"


async def test_get_trades_no_filter_omits_market_param(load_fixture, capturing_client):
    client, captured = capturing_client(load_fixture("data_trades.json"))
    async with client:
        await DataClient(client, Settings()).get_trades()
    assert "market" not in captured["request"].url.params
