"""Tedarikçi bulma ve tedarikçi fiyatı karşılaştırması."""
from urllib.parse import unquote_plus

import pytest

from app.demo import make_ean
from app.importers import import_supplier_csv
from app.services import Context, analyze_product, supplier_items_for
from app.sourcing import SOURCES, links_for


def pid_of(conn, frag):
    return conn.execute("SELECT id FROM products WHERE title LIKE ?", (f"%{frag}%",)).fetchone()[0]


def test_source_links_use_verified_formats_only():
    product = dict(ean="4006381333931", brand="Lumo", model="DL-20", title="Lumo LED bureaulamp dimbaar zwart")
    links = {l["key"]: l for l in links_for(product)}
    assert unquote_plus(links["ankorstore"]["link"]).endswith("q=Lumo DL-20")
    assert "orderchamp.com/nl/search?q=" in links["orderchamp"]["link"]
    assert "Lumo" not in unquote_plus(links["alibaba"]["link"])           # jenerik arama: marka çıkarılır
    assert links["faire"]["link"] == "https://www.faire.com/" and not links["faire"]["direct_search"]
    assert links["gs1"]["q"] == "4006381333931" and not links["gs1"]["direct_search"]


def test_every_source_states_price_visibility():
    assert all(s["price_visibility"] and s["how"] for s in SOURCES)


def test_cheapest_unit_cost_supplier_is_used(demo_conn):
    ctx = Context.load(demo_conn)
    a = analyze_product(demo_conn, pid_of(demo_conn, "bureaulamp"), ctx)
    assert len(a.supplier_options) == 3
    assert a.supplier["supplier"].startswith("Lumo Benelux")            # €10,40 < €11,20+0,90 < €12,90
    costs = [o["unit_cost"] for o in a.supplier_options]
    assert costs == sorted(costs)


def test_profit_per_supplier_matches_direct_calculation(demo_conn):
    ctx = Context.load(demo_conn)
    a = analyze_product(demo_conn, pid_of(demo_conn, "bureaulamp"), ctx)
    amazon_best = a.view("amazon_nl").result.net_profit
    by_supplier = {o["item"]["supplier"]: o["results"]["amazon_nl"].net_profit for o in a.supplier_options}
    assert by_supplier["Lumo Benelux distributeur (ÖRNEK)"] == pytest.approx(amazon_best)
    # daha pahalı tedarikçi → birim maliyet farkı kadar daha az kâr
    diff = by_supplier["Lumo Benelux distributeur (ÖRNEK)"] - by_supplier["Toptan platform — Lumo merkpagina (ÖRNEK)"]
    assert diff == pytest.approx(12.90 - 10.40)


def test_stale_and_expired_quotes_are_flagged(demo_conn):
    ctx = Context.load(demo_conn)
    a = analyze_product(demo_conn, pid_of(demo_conn, "drinkfles"), ctx)
    io = next(o for o in a.supplier_options if o["item"]["supplier"].startswith("Import Oost"))
    assert io["expired"] and io["stale"] and io["item"]["moq"] == 300


def test_quote_fields_imported(conn):
    ean = make_ean(901)
    csv = ("supplier;supplier_sku;ean;title;purchase_price_excl_vat;price_basis;moq;lead_time_days;quote_date;valid_until;source_url\n"
           f"A BV;1;{ean};X;5,00;quote;24;10;2026-09-01;2026-12-01;https://example.com/q1\n"
           f"B BV;2;{ean};X;4,80;teklif;x;;;;\n")
    rep = import_supplier_csv(conn, csv, "q.csv")
    assert any("price_basis" in w for w in rep.warnings) and any("MOQ" in w for w in rep.warnings)
    rows = {r["supplier"]: r for r in supplier_items_for(conn, conn.execute("SELECT id FROM products").fetchone()[0])}
    assert rows["A BV"]["moq"] == 24 and rows["A BV"]["price_basis"] == "quote"
    assert rows["A BV"]["source_url"] == "https://example.com/q1"
    assert rows["B BV"]["price_basis"] == "catalog" and rows["B BV"]["moq"] is None


def test_migration_adds_quote_columns(tmp_path):
    import sqlite3
    from app.db import init_db
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE supplier_items (id INTEGER PRIMARY KEY, supplier TEXT NOT NULL, supplier_sku TEXT, "
                "title TEXT NOT NULL, source TEXT NOT NULL, updated_at TEXT NOT NULL)")
    old.commit()
    init_db(old)
    cols = {r[1] for r in old.execute("PRAGMA table_info(supplier_items)")}
    assert {"moq", "lead_time_days", "quote_date", "source_url"} <= cols


def test_supplier_pages_render(demo_conn):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    assert "Tedarikçi bulma kaynakları" in client.get("/suppliers").text
    pid = pid_of(demo_conn, "bureaulamp")
    page = client.get(f"/product/{pid}").text
    assert "Tedarikçiler ve alış fiyatları" in page and "Lumo Benelux" in page and "Verified by GS1" in page
    r = client.post(f"/product/{pid}/quote", data={"supplier": "X", "price": "", "price_basis": "quote"})
    assert r.status_code in (400, 422)
