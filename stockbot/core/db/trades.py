"""Trade-Zustandsmaschine — der Schreibpfad.

Ein Trade läuft ``pending`` → ``active`` → geschlossen, mit den Broker-Zwischenständen
``broker_pending``/``broker_closing`` und den Abbrüchen ``rejected``/``expired``/
``broker_failed``.

Jeder Übergang läuft als statusbewachtes ``UPDATE`` (Compare-and-set): Die Bedingung
nennt den erwarteten Ausgangsstatus, und ``rowcount`` sagt, ob man ihn gewonnen hat.
Damit gewinnt bei gleichzeitigem Zugriff genau ein Aufrufer, ohne Row Locks. Der
Statuswechsel und sein ``trade_events``-Eintrag (`_log_event`) teilen sich immer dieselbe
Transaktion — entweder beides oder nichts.

Lesende Abfragen liegen in ``trade_queries.py``.
"""

import json
from stockbot.core import db_backend

# Das Paket ``db`` ist zugleich die Test-Naht: Fundament-Namen wie
# ``_database``/``_today``/``_utc_timestamp`` werden in Tests auf dem Paket ersetzt
# und deshalb hier bewusst über ``db.`` nachgeschlagen statt importiert.
from stockbot.core import db


def _log_event(transaction: db_backend.DbTransaction, *, trade_id: int, user_id: int, ticker: str,
               trade_date: str, from_status: str | None, to_status: str,
               broker_status: str | None = None, note: str | None = None) -> None:
    """Schreibt einen Status-Übergang in `trade_events` — in DERSELBEN Transaktion wie der
    auslösende UPDATE/INSERT. Reine Wiederholungen (gleicher Status) werden übersprungen, damit
    der periodische Monitor (broker_pending→broker_pending) den Log nicht zumüllt."""
    if from_status == to_status:
        return
    transaction.execute(
        """INSERT INTO trade_events (trade_id, user_id, ticker, trade_date,
                                     from_status, to_status, broker_status, ts, note)
           VALUES (:trade_id, :user_id, :ticker, :trade_date, :from_status, :to_status,
                   :broker_status, :ts, :note)""",
        {"trade_id": trade_id, "user_id": user_id, "ticker": ticker,
         "trade_date": trade_date, "from_status": from_status, "to_status": to_status,
         # Zeitvertrag: Der Schema-Default darf auf PostgreSQL nie greifen (tz-aware String).
         "broker_status": broker_status, "ts": db._utc_timestamp(), "note": note},
    )


def set_trade_leverage(user_id: int, ticker: str, leverage: float):
    """Ändert den Hebel eines noch ausstehenden Trades (im gespeicherten signal_json) —
    serverseitig hart auf `MAX_LEVERAGE` begrenzt (TSAFE-002, Default 1×)."""
    leverage = max(1.0, min(db.MAX_LEVERAGE, float(leverage)))
    with db._database().transaction() as transaction:
        row = transaction.one(
            """SELECT signal_json FROM trades
               WHERE user_id = :user_id AND trade_date = :trade_date AND ticker = :ticker""",
            {"user_id": user_id, "trade_date": db._today(), "ticker": ticker},
        )
        if not row:
            return
        sig = json.loads(row["signal_json"])
        sig["leverage"] = leverage
        transaction.execute(
            """UPDATE trades SET signal_json = :signal_json
               WHERE user_id = :user_id AND trade_date = :trade_date AND ticker = :ticker""",
            {"signal_json": json.dumps(sig, default=str), "user_id": user_id,
             "trade_date": db._today(), "ticker": ticker},
        )


def merge_active_trade_signal(user_id: int, ticker: str, extra: dict) -> None:
    """Ergänzt das gespeicherte Signal eines aktiven Trades um zusätzliche Felder (z. B. den
    gewählten Optionskontrakt: option_symbol/entry_premium/delta/omega/contracts). Idempotent."""
    if not extra:
        return
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT id, signal_json FROM trades WHERE user_id = :user_id AND ticker = :ticker "
            "AND status IN ('active', 'broker_pending') "
            "ORDER BY trade_date DESC, id DESC LIMIT 1",
            {"user_id": user_id, "ticker": ticker},
        )
        if not row:
            return
        sig = json.loads(row["signal_json"])
        sig.update(extra)
        transaction.execute(
            "UPDATE trades SET signal_json = :signal_json WHERE id = :id",
            {"signal_json": json.dumps(sig, default=str), "id": row["id"]},
        )


def reset_user_trades(user_id: int) -> int:
    """Leert den sichtbaren Trading-Verlauf eines Nutzers.

    Trades jedes Status und ihre Intraday-Ticks werden in derselben Transaktion mit Grund
    ``user_reset`` archiviert und erst danach aus den Live-Tabellen entfernt. Das Archiv ist
    reine Audit-/Forschungsaufbewahrung und wird von Dashboard/Reports derzeit nicht gelesen.
    In-App-Mitteilungen werden weiterhin endgültig gelöscht: Sie sind UI-Zustellungen und
    keine Performance-Daten. Profil, Einstellungen und Broker-Zugangsdaten bleiben erhalten.
    Gibt die Zahl der aus den Live-Tabellen entfernten Zeilen zurück.
    """
    archived_at = db._utc_timestamp()
    with db._database().transaction() as transaction:
        transaction.execute(
            """INSERT INTO trades_archive
               (id, user_id, trade_date, ticker, direction, signal_json, message_id, status,
                entry, exit, pnl_eur, pnl_pct, broker_order_id, broker_status,
                broker_filled_qty, broker_filled_avg_price, broker_updated_at, high_water,
                created_at, archived_at, archive_reason)
               SELECT id, user_id, trade_date, ticker, direction, signal_json, message_id, status,
                      entry, exit, pnl_eur, pnl_pct, broker_order_id, broker_status,
                      broker_filled_qty, broker_filled_avg_price, broker_updated_at, high_water,
                      created_at, :archived_at, 'user_reset'
                 FROM trades WHERE user_id = :user_id""",
            {"user_id": user_id, "archived_at": archived_at},
        )
        transaction.execute(
            """INSERT INTO trade_ticks_archive
               (id, user_id, trade_date, ticker, ts, price, strength, archived_at, archive_reason)
               SELECT id, user_id, trade_date, ticker, ts, price, strength,
                      :archived_at, 'user_reset'
                 FROM trade_ticks WHERE user_id = :user_id""",
            {"user_id": user_id, "archived_at": archived_at},
        )
        total = 0
        for table in ("trades", "trade_ticks", "notifications"):
            total += transaction.execute(
                f"DELETE FROM {table} WHERE user_id = :user_id", {"user_id": user_id}
            )
    db.log.info(
        f"Reset: user_id={user_id} — Trades/Ticks archiviert; "
        f"{total} Live-/UI-Zeile(n) entfernt."
    )
    return total


def add_pending(user_id: int, signal: dict, message_id: int) -> bool:
    """Fügt einen vorgemerkten Trade hinzu. Gibt True zurück, wenn neu angelegt;
    False, wenn für diese Aktie heute bereits ein Datensatz existiert (Duplikat-Schutz —
    ein bereits aktiver/abgeschlossener Trade wird NICHT auf 'pending' zurückgesetzt)."""
    signal = db._with_strategy_version(signal)   # Gate P5: jedes persistierte Signal referenziert seine Version
    ticker, trade_date = signal["ticker"], db._today()
    with db._database().transaction() as transaction:
        trade_id = transaction.insert_id(
            """INSERT INTO trades (user_id, trade_date, ticker, direction, signal_json,
                                    message_id, status, created_at)
               VALUES (:user_id, :trade_date, :ticker, :direction, :signal_json, :message_id,
                       'pending', :created_at)
               ON CONFLICT (user_id, trade_date, ticker) DO NOTHING""",
            {"user_id": user_id, "trade_date": trade_date, "ticker": ticker,
             "direction": signal["direction"], "signal_json": json.dumps(signal, default=str),
             "message_id": message_id, "created_at": db._utc_timestamp()},
        )
        created = trade_id > 0
        if created:
            db._log_event(transaction, trade_id=trade_id, user_id=user_id, ticker=ticker,
                       trade_date=trade_date, from_status=None, to_status="pending")
    if created:
        db.log.info(f"Trade vorgemerkt: user_id={user_id} {ticker}")
    else:
        db.log.info(f"Trade übersprungen (heute schon vorhanden): user_id={user_id} {ticker}")
    return created


def activate_trade(user_id: int, ticker: str, status: str = "active") -> dict | None:
    """Aktiviert einen pendenten Trade nach JA-Klick, holt den aktuellen Kurs.

    Standard ist `active` (Demo-Modus). Bei echter Broker-Ausführung kann der Aufrufer
    `status='broker_pending'` setzen; dann wird der Trade erst nach Fill zu `active`.
    """
    if status not in ("active", "broker_pending"):
        raise ValueError(f"Ungültiger Aktivierungsstatus: {status}")
    trade_date = db._today()
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT * FROM trades WHERE user_id = :user_id AND trade_date = :trade_date "
            "AND ticker = :ticker",
            {"user_id": user_id, "trade_date": trade_date, "ticker": ticker},
        )
        if not row or row["status"] != "pending":
            return None
        signal = json.loads(row["signal_json"])
    try:
        entry_price = float(db.yf.Ticker(ticker).fast_info.last_price)
    except Exception:
        entry_price = float(signal["price"])
    with db._database().transaction() as transaction:
        changed = transaction.execute(
            "UPDATE trades SET status = :status, entry = :entry WHERE id = :trade_id "
            "AND status = 'pending'",
            {"status": status, "entry": entry_price, "trade_id": row["id"]},
        )
        if changed != 1:
            return None
        db._log_event(transaction, trade_id=row["id"], user_id=user_id, ticker=ticker,
                   trade_date=row["trade_date"], from_status="pending", to_status=status)
    db.log.info(f"Trade aktiviert: user_id={user_id} {ticker} @ ${entry_price:.2f} ({status})")
    return db.get_trade(user_id, ticker)


def set_active_entry(user_id: int, ticker: str, entry: float) -> bool:
    """Setzt den Einstiegskurs eines aktiven Trades neu (z. B. Reparatur eines fehlerhaft
    übernommenen Options-Trades, dessen entry die Prämie statt des Underlying-Kurses war)."""
    with db._database().transaction() as transaction:
        changed = transaction.execute(
            "UPDATE trades SET entry = :entry WHERE user_id = :user_id AND ticker = :ticker "
            "AND status = 'active'",
            {"entry": float(entry), "user_id": user_id, "ticker": ticker},
        )
        return changed > 0


def heal_absurd_closed_pnl(user_id: int) -> list[dict]:
    """Korrigiert abgeschlossene AKTIEN-Trades mit unplausiblem Einstieg (Glitch-Fill).

    Signatur: geschlossener Trade, dessen `entry` außerhalb 0,5–2,0× des Signalkurses liegt
    (z. B. KHC @ 0,26 statt Signalkurs 23,95 → +9.182 % / +53.810 € Fake-P&L). Setzt
    `entry` = Signalkurs und rechnet pnl_pct/pnl_eur konsistent neu — die Geld-Skala des
    Trades bleibt erhalten (K = pnl_eur/pnl_pct, unabhängig von der aktuellen Trade-Größe).

    Options-/übernommene Trades werden übersprungen (dort ist entry≠Signalkurs legitim).
    Idempotent: nach der Korrektur liegt entry im Band → wird nicht erneut angefasst.
    Gibt die Liste der Korrekturen zurück (für Logging). Gegenstück zum Fill-Guard in
    `mark_broker_filled` (verhindert neue Fälle)."""
    fixed: list[dict] = []
    # Operationally single-run; the plausibility predicate makes repeats idempotent.
    with db._database().transaction() as transaction:
        rows = transaction.all(
            "SELECT id, ticker, direction, signal_json, entry, exit, pnl_pct, pnl_eur "
            "FROM trades WHERE user_id = :user_id AND status = 'closed'",
            {"user_id": user_id},
        )
        for r in rows:
            try:
                sig = json.loads(r["signal_json"]) or {}
            except Exception:
                continue
            if sig.get("option_symbol") or sig.get("adopted"):
                continue                              # dort ist entry (Underlying) ≠ Signal (Prämie) ok
            sp, e, ex = sig.get("price"), r["entry"], r["exit"]
            if not sp or not e or float(sp) <= 0 or float(e) <= 0 or ex is None:
                continue
            if 0.5 <= float(e) / float(sp) <= 2.0:
                continue                              # Einstieg plausibel → nichts zu tun
            new_entry = float(sp)
            if r["direction"] == "short":
                new_pct = (new_entry - float(ex)) / new_entry * 100
            else:
                new_pct = (float(ex) - new_entry) / new_entry * 100
            # Geld-Skala (trade_size×Hebel/100) aus dem Altwert ableiten → konsistente Neuberechnung.
            k = (r["pnl_eur"] / r["pnl_pct"]) if r["pnl_pct"] else 0.0
            new_eur = round(k * new_pct, 2)
            transaction.execute(
                "UPDATE trades SET entry = :entry, pnl_pct = :pnl_pct, pnl_eur = :pnl_eur "
                "WHERE id = :trade_id",
                {"entry": round(new_entry, 6), "pnl_pct": round(new_pct, 4),
                 "pnl_eur": new_eur, "trade_id": r["id"]},
            )
            fixed.append({"ticker": r["ticker"], "old_entry": float(e), "new_entry": new_entry,
                          "old_eur": r["pnl_eur"], "new_eur": new_eur})
    if fixed:
        db.log.warning("[%s] %d absurde(r) geschlossene(r) Trade(s) korrigiert: %s", user_id, len(fixed),
                    ", ".join(f"{x['ticker']} {x['old_eur']:+.0f}€→{x['new_eur']:+.2f}€" for x in fixed))
    return fixed


def mark_broker_pending(user_id: int, ticker: str, *, order_id: str | None, broker_status: str | None) -> bool:
    """Speichert, dass die Broker-Order angenommen, aber noch nicht gefüllt ist."""
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT id, status, trade_date FROM trades WHERE user_id = :user_id AND ticker = :ticker "
            "AND status IN ('broker_pending', 'active') ORDER BY trade_date DESC, id DESC LIMIT 1",
            {"user_id": user_id, "ticker": ticker},
        )
        if not row:
            return False
        changed = transaction.execute(
            """UPDATE trades
               SET status = 'broker_pending', broker_order_id = :order_id,
                   broker_status = :broker_status,
                   broker_updated_at = :updated_at
               WHERE id = :trade_id AND status = :expected_status""",
            {"order_id": order_id, "broker_status": broker_status, "updated_at": db._utc_timestamp(),
             "trade_id": row["id"], "expected_status": row["status"]},
        )
        if changed != 1:
            return False
        db._log_event(transaction, trade_id=row["id"], user_id=user_id, ticker=ticker,
                   trade_date=row["trade_date"], from_status=row["status"],
                   to_status="broker_pending", broker_status=broker_status)
        return True


def mark_broker_filled(user_id: int, ticker: str, *, broker_status: str = "filled",
                       filled_qty: float | None = None, filled_avg_price: float | None = None) -> bool:
    """Macht aus einer broker_pending-Order erst nach tatsächlichem Fill einen aktiven Trade."""
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT id, entry, trade_date FROM trades WHERE user_id = :user_id AND ticker = :ticker AND status = 'broker_pending' "
            "ORDER BY trade_date DESC, id DESC LIMIT 1",
            {"user_id": user_id, "ticker": ticker},
        )
        if not row:
            return False
        # Fill-Preis nur dann als Einstieg übernehmen, wenn er plausibel ist. Ein absurder
        # Broker-Fill (z. B. KHC @ 0,26 statt Signalkurs 23,95) überschrieb sonst den korrekten
        # Einstieg und erzeugte gigantische Fake-P&L (+53.810 €). Außerhalb des 0,5–2,0-Bands
        # um den erwarteten Einstieg (Signalkurs) behalten wir diesen und protokollieren den Fill.
        expected = float(row["entry"] or 0)
        fp = float(filled_avg_price or 0)
        if fp > 0 and (expected <= 0 or 0.5 <= fp / expected <= 2.0):
            entry = fp
        else:
            entry = expected or fp or 0.0
            if fp > 0 and expected > 0:
                db.log.warning(
                    f"[{user_id}] {ticker}: unplausibler Fill-Preis {fp:g} (erwartet ~{expected:g}) "
                    f"— Signalkurs als Einstieg behalten, um Fake-P&L zu vermeiden."
                )
        changed = transaction.execute(
            """UPDATE trades
               SET status = 'active', entry = :entry, broker_status = :broker_status,
                   broker_filled_qty = :filled_qty,
                   broker_filled_avg_price = :filled_avg_price, broker_updated_at = :updated_at
               WHERE id = :trade_id AND status = 'broker_pending'""",
            {"entry": entry, "broker_status": broker_status, "filled_qty": filled_qty,
             "filled_avg_price": filled_avg_price, "updated_at": db._utc_timestamp(),
             "trade_id": row["id"]},
        )
        if changed != 1:
            return False
        db._log_event(transaction, trade_id=row["id"], user_id=user_id, ticker=ticker,
                   trade_date=row["trade_date"], from_status="broker_pending",
                   to_status="active", broker_status=broker_status)
        return True


def mark_broker_failed(user_id: int, ticker: str, *, broker_status: str | None) -> bool:
    """Markiert eine nicht ausgeführte Broker-Order; sie darf nicht als aktiver Trade erscheinen."""
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT id, status, trade_date FROM trades WHERE user_id = :user_id AND ticker = :ticker "
            "AND status IN ('broker_pending', 'active') ORDER BY trade_date DESC, id DESC LIMIT 1",
            {"user_id": user_id, "ticker": ticker},
        )
        if not row:
            return False
        changed = transaction.execute(
            """UPDATE trades SET status = 'broker_failed', broker_status = :broker_status,
                                 broker_updated_at = :updated_at
               WHERE id = :trade_id AND status = :expected_status""",
            {"broker_status": broker_status, "updated_at": db._utc_timestamp(),
             "trade_id": row["id"], "expected_status": row["status"]},
        )
        if changed != 1:
            return False
        db._log_event(transaction, trade_id=row["id"], user_id=user_id, ticker=ticker,
                   trade_date=row["trade_date"], from_status=row["status"],
                   to_status="broker_failed", broker_status=broker_status)
        return True


def mark_broker_closing(user_id: int, ticker: str, *, order_id: str | None, broker_status: str | None) -> bool:
    """Speichert, dass eine Broker-Schließung angestoßen wurde, die Position aber noch offen ist."""
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT id, status, trade_date FROM trades WHERE user_id = :user_id AND ticker = :ticker "
            "AND status IN ('active', 'broker_closing') ORDER BY trade_date DESC, id DESC LIMIT 1",
            {"user_id": user_id, "ticker": ticker},
        )
        if not row:
            return False
        changed = transaction.execute(
            """UPDATE trades
               SET status = 'broker_closing', broker_order_id = :order_id,
                   broker_status = :broker_status, broker_updated_at = :updated_at
               WHERE id = :trade_id AND status = :expected_status""",
            {"order_id": order_id, "broker_status": broker_status, "updated_at": db._utc_timestamp(),
             "trade_id": row["id"], "expected_status": row["status"]},
        )
        if changed != 1:
            return False
        db._log_event(transaction, trade_id=row["id"], user_id=user_id, ticker=ticker,
                   trade_date=row["trade_date"], from_status=row["status"],
                   to_status="broker_closing", broker_status=broker_status)
        return True


def mark_broker_close_failed(user_id: int, ticker: str, *, broker_status: str | None) -> bool:
    """Bringt einen fehlgeschlagenen Sell-Versuch wieder in den aktiven Zustand zurück."""
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT id, status, trade_date FROM trades WHERE user_id = :user_id AND ticker = :ticker "
            "AND status = 'broker_closing' ORDER BY trade_date DESC, id DESC LIMIT 1",
            {"user_id": user_id, "ticker": ticker},
        )
        if not row:
            return False
        changed = transaction.execute(
            """UPDATE trades SET status = 'active', broker_status = :broker_status,
                                 broker_updated_at = :updated_at
               WHERE id = :trade_id AND status = 'broker_closing'""",
            {"broker_status": broker_status, "updated_at": db._utc_timestamp(),
             "trade_id": row["id"]},
        )
        if changed != 1:
            return False
        db._log_event(transaction, trade_id=row["id"], user_id=user_id, ticker=ticker,
                   trade_date=row["trade_date"], from_status="broker_closing",
                   to_status="active", broker_status=broker_status, note="close_failed")
        return True


def adopt_active_trade(user_id: int, ticker: str, *, entry: float, signal: dict,
                       filled_qty: float | None = None,
                       broker_order_id: str | None = None) -> bool:
    """Übernimmt eine **verwaiste Broker-Position** als aktiven Trade (Selbstheilung).

    Gerät eine Order beim Broker durch, ohne dass der Bot sie als aktiven Trade führt
    (z. B. mehrdeutiger Sende-Fehler nach dem eigentlichen Fill), legt diese Funktion den
    Trade nachträglich an, damit der Bot ihn wieder überwacht (SL/TP, Tagesende).

    - Existiert heute schon ein Datensatz dieser Aktie und ist er **nicht-terminal**
      (`active`/`broker_pending`/`broker_closing`) → nichts tun (schon getrackt), `False`.
    - Existiert ein terminaler heutiger Datensatz (z. B. `broker_failed`) → reaktivieren.
    - Sonst → neuen aktiven Trade einfügen.
    Gibt `True` zurück, wenn übernommen wurde."""
    direction = signal.get("direction", "long")
    payload = json.dumps(signal, default=str)
    trade_date = db._today()
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT id, status FROM trades WHERE user_id = :user_id AND trade_date = :trade_date "
            "AND ticker = :ticker",
            {"user_id": user_id, "trade_date": trade_date, "ticker": ticker},
        )
        if row and row["status"] in ("active", "broker_pending", "broker_closing"):
            return False
        if row:
            changed = transaction.execute(
                """UPDATE trades
                   SET status = 'active', direction = :direction, entry = :entry,
                       signal_json = :signal_json,
                       exit = NULL, pnl_eur = NULL, pnl_pct = NULL,
                       broker_order_id = :broker_order_id, broker_filled_qty = :filled_qty,
                       broker_status = 'adopted_orphan', broker_updated_at = :updated_at
                   WHERE id = :trade_id AND status = :expected_status""",
                {"direction": direction, "entry": entry, "signal_json": payload,
                 "broker_order_id": broker_order_id, "filled_qty": filled_qty,
                 "updated_at": db._utc_timestamp(), "trade_id": row["id"],
                 "expected_status": row["status"]},
            )
            if changed != 1:
                return False
            trade_id, from_status = row["id"], row["status"]
        else:
            trade_id = transaction.insert_id(
                """INSERT INTO trades (user_id, trade_date, ticker, direction, signal_json,
                                       message_id, status, entry, broker_order_id,
                                       broker_filled_qty, broker_status, broker_updated_at,
                                       created_at)
                   VALUES (:user_id, :trade_date, :ticker, :direction, :signal_json, 0,
                           'active', :entry, :broker_order_id, :filled_qty, 'adopted_orphan',
                           :broker_updated_at, :created_at)
                   ON CONFLICT (user_id, trade_date, ticker) DO NOTHING""",
                {"user_id": user_id, "trade_date": trade_date, "ticker": ticker,
                 "direction": direction, "signal_json": payload, "entry": entry,
                 "broker_order_id": broker_order_id, "filled_qty": filled_qty,
                 "broker_updated_at": db._utc_timestamp(), "created_at": db._utc_timestamp()},
            )
            if not trade_id:
                return False
            from_status = None
        db._log_event(transaction, trade_id=trade_id, user_id=user_id, ticker=ticker,
                   trade_date=trade_date, from_status=from_status, to_status="active",
                   broker_status="adopted_orphan", note="adopted_orphan")
    db.log.warning(f"Verwaiste Broker-Position übernommen: user_id={user_id} {ticker} @ ${entry:.2f}")
    return True


def reject_trade(user_id: int, ticker: str) -> bool:
    """Markiert den heutigen Trade als abgelehnt. Gibt True zurück, falls ein Datensatz aktualisiert wurde."""
    return _terminate_pending(user_id, ticker, "rejected")


def expire_trade(user_id: int, ticker: str) -> bool:
    """Markiert einen noch ausstehenden Trade als abgelaufen (Start-Zeitfenster verstrichen).
    Gibt True zurück, falls ein Datensatz aktualisiert wurde (also noch 'pending' war)."""
    return _terminate_pending(user_id, ticker, "expired")


def expire_stale_pending(cutoff_date: str | None = None) -> int:
    """Läuft ALLE noch ausstehenden (pending) Trades ab, deren Handelstag VOR `cutoff_date` liegt
    (Default: heute). Räumt liegengebliebene Signale auf, die nie angenommen wurden (z. B.
    Auto-Accept außerhalb der Sitzung) — `get_pending_trades` filtert ohnehin auf `trade_date=heute`,
    ältere blieben sonst als Karteileichen liegen (beobachteter Pending-Stau). Setzt Status
    'expired' + Event je Datensatz; gibt die Anzahl bereinigter Trades zurück."""
    cutoff = cutoff_date or db._today()
    with db._database().transaction() as transaction:
        rows = transaction.all(
            "SELECT id, user_id, ticker, trade_date FROM trades "
            "WHERE status = 'pending' AND trade_date < :cutoff",
            {"cutoff": cutoff},
        )
        changed_count = 0
        for r in rows:
            changed = transaction.execute(
                "UPDATE trades SET status = 'expired' WHERE id = :trade_id AND status = 'pending'",
                {"trade_id": r["id"]},
            )
            if changed != 1:
                continue
            changed_count += 1
            db._log_event(transaction, trade_id=r["id"], user_id=r["user_id"], ticker=r["ticker"],
                       trade_date=r["trade_date"], from_status="pending", to_status="expired")
    if changed_count:
        db.log.info(f"Stale-Pending bereinigt: {changed_count} liegengebliebene Signale abgelaufen (< {cutoff}).")
    return changed_count


def _terminate_pending(user_id: int, ticker: str, to_status: str) -> bool:
    """Setzt den heutigen pendenten Trade auf einen Endstatus (rejected/expired) + Event."""
    trade_date = db._today()
    with db._database().transaction() as transaction:
        row = transaction.one(
            "SELECT id FROM trades WHERE user_id = :user_id AND trade_date = :trade_date "
            "AND ticker = :ticker AND status = 'pending'",
            {"user_id": user_id, "trade_date": trade_date, "ticker": ticker},
        )
        if not row:
            return False
        changed = transaction.execute(
            "UPDATE trades SET status = :to_status WHERE id = :trade_id AND status = 'pending'",
            {"to_status": to_status, "trade_id": row["id"]},
        )
        if changed != 1:
            return False
        db._log_event(transaction, trade_id=row["id"], user_id=user_id, ticker=ticker,
                   trade_date=trade_date, from_status="pending", to_status=to_status)
        return True


def close_all(user_id: int, results: list[dict], *, broker_status: str | None = None):
    """Schließt die ausgewerteten Trades des Nutzers (matcht den AKTIVEN Trade je Aktie,
    datumsunabhängig — auch über Nacht gehaltene).

    Optional kann ein `broker_status` mitgeschrieben werden, z. B. für Reconcile-Fälle.
    """
    with db._database().transaction() as transaction:
        for r in results:
            row = transaction.one(
                "SELECT id, status, trade_date FROM trades WHERE user_id = :user_id "
                "AND ticker = :ticker "
                "AND status IN ('active', 'broker_closing') ORDER BY trade_date DESC, id DESC LIMIT 1",
                {"user_id": user_id, "ticker": r["ticker"]},
            )
            if not row:
                continue
            changed = transaction.execute(
                """UPDATE trades SET status = 'closed', exit = :exit, pnl_eur = :pnl_eur,
                                      pnl_pct = :pnl_pct,
                                      broker_status = COALESCE(:broker_status, broker_status),
                                      broker_updated_at = CASE WHEN :broker_status IS NOT NULL
                                          THEN :updated_at ELSE broker_updated_at END
                   WHERE id = :trade_id AND status = :expected_status""",
                {"exit": r["exit"], "pnl_eur": r["pnl_eur"], "pnl_pct": r["pnl_pct"],
                 "broker_status": broker_status, "updated_at": db._utc_timestamp(),
                 "trade_id": row["id"], "expected_status": row["status"]},
            )
            if changed != 1:
                continue
            db._log_event(transaction, trade_id=row["id"], user_id=user_id, ticker=r["ticker"],
                       trade_date=row["trade_date"], from_status=row["status"],
                       to_status="closed", broker_status=broker_status)
    db.log.info(f"user_id={user_id}: {len(results)} Trades geschlossen.")
