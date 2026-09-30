"""bol.com Retailer API v10 bağlayıcısı.

Doğrulanan resmî kaynaklar (kontrol: 2026-09-30):
  * OpenAPI: https://api.bol.com/retailer/public/apispec/Retailer%20API%20-%20v10
  * Kimlik: POST https://login.bol.com/token?grant_type=client_credentials, Basic auth, token ~299 sn geçerli
  * GET /retailer/products/{ean}/offers        — rakip teklifler (sayfa başı 50)
  * GET /retailer/products/{ean}/placement     — ürün URL'si ve kategori
  * GET /retailer/commission/{ean}             — EAN + fiyat için komisyon (fixedAmount, percentage; KDV dahil)
  * Hız sınırları uç nokta bazında değişir; 429'da Retry-After başlığına uyulur.

API'nin VERMEDİĞİ alanlar (uydurulmaz, NULL kalır):
  * Müşteriye kargo ücreti
  * Satıcı adı (yalnızca retailerId döner)
  * bol.com'un kendisinin satıcı olup olmadığı (bol'un kendi retailerId'si belgelenmemiştir)
"""
from __future__ import annotations

import base64
import time
from datetime import date, datetime, timezone
from urllib.parse import urlencode

from .. import config
from ..db import utc_now
from ..fees import CommissionRule, FeeBand, Tier
from .base import Connector, NotConfigured, OfferData, RateLimiter, http_json

API = "https://api.bol.com"
TOKEN_URL = "https://login.bol.com/token?grant_type=client_credentials"
MEDIA_TYPE = "application/vnd.retailer.v10+json"
SOURCE = "bol_retailer_api_v10"
MAX_PAGES = 4  # 200 teklif; daha fazlası nadiren anlamlıdır


def _days_until(iso_date: str | None, today: date) -> int | None:
    if not iso_date:
        return None
    try:
        return (date.fromisoformat(iso_date[:10]) - today).days
    except ValueError:
        return None


def parse_competing_offers(payload: dict, ean: str, captured_at: str) -> OfferData | None:
    offers = (payload or {}).get("offers") or []
    if not offers:
        return None
    best = next((o for o in offers if o.get("bestOffer")), None) or min(offers, key=lambda o: o.get("price") or 1e12)
    today = datetime.fromisoformat(captured_at).date()
    return OfferData(
        platform="bol",
        captured_at=captured_at,
        price=float(best["price"]),
        source=SOURCE,
        external_id=ean,
        shipping_cost=None,
        delivery_min_days=_days_until(best.get("minDeliveryDate"), today),
        delivery_max_days=_days_until(best.get("maxDeliveryDate"), today),
        offer_count=len(offers),
        seller_count=len({o.get("retailerId") for o in offers if o.get("retailerId")}),
        featured_seller_id=best.get("retailerId"),
        featured_seller_name=None,
        featured_is_platform=None,
        featured_fulfilled_by_platform=1 if best.get("fulfilmentMethod") == "FBB" else 0,
        platform_sells=None,
        note="bol API kargo ücretini, satıcı adını ve bol'un kendi teklifini ayırt edecek alanı vermez",
    )


def parse_commission(payload: dict, ean: str, checked_at: str) -> CommissionRule:
    reductions = payload.get("reductions") or []
    notes = "Commission API yanıtı; tutarlar KDV dahil."
    if reductions:
        notes += " İndirim koşulları: " + "; ".join(
            f"fiyat ≤ €{r.get('maximumPrice')} ise −€{r.get('costReduction')} ({r.get('startDate')}–{r.get('endDate')})"
            for r in reductions)
    return CommissionRule(
        platform="bol",
        category_key=f"ean:{ean}",
        category_label=f"bol.com komisyonu (EAN {ean})",
        tier_mode="flat",
        tiers=[Tier(None, float(payload.get("percentage") or 0))],
        fixed_fee_bands=[FeeBand(None, float(payload.get("fixedAmount") or 0))],
        amounts_include_vat=True,
        base="price",
        status="api",
        source_url=f"{API}/retailer/commission/{ean}",
        checked_at=checked_at,
        notes=notes,
    )


class BolConnector(Connector):
    platform = "bol"
    label = "bol.com Retailer API v10"

    def __init__(self):
        self._token: str | None = None
        self._token_exp = 0.0
        self._limiter = RateLimiter()

    def is_configured(self) -> bool:
        return bool(config.BOL_CLIENT_ID and config.BOL_CLIENT_SECRET)

    def _auth_header(self) -> dict:
        if not self.is_configured():
            raise NotConfigured("BOL_CLIENT_ID / BOL_CLIENT_SECRET tanımlı değil")
        if not self._token or time.time() > self._token_exp - 30:
            cred = base64.b64encode(f"{config.BOL_CLIENT_ID}:{config.BOL_CLIENT_SECRET}".encode()).decode()
            data = http_json("POST", TOKEN_URL, {"Authorization": f"Basic {cred}", "Accept": "application/json"})
            self._token = data["access_token"]
            self._token_exp = time.time() + int(data.get("expires_in", 299))
        return {"Authorization": f"Bearer {self._token}", "Accept": MEDIA_TYPE}

    def _get(self, path: str, params: dict | None = None):
        self._limiter.wait(path.split("/")[2], 0.2)
        url = f"{API}{path}" + (f"?{urlencode(params)}" if params else "")
        return http_json("GET", url, self._auth_header())

    def fetch_by_ean(self, ean: str) -> OfferData | None:
        captured_at = utc_now()
        offers = []
        for page in range(1, MAX_PAGES + 1):
            payload = self._get(f"/retailer/products/{ean}/offers",
                                {"country-code": "NL", "condition": "NEW", "page": page})
            batch = (payload or {}).get("offers") or []
            offers.extend(batch)
            if len(batch) < 50:
                break
        result = parse_competing_offers({"offers": offers}, ean, captured_at)
        if result is None:
            return None
        placement = self._get(f"/retailer/products/{ean}/placement", {"country-code": "NL"}) or {}
        result.url = placement.get("url")
        return result

    def get_commission(self, ean: str, price_gross: float) -> CommissionRule | None:
        payload = self._get(f"/retailer/commission/{ean}", {"unit-price": f"{price_gross:.2f}", "condition": "NEW"})
        if not payload:
            return None
        return parse_commission(payload, ean, datetime.now(timezone.utc).date().isoformat())
