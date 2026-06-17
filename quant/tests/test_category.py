"""Pure category resolution — derive a fee category from Gamma event tags.

Gamma /markets usually omits ``category``; the canonical category is derived from the event's
tags (fetched separately). These pin the vocabulary mapping, that generic tags are ignored, that
an explicit category wins, and that ``market_from_raw`` lands the derived category.
"""

from __future__ import annotations

from app.ingestion.transform import category_from_tags, derive_category, market_from_raw
from app.polymarket.schemas import RawGammaMarket, RawGammaTag


def _tags(*slugs) -> list[RawGammaTag]:
    return [RawGammaTag(slug=s, label=s) for s in slugs]


def test_maps_topical_tag_to_category():
    assert category_from_tags(_tags("politics")) == "politics"
    assert category_from_tags(_tags("bitcoin")) == "crypto"
    assert category_from_tags(_tags("nba")) == "sports"


def test_generic_tags_resolve_to_none():
    assert category_from_tags(_tags("all", "pop-culture", "exchange")) is None
    assert category_from_tags([]) is None


def test_first_mapped_tag_wins_over_generics():
    # real shape: generics first, the topical tag later -> still resolves
    assert category_from_tags(_tags("pop-culture", "all", "politics", "gta-vi")) == "politics"


def test_label_used_when_slug_missing():
    assert category_from_tags([RawGammaTag(label="Crypto")]) == "crypto"


def _raw(**over) -> RawGammaMarket:
    base = dict(
        id="1",
        question="Q",
        slug="q",
        conditionId="0xc",
        outcomes=["Yes", "No"],
        clobTokenIds='["111", "222"]',
        enableOrderBook=True,
        active=True,
        closed=False,
    )
    base.update(over)
    return RawGammaMarket(**base)


def test_explicit_category_wins_and_is_normalized():
    raw = _raw(category="Crypto", events=[{"id": "e1", "tags": [{"slug": "politics"}]}])
    assert derive_category(raw) == "crypto"


def test_derives_from_event_tags_when_category_absent():
    raw = _raw(category=None, events=[{"id": "e1", "tags": [{"slug": "sports"}, {"slug": "nfl"}]}])
    assert derive_category(raw) == "sports"
    # and it lands on the canonical Market
    assert market_from_raw(raw).category == "sports"


def test_no_category_no_tags_is_none():
    raw = _raw(category=None, events=[{"id": "e1"}])
    assert derive_category(raw) is None
    assert market_from_raw(raw).category is None
