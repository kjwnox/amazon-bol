import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Uyarı eşikleri ve genel ayarların varsayılanları. Hepsi Ayarlar sayfasından değişir.
DEFAULT_SETTINGS = {
    "vat_registered": "1",            # btw-ondernemer: alış KDV'si ve ücret KDV'si indirilebilir
    "default_sale_vat_rate": "0.21",
    "spike_threshold_pct": "10",       # ardışık iki ölçüm arası bu %'den büyük değişim işaretlenir
    "stale_hours": "72",               # bundan eski fiyat "güncel değil" sayılır
    "quote_stale_days": "90",          # bundan eski tedarikçi fiyatı "eski teklif" sayılır
    "min_net_profit": "3",
    "min_margin_pct": "10",
    "min_roi_pct": "20",
    "max_sellers": "10",
    "min_price_diff_pct": "5",
    "alerts_require_exact": "0",       # 1 = yalnızca "kesin" hesaplarda uyarı
    "alert_email_to": "",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Önceki sürümlerle oluşturulmuş veritabanlarına eklenecek sütunlar
MIGRATIONS = {
    "supplier_items": {"price_basis": "TEXT", "moq": "INTEGER", "lead_time_days": "INTEGER", "quote_date": "TEXT",
                       "valid_until": "TEXT", "source_url": "TEXT", "notes": "TEXT"},
    "product_cost_overrides": {"sale_vat_rate": "REAL", "source": "TEXT"},
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in MIGRATIONS.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, typ in cols.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _migrate(conn)
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def get_settings(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
