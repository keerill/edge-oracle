"""Gamma client tests — request construction + response parsing via MockTransport."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.polymarket.gamma_client import GammaClient


async def test_list_active_markets_request_and_parse(load_fixture, capturing_client):
    client, captured = capturing_client(load_fixture("gamma_markets.json"))
    async with client:
        markets = await GammaClient(client, Settings()).list_active_markets()

    req = captured["request"]
    assert req.url.host == "gamma-api.polymarket.com"
    assert req.url.path == "/markets"
    params = req.url.params
    assert params["active"] == "true"
    assert params["closed"] == "false"
    assert params["enableOrderBook"] == "true"
    assert params["order"] == "liquidity"
    assert params["ascending"] == "false"
    assert int(params["limit"]) <= 500

    assert len(markets) == 6  # all six fixture markets are structurally valid raw
    assert markets[0].id == "501234"


async def test_list_active_markets_skips_malformed_items(load_fixture, capturing_client):
    good = load_fixture("gamma_markets.json")[0]
    bad = load_fixture("gamma_market_malformed.json")  # missing conditionId
    client, _ = capturing_client([good, bad])
    async with client:
        markets = await GammaClient(client, Settings()).list_active_markets()
    assert len(markets) == 1  # the malformed market is skipped, not fatal
    assert markets[0].id == "501234"


async def test_list_active_markets_rejects_non_list(capturing_client):
    client, _ = capturing_client({"error": "unexpected object"})
    async with client:
        with pytest.raises(ValueError):
            await GammaClient(client, Settings()).list_active_markets()


async def test_ascending_flag_serialized(capturing_client):
    client, captured = capturing_client([])
    async with client:
        await GammaClient(client, Settings()).list_active_markets(ascending=True, order="volume24hr")
    params = captured["request"].url.params
    assert params["ascending"] == "true"
    assert params["order"] == "volume24hr"
