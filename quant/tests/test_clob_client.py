"""CLOB client tests — request construction + parsing, incl. the prices-history
``market=<token_id>`` gotcha."""

from __future__ import annotations

from app.config import Settings
from app.polymarket.clob_client import ClobClient

TOKEN = "71321045679252212594626385532706912750332728571942532289631379312455583992563"


async def test_get_book_request_and_parse(load_fixture, capturing_client):
    client, captured = capturing_client(load_fixture("clob_book_full.json"))
    async with client:
        book = await ClobClient(client, Settings()).get_book(TOKEN)

    req = captured["request"]
    assert req.url.host == "clob.polymarket.com"
    assert req.url.path == "/book"
    assert req.url.params["token_id"] == TOKEN
    assert len(book.bids) == 3
    assert len(book.asks) == 3
    assert book.bids[0].price == "0.51"


async def test_get_midpoint(load_fixture, capturing_client):
    client, captured = capturing_client(load_fixture("clob_midpoint.json"))
    async with client:
        mid = await ClobClient(client, Settings()).get_midpoint(TOKEN)
    assert captured["request"].url.path == "/midpoint"
    assert captured["request"].url.params["token_id"] == TOKEN
    assert mid.midpoint == "0.525"


async def test_get_spread(load_fixture, capturing_client):
    client, captured = capturing_client(load_fixture("clob_spread.json"))
    async with client:
        spread = await ClobClient(client, Settings()).get_spread(TOKEN)
    assert captured["request"].url.path == "/spread"
    assert captured["request"].url.params["token_id"] == TOKEN
    assert spread.spread == "0.03"


async def test_get_prices_history_uses_market_param_with_token_id(
    load_fixture, capturing_client
):
    client, captured = capturing_client(load_fixture("clob_prices_history.json"))
    async with client:
        hist = await ClobClient(client, Settings()).get_prices_history(
            TOKEN, interval="1h", fidelity=5
        )

    params = captured["request"].url.params
    assert captured["request"].url.path == "/prices-history"
    # The gotcha: param is named 'market' but carries the TOKEN id (not a condition id).
    assert params["market"] == TOKEN
    assert "token_id" not in params
    assert params["interval"] == "1h"
    assert params["fidelity"] == "5"
    assert len(hist.history) == 3
