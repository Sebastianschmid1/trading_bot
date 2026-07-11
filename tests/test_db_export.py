"""
Tests für den Read-only-Snapshot-Export der SQLite-DB (PLAT-001, stockbot/core/db_export.py).

Lauf:  pytest tests/test_db_export.py   (offline)
"""

import base64
import json
import tempfile
from pathlib import Path

from stockbot.core import db
from stockbot.core import db_export

CHAT = 9911


def _fresh():
    d = tempfile.mkdtemp(prefix="dbexporttest_")
    db.DB_FILE = Path(d) / "t.db"
    db.init_db()
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=100.0, broker_platform="alpaca",
                     broker_api_key="key-123", broker_api_secret="secret-456")
    return d


def test_export_snapshot_contains_all_tables_and_row_counts():
    _fresh()
    out_dir = Path(tempfile.mkdtemp(prefix="dbexportout_"))
    path = db_export.export_snapshot(out_dir, stamp="20260711T000000Z")

    assert path.parent == out_dir
    assert path.name == "sqlite_snapshot_20260711T000000Z.json"

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["exported_at"] == "20260711T000000Z"
    assert data["source_db"] == str(db.DB_FILE)
    assert data["row_counts"]["users"] == 1
    assert len(data["tables"]["users"]) == 1
    for name in db_export.TABLES:
        assert name in data["tables"]
        assert data["row_counts"][name] == len(data["tables"][name])


def test_export_snapshot_keeps_broker_credentials_encrypted():
    _fresh()
    out_dir = Path(tempfile.mkdtemp(prefix="dbexportout_"))
    path = db_export.export_snapshot(out_dir, stamp="20260711T000001Z")
    data = json.loads(path.read_text(encoding="utf-8"))

    user_row = data["tables"]["users"][0]
    # BLOB-Spalten kommen Base64-kodiert an — kein Klartext, keine Fehler beim Dekodieren.
    assert "key-123" not in json.dumps(user_row)
    assert "secret-456" not in json.dumps(user_row)
    decrypted_key = db.decrypt(base64.b64decode(user_row["broker_api_key"]))
    assert decrypted_key == "key-123"


def test_export_snapshot_default_dir_is_created(monkeypatch, tmp_path):
    _fresh()
    fake_export_dir = tmp_path / "sqlite_exports"
    monkeypatch.setattr(db_export, "EXPORT_DIR", fake_export_dir)

    path = db_export.export_snapshot(stamp="20260711T000002Z")

    assert path.parent == fake_export_dir
    assert path.exists()
