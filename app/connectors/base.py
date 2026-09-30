"""Platform bağlayıcı arayüzü. Yeni bir kanal (Kaufland, MediaMarkt, eBay, tedarikçi API'si) eklemek için
Connector'dan türetip connectors/__init__.py içindeki PLATFORMS kaydına ekleyin."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class OfferData:
    platform: str
    captured_at: str
    price: float
    source: str
    external_id: str | None = None
    url: str | None = None
    title: str | None = None
    shipping_cost: float | None = None
    delivery_min_days: int | None = None
    delivery_max_days: int | None = None
    offer_count: int | None = None
    seller_count: int | None = None
    featured_seller_id: str | None = None
    featured_seller_name: str | None = None
    featured_is_platform: int | None = None
    featured_fulfilled_by_platform: int | None = None
    platform_sells: int | None = None
    platform_category: str | None = None
    note: str | None = None


class NotConfigured(RuntimeError):
    pass


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


class RateLimiter:
    """Uç nokta başına minimum aralık (saniye). Belgelenmiş hız sınırının altında kalmak için."""

    def __init__(self):
        self._last: dict[str, float] = {}

    def wait(self, key: str, min_interval: float) -> None:
        last = self._last.get(key)
        if last is not None:
            delay = min_interval - (time.monotonic() - last)
            if delay > 0:
                time.sleep(delay)
        self._last[key] = time.monotonic()


def http_json(method: str, url: str, headers: dict, body: bytes | None = None,
              max_retries: int = 3, timeout: int = 30):
    """JSON istek. 429'da Retry-After başlığına uyarak geri çekilir; 404'te None döner."""
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="replace")
            if e.code == 404:
                return None
            if e.code == 429 and attempt < max_retries:
                retry_after = next((v for k, v in e.headers.items() if k.lower() == "retry-after"), None)
                time.sleep(float(retry_after) if retry_after else 2 ** (attempt + 1))
                continue
            raise ApiError(e.code, text) from None
    raise ApiError(429, "Hız sınırı aşıldı, yeniden deneme hakkı bitti")


class Connector(ABC):
    platform: str
    label: str

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    def fetch_by_ean(self, ean: str) -> OfferData | None:
        """EAN ile ara. None = arama sonucunda listelenmedi (ürünün hiç satılmadığı anlamına gelmez)."""

    def get_commission(self, ean: str, price_gross: float):
        """Varsa platformun EAN bazındaki komisyonunu CommissionRule olarak döndürür."""
        return None
