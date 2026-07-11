# SQLite-Schema (eingefroren) — Ausgangsstand für die PostgreSQL-Migration

*Teil von PLAT-001 (siehe [PLAN_CHECKLIST.md](PLAN_CHECKLIST.md) Phase 1 / [Plan.md](Plan.md)
§9.1). Migrationsstrategie: 1. Schema einfrieren (dieses Dokument) → 2. Exportskript
(`stockbot/core/db_export.py`, `tools/export_sqlite_snapshot.py`) → 3. Transformationsregeln
→ 4. Testmigration → 5. Zeilen/Summen vergleichen → 6. Staging migrieren → 7. Paper auf
PostgreSQL umstellen → 8. SQLite nur noch als Archiv.*

Quelle: `stockbot/core/db.py::SCHEMA_SQL` (+ additive `_migrate()`-Spalten, die hier bereits
in der jeweiligen `CREATE TABLE`-Definition enthalten sind). Stand: 2026-07-11.

**Wichtig:** Ändert sich `SCHEMA_SQL` künftig, muss dieses Dokument vor dem nächsten
PostgreSQL-Migrationsschritt (Transformationsregeln/Testmigration) nachgezogen werden —
sonst ist der eingefrorene Stand nicht mehr verlässlich.

---

## users

Ein Datensatz je Nutzer (`user_id` == Telegram-Chat-ID). Enthält Profil, Einstellungen und
— verschlüsselt — Broker-Zugangsdaten.

| Spalte              | Typ     | Default              | Hinweis |
|---------------------|---------|----------------------|---------|
| user_id             | INTEGER | —                    | Primary Key (== Telegram chat_id) |
| username            | TEXT    | NULL                 | |
| trade_size_eur      | REAL    | 25.0                 | Demo-Trade-Größe |
| broker_platform     | TEXT    | NULL                 | z. B. `'alpaca'` |
| broker_api_key      | BLOB    | NULL                 | Fernet-verschlüsselt (`db.encrypt`) |
| broker_api_secret   | BLOB    | NULL                 | Fernet-verschlüsselt (`db.encrypt`) |
| onboarding_state    | TEXT    | `'in_progress'`      | `in_progress` \| `complete` |
| is_active           | INTEGER | 1                    | Bool als 0/1 |
| dashboard_token     | TEXT    | NULL                 | Klartext-Token für Web-Dashboard-Link |
| market_region       | TEXT    | `'sp500'`            | kommagetrennte Liste möglich |
| top_n_signals       | INTEGER | 5                    | 1..20 |
| sl_tp_mode          | TEXT    | `'normal'`           | `aus`\|`passiv`\|`normal`\|`aggressiv` |
| leverage            | REAL    | 1.0                  | seit TSAFE-002 hart auf `MAX_LEVERAGE` (1) geklemmt |
| auto_accept         | INTEGER | 0                    | Bool |
| auto_universe       | INTEGER | 1                    | Bool |
| strategy            | TEXT    | `'standard'`         | kommagetrennte Liste möglich |
| llm_rank            | INTEGER | 1                    | Bool |
| eod_close           | INTEGER | 1                    | Bool |
| broker_exec         | INTEGER | 0                    | Bool — echte (Paper-)Order-Ausführung an/aus |
| signal_window       | INTEGER | 0                    | Bool |
| watchlist           | TEXT    | `''`                 | kommagetrennt, Großbuchstaben |
| notify_channel      | TEXT    | `'both'`             | `telegram`\|`web`\|`both` |
| asset_pref          | TEXT    | `'stocks'`           | |
| created_at          | TEXT    | `datetime('now')`    | ISO-ähnlich (SQLite-Format, UTC) |
| updated_at          | TEXT    | `datetime('now')`    | |

Keine Indizes über den Primary Key hinaus.

## trades

Ein Datensatz je Signal/Trade und Nutzer und Tag (Demo- **und** Broker-Trades).

| Spalte                    | Typ     | Default           | Hinweis |
|---------------------------|---------|-------------------|---------|
| id                        | INTEGER | —                 | Primary Key (Autoincrement) |
| user_id                   | INTEGER | —                 | FK → `users.user_id` |
| trade_date                | TEXT    | —                 | `YYYY-MM-DD` |
| ticker                    | TEXT    | —                 | |
| direction                 | TEXT    | —                 | `long` (Live) \| `short` (nur Backtest) |
| signal_json               | TEXT    | —                 | Serialisiertes Signal-Dict (JSON) |
| message_id                | INTEGER | NULL              | Telegram-Nachrichten-ID |
| status                    | TEXT    | `'pending'`       | `pending`→`active`/`broker_pending`→`closed`/`rejected`/`expired`/`broker_failed`/`broker_closing` |
| entry                     | REAL    | NULL              | |
| exit                      | REAL    | NULL              | |
| pnl_eur                   | REAL    | NULL              | |
| pnl_pct                   | REAL    | NULL              | |
| broker_order_id           | TEXT    | NULL              | |
| broker_status             | TEXT    | NULL              | |
| broker_filled_qty         | REAL    | NULL              | |
| broker_filled_avg_price   | REAL    | NULL              | |
| broker_updated_at         | TEXT    | NULL              | |
| created_at                | TEXT    | `datetime('now')` | |

- **UNIQUE** `(user_id, trade_date, ticker)` — ein Datensatz je Aktie/Tag/Nutzer.
- **Index** `idx_trades_user_date_status` auf `(user_id, trade_date, status)`.

## trade_ticks

Intraday-Kurs-/Stärke-Verlauf je aktivem Trade (für Charts). Kein Fremdschlüssel-Constraint
in SQLite hinterlegt (nur logisch über `user_id`/`ticker`/`trade_date`).

| Spalte     | Typ     | Default           |
|------------|---------|-------------------|
| id         | INTEGER | — (Primary Key)   |
| user_id    | INTEGER | —                 |
| trade_date | TEXT    | —                 |
| ticker     | TEXT    | —                 |
| ts         | TEXT    | `datetime('now')` |
| price      | REAL    | NULL              |
| strength   | REAL    | NULL              |

**Index** `idx_ticks_user_date_ticker` auf `(user_id, trade_date, ticker, ts)`.

## sessions

Web-Login-Sessions. Es wird **nur der SHA-256-Hash** des Tokens gespeichert — das
Klartext-Token existiert ausschließlich im Cookie des Nutzers (kein Secret in der DB).

| Spalte     | Typ  | Default           | Hinweis |
|------------|------|-------------------|---------|
| token      | TEXT | —                 | Primary Key — SHA-256-Hex des Session-Tokens |
| user_id    | INTEGER | —              | FK → `users.user_id` |
| created_at | TEXT | `datetime('now')` | |
| expires_at | TEXT | —                 | |

## notifications

In-App-Benachrichtigungen je Nutzer.

| Spalte  | Typ     | Default           | Hinweis |
|---------|---------|-------------------|---------|
| id      | INTEGER | — (Primary Key)   | |
| user_id | INTEGER | —                 | |
| ts      | TEXT    | `datetime('now')` | |
| type    | TEXT    | `'info'`          | |
| title   | TEXT    | —                 | |
| body    | TEXT    | `''`              | |
| read    | INTEGER | 0                 | Bool |

**Index** `idx_notifications_user` auf `(user_id, read, id)`.

## strategy_configs

Web-editierbare Strategie-Parameter (Backtest/Live-Overrides), ein Datensatz je Strategie.

| Spalte      | Typ     | Default           |
|-------------|---------|-------------------|
| key         | TEXT    | — (Primary Key)   |
| label       | TEXT    | —                 |
| description | TEXT    | `''`              |
| params_json | TEXT    | `'{}'`            |
| enabled     | INTEGER | 1                 |
| updated_at  | TEXT    | `datetime('now')` |

## trade_events

Append-only Status-Übergangs-Log je Trade (Vorstufe zum Audit-Log aus PLAT-002).

| Spalte        | Typ     | Default           | Hinweis |
|---------------|---------|-------------------|---------|
| id            | INTEGER | — (Primary Key)   | |
| trade_id      | INTEGER | —                 | logisch → `trades.id` |
| user_id       | INTEGER | —                 | |
| ticker        | TEXT    | —                 | |
| trade_date    | TEXT    | —                 | |
| from_status   | TEXT    | NULL              | NULL beim Anlage-Event |
| to_status     | TEXT    | —                 | |
| broker_status | TEXT    | NULL              | |
| ts            | TEXT    | `datetime('now')` | |
| note          | TEXT    | NULL              | |

- **Index** `idx_trade_events_trade` auf `(trade_id, ts)`.
- **Index** `idx_trade_events_user` auf `(user_id, ts)`.

---

## Verschlüsselte / gehashte Spalten (bei Migration/Export beachten)

- `users.broker_api_key` / `users.broker_api_secret`: Fernet-verschlüsselte BLOBs
  (`ENCRYPTION_KEY` aus der Konfiguration). Werden **nie** entschlüsselt exportiert oder
  geloggt — auch im Read-only-Snapshot bleiben sie verschlüsselt (Base64-kodiert).
- `sessions.token`: nur SHA-256-Hash, kein Secret in der DB.

## Read-only-Export

`stockbot/core/db_export.py::export_snapshot()` (CLI: `python tools/export_sqlite_snapshot.py`)
öffnet die DB strikt lesend (`sqlite3.connect(..., mode=ro)`) und schreibt alle Tabellen
+ Zeilenanzahlen als JSON nach `data/sqlite_exports/sqlite_snapshot_<UTC-Zeitstempel>.json`.
Dieser Snapshot dient als Referenz für den späteren Zeilen/Summen-Vergleich nach der
eigentlichen PostgreSQL-Migration (Schritt 5 der Migrationsstrategie).
