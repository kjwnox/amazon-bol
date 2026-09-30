"""Gerçek kârlılık hesabı (birim başına, platform başına).

Tanımlar (arayüzde de gösterilir):
  KDV hariç gelir   = (satış fiyatı + müşteriden alınan kargo) / (1 + satış KDV oranı)
  Net kâr           = KDV hariç gelir − tüm giderler (komisyon, fulfilment, kargo, paketleme, iade payı,
                      reklam, abonelik payı, alış fiyatı, ithalat/gümrük, indirilemeyen KDV)
  Kâr marjı (%)     = net kâr / KDV hariç gelir
  ROI (%)           = net kâr / toplam ürün maliyeti (alış KDV hariç + ithalat/gümrük [+ indirilemeyen alış KDV'si])
  Başabaş fiyatı    = net kârın 0 olduğu en düşük KDV dahil satış fiyatı (diğer varsayımlar sabit)

Durumlar:
  no_purchase_price  — alış fiyatı yok → kârlılık gösterilmez
  commission_missing — komisyon kuralı yok → kullanıcıdan oran istenir, kâr gösterilmez
  estimated          — hesaplandı ama eksik maliyet veya doğrulanmamış komisyon var ("tahmini")
  exact              — tüm kalemler mevcut ve komisyon güvenilir kaynaktan ("kesin")
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .fees import CommissionResult, CommissionRule, STATUS_LABELS, compute_commission

STATUS_TEXT = {
    "no_purchase_price": "Alış fiyatı yok",
    "commission_missing": "Komisyon oranı gerekli",
    "estimated": "Tahmini kâr",
    "exact": "Kesin net kâr",
}

FULFILMENT_LABELS = {
    "FBA": "Fulfilment by Amazon (FBA)",
    "FBM": "Satıcı gönderir (FBM)",
    "FBB": "Logistiek via bol (LVB/FBB)",
    "FBR": "Satıcı gönderir (FBR)",
}
PLATFORM_FULFILMENT = {"FBA", "FBB"}


@dataclass
class PlatformCosts:
    fulfilment_model: str = "FBM"
    sale_vat_rate: float | None = 0.21
    seller_shipping_per_unit: float | None = None
    fulfilment_fee_per_unit: float | None = None
    fulfilment_surcharge_pct: float = 0.0
    packaging_per_unit: float | None = None
    return_allowance_pct: float | None = None
    ad_spend_pct: float | None = None
    transaction_fixed_fee: float = 0.0
    monthly_plan_fee: float = 0.0
    expected_monthly_units: int | None = None

    @classmethod
    def from_row(cls, row, override=None) -> "PlatformCosts":
        r = dict(row)
        o = dict(override) if override else {}
        pick = lambda k: o[k] if o.get(k) is not None else r.get(k)  # noqa: E731
        return cls(
            fulfilment_model=r["fulfilment_model"],
            sale_vat_rate=pick("sale_vat_rate"),
            seller_shipping_per_unit=pick("seller_shipping_per_unit"),
            fulfilment_fee_per_unit=pick("fulfilment_fee_per_unit"),
            fulfilment_surcharge_pct=r.get("fulfilment_surcharge_pct") or 0.0,
            packaging_per_unit=pick("packaging_per_unit"),
            return_allowance_pct=r.get("return_allowance_pct"),
            ad_spend_pct=pick("ad_spend_pct"),
            transaction_fixed_fee=r.get("transaction_fixed_fee") or 0.0,
            monthly_plan_fee=r.get("monthly_plan_fee") or 0.0,
            expected_monthly_units=r.get("expected_monthly_units"),
        )


@dataclass
class ProfitInputs:
    platform: str
    sale_price_gross: float
    customer_shipping_gross: float = 0.0
    purchase_price_excl_vat: float | None = None
    purchase_vat_rate: float | None = 0.21
    import_cost_per_unit: float | None = None
    rule: CommissionRule | None = None
    costs: PlatformCosts = field(default_factory=PlatformCosts)
    vat_registered: bool = True
    fee_vat_rate: float = 0.21


@dataclass
class Line:
    label: str
    amount: float          # işaretli: gelir +, gider −
    kind: str              # price | vat | subtotal | commission | platform_fee | cost | product | result | info
    note: str = ""


@dataclass
class ProfitResult:
    platform: str
    status: str
    lines: list[Line]
    revenue_excl_vat: float
    commission: CommissionResult | None
    net_profit: float | None
    margin_pct: float | None
    roi_pct: float | None
    break_even_price: float | None
    max_purchase_price: float | None   # alış fiyatı yokken: bu satış fiyatında başabaş alış fiyatı
    missing: list[str]
    commission_status: str | None

    @property
    def status_text(self) -> str:
        return STATUS_TEXT[self.status]

    @property
    def is_exact(self) -> bool:
        return self.status == "exact"


def _val(v, missing: list[str], label: str) -> float:
    if v is None:
        missing.append(label)
        return 0.0
    return float(v)


def _compute(inp: ProfitInputs, price: float, collect_lines: bool = True) -> ProfitResult:
    c = inp.costs
    missing: list[str] = []
    lines: list[Line] = []
    add = lines.append if collect_lines else (lambda _l: None)

    vat_rate = _val(c.sale_vat_rate, missing, "Satış KDV oranı")
    gross = price + inp.customer_shipping_gross
    sale_vat = gross - gross / (1 + vat_rate)
    revenue = gross - sale_vat
    add(Line("Satış fiyatı (KDV dahil)", price, "price"))
    if inp.customer_shipping_gross:
        add(Line("Müşteriden alınan kargo (KDV dahil)", inp.customer_shipping_gross, "price"))
    add(Line(f"Satış KDV'si (%{vat_rate * 100:g})", -sale_vat, "vat"))
    add(Line("KDV hariç satış geliri", revenue, "subtotal"))

    # Her gider hem döküm satırına hem de cost_total'a (ürün maliyeti hariç) eklenir.
    cost_total = 0.0
    fee_vat_nonrec = 0.0      # KDV mükellefi değilse platform ücretlerinin KDV'si gider olur

    def cost(label: str, amount: float, kind: str, note: str = "", platform_fee: bool = False) -> None:
        nonlocal cost_total, fee_vat_nonrec
        cost_total += amount
        if platform_fee and not inp.vat_registered:
            fee_vat_nonrec += amount * inp.fee_vat_rate
        add(Line(label, -amount, kind, note))

    # --- Platform komisyonu (zorunlu kalem) ---
    commission = None
    if inp.rule is None:
        missing.append("Platform komisyon oranı")
    else:
        commission = compute_commission(inp.rule, price, inp.customer_shipping_gross, inp.fee_vat_rate)
        div = (1 + inp.fee_vat_rate) if inp.rule.amounts_include_vat else 1.0
        cost("Platform komisyonu (değişken)", commission.variable_fee / div, "commission",
             commission.explanation + f" · {STATUS_LABELS.get(inp.rule.status, inp.rule.status)}", platform_fee=True)
        if commission.fixed_fee:
            cost("Platform sabit satış ücreti", commission.fixed_fee / div, "commission", platform_fee=True)

    # --- Fulfilment modeline bağlı kalemler (ayrı satırlar) ---
    model = c.fulfilment_model
    if model in PLATFORM_FULFILMENT:
        fee = _val(c.fulfilment_fee_per_unit, missing, f"{model} fulfilment ücreti")
        cost(f"{FULFILMENT_LABELS.get(model, model)} ücreti", fee, "platform_fee", platform_fee=True)
        if c.fulfilment_surcharge_pct:
            cost(f"{model} ek ücreti (%{c.fulfilment_surcharge_pct:g} × fulfilment ücreti)",
                 fee * c.fulfilment_surcharge_pct / 100, "platform_fee", platform_fee=True)
    else:
        ship = _val(c.seller_shipping_per_unit, missing, "Satıcı kargo maliyeti")
        cost(f"Satıcı kargosu ({FULFILMENT_LABELS.get(model, model)})", ship, "cost")

    if c.transaction_fixed_fee:
        cost("Ek sabit işlem ücreti", c.transaction_fixed_fee, "platform_fee", platform_fee=True)
    if c.monthly_plan_fee:
        if c.expected_monthly_units:
            cost(f"Abonelik payı (€{c.monthly_plan_fee:g}/ay ÷ {c.expected_monthly_units} adet)",
                 c.monthly_plan_fee / c.expected_monthly_units, "platform_fee", platform_fee=True)
        else:
            missing.append("Abonelik payı için beklenen aylık satış adedi")

    cost("Paketleme", _val(c.packaging_per_unit, missing, "Paketleme"), "cost")
    ret_pct = _val(c.return_allowance_pct, missing, "İade payı %")
    cost(f"İade payı (%{ret_pct:g} × KDV hariç gelir)", revenue * ret_pct / 100, "cost")
    ad_pct = _val(c.ad_spend_pct, missing, "Reklam gideri %")
    cost(f"Reklam (%{ad_pct:g} × KDV hariç gelir)", revenue * ad_pct / 100, "cost")
    if fee_vat_nonrec:
        cost("Platform ücretlerinin KDV'si (indirilemiyor)", fee_vat_nonrec, "vat")

    # --- Ürün maliyeti ---
    product_cost = None
    if inp.purchase_price_excl_vat is not None:
        purchase = float(inp.purchase_price_excl_vat)
        add(Line("Alış fiyatı (KDV hariç)", -purchase, "product"))
        if inp.purchase_vat_rate is None:
            missing.append("Alış KDV oranı")
        p_vat = purchase * (inp.purchase_vat_rate or 0.0)
        if inp.vat_registered:
            add(Line(f"Alış KDV'si €{p_vat:.2f} (indirilebilir, kâra etkisi yok)", 0.0, "info"))
        else:
            add(Line("Alış KDV'si (indirilemiyor)", -p_vat, "vat"))
        imp = _val(inp.import_cost_per_unit, missing, "İthalat/gümrük maliyeti")
        add(Line("İthalat / gümrük", -imp, "product"))
        product_cost = purchase + imp + (0.0 if inp.vat_registered else p_vat)

    rule_trusted = inp.rule is not None and inp.rule.trusted
    if inp.rule is None:
        status = "commission_missing"
    elif product_cost is None:
        status = "no_purchase_price"
    elif missing or not rule_trusted:
        status = "estimated"
    else:
        status = "exact"
    if inp.rule is not None and not rule_trusted:
        missing.append("Komisyon oranı doğrulanmadı")

    net = margin = roi = None
    max_purchase = None
    if status in ("estimated", "exact"):
        net = revenue - cost_total - product_cost
        margin = net / revenue * 100 if revenue else None
        roi = net / product_cost * 100 if product_cost else None
        add(Line("Net kâr", net, "result"))
    elif status == "no_purchase_price":
        # Alış fiyatı yokken kâr gösterilmez; yalnızca "en fazla şu alış fiyatında başabaş" bilgisi verilir.
        max_purchase = revenue - cost_total - (inp.import_cost_per_unit or 0.0)
        if not inp.vat_registered and inp.purchase_vat_rate:
            max_purchase = max_purchase / (1 + inp.purchase_vat_rate)

    return ProfitResult(inp.platform, status, lines, revenue, commission, net, margin, roi, None,
                        max_purchase, missing, inp.rule.status if inp.rule else None)


def break_even_price(inp: ProfitInputs, start: float) -> float | None:
    """Net kârın ≥ 0 olduğu en düşük KDV dahil fiyat (kademeli komisyon nedeniyle tarama + ikiye bölme)."""
    def profit(p):
        return _compute(inp, p, collect_lines=False).net_profit

    if profit(start) is None:
        return None
    hi_limit = max(start * 20, 1000.0)
    step = max(0.5, start / 200)
    lo, p = 0.01, 0.01
    while p <= hi_limit:
        if profit(p) >= 0:
            hi = p
            for _ in range(40):
                mid = (lo + hi) / 2
                if profit(mid) >= 0:
                    hi = mid
                else:
                    lo = mid
            return round(hi + 0.005, 2)  # yukarı yuvarla: bu fiyatta zarar yok
        lo = p
        p += step
    return None


def calculate(inp: ProfitInputs) -> ProfitResult:
    result = _compute(inp, inp.sale_price_gross)
    if result.status in ("estimated", "exact"):
        result.break_even_price = break_even_price(inp, inp.sale_price_gross)
    return result


def price_difference(price_a: float, price_b: float) -> dict:
    """Platformlar arası brüt fiyat farkı. Yüzde, DÜŞÜK fiyat baz alınarak hesaplanır."""
    low, high = sorted((price_a, price_b))
    diff = high - low
    return {"diff_eur": round(diff, 2), "diff_pct": round(diff / low * 100, 2) if low else None, "base": "düşük fiyat"}
