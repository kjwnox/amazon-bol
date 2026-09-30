"""Ortam değişkenleri. API anahtarları yalnızca .env / ortamdan okunur, kodda veya DB'de tutulmaz."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APP_NAME = "MargeRadar"


def _load_dotenv(path: Path) -> None:
    """Basit .env okuyucu (ek bağımlılık olmadan). Var olan ortam değişkenlerini ezmez."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def env(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key, default)
    return value if value not in ("", None) else default


DB_PATH = Path(env("NLF_DB_PATH", str(BASE_DIR / "data" / "nlfirsat.db")))
OUTBOX_DIR = Path(env("NLF_OUTBOX_DIR", str(BASE_DIR / "data" / "outbox")))

# bol.com Retailer API v10
BOL_CLIENT_ID = env("BOL_CLIENT_ID")
BOL_CLIENT_SECRET = env("BOL_CLIENT_SECRET")

# Amazon SP-API (LWA)
AMAZON_LWA_CLIENT_ID = env("AMAZON_LWA_CLIENT_ID")
AMAZON_LWA_CLIENT_SECRET = env("AMAZON_LWA_CLIENT_SECRET")
AMAZON_LWA_REFRESH_TOKEN = env("AMAZON_LWA_REFRESH_TOKEN")
AMAZON_SP_ENDPOINT = env("AMAZON_SP_ENDPOINT", "https://sellingpartnerapi-eu.amazon.com")
AMAZON_MARKETPLACE_ID = env("AMAZON_MARKETPLACE_ID", "A1805IZSGTT6HS")  # Amazon.nl
# Amazon perakende (Amazon'un kendisi) satıcı kimlik(ler)i resmî olarak belgelenmez.
# Biliyorsanız virgülle girin; yoksa "Amazon satıcı mı?" alanı "bilinmiyor" kalır.
AMAZON_RETAIL_SELLER_IDS = [s.strip() for s in (env("AMAZON_RETAIL_SELLER_IDS", "") or "").split(",") if s.strip()]

# E-posta (SMTP). Ayarlanmazsa e-postalar data/outbox/ klasörüne .eml olarak yazılır.
SMTP_HOST = env("SMTP_HOST")
SMTP_PORT = int(env("SMTP_PORT", "587"))
SMTP_USER = env("SMTP_USER")
SMTP_PASSWORD = env("SMTP_PASSWORD")
SMTP_FROM = env("SMTP_FROM", SMTP_USER or "nlfirsat@localhost")
ALERT_EMAIL_TO = env("ALERT_EMAIL_TO")
