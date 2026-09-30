"""Platform komisyon kuralları.

Oranlar koda gömülmez: commission_rules tablosunda tutulur, Ayarlar sayfasından ve CSV ile düzenlenir.
Desteklenen kademe türleri (Amazon.nl resmî tablosunda ikisi de var):
  * flat      — tek oran
  * threshold — toplam fiyat hangi aralıktaysa O oran tüm fiyata uygulanır
                (ör. Home Products: ≤ €20 ise %8, > €20 ise %15)
  * portion   — dilim dilim: her fiyat dilimine kendi oranı
                (ör. Electronic Accessories: €100'e kadar olan kısım %15, aşan kısım %8)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

# Bu durumlardaki oranla yapılan hesap "kesin" olabilir. 'unverified' → her zaman "tahmini".
TRUSTED_STATUSES = {"official_verified", "api", "user_entered"}

STATUS_LABELS = {
    "official_verified": "Resmî kaynaktan doğrulandı",
    "api": "Platform API'sinden (EAN bazında)",
    "user_entered": "Kullanıcı girdi",
    "unverified": "Doğrulanmadı",
}


@dataclass
class Tier:
    up_to: float | None
    pct: float


@dataclass
class FeeBand:
    up_to: float | None
    fee: float


@dataclass
class CommissionRule:
    platform: str
    category_key: str
    category_label: str
    tier_mode: str
    tiers: list[Tier]
    min_fee: float = 0.0
    fixed_fee_bands: list[FeeBand] = field(default_factory=list)
    amounts_include_vat: bool = False
    base: str = "price_incl_shipping"
    status: str = "unverified"
    source_url: str | None = None
    checked_at: str | None = None
    notes: str | None = None
    id: int | None = None

    @property
    def trusted(self) -> bool:
        return self.status in TRUSTED_STATUSES

    @classmethod
    def from_row(cls, row) -> "CommissionRule":
        r = dict(row)
        return cls(
            id=r.get("id"),
            platform=r["platform"],
            category_key=r["category_key"],
            category_label=r["category_label"],
            tier_mode=r["tier_mode"],
            tiers=[Tier(t.get("up_to"), float(t["pct"])) for t in json.loads(r["tiers_json"] or "[]")],
            min_fee=float(r.get("min_fee") or 0),
            fixed_fee_bands=[FeeBand(b.get("up_to"), float(b["fee"])) for b in json.loads(r.get("fixed_fee_bands_json") or "[]")],
            amounts_include_vat=bool(r.get("amounts_include_vat")),
            base=r.get("base") or "price_incl_shipping",
            status=r["status"],
            source_url=r.get("source_url"),
            checked_at=r.get("checked_at"),
            notes=r.get("notes"),
        )

    def describe(self) -> str:
        if self.tier_mode == "flat":
            txt = f"%{self.tiers[0].pct:g}"
        else:
            parts = []
            for t in self.tiers:
                bound = f"≤ €{t.up_to:g}" if t.up_to is not None else "üstü"
                parts.append(f"{bound}: %{t.pct:g}")
            kind = "dilim bazında" if self.tier_mode == "portion" else "fiyat eşiğine göre"
            txt = f"{kind} ({'; '.join(parts)})"
        if self.min_fee:
            txt += f", min. €{self.min_fee:.2f}"
        if self.fixed_fee_bands:
            txt += ", sabit ücret: " + "; ".join(
                (f"≤ €{b.up_to:g}" if b.up_to is not None else "üstü") + f" €{b.fee:.2f}" for b in self.fixed_fee_bands)
        return txt


@dataclass
class CommissionResult:
    base_amount: float
    variable_fee: float
    min_fee_applied: bool
    fixed_fee: float
    total_stated: float     # kuralda yazan tutar (KDV dahil ya da hariç, kurala göre)
    total_excl_vat: float
    vat_on_fee: float
    explanation: str


def _variable_fee(rule: CommissionRule, base: float) -> float:
    tiers = sorted(rule.tiers, key=lambda t: float("inf") if t.up_to is None else t.up_to)
    if not tiers:
        raise ValueError(f"Komisyon kuralında oran yok: {rule.platform}/{rule.category_key}")
    if rule.tier_mode == "flat":
        return base * tiers[0].pct / 100
    if rule.tier_mode == "threshold":
        for t in tiers:
            if t.up_to is None or base <= t.up_to:
                return base * t.pct / 100
        return base * tiers[-1].pct / 100
    if rule.tier_mode == "portion":
        fee, lower = 0.0, 0.0
        for t in tiers:
            upper = base if t.up_to is None else min(base, t.up_to)
            if upper > lower:
                fee += (upper - lower) * t.pct / 100
            if t.up_to is None or base <= t.up_to:
                break
            lower = t.up_to
        return fee
    raise ValueError(f"Bilinmeyen kademe türü: {rule.tier_mode}")


def _fixed_fee(rule: CommissionRule, price: float) -> float:
    bands = sorted(rule.fixed_fee_bands, key=lambda b: float("inf") if b.up_to is None else b.up_to)
    for b in bands:
        if b.up_to is None or price <= b.up_to:
            return b.fee
    return bands[-1].fee if bands else 0.0


def compute_commission(rule: CommissionRule, price_gross: float, shipping_gross: float = 0.0,
                       fee_vat_rate: float = 0.21) -> CommissionResult:
    base = price_gross + (shipping_gross if rule.base == "price_incl_shipping" else 0.0)
    variable = _variable_fee(rule, base)
    min_applied = variable < rule.min_fee
    if min_applied:
        variable = rule.min_fee
    fixed = _fixed_fee(rule, price_gross)
    total = variable + fixed
    if rule.amounts_include_vat:
        excl = total / (1 + fee_vat_rate)
        vat = total - excl
    else:
        excl = total
        vat = total * fee_vat_rate
    expl = f"{rule.category_label}: {rule.describe()} · taban €{base:.2f}"
    if min_applied:
        expl += " · minimum komisyon uygulandı"
    return CommissionResult(round(base, 4), round(variable, 4), min_applied, round(fixed, 4),
                            round(total, 4), round(excl, 4), round(vat, 4), expl)


def rule_to_row(rule: CommissionRule) -> dict:
    return {
        "platform": rule.platform,
        "category_key": rule.category_key,
        "category_label": rule.category_label,
        "tier_mode": rule.tier_mode,
        "tiers_json": json.dumps([{"up_to": t.up_to, "pct": t.pct} for t in rule.tiers]),
        "min_fee": rule.min_fee,
        "fixed_fee_bands_json": json.dumps([{"up_to": b.up_to, "fee": b.fee} for b in rule.fixed_fee_bands]),
        "amounts_include_vat": int(rule.amounts_include_vat),
        "base": rule.base,
        "status": rule.status,
        "source_url": rule.source_url,
        "checked_at": rule.checked_at,
        "notes": rule.notes,
    }


def parse_tiers(text: str) -> list[Tier]:
    """'8@20; 15' → [Tier(20, 8), Tier(None, 15)]  (oran@üst_sınır; son dilim sınırsız)."""
    tiers = []
    for part in [p.strip() for p in (text or "").replace(",", ".").split(";") if p.strip()]:
        if "@" in part:
            pct, up = part.split("@", 1)
            tiers.append(Tier(float(up), float(pct)))
        else:
            tiers.append(Tier(None, float(part)))
    return tiers


def parse_bands(text: str) -> list[FeeBand]:
    """'0.20@10; 0.50' → sabit ücret bantları (ücret@fiyat_üst_sınırı)."""
    return [FeeBand(t.up_to, t.pct) for t in parse_tiers(text)]


def format_tiers(tiers) -> str:
    return "; ".join(f"{t.pct:g}@{t.up_to:g}" if t.up_to is not None else f"{t.pct:g}" for t in tiers)


def format_bands(bands) -> str:
    return "; ".join(f"{b.fee:g}@{b.up_to:g}" if b.up_to is not None else f"{b.fee:g}" for b in bands)
