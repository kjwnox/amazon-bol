"""CSV içe aktarma. API erişimi olmayan her kanal ve tedarikçi katalogları bu yolla çalışır.

Ayırıcı otomatik algılanır (; veya ,). Ondalık virgül (12,50) desteklenir. Kodlama UTF-8 (BOM'lu da olur).
Her satır kaynağıyla (csv:<dosya adı>) ve zamanıyla kaydedilir.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from . import compliance
from .db import utc_now
from .fees import CommissionRule, parse_bands, parse_tiers, rule_to_row
from .matching import attribute_match, normalize_ean

SUPPLIER_COLUMNS = ["supplier", "supplier_sku", "ean", "brand", "model", "variant", "size", "pack_qty", "title",
                    "purchase_price_excl_vat", "purchase_vat_rate", "import_cost_per_unit", "category_key",
                    "price_basis", "moq", "lead_time_days", "quote_date", "valid_until", "source_url", "notes"]
QUOTE_FIELDS = ["price_basis", "moq", "lead_time_days", "quote_date", "valid_until", "source_url", "notes"]
PRICE_BASES = {"quote", "catalog", "platform", "manual"}
OFFER_COLUMNS = ["platform", "ean", "external_id", "title", "brand", "model", "variant", "size", "pack_qty", "url",
                 "platform_category", "captured_at", "price", "shipping_cost", "delivery_min_days", "delivery_max_days",
                 "offer_count", "seller_count", "featured_seller_name", "featured_is_platform",
                 "featured_fulfilled_by_platform", "platform_sells"]
SEARCH_COLUMNS = ["supplier", "supplier_sku", "platform", "result", "searched_at", "query"]
RULE_COLUMNS = ["platform", "category_key", "category_label", "tier_mode", "tiers", "min_fee", "fixed_fee_bands",
                "amounts_include_vat", "base", "status", "source_url", "checked_at", "notes"]


@dataclass
class ImportReport:
    rows: int = 0
    created: int = 0
    updated: int = 0
    warnings: list[str] = field(default_factory=list)

    def warn(self, line: int, msg: str) -> None:
        self.warnings.append(f"Satır {line}: {msg}")


def read_csv(text: str) -> list[dict]:
    text = text.lstrip("﻿")
    first = text.splitlines()[0] if text else ""
    delim = ";" if first.count(";") > first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in reader]


def num(v) -> float | None:
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip().replace("€", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    return float(s)


def inum(v) -> int | None:
    n = num(v)
    return None if n is None else int(round(n))


def rate(v) -> float | None:
    """'21', '21%', '0,21' → 0.21"""
    n = num(str(v).replace("%", "")) if v not in (None, "") else None
    if n is None:
        return None
    return n / 100 if n > 1 else n


def boolish(v) -> int | None:
    s = (v or "").strip().lower()
    if s in ("1", "yes", "y", "true", "ja", "evet", "e"):
        return 1
    if s in ("0", "no", "n", "false", "nee", "hayır", "hayir", "h"):
        return 0
    return None


def _get_or_create_product(conn, ean: str | None, row: dict, now: str) -> tuple[int, bool]:
    if ean:
        r = conn.execute("SELECT id FROM products WHERE ean = ?", (ean,)).fetchone()
        if r:
            return r["id"], False
    cur = conn.execute(
        "INSERT INTO products(ean, brand, model, variant, size, pack_qty, title, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (ean, row.get("brand") or None, row.get("model") or None, row.get("variant") or None, row.get("size") or None,
         int(num(row.get("pack_qty")) or 1), row.get("title") or ean or "(adsız)", now))
    pid = cur.lastrowid
    compliance.ensure_checks(conn, pid, row.get("category_key") or row.get("platform_category") or None, now)
    return pid, True


def _attribute_candidate(conn, row: dict) -> tuple[int | None, list[str]]:
    """EAN'sız kayıt için tek bir aday ürün bul. Birden fazla aday varsa eşleştirme yapılmaz."""
    candidates = []
    for p in conn.execute("SELECT * FROM products"):
        m = attribute_match(row, dict(p))
        if m.status == "candidate":
            candidates.append((p["id"], m.reasons))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        return None, [f"{len(candidates)} olası aday var — otomatik eşleştirilmedi"]
    return None, []


def import_supplier_csv(conn, text: str, source_name: str) -> ImportReport:
    rep, now = ImportReport(), utc_now()
    for i, row in enumerate(read_csv(text), start=2):
        rep.rows += 1
        if not row.get("title") or not row.get("supplier"):
            rep.warn(i, "supplier ve title zorunlu — atlandı")
            continue
        ean, valid = normalize_ean(row.get("ean"))
        if valid is False:
            rep.warn(i, f"Geçersiz EAN '{row.get('ean')}' — nitelikle eşleştirme denenecek")
        try:
            price = num(row.get("purchase_price_excl_vat"))
            vat = rate(row.get("purchase_vat_rate"))
            imp = num(row.get("import_cost_per_unit"))
        except ValueError as e:
            rep.warn(i, f"Sayı okunamadı: {e} — atlandı")
            continue
        product_id = candidate_id = None
        if valid:
            product_id, _ = _get_or_create_product(conn, ean, row, now)
            status, note = "ean", "EAN/GTIN ile eşleşti"
        else:
            candidate_id, reasons = _attribute_candidate(conn, row)
            if candidate_id:
                status, note = "candidate", "; ".join(reasons)
            else:
                product_id, _ = _get_or_create_product(conn, None, row, now)
                status, note = "unmatched", "; ".join(reasons) or "EAN yok, aday bulunamadı — ayrı ürün olarak kaydedildi"
        basis = (row.get("price_basis") or "catalog").lower()
        if basis not in PRICE_BASES:
            rep.warn(i, f"price_basis '{basis}' tanınmadı — 'catalog' kabul edildi")
            basis = "catalog"
        try:
            quote = (basis, inum(row.get("moq")), inum(row.get("lead_time_days")), row.get("quote_date") or now[:10],
                     row.get("valid_until") or None, row.get("source_url") or None, row.get("notes") or None)
        except ValueError as e:
            rep.warn(i, f"MOQ/teslim süresi okunamadı: {e} — boş bırakıldı")
            quote = (basis, None, None, row.get("quote_date") or now[:10], row.get("valid_until") or None,
                     row.get("source_url") or None, row.get("notes") or None)
        existing = conn.execute("SELECT id FROM supplier_items WHERE supplier = ? AND supplier_sku IS ?",
                                (row["supplier"], row.get("supplier_sku") or None)).fetchone()
        values = (ean, None if valid is None else int(valid), row.get("brand") or None, row.get("model") or None,
                  row.get("variant") or None, row.get("size") or None, int(num(row.get("pack_qty")) or 1), row["title"],
                  price, vat, imp, product_id, status, note, candidate_id) + quote + (f"csv:{source_name}", now)
        cols = ("ean, ean_valid, brand, model, variant, size, pack_qty, title, purchase_price_excl_vat, purchase_vat_rate, "
                "import_cost_per_unit, product_id, match_status, match_note, candidate_product_id, "
                + ", ".join(QUOTE_FIELDS) + ", source, updated_at")
        if existing:
            conn.execute(f"UPDATE supplier_items SET {', '.join(c.strip() + '=?' for c in cols.split(','))} WHERE id=?",
                         values + (existing["id"],))
            rep.updated += 1
        else:
            conn.execute(f"INSERT INTO supplier_items({cols}, supplier, supplier_sku) VALUES ({','.join('?' * (len(values) + 2))})",
                         values + (row["supplier"], row.get("supplier_sku") or None))
            rep.created += 1
    conn.commit()
    return rep


def upsert_listing(conn, product_id: int, platform: str, external_id, url, category, match_method: str) -> int:
    conn.execute("""INSERT INTO listings(product_id, platform, external_id, url, platform_category, match_method)
        VALUES (?,?,?,?,?,?) ON CONFLICT(product_id, platform) DO UPDATE SET
        external_id = COALESCE(excluded.external_id, external_id), url = COALESCE(excluded.url, url),
        platform_category = COALESCE(excluded.platform_category, platform_category),
        match_method = CASE WHEN match_method = 'candidate' THEN excluded.match_method ELSE match_method END""",
                 (product_id, platform, external_id, url, category, match_method))
    return conn.execute("SELECT id FROM listings WHERE product_id = ? AND platform = ?", (product_id, platform)).fetchone()["id"]


def insert_snapshot(conn, listing_id: int, d: dict) -> None:
    dup = conn.execute("SELECT 1 FROM offer_snapshots WHERE listing_id = ? AND captured_at = ? AND source = ?",
                       (listing_id, d["captured_at"], d["source"])).fetchone()
    if dup:
        return
    conn.execute("""INSERT INTO offer_snapshots(listing_id, captured_at, price, shipping_cost, delivery_min_days,
        delivery_max_days, offer_count, seller_count, featured_seller_id, featured_seller_name, featured_is_platform,
        featured_fulfilled_by_platform, platform_sells, source, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (listing_id, d["captured_at"], d["price"], d.get("shipping_cost"), d.get("delivery_min_days"),
                  d.get("delivery_max_days"), d.get("offer_count"), d.get("seller_count"), d.get("featured_seller_id"),
                  d.get("featured_seller_name"), d.get("featured_is_platform"), d.get("featured_fulfilled_by_platform"),
                  d.get("platform_sells"), d["source"], d.get("note")))


def import_offers_csv(conn, text: str, source_name: str) -> ImportReport:
    """Pazaryeri teklif/fiyat CSV'si. Aynı ürün için farklı captured_at satırları fiyat geçmişi olur."""
    rep, now = ImportReport(), utc_now()
    for i, row in enumerate(read_csv(text), start=2):
        rep.rows += 1
        platform = (row.get("platform") or "").lower()
        try:
            price = num(row.get("price"))
        except ValueError:
            price = None
        if not platform or price is None:
            rep.warn(i, "platform ve price zorunlu — atlandı")
            continue
        ean, valid = normalize_ean(row.get("ean"))
        if valid:
            pid, created = _get_or_create_product(conn, ean, row, now)
            method = "ean"
            rep.created += int(created)
        else:
            if valid is False:
                rep.warn(i, f"Geçersiz EAN '{row.get('ean')}'")
            pid, reasons = _attribute_candidate(conn, row)
            if not pid:
                rep.warn(i, "EAN yok ve tek bir aday ürün bulunamadı — kaydedilmedi. " + "; ".join(reasons))
                continue
            method = "candidate"
            rep.warn(i, "EAN yok: nitelik eşleşmesi bulundu, ürün sayfasında onay bekliyor")
        listing_id = upsert_listing(conn, pid, platform, row.get("external_id") or (ean if platform == "bol" else None),
                                    row.get("url") or None, row.get("platform_category") or None, method)
        try:
            insert_snapshot(conn, listing_id, {
                "captured_at": row.get("captured_at") or now,
                "price": price,
                "shipping_cost": num(row.get("shipping_cost")),
                "delivery_min_days": inum(row.get("delivery_min_days")),
                "delivery_max_days": inum(row.get("delivery_max_days")),
                "offer_count": inum(row.get("offer_count")),
                "seller_count": inum(row.get("seller_count")),
                "featured_seller_name": row.get("featured_seller_name") or None,
                "featured_is_platform": boolish(row.get("featured_is_platform")),
                "featured_fulfilled_by_platform": boolish(row.get("featured_fulfilled_by_platform")),
                "platform_sells": boolish(row.get("platform_sells")),
                "source": f"csv:{source_name}",
            })
        except ValueError as e:
            rep.warn(i, f"Sayı okunamadı: {e} — atlandı")
            continue
        rep.updated += 1
    conn.commit()
    return rep


def import_search_results_csv(conn, text: str, source_name: str) -> ImportReport:
    """Tedarikçi ürünlerinin pazaryeri arama sonuçları. result: found | not_found."""
    rep = ImportReport()
    for i, row in enumerate(read_csv(text), start=2):
        rep.rows += 1
        item = conn.execute("SELECT id FROM supplier_items WHERE supplier = ? AND supplier_sku = ?",
                            (row.get("supplier"), row.get("supplier_sku"))).fetchone()
        if not item:
            rep.warn(i, "Tedarikçi ürünü bulunamadı — önce tedarikçi CSV'sini yükleyin")
            continue
        result = "found" if row.get("result", "").lower() in ("found", "bulundu", "1", "yes") else "not_listed_in_search"
        record_search(conn, item["id"], row.get("platform", "").lower(), result, row.get("query"),
                      f"csv:{source_name}", row.get("searched_at") or utc_now())
        rep.updated += 1
    conn.commit()
    return rep


def record_search(conn, supplier_item_id: int, platform: str, result: str, query, source: str, at: str) -> None:
    conn.execute("""INSERT INTO search_results(supplier_item_id, platform, searched_at, result, query, source)
        VALUES (?,?,?,?,?,?) ON CONFLICT(supplier_item_id, platform) DO UPDATE SET searched_at = excluded.searched_at,
        result = excluded.result, query = excluded.query, source = excluded.source""",
                 (supplier_item_id, platform, at, result, query, source))


def save_rule(conn, rule: CommissionRule) -> None:
    r = rule_to_row(rule)
    cols = ", ".join(r)
    conn.execute(f"""INSERT INTO commission_rules({cols}) VALUES ({", ".join("?" * len(r))})
        ON CONFLICT(platform, category_key) DO UPDATE SET {", ".join(f"{k} = excluded.{k}" for k in r)}""",
                 tuple(r.values()))


def import_rules_csv(conn, text: str, source_name: str) -> ImportReport:
    rep = ImportReport()
    for i, row in enumerate(read_csv(text), start=2):
        rep.rows += 1
        try:
            rule = CommissionRule(
                platform=row["platform"].lower(), category_key=row["category_key"],
                category_label=row.get("category_label") or row["category_key"],
                tier_mode=row.get("tier_mode") or "flat", tiers=parse_tiers(row["tiers"]),
                min_fee=num(row.get("min_fee")) or 0.0, fixed_fee_bands=parse_bands(row.get("fixed_fee_bands", "")),
                amounts_include_vat=bool(boolish(row.get("amounts_include_vat"))),
                base=row.get("base") or "price_incl_shipping", status=row.get("status") or "user_entered",
                source_url=row.get("source_url") or None, checked_at=row.get("checked_at") or None,
                notes=row.get("notes") or None)
            if not rule.tiers:
                raise ValueError("tiers boş")
            if rule.tier_mode not in ("flat", "threshold", "portion"):
                raise ValueError(f"tier_mode geçersiz: {rule.tier_mode}")
        except (KeyError, ValueError) as e:
            rep.warn(i, f"Kural okunamadı: {e}")
            continue
        save_rule(conn, rule)
        rep.updated += 1
    conn.commit()
    return rep
