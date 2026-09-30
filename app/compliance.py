"""Kısıt / uyum inceleme akışı.

Hiçbir madde otomatik "uygun" sayılmaz. Varsayılan durum "kontrol edilmedi"dir ve bu
fırsat puanında "manuel inceleme gerekli" olarak işlem görür. Marka aramalarında
"kayıt bulunamadı" sonucu satış hakkı garantisi DEĞİLDİR (tescilsiz marka hakları,
farklı yazımlar, görsel markalar, telif ve tasarım hakları ayrıca söz konusu olabilir).
"""
from __future__ import annotations

TRADEMARK_LINKS = [
    ("BOIP — Benelux marka sicili", "https://www.boip.int/en/trademarks-register"),
    ("EUIPO eSearch plus — AB markaları", "https://euipo.europa.eu/eSearch/"),
    ("TMview — AB ülke ofisleri dahil toplu arama", "https://www.tmdn.org/tmview/"),
]

CHECKS = [
    {
        "key": "branded",
        "label": "Ürün markalı mı?",
        "help": "Ürün veya ambalaj üzerinde bir marka var mı? Markalı ürünlerde marka sahibinin izni / orijinallik belgesi gerekebilir.",
    },
    {
        "key": "trademark_boip",
        "label": "BOIP (Benelux) marka kontrolü",
        "help": "Marka ve ürün adını BOIP sicilinde arayın. 'Kayıt bulunamadı' satış hakkı garantisi değildir.",
        "links": TRADEMARK_LINKS[:1],
    },
    {
        "key": "trademark_euipo",
        "label": "EUIPO / TMview marka kontrolü",
        "help": "AB markası ve ulusal ofisler için arayın. Otomatik doğrulama yapılmaz; sonucu ve tarihini not edin.",
        "links": TRADEMARK_LINKS[1:],
    },
    {
        "key": "authorized_seller",
        "label": "Yetkili satıcı / distribütör şartı",
        "help": "Marka seçici dağıtım uyguluyor mu? Fatura zinciri ve yetki belgesi gerekiyor mu?",
    },
    {
        "key": "amazon_restriction",
        "label": "Amazon listeleme kısıtı",
        "help": "Seller Central › Katalog › Ürün ekle ile ASIN'i arayın; 'onay gerekli' / marka veya kategori kısıtı var mı?",
    },
    {
        "key": "bol_category",
        "label": "bol.com kategori koşulları",
        "help": "bol.com Partnerplatform'da ilgili kategori için ek koşul, belge veya onay gereksinimi var mı?",
    },
    {
        "key": "product_safety",
        "label": "Ürün güvenliği / CE yükümlülükleri",
        "help": "GPSR (AB 2023/988): AB'de sorumlu ekonomik operatör, izlenebilirlik ve uyarı bilgileri. "
                "Kategoriye göre CE gerektiren mevzuat olabilir: Oyuncak 2009/48/EC, LVD 2014/35/EU, EMC 2014/30/EU, "
                "RED 2014/53/EU, RoHS 2011/65/EU; kozmetikte 1223/2009. Uygulanabilirliği ürün bazında doğrulayın.",
    },
]

STATUS_LABELS = {
    "not_checked": "Kontrol edilmedi",
    "manual_review": "Manuel inceleme gerekli",
    "ok": "Kontrol edildi — sorun yok",
    "risk": "Risk / kısıt var",
}

# Kategori ipuçları: yalnızca not ekler, durumu "uygun" yapmaz.
CATEGORY_HINTS = {
    "toys": "Oyuncak: CE işareti ve Oyuncak Güvenliği Direktifi (2009/48/EC) muhtemelen uygulanır.",
    "electronic_accessories": "Elektronik: EMC/LVD/RoHS ve WEEE yükümlülükleri uygulanabilir.",
    "home_products": "Elektrikli ise LVD/EMC/RoHS uygulanabilir.",
    "beauty": "Kozmetik: 1223/2009 (CPNP bildirimi, sorumlu kişi) uygulanabilir.",
}


def ensure_checks(conn, product_id: int, category_key: str | None, now: str) -> None:
    for check in CHECKS:
        note = None
        if check["key"] == "product_safety" and category_key in CATEGORY_HINTS:
            note = CATEGORY_HINTS[category_key]
        conn.execute(
            "INSERT OR IGNORE INTO compliance_checks(product_id, check_key, status, note, updated_at) VALUES (?,?,?,?,?)",
            (product_id, check["key"], "manual_review" if note else "not_checked", note, now),
        )


def summarize(statuses: list[str]) -> str:
    """risk > manual_review > ok. Kontrol edilmemiş madde varsa özet 'manual_review' olur."""
    if not statuses:
        return "manual_review"
    if "risk" in statuses:
        return "risk"
    if any(s in ("not_checked", "manual_review") for s in statuses):
        return "manual_review"
    return "ok"
