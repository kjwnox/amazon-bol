"""Platform kaydı. Doğrulanmamış bir API için bağlayıcı yazılmaz; o kanal CSV ile çalışır."""
from __future__ import annotations

PLATFORMS = {
    "bol": {
        "label": "bol.com",
        "data_access": "Retailer API v10 (satıcı hesabı gerekir) + CSV",
        "api_docs": "https://api.bol.com/retailer/public/Retailer-API/",
        "has_connector": True,
    },
    "amazon_nl": {
        "label": "Amazon.nl",
        "data_access": "Selling Partner API (Seller Central + geliştirici kaydı gerekir) + CSV",
        "api_docs": "https://developer-docs.amazon.com/sp-api/",
        "has_connector": True,
    },
    "kaufland": {
        "label": "Kaufland",
        "data_access": "Yalnızca CSV — API erişimi bu sürümde doğrulanmadı",
        "api_docs": None,
        "has_connector": False,
    },
    "mediamarkt": {
        "label": "MediaMarkt Marketplace",
        "data_access": "Yalnızca CSV — API erişimi bu sürümde doğrulanmadı",
        "api_docs": None,
        "has_connector": False,
    },
    "ebay": {
        "label": "eBay",
        "data_access": "Yalnızca CSV — API erişimi bu sürümde doğrulanmadı",
        "api_docs": None,
        "has_connector": False,
    },
}

PLATFORM_LABELS = {k: v["label"] for k, v in PLATFORMS.items()}


def get_connectors():
    """Yapılandırılmış (ortam değişkenleri dolu) API bağlayıcılarını döndürür."""
    from .amazon_sp import AmazonSPConnector
    from .bol import BolConnector

    return [c for c in (BolConnector(), AmazonSPConnector()) if c.is_configured()]
