"""Fırsat puanı (0–100). Her bileşen kullanıcıya gerekçesiyle gösterilir.

Bileşen                       Aralık
Beklenen net kâr              0…30   (€10+ net kâr = 30)
Kâr marjı                     0…20   (%30+ = 20)
Rekabet yoğunluğu (satıcı)    0…15
Platformun kendi satışı       −15…+5
Veri güncelliği               0…10
Kısıt / uyum riski            −20…+10
Tahmini hesap                 −5
Puan 0–100 aralığına kırpılır. Veri yoksa bileşen puan kazandırmaz (tahmin yapılmaz).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScoreInput:
    net_profit: float | None
    margin_pct: float | None
    is_exact: bool
    seller_count: int | None
    platform_sells: int | None       # 1/0/None
    data_age_hours: float | None
    compliance_summary: str          # ok | manual_review | risk
    stale_hours: float = 72


def score(inp: ScoreInput) -> tuple[int, list[tuple[float, str]]]:
    reasons: list[tuple[float, str]] = []

    if inp.net_profit is None:
        reasons.append((0, "Net kâr hesaplanamadı (alış fiyatı veya komisyon eksik)"))
    else:
        pts = max(0.0, min(inp.net_profit / 10, 1.0)) * 30
        reasons.append((pts, f"Beklenen net kâr €{inp.net_profit:.2f}"))

    if inp.margin_pct is not None:
        pts = max(0.0, min(inp.margin_pct / 30, 1.0)) * 20
        reasons.append((pts, f"Kâr marjı %{inp.margin_pct:.1f}"))

    sc = inp.seller_count
    if sc is None:
        reasons.append((0, "Satıcı sayısı bilinmiyor"))
    else:
        pts = 15 if sc <= 2 else 10 if sc <= 5 else 5 if sc <= 10 else 0
        reasons.append((pts, f"{sc} satıcı (rekabet {'düşük' if sc <= 2 else 'orta' if sc <= 5 else 'yüksek'})"))

    if inp.platform_sells == 1:
        reasons.append((-15, "Platform bu ürünü kendisi satıyor"))
    elif inp.platform_sells == 0:
        reasons.append((5, "Platformun kendi teklifi görünmüyor"))
    else:
        reasons.append((0, "Platformun kendisi satıyor mu bilinmiyor"))

    age = inp.data_age_hours
    if age is None:
        reasons.append((0, "Fiyat zamanı bilinmiyor"))
    elif age <= 24:
        reasons.append((10, f"Veri güncel ({age:.0f} sa)"))
    elif age <= inp.stale_hours:
        reasons.append((5, f"Veri {age:.0f} saatlik"))
    else:
        reasons.append((0, f"Veri eski ({age / 24:.0f} gün) — yeniden güncelleyin"))

    cs = inp.compliance_summary
    if cs == "risk":
        reasons.append((-20, "Kısıt/uyum riski işaretlendi"))
    elif cs == "ok":
        reasons.append((10, "Tüm kısıt kontrolleri tamamlandı"))
    else:
        reasons.append((-10, "Manuel inceleme gerekli (kısıt kontrolleri tamamlanmadı)"))

    if inp.net_profit is not None and not inp.is_exact:
        reasons.append((-5, "Tahmini hesap (eksik maliyet veya doğrulanmamış komisyon)"))

    total = int(round(max(0.0, min(100.0, sum(p for p, _ in reasons)))))
    return total, reasons
