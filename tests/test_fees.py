import pytest

from app.connectors.bol import parse_commission
from app.fees import CommissionRule, FeeBand, Tier, compute_commission, parse_bands, parse_tiers


def amazon(mode, tiers, min_fee=0.30):
    return CommissionRule("amazon_nl", "x", "X", mode, tiers, min_fee=min_fee, status="official_verified")


def test_threshold_home_products():
    # Amazon.nl Home Products: ≤ €20 → %8 tüm fiyata, > €20 → %15 tüm fiyata
    rule = amazon("threshold", [Tier(20, 8), Tier(None, 15)])
    assert compute_commission(rule, 19.99).variable_fee == pytest.approx(1.5992)
    assert compute_commission(rule, 20.00).variable_fee == pytest.approx(1.60)
    assert compute_commission(rule, 20.01).variable_fee == pytest.approx(3.0015)


def test_portion_electronic_accessories():
    # €100'e kadar olan kısım %15, aşan kısım %8
    rule = amazon("portion", [Tier(100, 15), Tier(None, 8)])
    assert compute_commission(rule, 150).variable_fee == pytest.approx(15 + 4)
    assert compute_commission(rule, 80).variable_fee == pytest.approx(12)


def test_minimum_referral_fee():
    rule = amazon("flat", [Tier(None, 8)])
    res = compute_commission(rule, 2.00)
    assert res.variable_fee == pytest.approx(0.30)
    assert res.min_fee_applied


def test_amazon_base_includes_shipping():
    rule = amazon("flat", [Tier(None, 15)])
    assert compute_commission(rule, 10.0, shipping_gross=5.0).variable_fee == pytest.approx(2.25)


def test_price_only_base_ignores_shipping():
    rule = CommissionRule("bol", "x", "X", "flat", [Tier(None, 10)], base="price", status="user_entered")
    assert compute_commission(rule, 10.0, shipping_gross=5.0).variable_fee == pytest.approx(1.0)


def test_fixed_fee_bands_by_price():
    rule = CommissionRule("bol", "x", "X", "flat", [Tier(None, 10)], base="price", status="user_entered",
                          fixed_fee_bands=[FeeBand(10, 0.20), FeeBand(20, 0.50), FeeBand(None, 0.95)])
    assert compute_commission(rule, 9.99).fixed_fee == 0.20
    assert compute_commission(rule, 15).fixed_fee == 0.50
    assert compute_commission(rule, 40).fixed_fee == 0.95


def test_vat_inclusive_amounts_are_split():
    rule = CommissionRule("bol", "x", "X", "flat", [Tier(None, 12.1)], base="price", amounts_include_vat=True,
                          status="api")
    res = compute_commission(rule, 100.0, fee_vat_rate=0.21)
    assert res.total_stated == pytest.approx(12.1)
    assert res.total_excl_vat == pytest.approx(10.0)
    assert res.vat_on_fee == pytest.approx(2.1)


def test_parse_tier_syntax():
    tiers = parse_tiers("8@20; 15")
    assert [(t.up_to, t.pct) for t in tiers] == [(20.0, 8.0), (None, 15.0)]
    assert [(b.up_to, b.fee) for b in parse_bands("0,20@10; 0,50")] == [(10.0, 0.2), (None, 0.5)]


def test_bol_commission_api_payload():
    payload = {"ean": "4006381333931", "condition": "NEW", "unitPrice": 29.99, "fixedAmount": 0.99,
               "percentage": 15, "totalCost": 5.49, "reductions": []}
    rule = parse_commission(payload, "4006381333931", "2026-09-30")
    assert rule.status == "api" and rule.amounts_include_vat and rule.base == "price"
    res = compute_commission(rule, 29.99)
    assert res.total_stated == pytest.approx(0.99 + 29.99 * 0.15)
