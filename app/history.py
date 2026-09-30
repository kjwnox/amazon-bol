"""Fiyat geçmişi analizi. Yalnızca kayıtlı ölçümler kullanılır; eksik aralıklar doldurulmaz, tahmin yapılmaz."""
from __future__ import annotations


def detect_spikes(points: list[tuple[str, float]], threshold_pct: float) -> list[dict]:
    """Ardışık iki ölçüm arasında |değişim| ≥ eşik olan noktaları işaretler.

    points: [(captured_at_iso, price)] zamana göre sıralı.
    """
    spikes = []
    for (t_prev, p_prev), (t_cur, p_cur) in zip(points, points[1:]):
        if not p_prev:
            continue
        change = (p_cur - p_prev) / p_prev * 100
        if abs(change) >= threshold_pct:
            spikes.append({
                "at": t_cur,
                "from_at": t_prev,
                "from_price": p_prev,
                "to_price": p_cur,
                "change_pct": round(change, 1),
                "direction": "düşüş" if change < 0 else "artış",
            })
    return spikes
