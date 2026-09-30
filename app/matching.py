"""Ürün eşleştirme.

1. Öncelik EAN/GTIN: geçerli kontrol hanesi olan GTIN'ler 14 haneye tamamlanarak karşılaştırılır.
2. EAN yoksa marka + model + varyant + ölçü + paket adedi karşılaştırılır. Bu yol ASLA otomatik
   "aynı ürün" sonucu vermez; en iyi durumda "candidate" (kullanıcı onayı gerekli) döner.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


def gtin_check_digit_ok(digits: str) -> bool:
    body, check = digits[:-1], int(digits[-1])
    total = 0
    # Sağdan sola: kontrol hanesinden önceki hane ×3, sonraki ×1, ...
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10 == check


def normalize_ean(raw) -> tuple[str | None, bool | None]:
    """(normalize EAN, geçerli mi). EAN yoksa (None, None).

    Excel'in bilimsel gösterime çevirdiği değerler (8.71E+12) bilgi kaybettiği için geçersiz sayılır.
    """
    if raw is None:
        return None, None
    s = str(raw).strip()
    if not s:
        return None, None
    if re.search(r"[eE]\+", s):
        return s, False
    digits = re.sub(r"[\s-]", "", s)
    if digits.endswith(".0"):
        digits = digits[:-2]
    if not digits.isdigit() or len(digits) not in (8, 12, 13, 14):
        return s, False
    if not gtin_check_digit_ok(digits):
        return digits, False
    # Görüntüleme için 13 hane (UPC-12 → başa 0; GTIN-14 başında 0 varsa kırp). EAN-8 olduğu gibi kalır.
    if len(digits) == 12:
        digits = "0" + digits
    elif len(digits) == 14 and digits.startswith("0"):
        digits = digits[1:]
    return digits, True


def gtin_key(ean: str) -> str:
    """Karşılaştırma anahtarı: GTIN-14."""
    return ean.zfill(14)


def same_ean(a, b) -> bool:
    na, va = normalize_ean(a)
    nb, vb = normalize_ean(b)
    return bool(va and vb and gtin_key(na) == gtin_key(nb))


# --- Nitelik tabanlı eşleştirme -------------------------------------------------

_UNIT_FACTORS = {
    "ml": ("ml", 1), "cl": ("ml", 10), "dl": ("ml", 100), "l": ("ml", 1000), "ltr": ("ml", 1000), "liter": ("ml", 1000),
    "mg": ("g", 0.001), "g": ("g", 1), "gr": ("g", 1), "kg": ("g", 1000),
    "mm": ("mm", 1), "cm": ("mm", 10), "m": ("mm", 1000),
    "st": ("pcs", 1), "stuks": ("pcs", 1), "pcs": ("pcs", 1), "adet": ("pcs", 1),
}


def normalize_size(raw) -> str | None:
    """'0,5 L' → '500ml', '1kg' → '1000g'. Tanınmayan ifade küçük harfe çevrilip boşluksuz döner."""
    if raw is None or str(raw).strip() == "":
        return None
    s = str(raw).strip().lower().replace(",", ".")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([a-z]+)", s)
    if m and m.group(2) in _UNIT_FACTORS:
        unit, factor = _UNIT_FACTORS[m.group(2)]
        value = float(m.group(1)) * factor
        value_s = f"{value:.3f}".rstrip("0").rstrip(".")
        return f"{value_s}{unit}"
    return re.sub(r"\s+", "", s)


def _norm_text(raw) -> str | None:
    if raw is None:
        return None
    s = re.sub(r"[^0-9a-z]+", "", str(raw).lower())
    return s or None


@dataclass
class AttributeMatch:
    status: str                  # candidate | mismatch | insufficient
    reasons: list[str] = field(default_factory=list)


def attribute_match(a: dict, b: dict) -> AttributeMatch:
    """EAN olmadan iki kaydı karşılaştırır. 'candidate' bile olsa kullanıcı onayı gerekir."""
    reasons: list[str] = []
    brand_a, brand_b = _norm_text(a.get("brand")), _norm_text(b.get("brand"))
    model_a, model_b = _norm_text(a.get("model")), _norm_text(b.get("model"))
    if not (brand_a and brand_b and model_a and model_b):
        return AttributeMatch("insufficient", ["Marka veya model eksik; nitelikle eşleştirme yapılamaz"])
    if brand_a != brand_b:
        return AttributeMatch("mismatch", [f"Marka farklı: {a.get('brand')} ≠ {b.get('brand')}"])
    if model_a != model_b:
        return AttributeMatch("mismatch", [f"Model farklı: {a.get('model')} ≠ {b.get('model')}"])
    reasons.append("Marka ve model aynı")

    pack_a, pack_b = int(a.get("pack_qty") or 1), int(b.get("pack_qty") or 1)
    if pack_a != pack_b:
        return AttributeMatch("mismatch", reasons + [f"Paket adedi farklı: {pack_a} ≠ {pack_b}"])
    reasons.append(f"Paket adedi aynı ({pack_a})")

    uncertain = False
    for key, label, norm in (("variant", "Varyant", _norm_text), ("size", "Ölçü", normalize_size)):
        va, vb = norm(a.get(key)), norm(b.get(key))
        if va and vb:
            if va != vb:
                return AttributeMatch("mismatch", reasons + [f"{label} farklı: {a.get(key)} ≠ {b.get(key)}"])
            reasons.append(f"{label} aynı")
        elif va or vb:
            uncertain = True
            reasons.append(f"{label} yalnızca bir kayıtta var — belirsiz")
    reasons.append("EAN yok: aynı ürün olduğu kullanıcı onayı olmadan kabul edilmez"
                   + (" (eksik nitelik var)" if uncertain else ""))
    return AttributeMatch("candidate", reasons)
