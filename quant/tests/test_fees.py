"""Per-category taker fee — pure math (SPEC §6). Worked examples against the published peak
rates, the φ-peaks-at-0.5 shape, the geopolitical zero, and the conservative unknown default.

    φ_cat(p) = feeRate · (p·(1−p))^exp        # peaks at p=0.5
    fee_per_share(p) = p · φ_cat(p)
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.math.fees import fee_per_share, phi


def q(x, places="0.0000001") -> Decimal:
    return Decimal(x).quantize(Decimal(places))


# --- peak effective rate (φ at p=0.5) matches the published table -----------

@pytest.mark.parametrize(
    "category, peak",
    [
        ("crypto", "0.018"),       # 0.072 · 0.25         = 1.80%
        ("politics", "0.010"),     # 0.040 · 0.25         = 1.00%
        ("finance", "0.010"),      # 0.040 · 0.25         = 1.00%
        ("sports", "0.0075"),      # 0.030 · 0.25         = 0.75%
        ("economics", "0.015"),    # 0.030 · sqrt(0.25)   = 1.50%
        ("geopolitical", "0"),     # 0                    = 0%
    ],
)
def test_phi_peak_matches_published_rate(category, peak):
    assert q(phi(Decimal("0.5"), category)) == q(peak)


def test_unknown_and_none_use_conservative_crypto_rate():
    assert phi(Decimal("0.5"), "something-else") == phi(Decimal("0.5"), "crypto")
    assert phi(Decimal("0.5"), None) == phi(Decimal("0.5"), "crypto")


def test_category_is_case_insensitive():
    assert phi(Decimal("0.5"), "Crypto") == phi(Decimal("0.5"), "crypto")


def test_phi_peaks_at_one_half():
    # crypto: φ(0.3) < φ(0.5)
    assert phi(Decimal("0.3"), "crypto") < phi(Decimal("0.5"), "crypto")
    assert phi(Decimal("0.7"), "crypto") == phi(Decimal("0.3"), "crypto")  # symmetric


def test_fee_per_share_worked_examples():
    # crypto at p=0.5: 0.5 · 0.018 = 0.009
    assert q(fee_per_share(Decimal("0.5"), "crypto")) == q("0.009")
    # crypto at p=0.4: φ = 0.072·0.24 = 0.01728; fee = 0.4·0.01728 = 0.006912
    assert q(fee_per_share(Decimal("0.4"), "crypto")) == q("0.006912")
    # economics at p=0.5: φ = 0.015; fee = 0.0075
    assert q(fee_per_share(Decimal("0.5"), "economics")) == q("0.0075")


def test_extremes_have_zero_fee():
    # p·(1−p) = 0 at the boundaries -> no fee
    assert fee_per_share(Decimal("0"), "crypto") == 0
    assert fee_per_share(Decimal("1"), "crypto") == 0


def test_geopolitical_is_free_everywhere():
    assert phi(Decimal("0.5"), "geopolitical") == 0
    assert fee_per_share(Decimal("0.42"), "geopolitical") == 0


def test_rejects_out_of_range_price():
    with pytest.raises(ValueError):
        phi(Decimal("1.5"), "crypto")
    with pytest.raises(ValueError):
        fee_per_share(Decimal("-0.1"), "crypto")
