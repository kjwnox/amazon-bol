"""API senkronizasyonu: yapılandırılmış bağlayıcılarla EAN'ı olan ürünleri günceller.

Yalnızca resmî API'ler kullanılır; scraping yoktur. Bağlayıcı yoksa bu adım hiçbir şey yapmaz ve
veriler CSV ile yüklenir.
"""
from __future__ import annotations

from . import alerts
from .connectors import get_connectors
from .connectors.base import ApiError
from .db import utc_now
from .importers import insert_snapshot, record_search, save_rule, upsert_listing
from .services import Context, analyze_all


def run_sync(conn, log=print) -> dict:
    connectors = get_connectors()
    stats = {"connectors": [c.label for c in connectors], "updated": 0, "not_listed": 0, "errors": []}
    if not connectors:
        log("Yapılandırılmış API bağlayıcısı yok (.env boş). CSV içe aktarmayı kullanın.")
        return stats
    products = conn.execute("SELECT id, ean FROM products WHERE ean IS NOT NULL").fetchall()
    for c in connectors:
        for p in products:
            try:
                offer = c.fetch_by_ean(p["ean"])
            except ApiError as e:
                stats["errors"].append(f"{c.platform} {p['ean']}: {e}")
                continue
            items = conn.execute("SELECT id FROM supplier_items WHERE product_id = ?", (p["id"],)).fetchall()
            result = "found" if offer else "not_listed_in_search"
            for it in items:
                record_search(conn, it["id"], c.platform, result, f"EAN {p['ean']}", c.label, utc_now())
            if not offer:
                stats["not_listed"] += 1
                continue
            listing_id = upsert_listing(conn, p["id"], c.platform, offer.external_id, offer.url,
                                        offer.platform_category, "ean")
            insert_snapshot(conn, listing_id, offer.__dict__)
            try:
                rule = c.get_commission(p["ean"], offer.price)
                if rule:
                    save_rule(conn, rule)
            except ApiError as e:
                stats["errors"].append(f"{c.platform} komisyon {p['ean']}: {e}")
            stats["updated"] += 1
            conn.commit()
    ctx = Context.load(conn)
    stats["new_alerts"] = len(alerts.evaluate(conn, analyze_all(conn, ctx), ctx))
    return stats
