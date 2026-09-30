"""Demo verisi: ÖRNEK CSV'ler üretir ve normal içe aktarma yolundan yükler.

* Markalar kurgusaldır. EAN'lar GS1'in mağaza içi kullanıma ayrılmış "20" önekiyle üretilir (gerçek ürünle çakışmaz).
* Fiyatlar örnektir, gerçek pazar fiyatı değildir.
* Amazon.nl komisyon oranları resmî tablodan alınmıştır (kaynak ve kontrol tarihi kayıtlı).
* bol.com resmî tablosu yalnızca kategori ARALIĞI verir; demo kuralları aralığın üst sınırıyla "doğrulanmadı"
  olarak eklenir → bol.com hesapları "tahmini" görünür. Güzellik kategorisi için bilerek bol kuralı eklenmez
  ("komisyon oranı gerekli" durumunu göstermek için).
"""
from __future__ import annotations

import csv
import io
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import alerts
from .config import BASE_DIR
from .db import utc_now
from .fees import CommissionRule, FeeBand, Tier
from .importers import (OFFER_COLUMNS, SEARCH_COLUMNS, SUPPLIER_COLUMNS, import_offers_csv, import_search_results_csv,
                        import_supplier_csv, save_rule)
from .services import Context, analyze_all

SAMPLE_DIR = BASE_DIR / "sample_data"
AMAZON_SRC = "https://sell.amazon.nl/en/prijsstelling"
BOL_SRC = ("https://partnerplatform.bol.com/nl/idp/commissie — commissietabel_NL_Aug_26.xlsx "
           "(https://eu-assets.contentstack.com/v3/assets/blt27f326c486dc0d7d/blt3c1ec1fbe6c5e6e4/"
           "6a8c452bc9afba0b6ccd8c1a/commissietabel_NL_Aug_26.xlsx)")
CHECKED = "2026-09-30"


def make_ean(n: int) -> str:
    body = f"20{n:010d}"
    total = sum(int(ch) * (3 if i % 2 == 0 else 1) for i, ch in enumerate(reversed(body)))
    return body + str((10 - total % 10) % 10)


# (anahtar, marka, model, varyant, ölçü, başlık, kategori, alış €, ithalat €, amazon, bol)
# amazon/bol: (fiyat, satıcı sayısı, teklif sayısı, platform_sells, featured_is_platform, FBA/LVB ile mi)
PRODUCTS = [
    ("p1", "Kookfix", "SP-3", "grijs", None, "Kookfix siliconen spatelset 3-delig grijs", "kitchen", 3.40, 0.25,
     (14.99, 4, 5, 0, 0, 1), (16.99, 3, 3, None, None, 0)),
    ("p2", "Lumo", "DL-20", "zwart", None, "Lumo LED bureaulamp dimbaar zwart", "home_products", 11.20, 0.90,
     (34.95, 3, 3, 0, 0, 1), (39.99, 2, 2, None, None, 1)),
    ("p3", "Petvibe", "KP-60", None, "60 cm", "Petvibe krabpaal mini 60 cm", "pet_supplies", 9.50, 0.80,
     (29.99, 6, 7, 1, 1, 1), (27.49, 4, 4, None, None, 0)),
    ("p4", "Aquafles", "RVS-750", "zwart", "750 ml", "Aquafles RVS drinkfles 750 ml zwart", "sports_outdoors", 4.10, 0.20,
     (19.95, 14, 16, 0, 0, 1), (17.95, 11, 12, None, None, 0)),
    ("p5", "Bouwmax", "BM-32", None, None, "Bouwmax schroevendraaierset 32-delig", "tools", 6.80, 0.40,
     (24.99, 5, 5, 0, 0, 1), (22.99, 3, 3, None, None, 0)),
    ("p6", "Speelbos", "HP-24", None, None, "Speelbos houten puzzel 24 stukjes", "toys", 3.90, 0.30,
     (13.49, 2, 2, 0, 0, 0), (12.99, 2, 2, None, None, 0)),
    ("p7", "Snoerloos", "C2-2P", None, "2 m", "Snoerloos USB-C kabel 2 m 2-pack", "electronic_accessories", 2.20, 0.10,
     (11.99, 8, 9, 0, 0, 1), (12.49, 5, 5, None, None, 0)),
    ("p8", "Glowskin", "SER-30", None, "30 ml", "Glowskin gezichtsserum 30 ml", "beauty", 5.10, 0.00,
     (9.49, 3, 3, 0, 0, 1), (10.99, 2, 2, None, None, 0)),
    ("p9", "Vitaro", "CP-1", None, None, "Vitaro elektrische citruspers", "kitchen", None, None,
     (24.95, 7, 8, 1, 0, 1), (26.99, 4, 4, None, None, 0)),
]
FBA_FEES = {"p1": 3.15, "p2": 4.60, "p3": 6.20, "p4": 3.40, "p6": 3.25, "p7": 2.95, "p8": 2.90, "p9": 4.10}
# p5 bilerek eksik: FBA ücreti girilmedi → "tahmini"
SPIKES = {("p2", "amazon_nl"): -15.0, ("p4", "bol"): 12.0}
STALE = {"p5"}


def _csv_text(columns: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, delimiter=";", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in columns})
    return buf.getvalue()


def build_sample_csvs(now: datetime) -> dict[str, str]:
    rnd = random.Random(42)
    eans = {key: make_ean(i + 1) for i, (key, *_rest) in enumerate(PRODUCTS)}

    supplier = []
    for key, brand, model, variant, size, title, cat, buy, imp, _a, _b in PRODUCTS:
        if buy is None:
            continue  # p9: alış fiyatı yok → yalnızca pazaryeri verisi
        supplier.append(dict(supplier="Groothandel Noord (ÖRNEK)", supplier_sku=f"GN-{key.upper()}", ean=eans[key],
                             brand=brand, model=model, variant=variant, size=size, pack_qty=1, title=title,
                             purchase_price_excl_vat=f"{buy:.2f}".replace(".", ","), purchase_vat_rate="21",
                             import_cost_per_unit=f"{imp:.2f}".replace(".", ","), category_key=cat,
                             price_basis="catalog", moq=10, lead_time_days=2,
                             quote_date=(now - timedelta(days=14)).date().isoformat()))
    today = now.date()
    supplier += [
        # Aynı ürün (aynı EAN) için farklı tedarikçi teklifleri → ürün sayfasında karşılaştırma
        dict(supplier="Lumo Benelux distributeur (ÖRNEK)", supplier_sku="LB-DL20", ean=eans["p2"], brand="Lumo",
             model="DL-20", variant="zwart", pack_qty=1, title="Lumo LED bureaulamp dimbaar zwart",
             purchase_price_excl_vat="10,40", purchase_vat_rate="21", import_cost_per_unit="0,00",
             price_basis="quote", moq=50, lead_time_days=5, quote_date=(today - timedelta(days=6)).isoformat(),
             valid_until=(today + timedelta(days=24)).isoformat(), notes="Staffel ÖRNEK: 100+ stuks €9,90"),
        dict(supplier="Toptan platform — Lumo merkpagina (ÖRNEK)", supplier_sku="AK-LUMO-DL20", ean=eans["p2"],
             brand="Lumo", model="DL-20", variant="zwart", pack_qty=1, title="Lumo LED bureaulamp dimbaar zwart",
             purchase_price_excl_vat="12,90", purchase_vat_rate="21", import_cost_per_unit="0,00",
             price_basis="platform", moq=4, lead_time_days=7, quote_date=(today - timedelta(days=2)).isoformat()),
        dict(supplier="Import Oost (ÖRNEK)", supplier_sku="IO-RVS750", ean=eans["p4"], brand="Aquafles",
             model="RVS-750", variant="zwart", size="750 ml", pack_qty=1, title="Aquafles RVS drinkfles 750 ml zwart",
             purchase_price_excl_vat="3,20", purchase_vat_rate="21", import_cost_per_unit="0,55",
             price_basis="quote", moq=300, lead_time_days=35, quote_date=(today - timedelta(days=130)).isoformat(),
             valid_until=(today - timedelta(days=40)).isoformat(), notes="FOB-teklif ÖRNEK; zeevracht + invoerrecht geschat"),
        # EAN yok → nitelikle p1'e aday (onay gerekir; varyant eksik olduğu için "belirsiz" notu)
        dict(supplier="Import Oost (ÖRNEK)", supplier_sku="IO-7781", ean="", brand="Kookfix", model="SP 3", pack_qty=1,
             title="KOOKFIX spatel set 3st", purchase_price_excl_vat="2,95", purchase_vat_rate="21",
             import_cost_per_unit="0,60", category_key="kitchen"),
        # Pazaryerlerinde aranıp listelenmemiş ürün → yeni ürün fırsatı adayı
        dict(supplier="Groothandel Noord (ÖRNEK)", supplier_sku="GN-NB3", ean=make_ean(50), brand="Nordhout",
             model="NB-3", size=None, pack_qty=3, title="Nordhout bamboe snijplankenset 3 stuks",
             purchase_price_excl_vat="5,60", purchase_vat_rate="21", import_cost_per_unit="0,45", category_key="kitchen"),
        # Excel'in bozduğu EAN (bilimsel gösterim) → uyarı, nitelik eşleştirmesi denenir
        dict(supplier="Import Oost (ÖRNEK)", supplier_sku="IO-9120", ean="2,00E+12", brand="Kookfix", model="PL-2",
             pack_qty=2, title="Kookfix pannenlappen 2 stuks", purchase_price_excl_vat="1,80", purchase_vat_rate="21",
             import_cost_per_unit="", category_key="kitchen"),
    ]

    offers = []
    for key, brand, model, variant, size, title, cat, _buy, _imp, amz, bol in PRODUCTS:
        for platform, data in (("amazon_nl", amz), ("bol", bol)):
            price, sellers, n_offers, p_sells, f_is_p, fulfilled = data
            latest_age = timedelta(days=5) if key in STALE else timedelta(hours=3)
            spike = SPIKES.get((key, platform))
            points = 15
            for i in range(points):
                t = now - latest_age - timedelta(days=2 * (points - 1 - i))
                p = price * (1 + rnd.uniform(-0.015, 0.015)) if i < points - 1 else price
                if spike and i == points - 1:
                    p = price
                elif spike:
                    p = price / (1 + spike / 100) * (1 + rnd.uniform(-0.01, 0.01))
                external = f"B0DEMO{int(eans[key][-6:]):06d}" if platform == "amazon_nl" else eans[key]
                url = (f"https://www.amazon.nl/dp/{external}" if platform == "amazon_nl" else None)
                offers.append(dict(
                    platform=platform, ean=eans[key], external_id=external, title=title, brand=brand, model=model,
                    variant=variant, size=size, pack_qty=1, url=url, platform_category=cat,
                    captured_at=t.replace(microsecond=0).isoformat(), price=f"{p:.2f}",
                    shipping_cost="0" if platform == "amazon_nl" else "",
                    delivery_min_days=1 if fulfilled else 2, delivery_max_days=2 if fulfilled else 4,
                    offer_count=n_offers, seller_count=sellers,
                    featured_seller_name=("Amazon.nl" if f_is_p else "DemoSeller BV (ÖRNEK)") if platform == "amazon_nl" else "",
                    featured_is_platform="" if f_is_p is None else f_is_p,
                    featured_fulfilled_by_platform=fulfilled,
                    platform_sells="" if p_sells is None else p_sells))
    # EAN'sız Kaufland satırı → p5'e nitelik adayı (onay bekler)
    offers.append(dict(platform="kaufland", ean="", title="Bouwmax schroevendraaier set 32 dlg", brand="Bouwmax",
                       model="BM 32", pack_qty=1, platform_category="tools",
                       captured_at=(now - timedelta(hours=20)).replace(microsecond=0).isoformat(), price="23.49",
                       shipping_cost="3.95", delivery_min_days=2, delivery_max_days=5, offer_count=2, seller_count=2))

    searches = []
    for key, *_rest in PRODUCTS:
        if key == "p9":
            continue
        for platform in ("amazon_nl", "bol"):
            searches.append(dict(supplier="Groothandel Noord (ÖRNEK)", supplier_sku=f"GN-{key.upper()}",
                                 platform=platform, result="found", searched_at=utc_now(), query=f"EAN {eans[key]}"))
    for platform in ("amazon_nl", "bol"):
        searches.append(dict(supplier="Groothandel Noord (ÖRNEK)", supplier_sku="GN-NB3", platform=platform,
                             result="not_found", searched_at=utc_now(), query=f"EAN {make_ean(50)}"))

    return {
        "supplier_catalog.csv": _csv_text(SUPPLIER_COLUMNS, supplier),
        "marketplace_offers.csv": _csv_text(OFFER_COLUMNS, offers),
        "search_results.csv": _csv_text(SEARCH_COLUMNS, searches),
    }


def seed_rules(conn) -> None:
    amazon = [
        ("kitchen", "Kitchen", "flat", [Tier(None, 15)]),
        ("home_products", "Home Products", "threshold", [Tier(20, 8), Tier(None, 15)]),
        ("pet_supplies", "Pet Supplies", "flat", [Tier(None, 15)]),
        ("sports_outdoors", "Sports and Outdoors", "flat", [Tier(None, 15)]),
        ("tools", "Tools and Home Improvement", "flat", [Tier(None, 13)]),
        ("toys", "Toys and Games", "flat", [Tier(None, 15)]),
        ("electronic_accessories", "Electronic Accessories; Computer Accessories", "portion", [Tier(100, 15), Tier(None, 8)]),
        ("beauty", "Beauty, Health and Personal Care", "threshold", [Tier(10, 8), Tier(None, 15)]),
        ("other", "Other Categories", "flat", [Tier(None, 15)]),
    ]
    for key, label, mode, tiers in amazon:
        save_rule(conn, CommissionRule(
            platform="amazon_nl", category_key=key, category_label=label, tier_mode=mode, tiers=tiers, min_fee=0.30,
            amounts_include_vat=False, base="price_incl_shipping", status="official_verified",
            source_url=AMAZON_SRC, checked_at=CHECKED,
            notes="Referral fee toplam satış fiyatı (kargo dahil) üzerinden. Tutarlar KDV hariç kabul edildi."))
    # bol.com: resmî tablo yalnızca aralık veriyor → üst sınır, "doğrulanmadı"
    bol = [
        ("kitchen", "Keukenartikelen", "5–12,5", 12.5, "0,05–0,95", 0.95),
        ("home_products", "Woon-/Kantoorinrichting", "4–14", 14.0, "0,05–3,09", 3.09),
        ("pet_supplies", "Dierenverzorging/Dierenvoeding", "6–12,4", 12.4, "0,25–0,95", 0.95),
        ("sports_outdoors", "Sportartikelen", "4–13,2", 13.2, "0,25–0,95", 0.95),
        ("tools", "Gereedschap/Apparatuur", "4,1–12,4", 12.4, "0,25–0,95", 0.95),
        ("toys", "Speelgoed/Spellen", "4,1–20,7", 20.7, "0,25–3,09", 3.09),
        ("electronic_accessories", "Communicatie", "2,1–12,4", 12.4, "0,25–3,09", 3.09),
    ]
    for key, label, pct_range, pct, fee_range, fee in bol:
        save_rule(conn, CommissionRule(
            platform="bol", category_key=key, category_label=label, tier_mode="flat", tiers=[Tier(None, pct)],
            fixed_fee_bands=[FeeBand(None, fee)], amounts_include_vat=False, base="price", status="unverified",
            source_url=BOL_SRC, checked_at=CHECKED,
            notes=f"Resmî tablo yalnızca aralık veriyor: %{pct_range}, sabit €{fee_range}. Aralığın üst sınırı "
                  "ihtiyatlı yer tutucu olarak kullanıldı. Gerçek oranı partner hesabınızdan girin veya Commission API'yi bağlayın."))


def seed_costs(conn) -> None:
    now = utc_now()
    rows = [
        ("amazon_nl", "FBA", 0.21, None, None, 1.5, 0.15, 4.0, 6.0, 0.0, 39.0, 150,
         "FBA ücreti ürün bazında girilir (Amazon oran kartı / SP-API tahmini). %1,5 yakıt ve enflasyon ek ücreti "
         "17 Nisan 2026'dan itibaren FBA ücretlerine uygulanır (sell.amazon.nl, kontrol 2026-09-30). "
         "Profesyonel plan €39/ay KDV hariç. Paketleme/iade/reklam değerleri ÖRNEKTİR."),
        ("bol", "FBR", 0.21, 4.25, None, 0.0, 0.35, 3.0, 5.0, 0.0, 0.0, None,
         "Kargo €4,25 ÖRNEK değerdir — kendi taşıyıcı tarifenizi girin. İade/reklam ÖRNEKTİR."),
        ("kaufland", "FBR", 0.21, 4.25, None, 0.0, 0.35, 3.0, 0.0, 0.0, 0.0, None,
         "Kaufland komisyonu doğrulanmadı — kural eklenmedi. Değerler ÖRNEKTİR."),
    ]
    for r in rows:
        conn.execute("""INSERT OR REPLACE INTO platform_costs(platform, fulfilment_model, sale_vat_rate,
            seller_shipping_per_unit, fulfilment_fee_per_unit, fulfilment_surcharge_pct, packaging_per_unit,
            return_allowance_pct, ad_spend_pct, transaction_fixed_fee, monthly_plan_fee, expected_monthly_units,
            fee_source_note, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", r + (now,))


def run_demo(conn, write_files: bool = True, log=print) -> None:
    now = datetime.now(timezone.utc)
    files = build_sample_csvs(now)
    if write_files:
        SAMPLE_DIR.mkdir(exist_ok=True)
        for name, text in files.items():
            (SAMPLE_DIR / name).write_text(text, encoding="utf-8")
    seed_rules(conn)
    seed_costs(conn)
    for name, fn in (("supplier_catalog.csv", import_supplier_csv), ("marketplace_offers.csv", import_offers_csv),
                     ("search_results.csv", import_search_results_csv)):
        rep = fn(conn, files[name], name)
        log(f"{name}: {rep.rows} satır, {rep.created} yeni, {rep.updated} güncel, {len(rep.warnings)} uyarı")
        for w in rep.warnings:
            log(f"   ⚠ {w}")
    eans = {key: make_ean(i + 1) for i, (key, *_r) in enumerate(PRODUCTS)}
    ts = utc_now()
    for key, fee in FBA_FEES.items():
        pid = conn.execute("SELECT id FROM products WHERE ean = ?", (eans[key],)).fetchone()["id"]
        conn.execute("""INSERT OR REPLACE INTO product_cost_overrides(product_id, platform, fulfilment_fee_per_unit,
            source, updated_at) VALUES (?, 'amazon_nl', ?, 'user (ÖRNEK)', ?)""", (pid, fee, ts))
    # Örnek uyum kararları
    p1 = conn.execute("SELECT id FROM products WHERE ean = ?", (eans["p1"],)).fetchone()["id"]
    conn.execute("UPDATE compliance_checks SET status='ok', note='ÖRNEK: demo için işaretlendi', updated_at=? WHERE product_id=?",
                 (ts, p1))
    p7 = conn.execute("SELECT id FROM products WHERE ean = ?", (eans["p7"],)).fetchone()["id"]
    conn.execute("""UPDATE compliance_checks SET status='risk', note='ÖRNEK: tedarikçi CE/RoHS uygunluk beyanı sağlamadı',
        updated_at=? WHERE product_id=? AND check_key='product_safety'""", (ts, p7))
    conn.commit()
    ctx = Context.load(conn)
    new = alerts.evaluate(conn, analyze_all(conn, ctx), ctx)
    log(f"Uyarı: {len(new)} yeni fırsat bildirimi oluşturuldu")
