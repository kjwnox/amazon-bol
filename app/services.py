"""Ürün analizi: eşleştirme + son fiyatlar + komisyon + kâr + fırsat puanı + uyum durumu."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from . import compliance, scoring
from .connectors import PLATFORM_LABELS
from .db import get_settings
from .fees import CommissionRule
from .history import detect_spikes
from .profit import PlatformCosts, ProfitInputs, ProfitResult, calculate, price_difference


def parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class Context:
    rules: dict
    costs: dict
    settings: dict
    now: datetime

    @classmethod
    def load(cls, conn) -> "Context":
        rules = {(r["platform"], r["category_key"]): CommissionRule.from_row(r)
                 for r in conn.execute("SELECT * FROM commission_rules")}
        costs = {r["platform"]: dict(r) for r in conn.execute("SELECT * FROM platform_costs")}
        return cls(rules, costs, get_settings(conn), datetime.now(timezone.utc))

    def setting(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.settings.get(key) or default)
        except ValueError:
            return default


def find_rule(ctx: Context, platform: str, ean: str | None, category: str | None) -> CommissionRule | None:
    """Öncelik: platform API'sinden gelen EAN bazlı kural → kategori kuralı. Varsayılan kural YOK."""
    if ean and (platform, f"ean:{ean}") in ctx.rules:
        return ctx.rules[(platform, f"ean:{ean}")]
    return ctx.rules.get((platform, category)) if category else None


@dataclass
class PlatformView:
    platform: str
    label: str
    listing: dict
    snapshot: dict
    history: list[tuple[str, float]]
    spikes: list[dict]
    age_hours: float
    stale: bool
    rule: CommissionRule | None
    result: ProfitResult | None
    customer_price: float            # fiyat + bilinen kargo
    inputs: ProfitInputs | None = None


@dataclass
class ProductAnalysis:
    product: dict
    supplier: dict | None
    platforms: list[PlatformView]
    price_diff: dict | None
    best: PlatformView | None
    compliance_rows: list[dict]
    compliance_summary: str
    score: int
    score_reasons: list[tuple[float, str]]
    candidates: list[dict] = field(default_factory=list)
    supplier_options: list[dict] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.best and self.best.result:
            return self.best.result.status
        results = [p.result for p in self.platforms if p.result]
        if not results:
            return "no_listing"
        if all(r.status == "no_purchase_price" for r in results):
            return "no_purchase_price"
        return "commission_missing"

    def view(self, platform: str) -> PlatformView | None:
        return next((p for p in self.platforms if p.platform == platform), None)


MATCHED = ('ean', 'confirmed', 'unmatched', 'manual')


def supplier_items_for(conn, product_id: int) -> list[dict]:
    """Eşleşmiş tedarikçi kayıtları, alış + ithalat (birim maliyet) sırasıyla; fiyatı olmayanlar en sonda."""
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM supplier_items WHERE product_id = ? AND match_status IN {MATCHED}", (product_id,))]
    return sorted(rows, key=lambda r: (r["purchase_price_excl_vat"] is None,
                                       (r["purchase_price_excl_vat"] or 0) + (r["import_cost_per_unit"] or 0)))


def best_supplier_item(conn, product_id: int) -> dict | None:
    """Hesapta kullanılan tedarikçi: birim maliyeti (alış + ithalat) en düşük olan."""
    rows = supplier_items_for(conn, product_id)
    return rows[0] if rows else None


def supplier_options(conn, product_id: int, views: list, ctx: Context) -> list[dict]:
    """Her tedarikçi fiyatıyla her platformdaki kârlılık (aynı satış fiyatı ve maliyet varsayımlarıyla)."""
    stale_days = ctx.setting("quote_stale_days", 90)
    out = []
    for s in supplier_items_for(conn, product_id):
        per_platform = {}
        for v in views:
            if v.inputs is None:
                continue
            res = calculate(replace(v.inputs, purchase_price_excl_vat=s["purchase_price_excl_vat"],
                                    purchase_vat_rate=s["purchase_vat_rate"],
                                    import_cost_per_unit=s["import_cost_per_unit"]))
            per_platform[v.platform] = res
        age_days = None
        if s.get("quote_date"):
            try:
                age_days = (ctx.now - parse_ts(s["quote_date"])).days
            except ValueError:
                age_days = None
        expired = bool(s.get("valid_until") and s["valid_until"] < ctx.now.date().isoformat())
        unit_cost = None if s["purchase_price_excl_vat"] is None else s["purchase_price_excl_vat"] + (s["import_cost_per_unit"] or 0)
        out.append({"item": s, "unit_cost": unit_cost, "results": per_platform, "age_days": age_days,
                    "stale": age_days is not None and age_days > stale_days, "expired": expired})
    return out


def analyze_product(conn, product_id: int, ctx: Context) -> ProductAnalysis:
    product = dict(conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone())
    supplier = best_supplier_item(conn, product_id)
    vat_registered = ctx.settings.get("vat_registered", "1") == "1"
    spike_pct = ctx.setting("spike_threshold_pct", 10)
    stale_hours = ctx.setting("stale_hours", 72)

    views: list[PlatformView] = []
    candidates = []
    for listing in conn.execute("SELECT * FROM listings WHERE product_id = ? ORDER BY platform", (product_id,)):
        listing = dict(listing)
        if listing["match_method"] == "candidate":
            candidates.append(listing)
            continue
        snaps = [dict(s) for s in conn.execute(
            "SELECT * FROM offer_snapshots WHERE listing_id = ? ORDER BY captured_at", (listing["id"],))]
        if not snaps:
            continue
        latest = snaps[-1]
        history = [(s["captured_at"], s["price"]) for s in snaps]
        age = (ctx.now - parse_ts(latest["captured_at"])).total_seconds() / 3600
        platform = listing["platform"]
        rule = find_rule(ctx, platform, product["ean"], listing["platform_category"])
        result = None
        cost_row = ctx.costs.get(platform)
        if cost_row:
            override = conn.execute("SELECT * FROM product_cost_overrides WHERE product_id = ? AND platform = ?",
                                    (product_id, platform)).fetchone()
            costs = PlatformCosts.from_row(cost_row, override)
            if costs.sale_vat_rate is None:
                costs.sale_vat_rate = ctx.setting("default_sale_vat_rate", 0.21)
            inputs = ProfitInputs(
                platform=platform,
                sale_price_gross=latest["price"],
                customer_shipping_gross=latest["shipping_cost"] or 0.0,
                purchase_price_excl_vat=supplier["purchase_price_excl_vat"] if supplier else None,
                purchase_vat_rate=supplier["purchase_vat_rate"] if supplier else None,
                import_cost_per_unit=supplier["import_cost_per_unit"] if supplier else None,
                rule=rule, costs=costs, vat_registered=vat_registered)
            result = calculate(inputs)
        else:
            inputs = None
        views.append(PlatformView(
            platform=platform, label=PLATFORM_LABELS.get(platform, platform), listing=listing, snapshot=latest,
            history=history, spikes=detect_spikes(history, spike_pct), age_hours=age, stale=age > stale_hours,
            rule=rule, result=result, customer_price=latest["price"] + (latest["shipping_cost"] or 0.0),
            inputs=inputs))

    price_diff = None
    if len(views) >= 2:
        lo = min(views, key=lambda v: v.customer_price)
        hi = max(views, key=lambda v: v.customer_price)
        price_diff = price_difference(lo.customer_price, hi.customer_price)
        price_diff.update(low_platform=lo.label, high_platform=hi.label,
                          shipping_unknown=any(v.snapshot["shipping_cost"] is None for v in (lo, hi)))

    computed = [v for v in views if v.result and v.result.net_profit is not None]
    best = max(computed, key=lambda v: v.result.net_profit) if computed else (views[0] if views else None)

    comp_rows = [dict(r) for r in conn.execute(
        "SELECT * FROM compliance_checks WHERE product_id = ? ORDER BY id", (product_id,))]
    comp_summary = compliance.summarize([r["status"] for r in comp_rows])

    if best:
        res = best.result
        total, reasons = scoring.score(scoring.ScoreInput(
            net_profit=res.net_profit if res else None, margin_pct=res.margin_pct if res else None,
            is_exact=bool(res and res.is_exact), seller_count=best.snapshot["seller_count"],
            platform_sells=best.snapshot["platform_sells"], data_age_hours=best.age_hours,
            compliance_summary=comp_summary, stale_hours=stale_hours))
    else:
        total, reasons = 0, [(0, "Pazaryeri verisi yok")]

    return ProductAnalysis(product, supplier, views, price_diff, best, comp_rows, comp_summary, total, reasons, candidates,
                           supplier_options(conn, product_id, views, ctx))


def analyze_all(conn, ctx: Context | None = None) -> list[ProductAnalysis]:
    ctx = ctx or Context.load(conn)
    ids = [r["id"] for r in conn.execute("SELECT id FROM products ORDER BY title")]
    return [analyze_product(conn, pid, ctx) for pid in ids]


def supplier_opportunities(conn) -> list[dict]:
    """Tedarikçi ürünleri ve pazaryeri arama durumları (yeni ürün fırsatları)."""
    items = [dict(r) for r in conn.execute("SELECT * FROM supplier_items ORDER BY supplier, title")]
    for it in items:
        it["searches"] = {r["platform"]: dict(r) for r in conn.execute(
            "SELECT * FROM search_results WHERE supplier_item_id = ?", (it["id"],))}
        listed = set()
        if it["product_id"]:
            listed = {r["platform"] for r in conn.execute(
                "SELECT platform FROM listings WHERE product_id = ? AND match_method != 'candidate'", (it["product_id"],))}
        it["listed_platforms"] = listed
    return items
