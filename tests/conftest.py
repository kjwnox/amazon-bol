import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.db import connect, init_db  # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "OUTBOX_DIR", tmp_path / "outbox")
    monkeypatch.setattr(config, "SMTP_HOST", None)
    c = connect()
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def demo_conn(conn):
    from app.demo import run_demo
    run_demo(conn, write_files=False, log=lambda *_: None)
    return conn
