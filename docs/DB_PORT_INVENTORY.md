# Inventar: Port der Laufzeit-DB SQLite → PostgreSQL (PLAT-001)

*Stand: 2026-07-13. Scope: Analyse und Umsetzungsplan; kein Produktionscode-Umbau.*

Dieses Dokument ist die vollständige Landkarte der direkten SQL-/Datei-Zugriffe, die für den
Full Cutover der Paper-Laufzeit relevant sind. Erfasst wurden repo-weit insbesondere
`sqlite3`, `DB_FILE`, `_connect`, `conn.execute`, `execute`, `row_factory`, `lastrowid` und
`PRAGMA` (einschließlich Tests und Tools). Normale Aufrufer der öffentlichen Funktionen aus
`stockbot.core.db` enthalten kein eigenes SQL und sind daher keine Port-Fundstellen.

## 1. Legende und Zählweise

Eine **Fundstelle** ist eine Funktion mit einem oder mehreren zusammengehörigen DB-Zugriffen;
mehrere Statements derselben Transaktion stehen in einer Tabellenzeile. Dadurch bleiben die
Transaktionsgrenzen sichtbar. `db.py` enthält 150 `execute`-/`executescript`-Aufrufe in 85
SQL-ausführenden Funktionen plus den zentralen Verbindungszugriff `_connect`.

| Klasse | Bedeutung |
|---|---|
| **A** | Trivial portierbar: SQL ist dialektneutral; nur `?` → SQLAlchemy-Binds und `sqlite3.Row` → Mapping-Result erforderlich. |
| **B** | SQL muss gezielt umformuliert werden (Zeitfunktionen, dynamisches SQL, Dialekt-/Typdetails). |
| **C** | Verhaltens- oder Transaktionsentscheidung nötig (Startup-DDL, atomare Mehrschrittoperation, Race/Locking, `lastrowid`, Fehlerklasse oder Cutover-Tooling). |

Querschnittlich gilt für praktisch jedes parametrisierte Statement in `db.py`: SQLite nutzt
`?`; SQLAlchemy `text()` braucht benannte Binds (oder Core-Ausdrücke). Alle Resultate erwarten
Namenszugriff wie `row["id"]`, teilweise `row.keys()` oder `dict(row)`; bei SQLAlchemy 2.x ist
dafür konsequent `result.mappings()`/`row._mapping` zu verwenden. Diese zwei Punkte werden in
den Einzelzeilen nicht jedes Mal wiederholt.

## 2. Vollinventar `stockbot/core/db.py`

### 2.1 Schema, Startup, Verbindung und OMS

| Kl. | Funktion / Zeile | Tabellen | Art / Statements | PostgreSQL-Befund |
|---|---|---|---|---|
| **C** | `init_db` :182–187 | alle 10 Laufzeittabellen | DDL: `executescript(SCHEMA_SQL)`, danach `_migrate` | `executescript`, `CREATE TABLE IF NOT EXISTS`, SQLite-Typen (`INTEGER`, `REAL`, `BLOB`), `INTEGER PRIMARY KEY AUTOINCREMENT` und `datetime('now')` sind kein PostgreSQL-Startup-Pfad. Auf PostgreSQL muss ausschließlich Alembic das Schema besitzen. |
| **C** | `_migrate` :190–360 | `users`, `strategy_configs`, `trades`, `trade_events`, `sessions`, `trade_intents`, `orders`, `order_events` | 32 Zugriffe: `PRAGMA table_info` (:192/:239/:251/:267), additive `ALTER TABLE` (:194–276), Event-DDL/-Backfill (:281–325), OMS-`executescript` (:334) | Stark SQLite-spezifischer Startup-Migrator: `PRAGMA`, `executescript`, `AUTOINCREMENT`, `datetime('now')`, SQLite-DDL/Typen, breit gefangenes `Exception`, Row-Namenszugriff. Backfill und Token-Hashing sind Datenmigrationen und müssen als versionierte, genau einmal laufende Jobs entschieden werden; nicht bei jedem Postgres-Prozessstart. |
| **C** | `_migrate_leverage_values` :363–395 | `users`, `trades` | `UPDATE` + `SELECT` + Python-JSON-Loop + `UPDATE` | SQL nur Bind-/Row-Port, aber das bei jedem Start laufende Read-modify-write-Healing braucht Single-run/Locking-Entscheidung. Bei mehreren Prozessen sonst doppelte Arbeit bzw. Lost Updates auf `signal_json`. |
| **C** | `_connect` :398–406 | alle | `sqlite3.connect(DB_FILE)`, `row_factory`, yield, `commit`, close | Zentrale Laufzeitkopplung. Kein explizites Rollback im `finally`; SQLite-Context wird pro Funktionsaufruf exklusiv neu geöffnet. PostgreSQL-Seam muss `session_scope`/`engine.begin`, Commit **und** Rollback sowie Mapping-Results festlegen. |
| **A** | `get_order_by_idempotency_key` :415–421 | `orders` | `SELECT *` | Nur Bind/Mapping. |
| **A** | `get_oms_order` :424–427 | `orders` | `SELECT *` | Nur Bind/Mapping. |
| **C** | `create_oms_order` :430–480 | `trade_intents`, `orders`, `order_events` | Vorab-`SELECT`; 2× `INSERT`; `lastrowid`; `UPDATE client_order_id`; Event-`INSERT`; Abschluss-`SELECT`; fängt `sqlite3.IntegrityError` | Muss atomar bleiben. IDs via `INSERT ... RETURNING`, Konfliktpfad via passender SQLAlchemy-/Postgres-`IntegrityError` und Rollback/Savepoint; nach einem Postgres-Constraintfehler ist die Transaktion bis Rollback unbrauchbar. Vorab-SELECT verhindert das Race nicht, Unique Constraints bleiben Endschutz. |
| **C** | `transition_oms_order` :483–511 | `orders`, `order_events` | Compare-and-set-`UPDATE`, `rowcount`, Race-`SELECT`, Event-`INSERT`, Abschluss-`SELECT` | Zeitfunktion ersetzen. CAS + Event müssen in derselben Transaktion bleiben; `rowcount == 1` und Isolationssemantik unter Konkurrenz auf beiden Backends testen. |
| **C** | `record_oms_order_event` :514–540 | `orders`, `order_events` | Statusbewachtes `UPDATE`, `rowcount`, Race-`SELECT`, Event-`INSERT`, Abschluss-`SELECT` | Wie `transition_oms_order`: Zeitfunktion plus atomare Statusprüfung/Eventschreibweise. |
| **A** | `get_oms_order_events` :543–548 | `order_events` | `SELECT * ... ORDER BY id` | Nur Bind/Mapping. |
| **C** | `_log_event` :551–564 | `trade_events` | `INSERT` in vom Aufrufer gelieferter Connection | SQL trivial, aber die API garantiert dieselbe Transaktion wie der Trade-Write. Der neue Seam muss explizit eine Transaction/Connection entgegennehmen; keine eigene Session öffnen. |

`SCHEMA_SQL` (:26–179) selbst definiert zehn Tabellen und vier Indizes. SQLite-Spezifika sind
`BLOB`/`REAL`, implizite 0/1-Bools, TEXT-Zeitstempel, `datetime('now')`, sieben
`INTEGER PRIMARY KEY AUTOINCREMENT`-IDs sowie SQLite-typische Typ-Laxheit. Es gibt **keine**
`INSERT OR REPLACE`, `INSERT OR IGNORE` oder Trigger im aktuellen Repo; Upserts verwenden
bereits `ON CONFLICT`. Die SQLite-Verbindung setzt allerdings auch kein
`PRAGMA foreign_keys=ON`; heutige FK-Deklarationen können daher praktisch unenforced sein,
während PostgreSQL sie strikt erzwingt.

### 2.2 Nutzerprofile und Einstellungen

| Kl. | Funktion / Zeile | Tabellen | Art | PostgreSQL-Befund |
|---|---|---|---|---|
| **C** | `get_or_create_user` :625–635 | `users` | `SELECT`, ggf. `INSERT`, erneut `SELECT` | Read-then-insert-Race; als `INSERT ... ON CONFLICT DO NOTHING` + Read oder `RETURNING` formulieren. |
| **A** | `get_user` :638–642 | `users` | `SELECT *` | Nur Bind/Mapping. |
| **B** | `save_profile` :645–662 | `users` | `UPDATE` | `datetime('now')` ersetzen; Fernet-`bytes` müssen nach `BYTEA` gebunden werden. |
| **B** | `get_decrypted_credentials` :665–673 | `users` | `SELECT` zweier BLOBs | PostgreSQL kann `BYTEA` als `memoryview` liefern; bestehendes `decrypt(bytes(ciphertext))` ist robust, der Mapping-Vertrag muss das absichern. |
| **A** | `list_active_users` :676–682 | `users` | `SELECT *` | Nur Mapping; 0/1-Spalten bleiben laut Zielschema `Integer`, nicht native `Boolean`. |
| **B** | `set_user_active` :685–691 | `users` | `UPDATE` | `datetime('now')` ersetzen. |
| **B** | `set_market_region` :694–700 | `users` | `UPDATE` | `datetime('now')` ersetzen. |
| **C** | `toggle_region` :703–718 | `users` | `SELECT`, Python-Listenmutation, `UPDATE` | Read-modify-write kann unter Postgres Updates verlieren; Lock (`SELECT ... FOR UPDATE`) oder atomarer/serialisierter Service-Entscheid nötig. Zusätzlich Zeitfunktion. |
| **B** | `set_trade_size` :721–729 | `users` | `UPDATE` | `datetime('now')` ersetzen. |
| **B** | `set_top_n` :732–740 | `users` | `UPDATE` | `datetime('now')` ersetzen. |
| **B** | `set_sl_tp_mode` :742–749 | `users` | `UPDATE` | `datetime('now')` ersetzen. |
| **B** | `set_leverage` :751–760 | `users` | `UPDATE` | `datetime('now')` ersetzen; Python-Cap bleibt Domänenlogik. |
| **B** | `set_auto_accept` :762–769 | `users` | `UPDATE` | `datetime('now')` ersetzen. |
| **B** | `set_auto_universe` :771–779 | `users` | `UPDATE` | `datetime('now')` ersetzen. |
| **B** | `set_strategy` :781–788 | `users` | `UPDATE` | `datetime('now')` ersetzen. |
| **B** | `set_llm_rank` :790–797 | `users` | `UPDATE` | `datetime('now')` ersetzen. |
| **B** | `set_eod_close` :799–806 | `users` | `UPDATE` | `datetime('now')` ersetzen. |
| **B** | `set_signal_window` :808–815 | `users` | `UPDATE` | `datetime('now')` ersetzen. |
| **B** | `set_broker_exec` :817–824 | `users` | `UPDATE` | `datetime('now')` ersetzen. |
| **B** | `set_alpaca_credentials` :826–834 | `users` | `UPDATE` | Zeitfunktion + verschlüsselte BLOBs → `BYTEA`; niemals Klartext loggen. |
| **B** | `clear_alpaca_credentials` :837–846 | `users` | `UPDATE` | `datetime('now')` ersetzen. |
| **A** | `has_alpaca_credentials` :849–855 | `users` | `SELECT` | Bind/Mapping; nur Nicht-NULL-Prüfung auf `BYTEA`. |
| **C** | `toggle_strategy` :858–873 | `users` | `SELECT`, Python-Listenmutation, `UPDATE` | Lost-update-Risiko; Lock/Versionsspalte/atomare Entscheidung plus Zeitfunktion. |
| **C** | `add_watchlist_tickers` :876–888 | `users` | `SELECT`, Mengenmutation, `UPDATE` | Lost-update-Risiko; wie oben. |
| **C** | `remove_watchlist_ticker` :891–901 | `users` | `SELECT`, Listenmutation, `UPDATE` | Lost-update-Risiko; wie oben. |
| **C** | `set_trade_leverage` :904–920 | `trades` | `SELECT signal_json`, Python-JSON-Mutation, `UPDATE` | Read-modify-write auf TEXT-JSON; Zeilensperre oder atomare JSONB-Entscheidung. Zielschema ist derzeit weiterhin `Text`, nicht JSONB. |
| **C** | `merge_active_trade_signal` :923–940 | `trades` | neuestes offenes Signal `SELECT`, Python-Merge, `UPDATE` | Lost Updates; `SELECT ... FOR UPDATE`/Versionierung. Die Auswahl des „neuesten“ Datensatzes und Update müssen eine Transaktion bleiben. |
| **C** | `get_or_create_dashboard_token` :945–953 | `users` | `SELECT`, ggf. Token-`UPDATE` | Gleichzeitige Aufrufe können verschiedene Tokens zurückgeben; Lock/atomarer `UPDATE ... RETURNING`-Entscheid. |
| **B** | `rotate_dashboard_token` :956–963 | `users` | `UPDATE` | `datetime('now')` ersetzen. |
| **A** | `get_user_by_token` :966–972 | `users` | `SELECT *` | Nur Bind/Mapping. |

Die Mapper `_user_to_dict` (:581), `_strategy_config_to_dict` (:1110) und `_trade_to_dict`
(:1180) sind keine eigenen SQL-Fundstellen, aber direkte `sqlite3.Row`-Kopplungen. Besonders
`"column" in row.keys()`, `dict(row)` und boolesche 0/1-Konvertierung gehören in Contract-Tests
des neuen Mapping-Seams.

### 2.3 Sessions, Benachrichtigungen und Strategiekonfiguration

| Kl. | Funktion / Zeile | Tabellen | Art | PostgreSQL-Befund |
|---|---|---|---|---|
| **B** | `create_session` :990–999 | `sessions` | `INSERT` | SQLite-Datumsmodifikator `datetime('now', ?)` mit String `+N days`; in Python UTC berechnen oder dialektneutral binden. |
| **B** | `user_id_for_session` :1002–1011 | `sessions` | `SELECT` | `expires_at > datetime('now')` ersetzen; TEXT-Zeitvergleich-Semantik bewusst beibehalten oder Schema auf echte Timestamps migrieren (separater Scope). |
| **A** | `delete_session` :1014–1019 | `sessions` | `DELETE` | Nur Bind. |
| **A** | `delete_user_sessions` :1022–1026 | `sessions` | `DELETE`, `rowcount` | Dialektneutral; `rowcount` contract-testen. |
| **B** | `delete_expired_sessions` :1029–1033 | `sessions` | `DELETE` | `datetime('now')` ersetzen. |
| **C** | `reset_user_trades` :1036–1046 | `trades`, `trade_ticks`, `notifications` | drei dynamische `DELETE`, summiert `rowcount` | Tabellenname ist intern allowlisted, aber Core-Tabellenobjekte statt String-Interpolation. Eine Transaktion beibehalten. Achtung: `trade_events` und OMS-Tabellen werden heute nicht gelöscht; unter strengeren FKs muss die beabsichtigte Retention/Cascade-Regel entschieden werden. |
| **B** | `set_notify_channel` :1051–1059 | `users` | `UPDATE` | `datetime('now')` ersetzen. |
| **B** | `set_asset_pref` :1062–1070 | `users` | `UPDATE` | `datetime('now')` ersetzen. |
| **C** | `add_notification` :1073–1080 | `notifications` | `INSERT`, gibt `lastrowid` zurück | `INSERT ... RETURNING id`; Sequence-Verhalten testen. |
| **A** | `get_notifications` :1083–1092 | `notifications` | `SELECT`, `ORDER BY`, gebundenes `LIMIT` | Bind/Mapping; `LIMIT`-Bind auf beiden Engines testen. |
| **A** | `unread_count` :1095–1100 | `notifications` | `SELECT COUNT(*) AS n` | Nur Bind/Mapping. |
| **A** | `mark_notifications_read` :1103–1105 | `notifications` | `UPDATE` | Nur Bind. |
| **A** | `list_strategy_configs` :1126–1131 | `strategy_configs` | `SELECT ... ORDER BY` | Nur Mapping. |
| **A** | `get_strategy_config` :1134–1140 | `strategy_configs` | `SELECT` | Nur Bind/Mapping. |
| **B** | `upsert_strategy_config` :1143–1159 | `strategy_configs` | `INSERT ... ON CONFLICT DO UPDATE` | Conflict-Syntax und `excluded` sind bereits PostgreSQL-kompatibel; nur Binds und zweimal `datetime('now')` ersetzen. |

### 2.4 Trades, Events und Ticks

| Kl. | Funktion / Zeile | Tabellen | Art | PostgreSQL-Befund |
|---|---|---|---|---|
| **A** | `has_trade_today` :1202–1209 | `trades` | Existenz-`SELECT` | Nur Bind/Mapping. |
| **A** | `has_open_position` :1212–1220 | `trades` | Existenz-`SELECT` | Nur Bind/Mapping. |
| **C** | `add_pending` :1224–1245 | `trades`, `trade_events` | `INSERT ... ON CONFLICT DO NOTHING`, `rowcount`, bei Erfolg Event mit `lastrowid` | Upsert ist kompatibel, aber ID via `RETURNING`; Trade + Event atomar. `rowcount` bei Konflikt und gleichzeitige Inserts auf beiden Engines testen. |
| **C** | `activate_trade` :1247–1278 | `trades`, `trade_events` | `SELECT`, yfinance-Aufruf, `UPDATE`, Event-`INSERT` | Read/check/update ist unter Konkurrenz nicht gesperrt; CAS (`WHERE status=...`) oder `FOR UPDATE`; Event atomar. Der externe yfinance-Aufruf liegt heute innerhalb der offenen DB-Transaktion und muss vor die kurze Schreibtransaktion oder in einen zweiphasigen CAS-Ablauf. |
| **A** | `set_active_entry` :1280–1289 | `trades` | statusbewachtes `UPDATE`, `rowcount` | Nur Bind; `rowcount` testen. |
| **C** | `heal_absurd_closed_pnl` :1291–1340 | `trades` | `SELECT` aller Closed Trades, Python-/JSON-Berechnung, je Treffer `UPDATE` | Potenziell lange Read-/Berechnungsschleife in einer Transaktion. Lesen/Berechnen/kurze CAS-Schreibtransaktion trennen; Idempotenz und parallele Änderung entscheiden. Die Funktion selbst macht keinen Netzwerkaufruf. |
| **C** | `mark_broker_pending` :1342–1363 | `trades`, `trade_events` | aktuelles Trade-`SELECT`, `UPDATE`, Event | Zustandsübergang braucht CAS/Lock; Zeitfunktion ersetzen; Trade + Event atomar. |
| **C** | `mark_broker_filled` :1365–1402 | `trades`, `trade_events` | `SELECT`, berechnetes `UPDATE`, `rowcount`, Event | Wie oben; berechneter Entry und Zustand dürfen nicht auf stale Row beruhen. |
| **C** | `mark_broker_failed` :1404–1422 | `trades`, `trade_events` | `SELECT`, `UPDATE`, Event | CAS/Lock + Zeitfunktion; atomar. |
| **C** | `mark_broker_closing` :1425–1445 | `trades`, `trade_events` | `SELECT`, `UPDATE`, Event | CAS/Lock + Zeitfunktion; atomar. |
| **C** | `mark_broker_close_failed` :1448–1466 | `trades`, `trade_events` | `SELECT`, `UPDATE`, Event | CAS/Lock + Zeitfunktion; atomar. |
| **C** | `adopt_active_trade` :1469–1516 | `trades`, `trade_events` | `SELECT`; terminalen Trade `UPDATE` oder `INSERT`; ggf. `lastrowid`; Event | Race zwischen Auswahl und Insert/Reaktivierung; Unique Constraint, CAS/Upsert und `RETURNING` festlegen. Trade + Event atomar. |
| **C** | `expire_stale_pending` :1530–1549 | `trades`, `trade_events` | Mengen-`SELECT`, je Row `UPDATE` + Event | Parallel laufende Worker können dieselben Rows verarbeiten. Set-basiertes `UPDATE ... RETURNING` oder Sperren; exakt ein Event je erfolgreichem Übergang. |
| **C** | `_terminate_pending` :1552–1564 (Aufrufer `reject_trade` :1519, `expire_trade` :1524) | `trades`, `trade_events` | `SELECT`, `UPDATE`, Event | CAS auf `status='pending'` + `RETURNING`; Event nur für tatsächlich geänderte Row. |
| **A** | `get_active_trades` :1567–1574 | `trades` | `SELECT *` | Nur Bind/Mapping. |
| **A** | `get_pending_trades` :1577–1584 | `trades` | `SELECT *` | Nur Bind/Mapping. |
| **A** | `get_broker_pending_trades` :1587–1594 | `trades` | `SELECT *` | Nur Bind/Mapping. |
| **A** | `get_broker_closing_trades` :1597–1604 | `trades` | `SELECT *` | Nur Bind/Mapping. |
| **A** | `get_trade` :1607–1616 | `trades` | priorisierendes `SELECT`, `ORDER BY (status='active') DESC` | PostgreSQL kann boolesche Ausdrücke sortieren; Ergebnisreihenfolge als Contract-Test sichern. Sonst Bind/Mapping. |
| **A** | `get_trade_by_id` :1619–1623 | `trades` | `SELECT *` | Nur Bind/Mapping. |
| **C** | `close_all` :1626–1651 | `trades`, `trade_events` | je Ergebnis `SELECT`, `UPDATE` mit `CASE`/`COALESCE`, Event | Batch muss atomar bleiben; parallele Statusänderungen via CAS/Lock schützen. `datetime('now')` im `CASE` ersetzen. Log meldet heute `len(results)`, nicht tatsächlich geänderte Rows. |
| **B** | `get_history` :1654–1663 | `trades` | Datums-`SELECT` | SQLite `date('now', ?)` ersetzen; besser Cutoff in Python als ISO-Datum binden. |
| **A** | `get_closed_trades` :1666–1675 | `trades` | `SELECT *` | Nur Bind/Mapping. |
| **A** | `get_all_trades` :1678–1687 | `trades` | `SELECT *` | Nur Bind/Mapping. |
| **B** | `get_all_trades_between` :1690–1703 | `trades` | dynamisch zusammengesetztes `SELECT` mit optionalen Grenzen | Keine untrusted SQL-Fragmente, aber positionaler Parameterlistenbau passt nicht zu SQLAlchemy `text`; Core-Prädikate/benannte Binds verwenden. |
| **A** | `get_trade_events` :1706–1713 | `trade_events` | `SELECT *` | Nur Bind/Mapping. |
| **A** | `get_events_by_trade` :1716–1726 | `trade_events` | `SELECT *`, Python-Gruppierung | Nur Bind/Mapping. |
| **B** | `get_trade_events_between` :1729–1742 | `trade_events` | dynamischer Zeitfenster-`SELECT` | Wie `get_all_trades_between`; TEXT-Zeitstempel und angehängtes `23:59:59` als bestehende Semantik contract-testen. |
| **A** | `add_tick` :1747–1753 | `trade_ticks` | `INSERT` | Nur Bind; `ts` kommt aus Server-Default, dessen Format/Zeitzone gleichwertig sein muss. |
| **A** | `get_today_ticks` :1756–1770 | `trade_ticks` | `SELECT`, Python-Gruppierung | Nur Bind/Mapping. |

## 3. Direkte Zugriffe außerhalb von `db.py`

### 3.1 Produktions-/Toolcode

| Kl. | Datei, Funktion / Zeile | Tabellen | Art | Befund / Behandlung |
|---|---|---|---|---|
| **B** | `stockbot/optimize/lab.py::reality_check` :668–691 | `trades` | direkter `db._connect()` + `SELECT`; SQLite `date('now', ?)`; Row-Namenszugriff | Einziger Laufzeit-/Lab-Pfad außerhalb `db.py`, der den Seam umgeht. In eine öffentliche Read-Funktion verschieben; Python-Cutoff binden. Fehlerbehandlung darf echte Postgres-/Poolfehler weiterhin in `out["note"]` abbilden. |
| **C** | `stockbot/core/db_export.py::_connect_readonly` :45–49 | DB-Datei | `sqlite3.connect(file:...?mode=ro, uri=True)`, `sqlite3.Row` | Bewusst SQLite-spezifisches Quell-/Archivtool; nicht auf Postgres „portieren“. Nach Cutover nur für finalen Freeze/Archiv behalten und klar vom Runtime-Seam trennen. |
| **C** | `stockbot/core/db_export.py::export_snapshot` :52–87 | die sieben `TABLES` | dynamisches `SELECT *`, fängt `sqlite3.OperationalError`, liest `db.DB_FILE` | Cutover-kritisch: derzeit fehlen `trade_intents`, `orders`, `order_events`. Finaler Snapshot/Delta-Sync darf keine Tabelle still auslassen; SQLite-Dateikonsistenz (WAL/aktive Writer) festlegen. |

`tools/export_sqlite_snapshot.py::main` (:16–18) ruft nur `export_snapshot()` auf und besitzt
keinen eigenen DB-Zugriff. Die indirekten Startup-Aufrufer `stockbot/web/dashboard.py:113`,
`stockbot/tgbot/bot.py:2521` und `tools/strategy_report.py:356` rufen `db.init_db()` auf; beim
Postgres-Backend dürfen diese Entry-Points deshalb nur Schema-Readiness prüfen und niemals den
SQLite-Migrator starten. Webapp, Telegram-Bot, Broker- und Service-Module enthalten nach
Repo-Suche keine weiteren direkten `sqlite3`-/`DB_FILE`-/`_connect`-/`execute`-Zugriffe.

### 3.2 Bereits dialektneutrale Ziel-/Migrationsschicht (kein SQLite-Runtime-Leak)

- `stockbot/core/db_pool.py::get_engine` (:37–52) erzeugt die SQLAlchemy-Engine;
  `session_scope` (:65–78) stellt die Transaktionsgrenze bereit. Es existiert noch kein
  Backend-Seam und keine Runtime-Funktion nutzt
  den Pool. `session_scope` hat korrekten Commit/Rollback, liefert aber ORM-`Session`; für den
  empfohlenen Core-Port ist `engine.begin()` bzw. eine Core-fähige Unit-of-Work zweckmäßiger.
- `stockbot/core/db_migrate.py::migrate_snapshot_to_engine` (:55–72) und
  `compare_snapshot_to_engine` (:75–116) verwenden bereits SQLAlchemy Core. Vor dem echten
  Cutover müssen sie alle zehn Tabellen kennen. Nach Inserts mit expliziten BigInteger-IDs
  müssen PostgreSQL-Sequences auf mindestens `MAX(id)`/`setval` synchronisiert und durch einen
  anschließenden Insert getestet werden; der aktuelle Code tut das nicht sichtbar.
- Die einzige Alembic-Revision `a1b2c3d4e5f6_initial_schema.py` enthält nur die sieben
  Alt-Tabellen. `trade_intents`, `orders`, `order_events` samt Constraints/Index fehlen. Das ist
  ein **Cutover-Blocker**, kein späterer Nice-to-have.
- Zusätzlich driftet die Revision in Nullability und Defaults vom SQLite-Vertrag: unter anderem
  sind `users.created_at/updated_at`, `trades.created_at`, `trade_ticks.ts`,
  `sessions.created_at`, `notifications.ts`, `strategy_configs.updated_at` und
  `trade_events.ts` nullable und ohne Zeit-Default; `trades.direction/signal_json` sind nullable.
  Mehrere Laufzeit-Inserts verlassen sich auf SQLite-Defaults. Ohne Schemaangleichung entstehen
  in PostgreSQL stille `NULL`-Werte statt gleichwertiger Zeitstempel bzw. schwächere Constraints.

### 3.3 Test-only-Kopplungen (müssen für Dual-Backend-Tests entkoppelt werden)

Die folgenden Tests greifen direkt auf `DB_FILE`, `_connect`, `sqlite3.connect` oder
`execute` zu. Ein reines Ersetzen der Produktionsschicht reicht daher nicht, um dieselben
Contracts gegen PostgreSQL auszuführen.

| Datei / Funktion (Zeilen) | Tabellen / Zugriff |
|---|---|
| `test_asset_classes.py::fresh` :21; `test_broker_event_worker.py::accepted_order` :34; `test_settings.py::fresh_db` :24; `test_smartmoney.py::fresh_db` :26 | Nur `DB_FILE` auf temporäre SQLite-Datei umbiegen. |
| `test_db_export.py::_fresh` :20 und Exporttest :38; `test_db_migrate.py::_seed_source_db` :50/:58 | `DB_FILE`; Export-/Migrationsquelle ist bewusst SQLite. |
| `test_signals.py::fresh_db` :35 und `test_overnight_trade_is_managed_across_days` :268–284 | `DB_FILE`; direkte `trades`-INSERT/SELECT mit `date('now','-1 day')` und `?`. |
| `test_onboarding.py::fresh_db` :33, `_raw_creds` :55–56 | `DB_FILE`; direkter SELECT verschlüsselter `users`-BLOBs. |
| `test_watchlist.py::fresh_db`/:`_complete_user` :32/:39–40; Migrations-Test :79–86 | `users`; direkter UPDATE sowie `sqlite3.connect`, Alt-DDL und `PRAGMA table_info`. Muss als SQLite-Startup-Migrationstest separat bleiben. |
| `test_reset_and_options.py::_fresh_db` :21, Options-Tests :49–50/:72–73 | `DB_FILE`; direkte `trades`-UPDATEs. |
| `test_orphan_adopt.py::_fresh_db` :24, Adopt-Tests :82–83/:98–99 | `DB_FILE`; direkte `trades`-UPDATEs. |
| `test_services.py::fresh_db` :54, Healing-Test :141–142 | `DB_FILE`; direkte Korruption in `trades`. |
| `test_tracking_export.py::_fresh` :43, Event-Test :137–141 | `DB_FILE`; direkte DELETE/INSERTs in `trade_events`. |
| `test_trading.py::fresh_db` :23, Migrations-Test :151–153 | `DB_FILE`; direkte Updates in `users` und `trades.signal_json`. |
| `test_roundup_queue.py` :71/:78–79/:124 | `DB_FILE`; direkter `trades`-UPDATE. |
| `test_optimize_lab.py` :395/:415–416/:422/:554–566/:579 | `DB_FILE`; direkte `strategy_configs`-/`users`-/`trades`-Writes; SQLite `date('now')`. |
| `test_webapp.py::fresh` :57, Broker-Exec-Test :220–222 | `DB_FILE`; direkte SELECTs aus `trade_intents` und `orders`. |
| `test_intraday.py::fresh_db` :30 und :312–313/:489–490/:534–535/:726–754/:824–825 | `DB_FILE`; direkte Reads/Writes in `trade_intents`, `trades`, `trade_ticks`; SQLite `date`/`datetime`. |
| `test_oms.py::oms_db` :23, Idempotenztest :103–106 | `DB_FILE`; direktes `sqlite3.connect`, `orders`-COUNT/Constraint-Insert und Erwartung `sqlite3.IntegrityError`. |
| `test_db_pool.py` :63–134 | Bereits SQLAlchemy Core/Session; Commit/Rollback-Contracts gegen SQLite und optional echtes PostgreSQL. |

## 4. Klassifikation und besondere Dialektbefunde

Für die **89 direkten Runtime-/SQLite-Fundstellen** (86 in `db.py`, eine im Lab, zwei im
SQLite-Exporter) ergibt sich:

| Klasse | Anzahl | Schwerpunkt |
|---|---:|---|
| **A – trivial** | **29** | Dialektneutrale Reads/einfache Writes; Bind- und Mapping-Umstellung. |
| **B – SQL-Umformulierung** | **28** | Vor allem `datetime/date('now', modifier)`, dynamische Filter und `BYTEA`-Resultate. |
| **C – Verhalten/Transaktion** | **32** | Startup-Migration, Connection-Seam, `lastrowid`, Mehrschritt-State-Transitions, Lost Updates, Export/Cutover. |
| **Summe** | **89** | Test-only-Zugriffe und die bereits dialektneutrale Migrationsschicht sind separat dokumentiert und nicht mitgezählt. |

Weitere globale Befunde:

- **Paramstyle:** `?` ist flächendeckend; keine String-Substitution von Nutzwerten. Dynamische
  SQL-Strings verwenden nur interne Tabellen-/Prädikatfragmente.
- **Upserts:** Keine `OR REPLACE`/`OR IGNORE`-Fundstelle. `add_pending` und
  `upsert_strategy_config` nutzen schon `ON CONFLICT`, benötigen aber SQLAlchemy-Binds bzw.
  `RETURNING`/Zeitersatz.
- **IDs:** `lastrowid` in `create_oms_order`, `add_notification`, `add_pending` und
  `adopt_active_trade`; PostgreSQL verlangt `RETURNING`. Explizit migrierte IDs verlangen
  Sequence-Synchronisation.
- **Zeit:** DB-Spalten sind im Zielschema weiterhin `Text`. Für diesen Cutover sollte die
  bestehende UTC-Stringdarstellung erhalten werden; ein Wechsel auf `TIMESTAMPTZ` ist eine
  eigene, spätere Migration, um den Port nicht mit Semantikänderung zu vermischen.
- **Typ-Laxheit:** SQLite akzeptiert Werte flexibler als PostgreSQL. Vor Cutover sind mindestens
  BigInteger-Reichweite, NOT NULL/FKs, numerische Strings/NaN, 0/1-Bools und TEXT-Datumsformate
  gegen echte Daten zu validieren.

## 5. Strategieentscheidung

### Bewertete Optionen

| Option | Risiko / Testbarkeit / Rollback |
|---|---|
| **(i) Vollständig SQLAlchemy Core über `db_pool`** | Langfristig sauberste Dialektkontrolle, strukturierte Binds/`RETURNING`, klare Result-Mappings und echte Transaktionen. Als Big-Bang über 89 Fundstellen aber große Review-Fläche und schwierige Fehlerlokalisierung; ohne temporären Dual-Backend-Seam ist Rollback operativ grob. |
| **(ii) Dünner DB-API-Adapter mit SQL-Übersetzung** | Kleinster Diff bei trivialen Queries, aber gefährlich: Regex-/Textübersetzung von `?`, Zeitfunktionen, DDL und `lastrowid` bildet Transaktionsfehlerzustand, `RETURNING`, Fehlerklassen und Mapping-Semantik nicht verlässlich ab. Er konserviert implizite SQLite-Annahmen und erzeugt eine zweite Mini-Datenbankbibliothek. |
| **(iii) Hybrid: neuer Engine-Seam, Funktionen scheibenweise auf SQLAlchemy Core, `DB_BACKEND=sqlite|postgres`** | Kleine, reviewbare Scheiben; dieselben API-Contracts laufen gegen beide Engines. SQLite bleibt bis Cutover Default/Source of Truth, Umschalten und Zurückschalten sind konfigurierbar. Temporär zwei Implementierungen/Codepfade, daher kurze Migrationsphase und harte Paritätstests erforderlich. |

### Empfehlung

**Option (iii), mit SQLAlchemy Core als Zielimplementierung, ist die empfohlene Strategie.**
Sie verbindet die saubere Dialekt-/Transaktionsbehandlung von (i) mit kleinem Blast Radius,
Dual-Backend-Contract-Tests und einem kontrollierten Rollback; der Feature-Flag ist ein
Migrationswerkzeug und wird nach stabilisiertem Cutover entfernt. Option (ii) wird verworfen,
weil gerade die risikoreichen Stellen nicht Paramstyle-Probleme, sondern Race-, Rollback-,
`RETURNING`- und Startup-Semantik sind. Ein Big-Bang nach (i) ist bei Live-/Paper-Tradingdaten
unnötig schwer zu reviewen und zurückzurollen.

Wichtig: Nach dem Umschalttag darf ein Flag-Rollback nicht blind auf die inzwischen veraltete
SQLite-Datei schreiben. Rollback bedeutet entweder (a) vor jedem neuen Postgres-Write innerhalb
eines kurzen, vereinbarten Beobachtungsfensters zurückschalten oder (b) Postgres→SQLite-
Rücksynchronisation/Restore mit Schreibstopp. Ein Runtime-Dual-Write wird wegen uneindeutiger
Fehler- und Reihenfolgensemantik **nicht** empfohlen.

## 6. Scheibenplan (einzeln test- und reviewbar)

### 1. Engine-Seam + `users` read-only (kleinstmögliche Scheibe)

- **Umfang:** additives Backend-Interface/Unit-of-Work; `DB_BACKEND=sqlite|postgres`, SQLite bleibt
  Default. Nur `get_user`, `list_active_users`, `get_user_by_token`,
  `has_alpaca_credentials`, `get_decrypted_credentials` über den Seam; einheitliche Mapping-
  und `BYTEA`-Normalisierung. Noch keine Writes.
- **Tabellen:** `users`.
- **Test beide Backends:** parametrisierte Contract-Suite gegen temporäre SQLite-Engine und
  echtes Test-Postgres; gleiche Dicts, 0/1-Bools, Telegram-BigInteger-ID und Fernet-BLOB/
  `memoryview`-Roundtrip. Bestehende SQLite-Suite unverändert weiter grün.
- **Rollback:** Flag auf `sqlite`; neue Postgres-Reads sind zustandslos.

### 2. Zielschema und Cutover-Tooling vervollständigen

- **Umfang:** neue Alembic-Revision für `trade_intents`, `orders`, `order_events`, Constraints
  und Index; Export-/Migrations-`TABLES` ergänzen; Sequence-Sync und Vollvergleich aller zehn
  Tabellen. Startup-DDL auf PostgreSQL explizit verbieten und Nullability/Server-Defaults gegen
  `SCHEMA_SQL` maschinell abgleichen.
- **Tabellen:** alle, Schwerpunkt drei OMS-Tabellen und sieben BigInteger-Sequences.
- **Test beide Backends:** Schema-/Constraint-Contract, Snapshot mit OMS-Daten, Zeilenzahlen/
  Summen/Max-IDs, danach je Sequence ein Insert ohne explizite ID. Testmigration gegen echtes
  Postgres wiederholen.
- **Rollback:** Alembic-Downgrade nur in leerem Staging; Runtime bleibt weiterhin SQLite.

### 3. `users`-Writes und Sessions

- **Umfang:** einfache User-Setter, Credentials, Dashboard-Token, get-or-create, Sessions;
  Zeitwerte in Python/benannten Binds; `get_or_create_user`/Dashboard-Token atomar machen.
- **Tabellen:** `users`, `sessions`.
- **Test beide Backends:** bestehende Settings/Onboarding/Auth-Tests als Contract-Suite;
  parallele get-or-create-/Token-Aufrufe, Session-Ablauf an UTC-Grenzen, `rowcount`, BLOBs.
- **Rollback:** Flag auf SQLite; noch kein Cutover, SQLite bleibt alleinige Schreibquelle.

### 4. Benachrichtigungen und Strategiekonfiguration

- **Umfang:** Notification-CRUD (`RETURNING id`) und Strategy-Config-Upsert; Lab-Direktquery
  durch öffentliche DB-Read-API ersetzen.
- **Tabellen:** `notifications`, `strategy_configs`, read-only `trades` für Reality-Check.
- **Test beide Backends:** ID-Rückgabe, Upsert-Parität, Zeitformat, gebundenes `LIMIT`, Lab-
  Zeitraumfilter und Fehlerabbildung.
- **Rollback:** Flag auf SQLite; keine Datenkopplung zwischen Backends.

### 5. Trade-Reads und Ticks

- **Umfang:** alle reinen Trade-/Event-Reads, dynamische Zeitfenster sowie `add_tick`; Mapping-
  Contract für `_trade_to_dict`.
- **Tabellen:** `trades`, `trade_events`, `trade_ticks`.
- **Test beide Backends:** identische Sortierung (inkl. aktiver Trade zuerst), Datums-/Tages-
  grenzen, leere/NULL-Felder, Tick-Reihenfolge, große Datenmenge/Indexplan stichprobenartig.
- **Rollback:** Flag auf SQLite; SQLite ist weiterhin Writer.

### 6. Trade-Lifecycle + Events

- **Umfang:** `add_pending`, Aktivieren/Beenden, alle `mark_broker_*`, Adopt, Expire,
  `close_all`, `_log_event`; `RETURNING` und CAS/`FOR UPDATE`, genau ein Event pro erfolgreichem
  Zustandsübergang.
- **Tabellen:** `trades`, `trade_events`.
- **Test beide Backends:** bestehende Trading/Reconcile/Intraday-Tests plus parallele Worker-
  Tests mit Barriere: nur ein Gewinner, keine doppelten Events, Trade/Event immer gemeinsam
  committen oder rollbacken. PostgreSQL-Isolation explizit dokumentieren (zunächst READ COMMITTED
  + CAS/Row Locks).
- **Rollback:** Flag solange noch vor Cutover; Scheibe erst mergen, wenn beide Backends exakt
  dieselben Zustands-/Event-Contracts erfüllen.

### 7. OMS-Persistenz und Maintenance-Jobs

- **Umfang:** `create_oms_order`, Transition/Event, Idempotenz; `reset_user_trades`,
  Leverage-Healing und PnL-Healing. Netzwerkzugriff aus DB-Transaktion entfernen. Postgres-
  Startup führt nur Alembic-Head-Prüfung, keine `_migrate`-Datenjobs aus.
- **Tabellen:** `trade_intents`, `orders`, `order_events`, plus alle Maintenance-Tabellen.
- **Test beide Backends:** parallele gleiche Idempotency Keys, Unique-Verletzung mit sauberem
  Rollback/Retry, Status-CAS, Maintenance-Idempotenz, Retention/FK-Verhalten beim Reset.
- **Rollback:** Flag vor Cutover; Jobs können getrennt deaktiviert werden. Keine automatische
  destructive Alembic-Rückmigration.

### 8. Final Sync, Umschalten und Beobachtungsfenster

- **Umfang:** Wartungsfenster/Writer-Stopp; finalen SQLite-Snapshot ziehen; seit bewiesener
  Testmigration aufgelaufene Daten vollständig neu migrieren oder deterministisch delta-synchronisieren;
  zehn Tabellen + Summen + Max-IDs + Sequences vergleichen; Smoke-Reads, dann
  `DB_BACKEND=postgres`. SQLite unverändert read-only archivieren.
- **Test beide Backends:** vor Umschaltung gleiche kanonische Exporte/Counts; Postgres-Smoke für
  User-Login, Signal→Trade/Event, OMS-Idempotenz, Tick und Broker-State; Monitoring auf Pool,
  Deadlocks, Constraint-/Serialization-Fehler und Daten-Drift.
- **Rollback:** klarer Point of no return. Vor erstem Postgres-Write Flag-Rollback möglich;
  danach Writer stoppen und Postgres→SQLite restore/sync oder Postgres reparieren. Archiv-SQLite
  nie ohne Rücksync wieder als Writer starten.

### 9. Stabilisieren und temporären Dual-Backend-Code entfernen

- **Umfang:** nach vereinbartem Beobachtungsfenster SQLite-Runtimepfad, `DB_FILE`-Mutation in
  allgemeinen Contract-Tests und Feature-Flag entfernen; SQLite-Exporter als explizites
  Archivtool behalten; Runbook/Restore-Drill abschließen.
- **Tabellen:** alle.
- **Test beide Backends:** vor Entfernung finale Parität; danach volle Suite gegen Postgres und
  separate kleine Archiv-/Exporttests gegen SQLite.
- **Rollback:** Git-Revert der Cleanup-Scheibe stellt Codepfad wieder her, Datenrollback bleibt
  dennoch ein kontrollierter Restore und kein bloßer Flag-Wechsel.

## 7. Risiken und notwendige Entscheidungen

### Concurrency und Transaktionen

- Heute läuft im Wesentlichen ein Prozess; SQLite serialisiert Writer grob auf Dateiebene.
  PostgreSQL erlaubt echte parallele Transaktionen. Read-modify-write-Funktionen können dadurch
  Lost Updates erzeugen, auch wenn sie unter SQLite unauffällig waren.
- Zustandswechsel brauchen CAS (`UPDATE ... WHERE status=:expected RETURNING ...`) oder gezielte
  Row Locks. Trade/Order und zugehöriges Event müssen immer dieselbe Transaktion teilen.
- Nach einem PostgreSQL-Statementfehler muss vor weiterer Nutzung rollback erfolgen. Der heutige
  `create_oms_order`-Catch außerhalb der SQLite-Connection darf nicht 1:1 übernommen werden;
  SQLAlchemy-`IntegrityError`, Constraint-Erkennung und Retry-Grenzen sind explizit zu testen.
- Poolgröße ist keine Nebenfrage: Bot, Web und Scheduler können künftig konkurrieren. Kurze
  Transaktionen, kein Netzwerk-I/O innerhalb einer Transaktion und Pool-/Lock-Metriken sind Pflicht.

### Startup-Migrationen

- `_migrate` mischt Schema-DDL, Backfills, Token-Hashing und wiederholtes Healing. Unter Postgres
  darf kein beliebiger App-Prozess dies beim Start ausführen. Schema nur per Alembic; Datenjobs
  versioniert, beobachtbar, idempotent und mit Advisory Lock/einmaligem Operator-Schritt.
- Die fehlenden drei OMS-Tabellen im Alembic-Zielschema und Snapshot sind ein harter Blocker.
- Strikte PostgreSQL-FKs können in echten Daten Orphans sichtbar machen, weil SQLite
  `foreign_keys` nicht aktiviert. Vor Migration prüfen und Retention/Cascade entscheiden.

### Backfill/Sync am Cutover

- Die bewiesene Testmigration ist eine Momentaufnahme; SQLite schreibt bis zum Umschalttag weiter.
  Sicherste Variante bei der vorhandenen Datenmenge: kurzer Writer-Stopp, finaler Vollsnapshot,
  leeres/frisches Ziel bzw. kontrolliertes Truncate+Reload, Vergleich, Sequence-Sync, Cutover.
- Falls kein Wartungsfenster möglich ist, braucht es einen echten Delta-Mechanismus mit stabilen
  Keys und Update-/Delete-Erfassung; `updated_at` fehlt bzw. ist nicht auf allen Tabellen
  verlässlich, daher ist ein naiver „seit Zeit X“-Sync nicht korrekt.
- Kein Dual-Write ohne Outbox/Reconciliation: ein halb erfolgreicher Write würde zwei Wahrheiten
  erzeugen. Die final eingefrorene SQLite-Datei plus Hash/Snapshot-Manifest bleibt Audit-/Rollback-
  Artefakt und danach read-only.

### Verschlüsselte BLOBs und Typen

- `users.broker_api_key`/`broker_api_secret` bleiben Fernet-Ciphertext; Export nur Base64,
  Migration zurück zu `bytes`, PostgreSQL-Spalte `BYTEA`. Treiber können `memoryview` liefern;
  `decrypt(bytes(value))` und Roundtrip sind bereits als notwendiger Contract aus
  `tests/test_db_migrate.py` bekannt.
- Keine Secrets in Logs, Testausgaben oder Vergleichsdiffs. Vergleiche für BLOBs verwenden
  Länge/Hash bzw. erfolgreichen Entschlüsselungs-Roundtrip, nie Klartext.
- BigInteger ist in der initialen Revision korrigiert, muss aber für alle Telegram-/Message-/
  wachsenden IDs und Sequences mit echten Maximalwerten validiert werden.

## 8. Optionaler additiver Seam-Entwurf (keine Implementierung)

```python
class Database(Protocol):
    @contextmanager
    def transaction(self) -> Iterator[DbTransaction]: ...

class DbTransaction(Protocol):
    def mappings(self, statement, params=None) -> MappingResult: ...
    def execute(self, statement, params=None) -> Result: ...

def get_database(backend: Literal["sqlite", "postgres"] | None = None) -> Database: ...
```

Der konkrete SQLAlchemy-Core-Adapter sollte Tabellen-Metadaten und benannte Binds verwenden.
Domänenfunktionen behalten zunächst ihre bestehende öffentliche Signatur; nur interne Helfer
wie `_log_event` erhalten die laufende Transaktion. Ein allgemeiner SQL-String-Translator ist
bewusst nicht Teil des Entwurfs.
