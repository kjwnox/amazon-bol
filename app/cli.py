"""Komut satırı.

    python -m app.cli init           # boş veritabanı oluştur
    python -m app.cli demo           # veritabanını SIFIRLA ve örnek veriyle doldur
    python -m app.cli sync           # yapılandırılmış API'lerden fiyatları güncelle + uyarıları değerlendir
    python -m app.cli alerts         # yalnızca uyarıları değerlendir
    python -m app.cli import-supplier dosya.csv
    python -m app.cli import-offers dosya.csv
    python -m app.cli import-rules dosya.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import alerts, config, importers
from .db import connect, init_db
from .services import Context, analyze_all


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = argv[0] if argv else "help"
    if cmd == "demo":
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        conn = connect()
        init_db(conn)
        from .demo import run_demo
        run_demo(conn)
        print(f"Demo hazır: {config.DB_PATH}")
        return 0
    conn = connect()
    init_db(conn)
    if cmd == "init":
        print(f"Veritabanı hazır: {config.DB_PATH}")
    elif cmd == "sync":
        from .sync import run_sync
        print(run_sync(conn))
    elif cmd == "alerts":
        ctx = Context.load(conn)
        print(f"{len(alerts.evaluate(conn, analyze_all(conn, ctx), ctx))} yeni uyarı")
    elif cmd in ("import-supplier", "import-offers", "import-rules", "import-search") and len(argv) > 1:
        fn = {"import-supplier": importers.import_supplier_csv, "import-offers": importers.import_offers_csv,
              "import-rules": importers.import_rules_csv, "import-search": importers.import_search_results_csv}[cmd]
        path = Path(argv[1])
        rep = fn(conn, path.read_text(encoding="utf-8-sig"), path.name)
        print(f"{rep.rows} satır, {rep.created} yeni, {rep.updated} güncel")
        for w in rep.warnings:
            print(" ⚠", w)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
