"""Category enrichment — the /events tag fetch + the discovery-time resolution.

Covers the client request/parse (the ``id`` multi-param + tag parsing) and the pure-decision
enrichment: only uncategorized selected markets get a category, failures fall back to defaults.
"""

from __future__ import annotations

from decimal import Decimal

from app.config import Settings
from app.ingestion.scanner import _resolve_categories
from app.models.market import Market
from app.polymarket.gamma_client import GammaClient
from app.polymarket.schemas import RawGammaTag


def _market(mid, *, category=None, event_id="e1") -> Market:
    return Market(
        market_id=mid, condition_id="c" + mid, question="q", slug="s",
        category=category, event_id=event_id, yes_token_id="1", no_token_id="2",
        enable_order_book=True, active=True, closed=False, liquidity=Decimal("1"),
    )


# --- client ----------------------------------------------------------------

async def test_fetch_event_tags_request_and_parse(load_fixture, capturing_client):
    client, captured = capturing_client(load_fixture("gamma_events.json"))
    async with client:
        tags = await GammaClient(client, Settings()).fetch_event_tags(["23784", "23785"])
    req = captured["request"]
    assert req.url.path == "/events"
    assert req.url.params.get_list("id") == ["23784", "23785"]  # repeated id param
    assert [t.slug for t in tags["23784"]] == ["pop-culture", "all", "politics", "gta-vi"]
    assert [t.slug for t in tags["23785"]] == ["crypto", "bitcoin"]


async def test_fetch_event_tags_empty_input_skips_request():
    # no ids -> no HTTP call, empty result
    assert await GammaClient.fetch_event_tags(GammaClient(None, Settings()), []) == {}


async def test_fetch_resolutions_request_and_parse(load_fixture, capturing_client):
    client, captured = capturing_client(load_fixture("gamma_markets.json"))
    async with client:
        out = await GammaClient(client, Settings()).fetch_resolutions(["0xc1", "0xc2"])
    req = captured["request"]
    assert req.url.path == "/markets"
    assert req.url.params["closed"] == "true"
    assert req.url.params.get_list("condition_ids") == ["0xc1", "0xc2"]
    assert len(out) >= 1  # parsed RawGammaMarket list


async def test_fetch_resolutions_empty_input_skips_request():
    assert await GammaClient.fetch_resolutions(GammaClient(None, Settings()), []) == []


# --- discovery enrichment --------------------------------------------------

class FakeGamma:
    def __init__(self, tags_by_event, *, fail=False):
        self._tags = tags_by_event
        self._fail = fail
        self.requested: list[str] | None = None

    async def fetch_event_tags(self, event_ids):
        self.requested = list(event_ids)
        if self._fail:
            raise RuntimeError("boom")
        return {e: self._tags.get(e, []) for e in event_ids}


async def test_resolve_fills_only_uncategorized():
    markets = [
        _market("m1", category="crypto", event_id="e1"),  # already known -> untouched
        _market("m2", category=None, event_id="e2"),       # -> politics
        _market("m3", category=None, event_id="e3"),       # tags unknown -> stays None
    ]
    gamma = FakeGamma({
        "e2": [RawGammaTag(slug="politics")],
        "e3": [RawGammaTag(slug="all")],  # generic -> no category
    })
    out = await _resolve_categories(gamma, markets)
    assert {m.market_id: m.category for m in out} == {"m1": "crypto", "m2": "politics", "m3": None}
    assert gamma.requested == ["e2", "e3"]  # only the uncategorized events were fetched


async def test_resolve_no_fetch_when_all_categorized():
    gamma = FakeGamma({})
    out = await _resolve_categories(gamma, [_market("m1", category="sports")])
    assert gamma.requested is None  # no event ids needed -> no call
    assert out[0].category == "sports"


async def test_resolve_failure_falls_back_to_defaults():
    gamma = FakeGamma({}, fail=True)
    out = await _resolve_categories(gamma, [_market("m2", category=None, event_id="e2")])
    assert out[0].category is None  # enrichment failed -> left as-is, never raises
