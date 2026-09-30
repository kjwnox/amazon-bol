"""Tedarikçi bulma kaynakları.

Bu modül tedarikçi FİYATI üretmez. Toptan fiyatlar çoğunlukla yalnızca doğrulanmış işletme hesaplarına
veya teklif üzerine verilir; bu nedenle uygulama yalnızca (1) resmî/meşru kaynaklarda arama bağlantıları,
(2) EAN'dan marka sahibini bulma adımı ve (3) kullanıcının girdiği / CSV ile yüklediği teklif kayıtlarını
sunar. Otomatik veri çekme (scraping) yapılmaz.

Arama bağlantısı biçimleri 2026-09-30'da denendi: search_url'si olanlar sorguyla açılıyor; olmayanlar
(otomatik erişimi engelleyen veya biçimi doğrulanamayan siteler) yalnızca ana sayfaya bağlanır.
"""
from __future__ import annotations

from urllib.parse import quote_plus

CHECKED = "2026-09-30"

SOURCES = [
    {
        "key": "gs1",
        "name": "Verified by GS1",
        "kind": "Marka sahibi / üretici tespiti",
        "region": "Global",
        "url": "https://www.gs1.org/services/verified-by-gs1",
        "search_url": None,
        "query": "ean",
        "price_visibility": "Fiyat yok. EAN'ın lisanslı olduğu şirketi (marka sahibini) gösterir.",
        "how": "EAN'ı yapıştırın → şirket adı ve ülke. Sonra marka sahibinin web sitesinden 'dealer / wholesale / "
               "distributeur worden' sayfasını veya Benelux distribütörünü arayın.",
    },
    {
        "key": "ankorstore",
        "name": "Ankorstore",
        "kind": "Avrupa B2B toptan pazaryeri (markalar → perakendeciler)",
        "region": "AB",
        "url": "https://www.ankorstore.com/",
        "search_url": "https://www.ankorstore.com/search?q={q}",
        "query": "name",
        "price_visibility": "Toptan fiyat yalnızca kayıtlı ve doğrulanmış perakendecilere görünür.",
        "how": "Perakendeci hesabı açın (işletme doğrulaması). Fiyat, minimum sipariş ve marka koşullarını teklif olarak kaydedin.",
    },
    {
        "key": "orderchamp",
        "name": "Orderchamp",
        "kind": "NL merkezli online toptan platformu",
        "region": "NL / AB",
        "url": "https://www.orderchamp.com/nl/retailers",
        "search_url": "https://www.orderchamp.com/nl/search?q={q}",
        "query": "name",
        "price_visibility": "Perakendeci hesabı gerekir (ücretsiz). İnkoopprijzen hesapla görünür.",
        "how": "Retailer hesabı açın; ürün ve markayı arayın; inkoopprijs ve minimum sipariş tutarını kaydedin.",
    },
    {
        "key": "faire",
        "name": "Faire",
        "kind": "Global B2B toptan pazaryeri",
        "region": "Global / AB",
        "url": "https://www.faire.com/",
        "search_url": None,  # otomatik denemede 403; arama biçimi doğrulanamadı
        "query": "name",
        "price_visibility": "Toptan fiyat perakendeci hesabıyla görünür.",
        "how": "Perakendeci hesabıyla giriş yapıp marka/ürün adıyla arayın.",
    },
    {
        "key": "alibaba",
        "name": "Alibaba.com",
        "kind": "Üretici / ihracatçı pazaryeri (çoğunlukla Asya)",
        "region": "Global",
        "url": "https://www.alibaba.com/",
        "search_url": "https://www.alibaba.com/trade/search?SearchText={q}",
        "query": "generic",
        "price_visibility": "Kademeli fiyat aralıkları ve MOQ çoğu ilanda herkese açık; kesin fiyat teklif (RFQ) ile.",
        "how": "Aynı markalı ürün değil, benzer/jenerik ürün bulunur. İthalat, gümrük, CE/GPSR sorumluluğu size geçer — "
               "ithalat/gümrük maliyetini ve uyum kontrollerini mutlaka girin.",
    },
    {
        "key": "europages",
        "name": "Europages",
        "kind": "Avrupa üretici/toptancı dizini",
        "region": "AB",
        "url": "https://www.europages.nl/",
        "search_url": None,  # arama URL biçimi doğrulanamadı
        "query": "generic",
        "price_visibility": "Fiyat yok; firma iletişim bilgisi. Fiyat teklif talebiyle alınır.",
        "how": "Ürün türüyle arayın, üretici/toptancılara teklif talebi gönderin.",
    },
    {
        "key": "web",
        "name": "Web araması (groothandel)",
        "kind": "Genel arama",
        "region": "NL",
        "url": "https://www.google.com/",
        "search_url": "https://www.google.com/search?q={q}",
        "query": "wholesale_nl",
        "price_visibility": "Değişken; çoğu toptancı fiyatı giriş sonrası gösterir.",
        "how": "Marka + 'groothandel' / 'distributeur' / 'dealer worden' ile arayın.",
    },
]


def _query(product: dict, mode: str) -> str | None:
    brand, model, title, ean = (product.get(k) for k in ("brand", "model", "title", "ean"))
    if mode == "ean":
        return ean
    if mode == "name":
        return " ".join(x for x in (brand, model) if x) or title
    if mode == "generic":
        # Marka adı olmadan ürün türü: jenerik tedarikçi aramak için başlıktan marka çıkarılır
        t = title or ""
        return t.replace(brand, "").strip() if brand else t
    if mode == "wholesale_nl":
        return f"{brand or title} groothandel"
    return title


def links_for(product: dict) -> list[dict]:
    out = []
    for s in SOURCES:
        q = _query(product, s["query"])
        url = s["search_url"].format(q=quote_plus(q)) if (s["search_url"] and q) else s["url"]
        out.append({**s, "q": q, "link": url, "direct_search": bool(s["search_url"] and q)})
    return out
