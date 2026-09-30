"""Uçtan uca: demo CSV içe aktarma, eşleştirme, uyarı tekrarını önleme, bağlayıcı ayrıştırma ve web sayfaları."""
from pathlib import Path

from app import alerts
from app.connectors.amazon_sp import parse_item_offers
from app.connectors.bol import parse_competing_offers
from app.demo import make_ean
from app.history import detect_spikes
from app.importers import import_offers_csv, import_rules_csv, import_supplier_csv
from app.scoring import ScoreInput, score
from app.services import Context, analyze_all

TEMPLATES = Path(__file__).resolve().parent.parent / "templates_csv"


def by_title(analyses, fragment):
    return next(a for a in analyses if fragment in a.product["title"])


def test_demo_statuses(demo_conn):
    res = analyze_all(demo_conn)
    spatel = by_title(res, "spatelset")
    assert spatel.view("amazon_nl").result.status == "exact"
    assert spatel.view("bol").result.status == "estimated"          # bol oranı doğrulanmadı
    assert by_title(res, "Glowskin").view("bol").result.status == "commission_missing"
    assert by_title(res, "citruspers").view("amazon_nl").result.status == "no_purchase_price"
    assert by_title(res, "schroevendraaier").view("amazon_nl").result.status == "estimated"  # FBA ücreti yok
    assert by_title(res, "bureaulamp").view("amazon_nl").spikes[-1]["direction"] == "düşüş"


def test_attribute_candidates_wait_for_confirmation(demo_conn):
    res = analyze_all(demo_conn)
    schroef = by_title(res, "schroevendraaier")
    assert len(schroef.candidates) == 1 and schroef.view("kaufland") is None   # onaysız analize girmez
    cand = demo_conn.execute("SELECT * FROM supplier_items WHERE supplier_sku = 'IO-7781'").fetchone()
    assert cand["match_status"] == "candidate" and cand["product_id"] is None
    # onaysız aday daha ucuz olsa bile hesapta kullanılmaz
    assert by_title(res, "spatelset").supplier["supplier_sku"] == "GN-P1"


def test_invalid_ean_row_not_merged(demo_conn):
    row = demo_conn.execute("SELECT * FROM supplier_items WHERE supplier_sku = 'IO-9120'").fetchone()
    assert row["ean_valid"] == 0 and row["match_status"] == "unmatched"


def test_same_ean_merges_across_sources(conn):
    ean = make_ean(777)
    import_supplier_csv(conn, f"supplier;supplier_sku;ean;title;purchase_price_excl_vat\nA;1;{ean};X;5\n", "a.csv")
    import_offers_csv(conn, f"platform;ean;price;captured_at\nbol;0{ean};12,50;2026-09-01T00:00:00+00:00\n", "o.csv")
    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 1    # GTIN-14 yazımı aynı ürüne bağlandı


def test_alerts_are_not_repeated(demo_conn):
    ctx = Context.load(demo_conn)
    first = demo_conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    assert first >= 1
    again = alerts.evaluate(demo_conn, analyze_all(demo_conn, ctx), ctx)
    assert again == []
    assert demo_conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == first


def test_alert_email_written_to_outbox_without_smtp(demo_conn):
    status = demo_conn.execute("SELECT email_status FROM alerts").fetchone()[0]
    assert "dosyaya yazıldı" in status


def test_unknown_seller_count_fails_threshold(demo_conn):
    ctx = Context.load(demo_conn)
    a = by_title(analyze_all(demo_conn, ctx), "spatelset")
    a.best.snapshot = dict(a.best.snapshot, seller_count=None)
    ok, details = alerts.check_conditions(a, ctx)
    assert not ok and "Satıcı sayısı bilinmiyor" in details


def test_templates_import_cleanly(conn):
    rep = import_rules_csv(conn, (TEMPLATES / "commission_rules_template.csv").read_text(encoding="utf-8"), "t")
    assert rep.warnings == [] and rep.updated == 3
    rules = {r["category_key"]: r for r in conn.execute("SELECT * FROM commission_rules")}
    assert rules["home_products"]["tier_mode"] == "threshold"
    rep = import_supplier_csv(conn, (TEMPLATES / "supplier_catalog_template.csv").read_text(encoding="utf-8"), "t")
    assert rep.warnings == []
    rep = import_offers_csv(conn, (TEMPLATES / "marketplace_offers_template.csv").read_text(encoding="utf-8"), "t")
    assert rep.warnings == [] and rep.updated == 2


def test_spike_detection():
    pts = [("t1", 20.0), ("t2", 20.2), ("t3", 16.0), ("t4", 16.1)]
    s = detect_spikes(pts, 10)
    assert len(s) == 1 and s[0]["direction"] == "düşüş" and s[0]["at"] == "t3"


def test_score_penalizes_platform_selling():
    common = dict(net_profit=6, margin_pct=20, is_exact=True, seller_count=3, data_age_hours=2,
                  compliance_summary="ok")
    assert score(ScoreInput(platform_sells=1, **common))[0] < score(ScoreInput(platform_sells=0, **common))[0]


def test_bol_competing_offers_parser():
    payload = {"offers": [
        {"offerId": "a", "retailerId": "111", "countryCode": "NL", "bestOffer": True, "price": 19.99,
         "fulfilmentMethod": "FBB", "condition": "NEW", "minDeliveryDate": "2026-10-01", "maxDeliveryDate": "2026-10-02"},
        {"offerId": "b", "retailerId": "222", "countryCode": "NL", "bestOffer": False, "price": 18.49,
         "fulfilmentMethod": "FBR", "condition": "NEW"},
    ]}
    o = parse_competing_offers(payload, "4006381333931", "2026-09-30T10:00:00+00:00")
    assert o.price == 19.99 and o.offer_count == 2 and o.seller_count == 2
    assert o.featured_fulfilled_by_platform == 1
    assert o.shipping_cost is None and o.platform_sells is None      # API vermiyor → uydurulmaz
    assert (o.delivery_min_days, o.delivery_max_days) == (1, 2)
    assert parse_competing_offers({"offers": []}, "x", "2026-09-30T10:00:00+00:00") is None


def test_amazon_item_offers_parser():
    payload = {"payload": {"Summary": {"TotalOfferCount": 25}, "Offers": [
        {"SellerId": "A1", "IsBuyBoxWinner": True, "IsFulfilledByAmazon": True,
         "ListingPrice": {"Amount": 24.99}, "Shipping": {"Amount": 0}, "ShippingTime": {"minimumHours": 24, "maximumHours": 48}},
        {"SellerId": "B2", "IsBuyBoxWinner": False, "IsFulfilledByAmazon": False,
         "ListingPrice": {"Amount": 23.50}, "Shipping": {"Amount": 2.95}},
    ]}}
    o = parse_item_offers(payload, "B000TEST", "2026-09-30T10:00:00+00:00", retail_ids=[])
    assert o.price == 24.99 and o.featured_fulfilled_by_platform == 1
    assert o.featured_is_platform is None and o.platform_sells is None   # Amazon perakende kimliği bilinmiyor
    assert o.offer_count == 25 and "alt sınır" in o.note
    o2 = parse_item_offers(payload, "B000TEST", "2026-09-30T10:00:00+00:00", retail_ids=["A1"])
    assert o2.featured_is_platform == 1 and o2.platform_sells == 1


def test_web_pages_render(demo_conn):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    for url in ["/", "/opportunities", "/import", "/settings", "/alerts", "/export.csv",
                "/?status=exact&min_profit=1", "/templates/supplier_catalog_template.csv"]:
        r = client.get(url)
        assert r.status_code == 200, url
    pid = demo_conn.execute("SELECT id FROM products WHERE title LIKE '%bureaulamp%'").fetchone()[0]
    page = client.get(f"/product/{pid}").text
    assert "Kârlılık dökümü" in page and "KDV hariç satış geliri" in page and "düşük müşteri fiyatı" in page
    r = client.post(f"/product/{pid}/quote", data={"supplier": "Test BV", "price": "10", "vat": "21", "import_cost": "0", "moq": "20"}, follow_redirects=False)
    assert r.status_code == 303
