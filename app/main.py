"""Web arayüzü (FastAPI + Jinja2, sunucu tarafında oluşturulan sayfalar). Çalıştırma: uvicorn app.main:app"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import alerts, compliance, config, importers, sourcing
from .connectors import PLATFORM_LABELS, PLATFORMS
from .connectors.amazon_sp import AmazonSPConnector
from .connectors.bol import BolConnector
from .db import connect, init_db, set_setting, utc_now
from .fees import STATUS_LABELS as RULE_STATUS, CommissionRule, format_bands, format_tiers, parse_bands, parse_tiers
from .profit import FULFILMENT_LABELS, STATUS_TEXT
from .services import Context, analyze_all, analyze_product, supplier_opportunities

HERE = Path(__file__).parent
app = FastAPI(title=config.APP_NAME)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


def eur(v, dash="—"):
    if v is None:
        return dash
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'−' if v < 0 else ''}€{s}"


def pct(v, dash="—"):
    return dash if v is None else f"{'−' if v < 0 else ''}%{abs(v):.1f}".replace(".", ",")


def tri(v, yes="Evet", no="Hayır", unknown="Bilinmiyor"):
    return unknown if v is None else (yes if v else no)


templates.env.filters.update(eur=eur, pct=pct, tri=tri)
templates.env.globals.update(APP_NAME=config.APP_NAME, PLATFORM_LABELS=PLATFORM_LABELS, STATUS_TEXT=STATUS_TEXT,
                             RULE_STATUS=RULE_STATUS, FULFILMENT_LABELS=FULFILMENT_LABELS,
                             COMPLIANCE_STATUS=compliance.STATUS_LABELS, CHECKS={c["key"]: c for c in compliance.CHECKS},
                             format_tiers=format_tiers, format_bands=format_bands)


def db():
    conn = connect()
    init_db(conn)
    return conn


def render(request: Request, name: str, **ctx):
    conn = db()
    ctx["unread_alerts"] = conn.execute("SELECT COUNT(*) FROM alerts WHERE is_read = 0").fetchone()[0]
    ctx["nav"] = name
    return templates.TemplateResponse(request, name, ctx)


def back(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def fnum(v):
    return importers.num(v) if v not in (None, "") else None


# ---------------------------------------------------------------- Pano
def _filter(analyses, q="", status="", platform="", min_profit=None, min_margin=None, max_sellers=None, only_alert=False,
            ctx=None):
    out = []
    for a in analyses:
        if q and q.lower() not in (a.product["title"] + " " + (a.product["ean"] or "") + " " + (a.product["brand"] or "")).lower():
            continue
        if status and a.status != status:
            continue
        if platform and not a.view(platform):
            continue
        r = a.best.result if a.best else None
        if min_profit is not None and (not r or r.net_profit is None or r.net_profit < min_profit):
            continue
        if min_margin is not None and (not r or r.margin_pct is None or r.margin_pct < min_margin):
            continue
        if max_sellers is not None and (not a.best or a.best.snapshot["seller_count"] is None
                                        or a.best.snapshot["seller_count"] > max_sellers):
            continue
        if only_alert and not alerts.check_conditions(a, ctx)[0]:
            continue
        out.append(a)
    return sorted(out, key=lambda a: -a.score)


def _query(request: Request):
    p = request.query_params
    return dict(q=p.get("q", ""), status=p.get("status", ""), platform=p.get("platform", ""),
                min_profit=fnum(p.get("min_profit")), min_margin=fnum(p.get("min_margin")),
                max_sellers=fnum(p.get("max_sellers")), only_alert=p.get("only_alert") == "1")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    conn = db()
    ctx = Context.load(conn)
    f = _query(request)
    rows = _filter(analyze_all(conn, ctx), ctx=ctx, **f)
    platforms = [p for p in PLATFORMS if any(a.view(p) for a in rows)] or ["amazon_nl", "bol"]
    return render(request, "dashboard.html", rows=rows, f=f, platforms=platforms,
                  query=str(request.query_params), api_bol=BolConnector().is_configured(),
                  api_amazon=AmazonSPConnector().is_configured())


@app.get("/export.csv")
def export_csv(request: Request):
    conn = db()
    ctx = Context.load(conn)
    rows = _filter(analyze_all(conn, ctx), ctx=ctx, **_query(request))
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["urun", "ean", "platform", "fiyat_kdv_dahil", "kargo", "fiyat_zamani", "kaynak", "satici_sayisi",
                "teklif_sayisi", "platform_kendisi_satiyor", "one_cikan_platform_mi", "platform_gonderiyor",
                "kdv_haric_gelir", "komisyon_kdv_haric", "komisyon_durumu", "net_kar", "marj_pct", "roi_pct",
                "basabas_fiyat", "hesap_durumu", "eksik_kalemler", "fark_eur", "fark_pct_dusuk_fiyat_bazli", "puan"])
    for a in rows:
        for v in a.platforms:
            r, s = v.result, v.snapshot
            w.writerow([a.product["title"], a.product["ean"] or "", v.platform, s["price"], s["shipping_cost"],
                        s["captured_at"], s["source"], s["seller_count"], s["offer_count"], s["platform_sells"],
                        s["featured_is_platform"], s["featured_fulfilled_by_platform"],
                        r and round(r.revenue_excl_vat, 2), r and r.commission and round(r.commission.total_excl_vat, 2),
                        r and r.commission_status, r and r.net_profit is not None and round(r.net_profit, 2) or "",
                        r and r.margin_pct is not None and round(r.margin_pct, 1) or "",
                        r and r.roi_pct is not None and round(r.roi_pct, 1) or "", r and r.break_even_price or "",
                        r and STATUS_TEXT[r.status], r and " | ".join(r.missing),
                        a.price_diff and a.price_diff["diff_eur"], a.price_diff and a.price_diff["diff_pct"], a.score])
    data = "﻿" + buf.getvalue()
    return StreamingResponse(iter([data]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=margeradar-export.csv"})


# ---------------------------------------------------------------- Ürün ayrıntısı
@app.get("/product/{pid}", response_class=HTMLResponse)
def product_detail(request: Request, pid: int):
    conn = db()
    if not conn.execute("SELECT 1 FROM products WHERE id = ?", (pid,)).fetchone():
        raise HTTPException(404)
    ctx = Context.load(conn)
    a = analyze_product(conn, pid, ctx)
    chart = {"series": [{"label": v.label, "platform": v.platform,
                         "points": [{"x": t, "y": p} for t, p in v.history]} for v in a.platforms]}
    supplier_rows = [dict(r) for r in conn.execute(
        "SELECT * FROM supplier_items WHERE product_id = ? OR candidate_product_id = ? ORDER BY purchase_price_excl_vat",
        (pid, pid))]
    cols = [r[1] for r in conn.execute("PRAGMA table_info(product_cost_overrides)")]
    overrides = {v.platform: dict.fromkeys(cols) for v in a.platforms}
    overrides.update({r["platform"]: dict(r) for r in conn.execute(
        "SELECT * FROM product_cost_overrides WHERE product_id = ?", (pid,))})
    rule_keys = sorted({k for (_p, k) in ctx.rules if not k.startswith("ean:")})
    return render(request, "product.html", a=a, chart_json=json.dumps(chart), supplier_rows=supplier_rows,
                  source_links=sourcing.links_for(a.product), today=utc_now()[:10],
                  overrides=overrides, costs=ctx.costs, rule_keys=rule_keys,
                  trademark_links=compliance.TRADEMARK_LINKS, stale_hours=ctx.setting("stale_hours", 72))


@app.post("/product/{pid}/quote")
def add_quote(pid: int, supplier: str = Form("Manuel giriş"), price: str = Form(...), vat: str = Form("21"),
              import_cost: str = Form(""), price_basis: str = Form("quote"), moq: str = Form(""),
              lead_time_days: str = Form(""), quote_date: str = Form(""), valid_until: str = Form(""),
              source_url: str = Form(""), notes: str = Form("")):
    """Tedarikçi fiyatı/teklifi kaydı. Aynı tedarikçinin aynı ürün için yeni teklifi eskisinin üzerine yazılır."""
    if price_basis not in importers.PRICE_BASES:
        raise HTTPException(400, "Geçersiz fiyat türü")
    try:
        values = (fnum(price), importers.rate(vat), fnum(import_cost), price_basis, importers.inum(moq),
                  importers.inum(lead_time_days))
    except ValueError as e:
        raise HTTPException(400, f"Sayı okunamadı: {e}")
    if values[0] is None:
        raise HTTPException(400, "Alış fiyatı zorunlu")
    conn = db()
    title = conn.execute("SELECT title FROM products WHERE id = ?", (pid,)).fetchone()["title"]
    now = utc_now()
    conn.execute("""INSERT INTO supplier_items(supplier, supplier_sku, title, purchase_price_excl_vat, purchase_vat_rate,
        import_cost_per_unit, price_basis, moq, lead_time_days, quote_date, valid_until, source_url, notes,
        product_id, match_status, match_note, source, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'manual','Kullanıcı elle girdi','user',?)
        ON CONFLICT(supplier, supplier_sku) DO UPDATE SET purchase_price_excl_vat = excluded.purchase_price_excl_vat,
        purchase_vat_rate = excluded.purchase_vat_rate, import_cost_per_unit = excluded.import_cost_per_unit,
        price_basis = excluded.price_basis, moq = excluded.moq, lead_time_days = excluded.lead_time_days,
        quote_date = excluded.quote_date, valid_until = excluded.valid_until, source_url = excluded.source_url,
        notes = excluded.notes, updated_at = excluded.updated_at""",
                 (supplier.strip() or "Manuel giriş", f"product-{pid}", title) + values
                 + (quote_date or now[:10], valid_until or None, source_url.strip() or None, notes.strip() or None, pid, now))
    conn.commit()
    return back(f"/product/{pid}#suppliers")


@app.get("/suppliers", response_class=HTMLResponse)
def suppliers_page(request: Request):
    conn = db()
    stale = Context.load(conn).setting("quote_stale_days", 90)
    rows = [dict(r) for r in conn.execute("""SELECT supplier, COUNT(*) AS items,
        SUM(purchase_price_excl_vat IS NOT NULL) AS priced, MAX(quote_date) AS last_quote,
        GROUP_CONCAT(DISTINCT price_basis) AS bases, MIN(moq) AS min_moq, MAX(moq) AS max_moq
        FROM supplier_items GROUP BY supplier ORDER BY supplier""")]
    return render(request, "suppliers.html", rows=rows, sources=sourcing.SOURCES, checked=sourcing.CHECKED,
                  stale_days=stale)


@app.post("/product/{pid}/override/{platform}")
def set_override(pid: int, platform: str, fulfilment_fee: str = Form(""), shipping: str = Form(""),
                 packaging: str = Form(""), ad_pct: str = Form(""), sale_vat: str = Form("")):
    conn = db()
    conn.execute("""INSERT OR REPLACE INTO product_cost_overrides(product_id, platform, fulfilment_fee_per_unit,
        seller_shipping_per_unit, packaging_per_unit, ad_spend_pct, sale_vat_rate, source, updated_at)
        VALUES (?,?,?,?,?,?,?,'user',?)""",
                 (pid, platform, fnum(fulfilment_fee), fnum(shipping), fnum(packaging), fnum(ad_pct),
                  importers.rate(sale_vat) if sale_vat else None, utc_now()))
    conn.commit()
    return back(f"/product/{pid}#costs")


@app.post("/listing/{lid}/category")
def set_category(lid: int, category_key: str = Form(...)):
    conn = db()
    conn.execute("UPDATE listings SET platform_category = ? WHERE id = ?", (category_key.strip() or None, lid))
    conn.commit()
    pid = conn.execute("SELECT product_id FROM listings WHERE id = ?", (lid,)).fetchone()["product_id"]
    return back(f"/product/{pid}")


@app.post("/listing/{lid}/{action}")
def listing_match(lid: int, action: str):
    conn = db()
    row = conn.execute("SELECT product_id FROM listings WHERE id = ?", (lid,)).fetchone()
    if action == "confirm":
        conn.execute("UPDATE listings SET match_method = 'attributes_confirmed' WHERE id = ?", (lid,))
    elif action == "reject":
        conn.execute("DELETE FROM offer_snapshots WHERE listing_id = ?", (lid,))
        conn.execute("DELETE FROM listings WHERE id = ?", (lid,))
    conn.commit()
    return back(f"/product/{row['product_id']}")


@app.post("/supplier/{sid}/{action}")
def supplier_match(sid: int, action: str):
    conn = db()
    it = conn.execute("SELECT * FROM supplier_items WHERE id = ?", (sid,)).fetchone()
    if not it:
        raise HTTPException(404)
    if action == "delete":
        conn.execute("DELETE FROM search_results WHERE supplier_item_id = ?", (sid,))
        conn.execute("DELETE FROM supplier_items WHERE id = ?", (sid,))
        conn.commit()
        return back(f"/product/{it['product_id']}#suppliers" if it["product_id"] else "/suppliers")
    if action == "confirm" and it["candidate_product_id"]:
        conn.execute("""UPDATE supplier_items SET product_id = candidate_product_id, match_status = 'confirmed',
            match_note = 'Kullanıcı onayladı (nitelik eşleşmesi)', updated_at = ? WHERE id = ?""", (utc_now(), sid))
        target = it["candidate_product_id"]
    else:
        pid, _ = importers._get_or_create_product(conn, None, dict(it), utc_now())
        conn.execute("""UPDATE supplier_items SET product_id = ?, candidate_product_id = NULL, match_status = 'unmatched',
            match_note = 'Kullanıcı reddetti — ayrı ürün', updated_at = ? WHERE id = ?""", (pid, utc_now(), sid))
        target = pid
    conn.commit()
    return back(f"/product/{target}")


@app.post("/product/{pid}/compliance/{key}")
def set_compliance(pid: int, key: str, status: str = Form(...), note: str = Form("")):
    if status not in compliance.STATUS_LABELS:
        raise HTTPException(400)
    conn = db()
    conn.execute("UPDATE compliance_checks SET status = ?, note = ?, updated_at = ? WHERE product_id = ? AND check_key = ?",
                 (status, note or None, utc_now(), pid, key))
    conn.commit()
    return back(f"/product/{pid}#compliance")


# ---------------------------------------------------------------- Tedarikçi & fırsatlar
@app.get("/opportunities", response_class=HTMLResponse)
def opportunities(request: Request):
    conn = db()
    ctx = Context.load(conn)
    ranked = sorted([a for a in analyze_all(conn, ctx) if a.platforms], key=lambda a: -a.score)
    return render(request, "opportunities.html", ranked=ranked, items=supplier_opportunities(conn),
                  platforms=["amazon_nl", "bol"])


@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request, report: str = ""):
    return render(request, "import.html", report=json.loads(report) if report else None, PLATFORMS=PLATFORMS)


@app.post("/import/{kind}")
async def do_import(kind: str, file: UploadFile = File(...)):
    fn = {"supplier": importers.import_supplier_csv, "offers": importers.import_offers_csv,
          "search": importers.import_search_results_csv, "rules": importers.import_rules_csv}.get(kind)
    if not fn:
        raise HTTPException(404)
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    conn = db()
    rep = fn(conn, text, file.filename or kind)
    if kind in ("offers", "supplier"):
        ctx = Context.load(conn)
        alerts.evaluate(conn, analyze_all(conn, ctx), ctx)
    report = {"kind": kind, "file": file.filename, "rows": rep.rows, "created": rep.created, "updated": rep.updated,
              "warnings": rep.warnings[:200]}
    from urllib.parse import quote
    return back("/import?report=" + quote(json.dumps(report)))


@app.get("/templates/{name}")
def csv_template(name: str):
    path = config.BASE_DIR / "templates_csv" / Path(name).name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="text/csv", filename=path.name)


# ---------------------------------------------------------------- Ayarlar
@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    conn = db()
    ctx = Context.load(conn)
    rules = sorted(ctx.rules.values(), key=lambda r: (r.platform, r.category_key))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(platform_costs)")]
    costs = {p: ctx.costs.get(p) or dict.fromkeys(cols) for p in PLATFORMS}
    return render(request, "settings.html", s=ctx.settings, costs=costs, rules=rules, PLATFORMS=PLATFORMS,
                  api_bol=BolConnector().is_configured(), api_amazon=AmazonSPConnector().is_configured(),
                  smtp=bool(config.SMTP_HOST))


@app.post("/settings")
async def save_settings(request: Request):
    form = await request.form()
    conn = db()
    for key in ("default_sale_vat_rate", "spike_threshold_pct", "stale_hours", "quote_stale_days", "min_net_profit", "min_margin_pct",
                "min_roi_pct", "max_sellers", "min_price_diff_pct", "alert_email_to"):
        if key in form:
            value = str(form[key]).strip()
            if key == "default_sale_vat_rate":
                value = str(importers.rate(value))
            elif key != "alert_email_to":
                value = str(importers.num(value) or 0)
            set_setting(conn, key, value)
    set_setting(conn, "vat_registered", "1" if form.get("vat_registered") else "0")
    set_setting(conn, "alerts_require_exact", "1" if form.get("alerts_require_exact") else "0")
    conn.commit()
    return back("/settings#general")


@app.post("/settings/costs/{platform}")
async def save_costs(request: Request, platform: str):
    f = await request.form()
    conn = db()
    conn.execute("""INSERT OR REPLACE INTO platform_costs(platform, fulfilment_model, sale_vat_rate, seller_shipping_per_unit,
        fulfilment_fee_per_unit, fulfilment_surcharge_pct, packaging_per_unit, return_allowance_pct, ad_spend_pct,
        transaction_fixed_fee, monthly_plan_fee, expected_monthly_units, fee_source_note, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (platform, f.get("fulfilment_model", "FBM"), importers.rate(f.get("sale_vat_rate")),
                  fnum(f.get("seller_shipping_per_unit")), fnum(f.get("fulfilment_fee_per_unit")),
                  fnum(f.get("fulfilment_surcharge_pct")) or 0, fnum(f.get("packaging_per_unit")),
                  fnum(f.get("return_allowance_pct")), fnum(f.get("ad_spend_pct")),
                  fnum(f.get("transaction_fixed_fee")) or 0, fnum(f.get("monthly_plan_fee")) or 0,
                  importers.inum(f.get("expected_monthly_units")), f.get("fee_source_note") or None, utc_now()))
    conn.commit()
    return back("/settings#costs")


@app.post("/settings/rule")
async def save_rule_form(request: Request):
    f = await request.form()
    try:
        rule = CommissionRule(
            platform=f["platform"], category_key=f["category_key"].strip(),
            category_label=(f.get("category_label") or f["category_key"]).strip(), tier_mode=f.get("tier_mode", "flat"),
            tiers=parse_tiers(f["tiers"]), min_fee=fnum(f.get("min_fee")) or 0.0,
            fixed_fee_bands=parse_bands(f.get("fixed_fee_bands", "")),
            amounts_include_vat=bool(f.get("amounts_include_vat")), base=f.get("base", "price"),
            status=f.get("status", "user_entered"), source_url=f.get("source_url") or None,
            checked_at=f.get("checked_at") or utc_now()[:10], notes=f.get("notes") or None)
        if not rule.tiers:
            raise ValueError("oran boş")
    except (KeyError, ValueError) as e:
        raise HTTPException(400, f"Kural okunamadı: {e}")
    conn = db()
    importers.save_rule(conn, rule)
    conn.commit()
    return back(f.get("next") or "/settings#rules")


@app.post("/settings/rule/{rid}/delete")
def delete_rule(rid: int):
    conn = db()
    conn.execute("DELETE FROM commission_rules WHERE id = ?", (rid,))
    conn.commit()
    return back("/settings#rules")


# ---------------------------------------------------------------- Uyarılar
@app.get("/alerts", response_class=HTMLResponse)
def alerts_page(request: Request):
    conn = db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM alerts ORDER BY created_at DESC")]
    return render(request, "alerts.html", rows=rows, s=Context.load(conn).settings)


@app.post("/alerts/run")
def alerts_run():
    conn = db()
    ctx = Context.load(conn)
    alerts.evaluate(conn, analyze_all(conn, ctx), ctx)
    return back("/alerts")


@app.post("/alerts/{aid}/{action}")
def alert_action(aid: int, action: str):
    conn = db()
    if action == "read":
        conn.execute("UPDATE alerts SET is_read = 1 WHERE id = ?", (aid,))
    elif action == "reset":
        conn.execute("DELETE FROM alerts WHERE id = ?", (aid,))
    conn.commit()
    return back("/alerts")


@app.post("/sync")
def sync_now():
    from .sync import run_sync
    run_sync(db(), log=lambda *_: None)
    return back("/")
