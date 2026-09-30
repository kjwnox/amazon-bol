import pytest

from app.fees import CommissionRule, Tier
from app.profit import PlatformCosts, ProfitInputs, calculate, price_difference

KITCHEN = CommissionRule("amazon_nl", "kitchen", "Kitchen", "flat", [Tier(None, 15)], min_fee=0.30,
                         status="official_verified")


def fba_costs(**kw):
    base = dict(fulfilment_model="FBA", sale_vat_rate=0.21, fulfilment_fee_per_unit=4.20, fulfilment_surcharge_pct=1.5,
                packaging_per_unit=0.30, return_allowance_pct=3, ad_spend_pct=5)
    base.update(kw)
    return PlatformCosts(**base)


def inputs(**kw):
    base = dict(platform="amazon_nl", sale_price_gross=29.95, purchase_price_excl_vat=10.0, purchase_vat_rate=0.21,
                import_cost_per_unit=0.50, rule=KITCHEN, costs=fba_costs())
    base.update(kw)
    return ProfitInputs(**base)


def test_full_fba_example_by_hand():
    r = calculate(inputs())
    revenue = 29.95 / 1.21                       # 24.7521
    commission = 29.95 * 0.15                    # 4.4925
    fba = 4.20 * 1.015                           # 4.263 (FBA + %1,5 ek ücret)
    other = 0.30 + revenue * 0.03 + revenue * 0.05
    expected = revenue - commission - fba - other - 10.0 - 0.50
    assert r.status == "exact"
    assert r.revenue_excl_vat == pytest.approx(revenue)
    assert r.net_profit == pytest.approx(expected)        # ≈ €3,22
    assert r.margin_pct == pytest.approx(expected / revenue * 100)
    assert r.roi_pct == pytest.approx(expected / 10.5 * 100)
    labels = [l.label for l in r.lines]
    assert any("FBA ek ücreti" in l for l in labels)       # fulfilment modeline bağlı ek ücret ayrı kalem
    assert labels[0].startswith("Satış fiyatı") and labels[-1] == "Net kâr"


def test_break_even_price_gives_zero_profit():
    r = calculate(inputs())
    be = r.break_even_price
    assert be is not None and be < 29.95
    at_be = calculate(inputs(sale_price_gross=be)).net_profit
    below = calculate(inputs(sale_price_gross=be - 0.02)).net_profit
    assert at_be >= 0 and below < 0


def test_break_even_with_threshold_tier():
    home = CommissionRule("amazon_nl", "home", "Home", "threshold", [Tier(20, 8), Tier(None, 15)], min_fee=0.3,
                          status="official_verified")
    r = calculate(inputs(rule=home, sale_price_gross=34.95, purchase_price_excl_vat=11.2, import_cost_per_unit=0.9))
    at_be = calculate(inputs(rule=home, sale_price_gross=r.break_even_price, purchase_price_excl_vat=11.2,
                             import_cost_per_unit=0.9)).net_profit
    assert at_be >= 0


def test_no_purchase_price_shows_no_profit():
    r = calculate(inputs(purchase_price_excl_vat=None))
    assert r.status == "no_purchase_price"
    assert r.net_profit is None and r.margin_pct is None and r.roi_pct is None
    assert r.max_purchase_price is not None   # yalnızca başabaş alış fiyatı


def test_missing_commission_rule_blocks_profit():
    r = calculate(inputs(rule=None))
    assert r.status == "commission_missing"
    assert r.net_profit is None
    assert "Platform komisyon oranı" in r.missing


def test_unverified_commission_is_estimated_not_exact():
    rule = CommissionRule("bol", "kitchen", "Keuken", "flat", [Tier(None, 12.5)], base="price", status="unverified")
    r = calculate(inputs(platform="bol", rule=rule))
    assert r.status == "estimated"
    assert "Komisyon oranı doğrulanmadı" in r.missing


def test_user_entered_commission_can_be_exact():
    rule = CommissionRule("bol", "kitchen", "Keuken", "flat", [Tier(None, 12.5)], base="price", status="user_entered")
    assert calculate(inputs(platform="bol", rule=rule)).status == "exact"


def test_missing_fba_fee_is_estimated():
    r = calculate(inputs(costs=fba_costs(fulfilment_fee_per_unit=None)))
    assert r.status == "estimated"
    assert "FBA fulfilment ücreti" in r.missing


def test_missing_import_cost_is_estimated():
    r = calculate(inputs(import_cost_per_unit=None))
    assert r.status == "estimated"
    assert "İthalat/gümrük maliyeti" in r.missing


def test_fbm_uses_seller_shipping():
    costs = PlatformCosts(fulfilment_model="FBM", sale_vat_rate=0.21, seller_shipping_per_unit=4.25,
                          packaging_per_unit=0.3, return_allowance_pct=3, ad_spend_pct=5)
    r = calculate(inputs(costs=costs))
    assert r.status == "exact"
    assert any("Satıcı kargosu" in l.label and l.amount == pytest.approx(-4.25) for l in r.lines)


def test_not_vat_registered_adds_non_recoverable_vat():
    registered = calculate(inputs()).net_profit
    r = calculate(inputs(vat_registered=False))
    commission_vat = 29.95 * 0.15 * 0.21
    fba_vat = 4.263 * 0.21
    purchase_vat = 10.0 * 0.21
    assert r.net_profit == pytest.approx(registered - commission_vat - fba_vat - purchase_vat)
    assert r.roi_pct == pytest.approx(r.net_profit / (10.5 + purchase_vat) * 100)


def test_plan_fee_allocation():
    r = calculate(inputs(costs=fba_costs(monthly_plan_fee=39.0, expected_monthly_units=150)))
    base = calculate(inputs()).net_profit
    assert r.net_profit == pytest.approx(base - 39 / 150)


def test_plan_fee_without_units_is_estimated():
    r = calculate(inputs(costs=fba_costs(monthly_plan_fee=39.0, expected_monthly_units=None)))
    assert r.status == "estimated"


def test_reduced_vat_rate():
    r = calculate(inputs(costs=fba_costs(sale_vat_rate=0.09)))
    assert r.revenue_excl_vat == pytest.approx(29.95 / 1.09)


def test_price_difference_uses_lower_price_as_base():
    d = price_difference(12.0, 10.0)
    assert d["diff_eur"] == 2.0 and d["diff_pct"] == 20.0 and d["base"] == "düşük fiyat"
