"""Uyarılar: eşikler sağlanınca panel içi uyarı + e-posta.

Tekrar bildirimi önleme: her uyarının parmak izi "ürün:platform"dur ve UNIQUE tutulur. Aynı fırsat için
ikinci kez uyarı oluşmaz. Kullanıcı uyarıyı "sıfırla" ile silerse fırsat yeniden bildirilebilir.
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path

from . import config
from .db import utc_now
from .services import Context, ProductAnalysis


def check_conditions(a: ProductAnalysis, ctx: Context) -> tuple[bool, list[str]]:
    """Eşikleri en iyi platform için kontrol eder. Bilinmeyen değer koşulu SAĞLAMAZ."""
    b = a.best
    if not b or not b.result or b.result.net_profit is None:
        return False, ["Net kâr hesaplanamadı"]
    r = b.result
    if ctx.settings.get("alerts_require_exact") == "1" and not r.is_exact:
        return False, ["Yalnızca kesin hesaplar için uyarı ayarlı"]
    fails, ok = [], []
    checks = [
        ("Net kâr", r.net_profit, ctx.setting("min_net_profit"), ">=", "€{:.2f}"),
        ("Marj", r.margin_pct, ctx.setting("min_margin_pct"), ">=", "%{:.1f}"),
        ("ROI", r.roi_pct, ctx.setting("min_roi_pct"), ">=", "%{:.1f}"),
        ("Satıcı sayısı", b.snapshot["seller_count"], ctx.setting("max_sellers", 999), "<=", "{:.0f}"),
    ]
    min_diff = ctx.setting("min_price_diff_pct")
    if min_diff > 0:
        checks.append(("Fiyat farkı", a.price_diff["diff_pct"] if a.price_diff else None, min_diff, ">=", "%{:.1f}"))
    for name, value, limit, op, fmt in checks:
        if value is None:
            fails.append(f"{name} bilinmiyor")
        elif (value >= limit) if op == ">=" else (value <= limit):
            ok.append(f"{name} {fmt.format(value)}")
        else:
            fails.append(f"{name} {fmt.format(value)} (eşik {op} {limit:g})")
    return not fails, ok if not fails else fails


def evaluate(conn, analyses: list[ProductAnalysis], ctx: Context) -> list[dict]:
    """Yeni uyarıları oluşturur ve döndürür (daha önce bildirilmiş fırsatlar atlanır)."""
    new = []
    for a in analyses:
        passed, details = check_conditions(a, ctx)
        if not passed:
            continue
        platform = a.best.platform
        fingerprint = f"{a.product['id']}:{platform}"
        label = a.best.result.status_text
        msg = f"{a.product['title']} — {a.best.label}: {', '.join(details)} · {label} · puan {a.score}"
        cur = conn.execute("INSERT OR IGNORE INTO alerts(product_id, platform, fingerprint, message, created_at) VALUES (?,?,?,?,?)",
                           (a.product["id"], platform, fingerprint, msg, utc_now()))
        if cur.rowcount:
            new.append({"id": cur.lastrowid, "message": msg, "product_id": a.product["id"]})
    conn.commit()
    if new:
        send_email(conn, new, ctx.settings.get("alert_email_to") or config.ALERT_EMAIL_TO)
    return new


def send_email(conn, alerts: list[dict], to_addr: str | None) -> str:
    """SMTP yapılandırılmışsa gönderir; değilse data/outbox/ altına .eml yazar. Sonuç kaydedilir."""
    msg = EmailMessage()
    msg["Subject"] = f"{config.APP_NAME}: {len(alerts)} yeni fırsat"
    msg["From"] = config.SMTP_FROM
    msg["To"] = to_addr or "alici-ayarlanmadi@localhost"
    msg.set_content("Yeni fırsatlar:\n\n" + "\n".join(f"• {a['message']}" for a in alerts)
                    + "\n\nAyrıntılar için paneli açın. Bu e-posta otomatik oluşturuldu.")
    if config.SMTP_HOST and to_addr:
        try:
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
                s.starttls()
                if config.SMTP_USER:
                    s.login(config.SMTP_USER, config.SMTP_PASSWORD or "")
                s.send_message(msg)
            status = f"gönderildi: {to_addr}"
        except (OSError, smtplib.SMTPException) as e:
            status = f"gönderilemedi: {e}"
    else:
        config.OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
        path = Path(config.OUTBOX_DIR) / f"alert-{utc_now().replace(':', '')}.eml"
        path.write_bytes(bytes(msg))
        status = f"SMTP/alıcı ayarlı değil — dosyaya yazıldı: {path.name}"
    now = utc_now()
    conn.executemany("UPDATE alerts SET emailed_at = ?, email_status = ? WHERE id = ?",
                     [(now, status, a["id"]) for a in alerts])
    conn.commit()
    return status
