"""Amazon Selling Partner API (Amazon.nl) bağlayıcısı.

Doğrulanan resmî kaynaklar (kontrol: 2026-09-30):
  * Uç nokta: https://sellingpartnerapi-eu.amazon.com, Amazon.nl marketplaceId A1805IZSGTT6HS
  * LWA token: POST https://api.amazon.com/auth/o2/token (grant_type=refresh_token)
  * GET  /catalog/2022-04-01/items?identifiers={EAN}&identifiersType=EAN   — EAN → ASIN
  * GET  /products/pricing/v0/items/{Asin}/offers                         — 0.5 istek/sn, burst 1; en fazla 20 teklif
  * POST /products/fees/v0/items/{Asin}/feesEstimate                      — 0.5 istek/sn; tahmini ücretler (garanti değil)

Sınırlar (uydurulmaz):
  * Amazon'un perakende satıcı kimliği resmî olarak belgelenmez. AMAZON_RETAIL_SELLER_IDS girilmezse
    "Amazon satıcı mı?" alanı NULL (bilinmiyor) kalır. "Amazon tarafından gönderiliyor" (IsFulfilledByAmazon)
    ayrı bir alandır ve API'den gelir.
  * getItemOffers en fazla 20 teklif döndürür; satıcı sayısı toplam teklif 20'yi aşarsa alt sınırdır.
  * Fiyat geçmişi / satış hacmi SP-API'de yoktur; geçmiş yalnızca bu uygulamanın kendi ölçümlerinden oluşur.
"""
from __future__ import annotations

import json
import time
from urllib.parse import urlencode

from .. import config
from ..db import utc_now
from .base import Connector, NotConfigured, OfferData, RateLimiter, http_json

LWA_URL = "https://api.amazon.com/auth/o2/token"
SOURCE = "amazon_sp_api"


def parse_item_offers(payload: dict, asin: str, captured_at: str, retail_ids: list[str]) -> OfferData | None:
    body = (payload or {}).get("payload") or {}
    offers = body.get("Offers") or []
    summary = body.get("Summary") or {}
    if not offers:
        return None
    featured = next((o for o in offers if o.get("IsBuyBoxWinner")), None)
    note = []
    if featured is None:
        featured = min(offers, key=lambda o: (o.get("ListingPrice") or {}).get("Amount", 1e12))
        note.append("Öne çıkan teklif (Buy Box) yok; en düşük teklif gösteriliyor")
    total = summary.get("TotalOfferCount")
    sellers = {o.get("SellerId") for o in offers if o.get("SellerId")}
    if total and total > len(offers):
        note.append(f"API {len(offers)}/{total} teklif döndürdü; satıcı sayısı alt sınırdır")

    def is_retail(seller_id):
        return (1 if seller_id in retail_ids else 0) if retail_ids else None

    platform_sells = None
    if retail_ids:
        if any(o.get("SellerId") in retail_ids for o in offers):
            platform_sells = 1
        elif not total or total <= len(offers):
            platform_sells = 0
    ship_time = featured.get("ShippingTime") or {}
    min_h, max_h = ship_time.get("minimumHours"), ship_time.get("maximumHours")
    return OfferData(
        platform="amazon_nl",
        captured_at=captured_at,
        price=float((featured.get("ListingPrice") or {})["Amount"]),
        shipping_cost=(featured.get("Shipping") or {}).get("Amount"),
        source=SOURCE,
        external_id=asin,
        url=f"https://www.amazon.nl/dp/{asin}",
        delivery_min_days=None if min_h is None else round(min_h / 24),
        delivery_max_days=None if max_h is None else round(max_h / 24),
        offer_count=total if total is not None else len(offers),
        seller_count=len(sellers),
        featured_seller_id=featured.get("SellerId"),
        featured_seller_name=None,
        featured_is_platform=is_retail(featured.get("SellerId")),
        featured_fulfilled_by_platform=1 if featured.get("IsFulfilledByAmazon") else 0,
        platform_sells=platform_sells,
        note="; ".join(note) or None,
    )


class AmazonSPConnector(Connector):
    platform = "amazon_nl"
    label = "Amazon SP-API (Amazon.nl)"

    def __init__(self):
        self._token: str | None = None
        self._token_exp = 0.0
        self._limiter = RateLimiter()

    def is_configured(self) -> bool:
        return bool(config.AMAZON_LWA_CLIENT_ID and config.AMAZON_LWA_CLIENT_SECRET and config.AMAZON_LWA_REFRESH_TOKEN)

    def _headers(self) -> dict:
        if not self.is_configured():
            raise NotConfigured("AMAZON_LWA_* ortam değişkenleri tanımlı değil")
        if not self._token or time.time() > self._token_exp - 60:
            body = urlencode({
                "grant_type": "refresh_token",
                "refresh_token": config.AMAZON_LWA_REFRESH_TOKEN,
                "client_id": config.AMAZON_LWA_CLIENT_ID,
                "client_secret": config.AMAZON_LWA_CLIENT_SECRET,
            }).encode()
            data = http_json("POST", LWA_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
            self._token = data["access_token"]
            self._token_exp = time.time() + int(data.get("expires_in", 3600))
        return {"x-amz-access-token": self._token, "Accept": "application/json", "Content-Type": "application/json"}

    def _call(self, method: str, path: str, params: dict | None = None, body: dict | None = None, interval: float = 2.0):
        self._limiter.wait(path.split("/")[1], interval)
        url = f"{config.AMAZON_SP_ENDPOINT}{path}" + (f"?{urlencode(params)}" if params else "")
        return http_json(method, url, self._headers(), json.dumps(body).encode() if body else None)

    def find_asin(self, ean: str) -> tuple[str, str | None] | None:
        data = self._call("GET", "/catalog/2022-04-01/items", {
            "identifiers": ean, "identifiersType": "EAN",
            "marketplaceIds": config.AMAZON_MARKETPLACE_ID, "includedData": "summaries",
        }, interval=0.5)
        items = (data or {}).get("items") or []
        if not items:
            return None
        summaries = items[0].get("summaries") or [{}]
        return items[0]["asin"], summaries[0].get("itemName")

    def fetch_by_ean(self, ean: str) -> OfferData | None:
        found = self.find_asin(ean)
        if not found:
            return None
        asin, title = found
        captured_at = utc_now()
        data = self._call("GET", f"/products/pricing/v0/items/{asin}/offers",
                          {"MarketplaceId": config.AMAZON_MARKETPLACE_ID, "ItemCondition": "New"})
        result = parse_item_offers(data, asin, captured_at, config.AMAZON_RETAIL_SELLER_IDS)
        if result:
            result.title = title
        return result

    def estimate_fees(self, asin: str, price: float, shipping: float = 0.0, fba: bool = True) -> dict[str, float]:
        """Amazon'un kendi ücret tahmini (FeeType → tutar). Tahmindir; gerçek ücret farklı olabilir."""
        body = {"FeesEstimateRequest": {
            "MarketplaceId": config.AMAZON_MARKETPLACE_ID,
            "IsAmazonFulfilled": fba,
            "PriceToEstimateFees": {
                "ListingPrice": {"CurrencyCode": "EUR", "Amount": price},
                "Shipping": {"CurrencyCode": "EUR", "Amount": shipping},
            },
            "Identifier": f"nlf-{asin}-{price}",
        }}
        data = self._call("POST", f"/products/fees/v0/items/{asin}/feesEstimate", body=body)
        est = (((data or {}).get("payload") or {}).get("FeesEstimateResult") or {}).get("FeesEstimate") or {}
        return {f["FeeType"]: float(f["FinalFee"]["Amount"]) for f in est.get("FeeDetailList", [])}
