"""Forschungsdaten: Rohdatenarchiv, Shadow-Snapshots, Polymarket.

Nichts hiervon liegt im Handelspfad. Es sind Metadaten und Messwerte, aus denen sich
Signale später nachrechnen und Schattenläufe gegen den Live-Betrieb halten lassen.
"""

from datetime import date, datetime, timezone

# Das Paket ``db`` ist zugleich die Test-Naht: Fundament-Namen wie
# ``_database``/``_today``/``_utc_timestamp`` werden in Tests auf dem Paket ersetzt
# und deshalb hier bewusst über ``db.`` nachgeschlagen statt importiert.
from stockbot.core import db


# ── Rohdatenarchiv-Metadaten + Shadow-Snapshots (W3.5) ───────────────────────

def record_raw_data_archive_entry(entry) -> int:
    """Persistiert die Metadaten einer archivierten Rohdaten-Partition (``RawDataArchiveEntry``)
    über den DB-Seam (nach dem Postgres-Cutover entblockt, W3.1). Idempotent je
    (symbol, trading_date, timeframe): ein erneuter Abruf aktualisiert Provider/Abrufzeit/
    Zeilenzahl/Pfad, statt zu duplizieren. Gibt die id zurück."""
    trading_date = entry.trading_date
    trading_date = trading_date.isoformat() if isinstance(trading_date, date) else str(trading_date)
    fetched_at = entry.fetched_at
    fetched_at = fetched_at.isoformat() if isinstance(fetched_at, datetime) else str(fetched_at)
    symbol = entry.symbol.upper()
    with db._database().transaction() as transaction:
        existing = transaction.one(
            "SELECT id FROM raw_data_archive WHERE symbol = :s AND trading_date = :d "
            "AND timeframe = :tf", {"s": symbol, "d": trading_date, "tf": entry.timeframe})
        if existing:
            transaction.execute(
                "UPDATE raw_data_archive SET provider = :p, fetched_at = :f, row_count = :rc, "
                "file_path = :fp WHERE id = :id",
                {"p": entry.provider, "f": fetched_at, "rc": int(entry.row_count),
                 "fp": entry.file_path, "id": existing["id"]})
            return int(existing["id"])
        return int(transaction.insert_id(
            """INSERT INTO raw_data_archive (symbol, trading_date, timeframe, provider,
                    fetched_at, row_count, file_path, created_at)
               VALUES (:symbol, :trading_date, :timeframe, :provider, :fetched_at, :row_count,
                    :file_path, :created_at)""",
            {"symbol": symbol, "trading_date": trading_date, "timeframe": entry.timeframe,
             "provider": entry.provider, "fetched_at": fetched_at,
             "row_count": int(entry.row_count), "file_path": entry.file_path,
             "created_at": db._utc_timestamp()}))


def list_raw_data_archive_entries(symbol: str | None = None) -> list[dict]:
    """Archiv-Metadaten (jüngster Handelstag zuerst), optional auf ein Symbol gefiltert."""
    with db._database().transaction() as transaction:
        if symbol:
            rows = transaction.all(
                "SELECT * FROM raw_data_archive WHERE symbol = :s "
                "ORDER BY trading_date DESC, timeframe", {"s": symbol.upper()})
        else:
            rows = transaction.all(
                "SELECT * FROM raw_data_archive ORDER BY trading_date DESC, symbol, timeframe", {})
    return [dict(row) for row in rows]


def record_shadow_snapshot(snapshot, ticker: str = "") -> int:
    """Persistiert eine Shadow-``PerformanceSnapshot`` (Modus muss SHADOW sein, RES-002-Isolation)."""
    from stockbot.core.domain import Mode
    if snapshot.mode != Mode.SHADOW:
        raise ValueError("record_shadow_snapshot erwartet mode=SHADOW")
    with db._database().transaction() as transaction:
        return int(transaction.insert_id(
            """INSERT INTO shadow_snapshots (strategy_version_id, ticker, captured_at, net_pnl,
                    open_risk, created_at)
               VALUES (:svid, :ticker, :captured_at, :net_pnl, :open_risk, :created_at)""",
            {"svid": int(snapshot.strategy_version_id), "ticker": ticker or "",
             "captured_at": snapshot.captured_at,
             "net_pnl": None if snapshot.net_pnl is None else float(snapshot.net_pnl),
             "open_risk": None if snapshot.open_risk is None else float(snapshot.open_risk),
             "created_at": db._utc_timestamp()}))


def get_shadow_snapshots(limit: int = 500) -> list:
    """Persistierte Shadow-``PerformanceSnapshot``s (jüngste zuerst) für den Shadow-Report."""
    from stockbot.core.domain import Mode, PerformanceSnapshot
    with db._database().transaction() as transaction:
        rows = transaction.all(
            "SELECT * FROM shadow_snapshots ORDER BY captured_at DESC, id DESC LIMIT :lim",
            {"lim": limit})
    return [PerformanceSnapshot(
        id=int(row["id"]), strategy_version_id=int(row["strategy_version_id"]),
        mode=Mode.SHADOW, captured_at=row["captured_at"],
        net_pnl=row["net_pnl"], open_risk=row["open_risk"]) for row in rows]


# ── Polymarket-Datenschicht (PM-0, read-only Ereignissensor) ─────────────────
#
# Reine Persistenz — Bewertung (usable/reject_code/mid) kommt von außen
# (``research/polymarket_quality.py::evaluate_snapshot``), dieses Modul rechnet nichts nach.

def _polymarket_timestamp(moment: datetime | str) -> str:
    """Zwingt einen Polymarket-Zeitwert auf den DB-Zeitvertrag: naiver UTC-String.
    Nimmt ``datetime`` (aware oder naiv) oder einen bereits ISO-formatierten String entgegen
    — Aufrufer müssen sich nicht um Backend-Unterschiede kümmern (analog
    ``burn_in._as_utc_text``/``_audit_timestamp``: PostgreSQL liefert für aware Werte sonst
    ``timestamptz`` und der Vergleich mit dem naiven TEXT-Schema scheitert hart)."""
    if isinstance(moment, datetime):
        if moment.tzinfo is not None:
            moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
        return db._utc_timestamp(moment)
    value = str(moment)
    if not value:
        return value
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return db._utc_timestamp(parsed)


def upsert_polymarket_market(info) -> int:
    """Legt einen Polymarket-Markt an oder aktualisiert seine Stammdaten (idempotent je
    ``condition_id``). ``first_seen_at`` bleibt beim ersten Insert stehen, ``last_seen_at``
    wird bei jedem Aufruf aktualisiert — Frage/Regeln können sich bei Polymarket ändern
    (das Rohdatenarchiv macht das nachweisbar), Stammdaten sonst kaum."""
    now = db._utc_timestamp()
    with db._database().transaction() as transaction:
        existing = transaction.one(
            "SELECT id FROM polymarket_markets WHERE condition_id = :cid",
            {"cid": info.condition_id})
        if existing:
            transaction.execute(
                """UPDATE polymarket_markets SET token_id = :tid, slug = :slug,
                        question = :q, resolution_source = :rs, resolution_rules = :rr,
                        end_date = :ed, category = :cat, last_seen_at = :now
                   WHERE id = :id""",
                {"tid": info.token_id, "slug": info.slug, "q": info.question,
                 "rs": info.resolution_source, "rr": info.resolution_rules,
                 "ed": info.end_date, "cat": info.category, "now": now,
                 "id": existing["id"]})
            return int(existing["id"])
        return int(transaction.insert_id(
            """INSERT INTO polymarket_markets (condition_id, token_id, slug, question,
                    resolution_source, resolution_rules, end_date, category,
                    first_seen_at, last_seen_at)
               VALUES (:cid, :tid, :slug, :q, :rs, :rr, :ed, :cat, :now, :now)""",
            {"cid": info.condition_id, "tid": info.token_id, "slug": info.slug,
             "q": info.question, "rs": info.resolution_source, "rr": info.resolution_rules,
             "ed": info.end_date, "cat": info.category, "now": now}))


def record_polymarket_snapshot(
    snapshot, *, mid: float | None, spread_bps: float | None, usable: bool,
    reject_code: str, raw_file_path: str = "",
) -> int:
    """Persistiert einen bewerteten Polymarket-Snapshot. ``mid``/``spread_bps``/``usable``/
    ``reject_code`` kommen bewusst vom Aufrufer (Ergebnis von
    ``polymarket_quality.evaluate_snapshot``) — dieses Modul ist reine Persistenz."""
    fetched_at = _polymarket_timestamp(snapshot.fetched_at)
    last_trade_at = (_polymarket_timestamp(snapshot.last_trade_at)
                     if snapshot.last_trade_at is not None else None)
    with db._database().transaction() as transaction:
        return int(transaction.insert_id(
            """INSERT INTO polymarket_snapshots (condition_id, fetched_at, bid, ask, mid,
                    spread_bps, depth_bid_usd, depth_ask_usd, volume_24h_usd,
                    liquidity_usd, trade_count_24h, last_trade_at, usable, reject_code,
                    raw_file_path, created_at)
               VALUES (:cid, :fa, :bid, :ask, :mid, :spread, :dbid, :dask, :vol24, :liq,
                    :tc, :lta, :usable, :code, :path, :created_at)""",
            {"cid": snapshot.condition_id, "fa": fetched_at,
             "bid": snapshot.bid, "ask": snapshot.ask, "mid": mid, "spread": spread_bps,
             "dbid": snapshot.depth_bid_usd, "dask": snapshot.depth_ask_usd,
             "vol24": snapshot.volume_24h_usd, "liq": snapshot.liquidity_usd,
             "tc": snapshot.trade_count_24h, "lta": last_trade_at,
             "usable": 1 if usable else 0, "code": reject_code or "",
             "path": raw_file_path or "", "created_at": db._utc_timestamp()}))


def polymarket_snapshot_history(condition_id: str, *, since: datetime) -> list[dict]:
    """Snapshot-Historie eines Markts seit ``since`` (älteste zuerst) — Eingabe für
    ``polymarket_quality.evaluate_snapshot``'s Δp-Berechnung. Zeitvertrag beim Lesen:
    ``fetched_at`` kommt aware (UTC) zurück, unabhängig davon, ob das Backend es naiv
    (SQLite) oder tz-aware (PostgreSQL-Treiber) liefert."""
    since_text = _polymarket_timestamp(since)
    with db._database().transaction() as transaction:
        rows = transaction.all(
            """SELECT fetched_at, mid FROM polymarket_snapshots
               WHERE condition_id = :cid AND fetched_at >= :since
               ORDER BY fetched_at ASC""",
            {"cid": condition_id, "since": since_text})
    history = []
    for row in rows:
        fetched_at = row["fetched_at"]
        if isinstance(fetched_at, str):
            fetched_at = datetime.fromisoformat(fetched_at)
        if fetched_at.tzinfo is not None:
            fetched_at = fetched_at.astimezone(timezone.utc).replace(tzinfo=None)
        history.append({"fetched_at": fetched_at.replace(tzinfo=timezone.utc),
                        "mid": row["mid"]})
    return history


def list_polymarket_markets() -> list[dict]:
    """Alle bekannten Polymarket-Märkte (Stammdaten), zuletzt gesehen zuerst."""
    with db._database().transaction() as transaction:
        rows = transaction.all("SELECT * FROM polymarket_markets ORDER BY last_seen_at DESC")
    return [dict(row) for row in rows]


def list_polymarket_snapshots(condition_id: str, *, limit: int = 500) -> list[dict]:
    """Snapshot-Historie eines Markts (jüngste zuerst) für Reports/Debug."""
    with db._database().transaction() as transaction:
        rows = transaction.all(
            """SELECT * FROM polymarket_snapshots WHERE condition_id = :cid
               ORDER BY fetched_at DESC, id DESC LIMIT :lim""",
            {"cid": condition_id, "lim": limit})
    return [dict(row) for row in rows]
