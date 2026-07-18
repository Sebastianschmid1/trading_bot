# Umsetzungsplan — trading_bot (Wellen ab 2026-07-15)

> Sequenzierter Fahrplan für das offene Backlog aus [PLAN_CHECKLIST.md](PLAN_CHECKLIST.md).
> Erstellt am 2026-07-15 (Fabel-Plan-Architekt, gegen den echten Code kalibriert).
> **Arbeitsmodell:** Engineering-Manager (Claude) plant/reviewt, alle Coding-Aufgaben gehen an
> parallele "Sol"-Worker (Codex CLI). Unabhängige Pakete → mehrere Sol-Worker gleichzeitig.

## Projektstand (Einstieg für eine frische Sitzung)

- Gehärteter Rebuild eines Multi-User-Signal-Bots (Telegram + Web-App, Broker Alpaca) zum
  sicherheitsorientierten "Trading Research & Execution Assistant". **Paper ist Standard, Live hart
  gesperrt** (Kill-Switch TSAFE-001).
- **Phasen 0, 1, 4, 5 fertig; Phase 6 größtenteils fertig.**
- **PostgreSQL-Cutover vollzogen:** Produktion läuft live auf `DB_BACKEND=postgres` (Docker-Postgres 16
  auf VPS), Paper-Modus, mit **5× Hebel NUR im Paper** (Live bleibt 1×, TSAFE-002). SQLite-Seam bleibt
  vorerst als Rollback-Netz (PLAT-001 Scheibe 9 = Seam-Entfernung, bewusst zurückgestellt bis Postgres
  ein paar Markttage stabil lief).
- Zuletzt erledigt: DB-Seam-Restsanierung (`set_trade_leverage`/`merge_active_trade_signal` auf den
  Seam, `bot.py` Zeitvertrag robust), Commit `11dda4a`, deployed & verifiziert.
- **W0 komplett (2026-07-15, `c0e43bd`, auf GitHub `main`, NOCH NICHT auf VPS deployt):** alle vier
  Betriebsschutz-Tasks via parallele Sol-Worker gebaut, reviewt, gemergt, Suite grün (877 passed,
  27 skipped). W0.1 Postgres-Backups (PLAT-009), W0.2 Deps gepinnt (PLAT-006a), W0.3 systemd-Härtung
  (PLAT-008), W0.4 typisierte Settings + Start-Validierung (Paket A). Offene menschliche Schritte:
  **VPS-Migration auf `stockbot`-User/`/opt/stockbot` (Tor T1, docs/DEPLOY_HARDENING.md)**, Backup-Timer
  + age-Recipient auf dem VPS aktivieren (docs/BACKUP_RESTORE.md), optional Lock-Hashes nachziehen
  (`pip-compile --generate-hashes`). Nächster Fokus: **W1 Risk-Wiring** (erst nach 2-3 stabilen
  Postgres-Markttagen deployen).

## W1 KOMPLETT (2026-07-16, `5aa6ece`, GitHub `main`, NICHT deployt)

Alle W1-Tasks (W1.1–W1.6) gebaut, reviewt, gemergt, Suite grün (913 passed, 27 skipped,
1 vorbestehender Fehler s. u.). **Gates P3 (Risk-Wiring) + P1.1 (Audit) + P2-Quote geschlossen.**
W1.3 Kill-Switch persistent (eigene Alembic-Revision c3d4e5f6a7b8, read-through über
Prozessgrenzen, OMS-Gate NACH Idempotenz-Check, Telegram `/killswitch` + Web-Toggle,
Schutz-Exits bleiben erlaubt). Alembic-Head jetzt `c3d4e5f6a7b8`.

## W2 KOMPLETT (2026-07-16, OMS-Live-Orchestrierung, Gate P4)

- **W2.1 Broker-Event-Ingestion ✅** (`c516fab`, GitHub main, NICHT deployt): `broker_poll.py`
  Polling-Loop (`BROKER_POLL_INTERVAL_SEC=30`, handelszeitbegrenzt) → `broker_event_worker.
  process_broker_event` mit stabiler `broker_event_id` (idempotente Dedup). Suite 928 passed.
- **W2.4 E2E-Doppelklick-Beweis ✅** (`a88d518`, test-only): Doppel-POST/Doppel-Callback
  erzeugen genau EINE Broker-/OMS-Order. Empirisch verifiziert — der zweite Accept wird sicher
  als „nicht mehr verfügbar" abgewiesen (Idempotenz am Service-Layer + OMS-Idempotency-Key als
  zweite Linie). Prod-Code korrekt, kein Bug.
- **W2.2 Reconciliation-Scheduler + Alarm ✅** (`95953bd`): `reconcile_scheduler.py`
  periodischer OMS-Abgleich (AlpacaBrokerAdapter + `run_periodic_reconciliation`,
  `RECONCILE_PERIODIC_SEC=600`, per User, gebündelter Admin-Alarm + Dedup). Nur Erkennung.
- **W2.3 Partial-Fill-Orchestrierung ✅** (`0ce4a65`): bei nicht-dedupliziertem partial_fill
  → `decide_partial_fill_action`; submit_protective = **broker-seitige Stop-SELL**
  (`broker.submit_stop_sell`, Alpaca StopOrderRequest, Fillgröße, Signal-Stop-Loss) +
  Persistenz in separater `protective_orders`-Tabelle (Alembic `d4e5f6a7b8c9`) → keine
  Doppel-Schutzorder; cancel_restorder → cancel_order; resize/kein-Stop → Admin-Alarm.
  Bypass-Guard um submit_stop_sell erweitert. **Vom Nutzer freigegebenes Verhalten.**

**→ W2 KOMPLETT (Gate P4). Volle Suite auf main: 942 passed. Nächste Welle W3 (Daten &
Versionen) ist per Tor T0 gated (Postgres-Stabilität nach ~3-5 Markttagen bestätigen →
entblockt W3.1 Scheibe 9). W4/W5 laufen parallel und sind NICHT durch T0 gated.**

## W4/W5 ANGELAUFEN (2026-07-16, GitHub `main`, NICHT deployt)

Parallele Sol-Worker (Shared-venv-Setup wegen User-Quota; max. 2 parallel). Erste Charge
gemergt + reviewt, volle Suite auf gemergtem main selbst gefahren:

- **W4.1 JSON-Logging ✅** (`core/logging_setup.py`): Text/JSON-Formatter, `LOG_FORMAT`
  opt-in (Default `text` → Log-Ansicht kompatibel), ContextVars trace_id +
  HMAC-pseudonymisierte user_id, Secret-/PII-Redaction.
- **W4.3 Secrets ✅** (PLAT-006b): `config._secret()` Präzedenz systemd-Credential > Env >
  `.env`; `Settings.__repr__` maskiert Secrets (`***`); `deploy/*.service` LoadCredential-
  Vorlage + Rotation dokumentiert. Dev-Verhalten unverändert.
- **W5.1 Backtest-Seam ✅** (Gate-P7-Fundament): `yfinance` raus aus `backtest/engine.py`,
  Bars über injizierbaren `MarketDataProvider`, neue `backtest/clock.py` (`BarClock`).
  Strategiecode war bereits geteilt → Aufwand L→S.

**Diese Session gemergt (lokal main, NICHT gepusht/deployt):** W4.1 Logging, W4.3 Secrets,
W5.1 Backtest-Seam, W4.2 Metriken, W5.3 Kostenmodell, W5.2 Look-ahead — **6 Tasks**.

**⚠️ Codex/Sol-Usage-Limit erreicht (2026-07-17)** — Sol kann bis zum Reset keine neuen
Coding-Tasks fahren. **Nutzer-Override für diese Session: der Manager implementiert die
restlichen ungateten Tasks selbst** (mit Tests + Review-Sorgfalt), bis Sol zurück ist.
Manager-implementiert: **W5.5 ✅** (`a1d74a7`), **W5.4 ✅** (`05a17fd`), **W5.6 Validierung ✅**
(`d1f53d4`) → **ganz W5 fertig (Gate P7)**, **W4.4 Alpaca-OAuth-Seam ✅** (`2bb1636`, Alembic-Head
`e5f6a7b8c9d0`), **W4.5 Pakete B/C/D ✅** (`85ecf6c`, Alembic-Head `f6a7b8c9d0e1`) → **ganz W4 komplett**.
**W6 Labor ✅** (`43c9b67`, Gate P8, Framework). **W7 (UI/Design) GESTARTET** — Style-Phase 2
Kernkomponenten (`static/components.css` + Makros, `bf4d593`) fertig + Gallery zur Abnahme.
**Offen (W7):** Style-Phasen 3–5 (Seiten/Risikointeraktionen/A11y), Web-App-Umbau (Pflicht-
Bestätigungsdialog), Telegram-Umbau (Callback-Sicherheit), API v1 (Idempotency/RBAC) — visuelle
Teile brauchen laufende App/Browser-Verifikation, großer iterativer Rest. **W1/W2/W4/W5 Deploy weiterhin gebündelt freigabe-pflichtig; auf
GitHub `main` gepusht, nichts deployt.**

**⚠️ Vorbestehender Bug (NICHT W1):** `tests/test_db_backend_users.py::test_trade_read_mapping_
order_and_day_contract[sqlite]` schlägt seit dem Datumswechsel auf 2026-07-16 fehl
(`db.has_trade_today → False`, an einer Tagesgrenze). Datums-/Zeitzonen-abhängig, riecht nach
der bekannten DB-Zeitvertrag-Klasse (naive/aware, Berlin vs. UTC). Von 3 Sol-Workern
unabhängig bestätigt. **Eigener Debug-Task nötig** (könnte `has_trade_today` in Prod nahe
Mitternacht betreffen → Doppeltrade-/Blockade-Risiko).

**⚠️ Zweiter vorbestehender, ZEITABHÄNGIGER Bug (2026-07-16 gefunden, NICHT W4/W5):**
`tests/test_quote_context.py::test_real_oms_applies_quote_quality_gates` (3 Parametrisierungen)
failt am Nachmittag, war vormittags grün. Ursache: Der Test setzt `NOW=2026-07-16 12:00 UTC`
und übergibt `now` im Callsite-Context, aber die Quote-Age-Prüfung im LIVE-OMS-Pfad vergleicht
gegen die **echte Wall-Clock** statt gegen das injizierte `now` → sobald real-now > NOW+60s,
gilt auch die frische Quote als „stale". **Wall-Clock-Leak im OMS-Quote-Gate** — dieselbe
Determinismus-Klasse wie der `has_trade_today`-Bug; verletzt „gleiche Inputs → gleiche
RiskDecision" (Gate P3/W1.6). In Prod fail-safe (blockiert eher), aber Determinismus-Bug.
**Eigener Debug-Task** (Quote-Age auf injiziertes `now` umstellen). Deshalb zeigt die volle
Suite am Abend `3 failed` (nur diese) — kein W4/W5-Regress.

**⚠️ FREIGABE-PFLICHTIG VOR DEPLOY (ändert Live-Trade-Verhalten):** W1.1 schaltet ~10 zuvor
still übersprungene Risk-Checks scharf (max. Positionen=5, risikobasiertes Sizing-Gate,
Buying-Power, Exposure, Brokerstatus=ACTIVE). Mit den **Default-RiskProfile-Werten** können
Orders jetzt abgelehnt werden, die vorher durchliefen (v. a. `max_open_positions=5` und der
Brokerstatus-Gate). Vor dem Deploy: Default-Profile für den Paper-Betrieb (5× Hebel, kleine
Budgets) prüfen/justieren. `market_open` wurde bewusst NICHT verdrahtet (Extended-Hours).

## Wichtigster Befund: das meiste ist gebaut, aber nicht verdrahtet

Kalibrierung gegen den echten Code (entscheidend für die Aufwandsschätzung):

- **OMS ist live verdrahtet** (`stockbot/tgbot/bot.py`, `stockbot/web/webapp.py`): Entry-Orders laufen
  über `submit_intent` mit Idempotency-Key. **Aber** der `risk_context` übergibt nur Phase-0-Inputs
  (`is_live_account`, `is_option`, `leverage`). Alle Phase-3-Checks (Quote-Alter, Spread, Tagesverlust,
  Exposure, Max-Positionen, Sizing, Buying Power, Brokerstatus) existieren fertig getestet in
  `stockbot/core/risk.py::pretrade_check` — werden aber mangels Inputs **stillschweigend übersprungen**.
  → Gate P3 ist fast reine Verdrahtung, keine Neuentwicklung.
- **Regal fertiger, unverdrahteter Seams:** `data_quality`, `kill_switch` (In-Prozess, kein Persist),
  `audit_log` (In-Prozess), `strategy_registry` (In-Prozess), `exit_policies`, `allocator`,
  `partial_fill_policy`, `broker_event_worker`, `reconciliation` (kein Scheduler/Alarm), `mode_report`
  (keine UI), `shadow` (kein Scheduler), `post_trade_risk` (kein Scan). Keiner wird aus
  `bot.py`/`webapp.py` aufgerufen.
- **yfinance steckt noch im Produktionssignalpfad** (`core/evaluator.py`, `core/db.py`,
  `market/smartmoney.py`, `services/watchlist.py` u.a.) — verletzt eine Leitplanke; `MarketDataProvider`
  existiert, hat aber null Aufrufer.
- **Betriebsrisiken JETZT akut:** `deploy/*.service` laufen als `User=root`; `requirements.txt`
  ungepinnt; **die frisch produktive Postgres-DB hat kein Backup** (der SQLite-Snapshot altert ab sofort
  täglich).
- Das `[!]` beim Rohdatenarchiv (Metadaten in Postgres) ist durch den Cutover **entblockt**.

## Wellen-Übersicht

| Welle | Ziel | Schließt Gate | Status |
|---|---|---|---|
| **W0** Betriebsschutz | Prod-Postgres & Deployment absichern, ohne den Trading-Pfad im Burn-in anzufassen | Teile P9 | ✅ erledigt (`c0e43bd`) |
| **W1** Risk-Wiring | `pretrade_check` mit echten Inputs live; Kill-Switch & Audit persistent | **P3, P1.1**, P2-Quote | ✅ erledigt (`5aa6ece`) |
| **W2** OMS-Orchestrierung | Broker-Events, Reconciliation-Alarm, Partial-Fill-Handling live | **P4** | ✅ erledigt (`0ce4a65`) |
| **W3** Daten & Versionen | yfinance raus aus Prod-Pfad, Strategieversion je Signal, Mode-Dashboards, Scheibe 9 | **P2, P5, P6** | ⏸ gated auf Tor T0 |
| **W4** Observability & Platform | JSON-Logging, Metriken/Alarme, Secrets, OAuth | **P9** Rest | ✅ erledigt (W4.1–W4.5 komplett; Code-seitig P9-Rest, VPS-Aktivierung/Restore-Test menschlich) |
| **W5** Backtest-Härtung | gemeinsamer Strategiecode, Kostenmodell, Validierung, Reproduzierbarkeit | **P7** | ✅ erledigt (W5.1–W5.6 komplett; Gate P7 im Wesentlichen erfüllt) |
| **W6** Labor begrenzen | Champion/Candidate, Promotion-Gates, Holdout-Schutz | **P8** | ✅ erledigt (`research/lab.py` Framework, Gate P8; reale Promotion = Tor T3; Manager-implementiert) |
| **W7** UI/Design & Querschnitt | Style-Phasen 2–5, Web-/Telegram-Umbau, API v1 | **Gate Style** | 🔄 laufend (Style-Phase 2 Kernkomponenten ✅ `bf4d593`; Phasen 3–5 + Web-/Telegram-Umbau + API v1 offen) |
| **W8** Test & Paper-Freigabe | Testsuiten + Paper-Burn-in + Go/No-Go | **P10** | offen (nach W1–W3 deployt) |

---

## W0 — Betriebsschutz — ✅ ERLEDIGT (`c0e43bd`)

> Alle vier Tasks gebaut, reviewt, gemergt, gepusht. Detail-Status siehe oben (Projektstand).
> Die Tabelle unten ist die ursprüngliche Planung (historisch).

Kein Eingriff in den Trading-Codepfad → stört den Postgres-Burn-in nicht.

| # | Task (Sol-tauglich) | Scope | Aufwand | Abh. | Parallel? |
|---|---|---|---|---|---|
| W0.1 | **PLAT-009 Postgres-Backups** | `pg_dump` verschlüsselt (age/gpg) per Timer, Aufbewahrungsplan, Restore-Test-Skript + dokumentierter Test gegen Wegwerf-DB. **Höchste Dringlichkeit: Prod-DB hat aktuell kein Backup.** | M | — | ✅ |
| W0.2 | **PLAT-006a Deps pinnen** | `requirements.lock` (pip-tools/freeze), `pip-audit`, yfinance-tz-Cache-FD-Leck prüfen (todo.md A2), Dependabot-Konfig. | S | — | ✅ |
| W0.3 | **PLAT-008 systemd-Härtung** | Units umschreiben (eigener User `stockbot`, `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict` + `ReadWritePaths`, Limits), Pfad-Anpassung in `deploy/*.sh`/`upload.ps1`. Sol schreibt Units + Migrationsanleitung; **VPS-Migration = menschlicher Deploy-Schritt (Tor T1).** | M | — | ✅ |
| W0.4 | **Paket A Konfig/Flags** | Typisierte Settings-Klasse um `config.py`, Modusvalidierung beim Start, Start-Verweigerung bei riskanter Fehlkonfig. | M | — | ✅ |

## W1 — Risk-Wiring (Kern-Sicherheitswelle) — ✅ ERLEDIGT (`5aa6ece`)

> Alle sechs Tasks (W1.1–W1.6) gebaut, reviewt, gemergt, gepusht. Detail-Status siehe oben.
> Die Tabelle unten ist die ursprüngliche Planung (historisch).

Höchster Sicherheitsnutzen pro Aufwand: Logik existiert und ist getestet, es fehlen nur die Inputs.

| # | Task | Scope | Aufwand | Abh. | Parallel? |
|---|---|---|---|---|---|
| W1.1 | **Risk-Context-Loader für OMS** | `context_loader`, der pro Intent liefert: `realized_pnl_today`, `account_value`, `open_position_count`, `has_existing_ticker_position`, `buying_power`, `broker_status`, `risk_profile`, `entry_price`/`stop_price` (Sizing), `candidate_notional`. Verdrahtung in bot.py + webapp.py (ein Seam für beide). | M | W0.4 hilfreich | Seriell (Kernstück) |
| W1.2 | **Quote-Frische im Orderpfad** | Alpaca-Quote bei Accept ziehen, `data_quality.check_quote_age`/`check_spread` in den Risk-Context; veraltete Quotes blockieren Orders (Gate-P2-Kriterium). | M | W1.1 | Seriell nach W1.1 |
| W1.3 | **Kill-Switch persistent + UI** | `KillSwitchService` an DB-Seam binden (überlebt Neustart), in Risk-Context einspeisen, Telegram-Befehl + Web-Schalter (global/user), Schutz-Exits bleiben erlaubt. | M | eigene Tabelle | ✅ parallel zu W1.1 |
| W1.4 | **Audit-Log persistent** | `AuditLog` an DB binden (append-only Tabelle), jede Brokeraktion (OMS-Submit, Exit, Cancel) erzeugt Event mit Trace-ID → schließt Gate P1.1. | M | — | ✅ |
| W1.5 | **Post-Trade-Scan** | Periodischer Job: `post_trade_risk.check_open_position_has_protective_order` über alle offenen Positionen, Alarm via bestehendem Notifier. | S | — | ✅ |
| W1.6 | **Deterministik-Beweis** | Test: gleiche Inputs → gleiche `RiskDecision`; Test "keine Order umgeht Risk Service" (Import-/Grep-Guard). | S | W1.1 | Seriell (Abschluss) |

**Abschluss:** Gate P3 + Gate P1.1 + "veraltete Quotes blockieren Orders" (P2). W1.1/W1.2 als ein
Sol-Strang, W1.3/W1.4/W1.5 als drei parallele Stränge. **Deploy der W1-Ergebnisse erst nach 2–3
stabilen Postgres-Markttagen bündeln.**

## W2 — OMS-Live-Orchestrierung (Gate P4) — ✅ ERLEDIGT (`0ce4a65`)

> Alle vier Tasks (W2.1–W2.4) gebaut, reviewt, gemergt, gepusht. Detail-Status siehe oben.
> Die Tabelle unten ist die ursprüngliche Planung (historisch).

| # | Task | Scope | Aufwand | Abh. | Parallel? |
|---|---|---|---|---|---|
| W2.1 | **Broker-Event-Ingestion** | Polling-Loop (oder TradingStream) → `broker_event_worker.process_broker_event`; Orderstatus = Brokerereignisse end-to-end, Events in `order_events`. | M | W1 deployt | Seriell (Basis) |
| W2.2 | **Reconciliation-Scheduler + Alarm** | `run_periodic_reconciliation` (5–15 min) + täglicher Voll-Abgleich in den Scheduler; Findings → Telegram-Admin-Alarm. | M | — | ✅ parallel zu W2.1 |
| W2.3 | **Partial-Fill-Orchestrierung** | `decide_partial_fill_action` ausführen (Schutzorder in Fillgröße, Restorder-Timeout-Cancel), keine doppelte Schutzorder. | M | W2.1 | Seriell |
| W2.4 | **E2E-Doppelklick-Beweis** | Integrationstests über die echten UI-Pfade (Web-Doppel-POST, Telegram-Doppel-Callback) → genau eine Order. | S | W2.1 | Seriell |

## W3 — Daten & Versionen (Gates P2/P5/P6 final; nach Tor T0)

| # | Task | Scope | Aufwand | Abh. | Parallel? |
|---|---|---|---|---|---|
| W3.1 | **PLAT-001 Scheibe 9** | Dual-Backend-Seam-Code entfernen, Postgres-only. Zuerst, damit W3.3/W3.4 nicht doppelt gepflegt werden. | S | Tor T0 | Seriell (zuerst) |
| W3.2 | **yfinance raus aus Prod-Signalpfad** | `evaluator`/`analyzer`/`smartmoney`/`watchlist`/`db`-Kursabrufe auf `MarketDataProvider` umstellen (Alpaca für Prod, yfinance nur Research). Größter Brocken. | L | W3.1 | ✅ eigener Strang |
| W3.3 | **Strategieversion an jedes Signal** | `StrategyVersionRegistry` persistent; Live-Signalerzeugung schreibt `strategy_version_id` → Gate P5. | M | W3.1 | ✅ |
| W3.4 | **Mode-Reports in Dashboards** | Legacy-Dashboards auf `build_mode_report` umstellen, Paper/Shadow getrennte Ansichten → Gate P6 + RES-002. | M | W3.1 | ✅ |
| W3.5 | **Shadow-Scheduler + Rohdatenarchiv-Metadaten** | Regelmäßige Shadow-Signalerzeugung; `RawDataArchiveEntry` via db-Seam persistieren (ehem. `[!]`, jetzt entblockt). | S | W3.1 | ✅ |
| W3.6 | **Exit-Policies verdrahten** | `evaluate_strategy_exit` in `evaluate_active_trade` — **ändert Live-Trade-Verhalten → menschliche Freigabe vor Deploy (Tor T2).** | M | W3.1 | ✅ (Deploy gated) |

## W4 — Observability & Platform (Rest P9; parallel zu W5)

| # | Task | Aufwand | Parallel? |
|---|---|---|---|
| W4.1 | PLAT-004 strukturiertes JSON-Logging (trace_id, pseudonymisierte user_id, keine PII/Keys) — **✅ ERLEDIGT** (`logging_setup.py`, LOG_FORMAT opt-in, HMAC-Pseudonym., Redaction) | M | ✅ |
| W4.2 | PLAT-005 Metriken + Alarmregeln (Quote-Alter, Reject/Fill-Rate, Reconciliation-Fehler, Positionen ohne Stop, Kill-Switch-Status) — **✅ ERLEDIGT** (`core/metrics.py` Registry + `core/alerts.py`, 9 Kennzahlen, schlanke Emits an OMS/Reconcile/Poll/Kill-Switch/Post-Trade/Heartbeat; Quote-Age-Emit try/except-gehärtet) | M/L | ✅ (nutzt W1.5/W2.2) |
| W4.3 | PLAT-006b Secrets (systemd-Credentials, Rotation, kein Secret in Logs) — **✅ ERLEDIGT** (`_secret()` Präzedenz cred>env>.env, Settings-Maskierung, LoadCredential-Unit-Vorlage) | M | ✅ |
| W4.4 | PLAT-007 Alpaca OAuth (Scopes, Token verschlüsselt, Revoke, Paper/Live getrennt) — **✅ ERLEDIGT** (`broker_oauth_connections`-Tabelle, Alembic `e5f6a7b8c9d0`; `execution/broker_oauth.py` additiv/opt-in, Fernet-verschlüsselte Token, Paper/Live getrennt, injizierbarer Revoke; API-Key-Pfad+TSAFE unberührt; Manager-implementiert) | L | ✅ |
| W4.5 | Pakete B/C/D (Domain Events, Outbox, Notifications als Consumer) — **✅ ERLEDIGT** (`core/events.py` versioniert, `core/outbox.py` Worker mit Retry/Dead-Letter/Backlog + `outbox_events`-Tabelle Alembic `f6a7b8c9d0e1`, `core/event_consumers.py` DedupConsumer + NotificationConsumer; seam-first, noch nicht verdrahtet; Manager-implementiert) | L | ✅ (füttert W4.2-Alarme) |

## W5 — Backtest-Härtung (Gate P7; Research-Strang, parallel zu W2–W4)

Blockiert nicht den Paper-Burn-in, wohl aber Canary. Kann früh parallel anlaufen.

| # | Task | Aufwand | Parallel? |
|---|---|---|---|
| W5.1 | Gemeinsamer Strategiecode + Clock/Data-Abstraktion (Backtest nutzt Prod-Strategiemodule) — **✅ ERLEDIGT** (yfinance raus aus engine.py, `MarketDataProvider`+`BarClock` injizierbar; Strategiecode war schon geteilt) | L→S | Seriell (Fundament) |
| W5.2 | Multi-Timeframe-Korrektheit / kein Look-ahead (Tests) — **✅ ERLEDIGT** (kein Look-ahead-Bug gefunden; Future-Perturbations-Test + „keine Entscheidung/Entry auf letzter offener Tages-Bar"; 1d-only, kein Resampling) | M | nach W5.1 |
| W5.3 | RES-004 Kostenmodell (Kommission/Spread/Slippage/SEC/FINRA/Teilfüllung/Market-Impact) — **✅ ERLEDIGT** (`backtest/cost_model.py` CostModel+CostBreakdown, injizierbar, Default bitgleich zum alten 2×cost_pct) | M | ✅ parallel zu W5.2 |
| W5.4 | Universen point-in-time + Survivorship-Messung — **✅ ERLEDIGT** (`backtest/universe_history.py`: UniverseSnapshot versioniert, `members_as_of`, `measure_survivorship_bias`; Seam, Live-Universum unberührt, Datenlücke dokumentiert; Manager-implementiert) | L | ✅ |
| W5.5 | RES-003 Reproduzierbarkeit (Run-ID, Commit, Seed, Deps) — **✅ ERLEDIGT** (`backtest/reproducibility.py` RunMetadata + deterministische run_id; opt-in in run_backtest; **vom Manager selbst implementiert, Sol rate-limited**) | M | ✅ |
| W5.6 | Validierung (Nested Walk-forward, Purging, Embargo, Holdout-Sperre, Bootstrap, Regime) — **✅ ERLEDIGT** (`backtest/validation.py`: walk_forward/nested, Embargo=Purging, HoldoutGuard, geseedetes Bootstrap-KI, Regime, Sensitivität; Manager-implementiert) | L | nach W5.2/W5.5 |

## W6 — Labor begrenzen (Gate P8; nach W5)

RES-006 Champion/Candidate (M) → Suchraumgrenzen/LLM-Validierung (M, parallel) → Pending-Workflow +
RES-005 Holdout-Schutz (M, parallel) → Promotion-Gates (S, seriell zuletzt). **Jede Promotion =
menschliches Tor T3, kein Sol-Task.**

## W7 — UI/Design & Querschnitt (parallel zu W5/W6)

Style-Phase 2 Komponenten (M, ✅ pro Komponente parallel) → Style-Phase 3 Seiten + Web-App-Umbau inkl.
Pflicht-Bestätigungsdialog (L) → Style-Phase 4 Risikointeraktionen inkl. Risk-Profile-Editor (M, braucht
W1.3/RiskProfile-DB) → Style-Phase 5 A11y (M). Telegram-Umbau (Callback-Sicherheit; M) und API v1
(Idempotency-Header, RBAC; M) als eigene parallele Stränge.

## W8 — Test & Paper-Freigabe (Gate P10)

Unit/Integration/Replay/Failure-Injection-Suiten (je M, ✅ 4 parallele Sol-Stränge) + **Paper-Burn-in =
Kalenderzeit** (mehrere Marktwochen, ≥1 Feiertag — Labor Day 2026-09-07 als natürlicher Kandidat),
Fehlerquote dokumentieren. **Go/No-Go = menschliches Tor T5.**

---

## Kritischer Pfad

**~~W1 (Risk-Wiring) → W2 (OMS-Orchestrierung)~~ ✅ erledigt → W3.1→W3.2 (yfinance-Ablösung, gated auf
Tor T0) → W8 (Burn-in-Kalenderzeit).** Alles andere (W4, W5–W6, W7) hängt parallel daneben. Der Burn-in
kann formal erst "zählen", wenn W1–W3 **deployt** sind — Deploy von W1+W2 ist freigabe-pflichtig
(Live-Trade-Verhalten) und erst nach 2–3 stabilen Postgres-Markttagen sinnvoll.

## Quick Wins (größter Nutzen pro Aufwand)

1. **W0.1 Postgres-Backup** — Prod-DB ist Quelle der Wahrheit und ungesichert. Größtes reales Risiko.
2. **W0.2 Deps pinnen** — S-Aufwand, schließt bekanntes Supply-Chain-/FD-Leck-Risiko.
3. **W1.1 Risk-Context-Loader** — schaltet ~10 fertige, getestete Risk-Checks scharf; fast reine Verdrahtung.
4. **W1.5 Post-Trade-Scan** — S-Aufwand, erkennt ungeschützte Positionen (P9-Kriterium nebenbei).

## Menschliche Entscheidungs-Tore (keine Sol-Tasks)

| Tor | Wann | Inhalt |
|---|---|---|
| T0 | nach ~3–5 Markttagen | Postgres-Stabilität bestätigen → Scheibe 9 (W3.1) frei |
| T1 | W0.3 | VPS-Migration auf Nicht-Root-User durchführen/abnehmen |
| T2 | W3.6 | Freigabe Exit-Policies (ändert Live-Trade-Verhalten) |
| T3 | laufend ab W6 | Jede Strategie-Promotion (Gate P8) |
| T4 | **jetzt anstoßen** | **Regulatorische Einordnung** (extern, lange Vorlaufzeit — blockiert P11/P12) |
| T5 | Ende W8 | Paper-Go/No-Go-Abzeichnung |
| T6 | P11 | Canary-Live-Entscheidung (separat) |

## Was jetzt (Stand 2026-07-16, nach W0–W2)

W0–W2 sind erledigt (Gates P1.1/P2-Quote/P3/P4 geschlossen), alles auf GitHub `main`, **nichts
deployt**. Der kritische Pfad hängt jetzt an **menschlichen Entscheidungen**:

1. **Tor T0** — nach ~3–5 stabilen Postgres-Markttagen (seit Cutover 2026-07-15) Stabilität bestätigen
   → entblockt **W3.1 (Scheibe 9)** und damit die ganze Welle W3.
2. **Deploy-Freigabe W1+W2** — ändert Live-Trade-Verhalten (Risk-Gates scharf, Kill-Switch, broker-
   seitige Partial-Fill-Stops). Vor Deploy Default-`RiskProfile` fürs Paper-Setup (5× Hebel, max. 5
   Positionen) prüfen; dann gebündelt deployen, erst nach den Burn-in-Tagen.
3. **Ohne Gate (2026-07-16/17 bearbeitet):** **W5 (Backtest-Härtung) KOMPLETT** (W5.1–W5.6, Gate P7);
   **W4 zu großen Teilen** (W4.1 Logging, W4.2 Metriken, W4.3 Secrets ✅; offen: W4.4 OAuth, W4.5 Pakete).
   Alles auf GitHub `main`, NICHT deployt. Sol ab 2026-07-17 rate-limited → Rest Manager-implementiert.
4. **T4 (regulatorische Einordnung)** weiter offen — extern/langläufig, blockiert P11/P12.

Offene Nebenbefunde: vorbestehender `has_trade_today`-Datums-/Zeitzonen-Bug (eigener Debug-Task,
`test_trade_read_mapping_order_and_day_contract[sqlite]`); `_audit_contexts`-Mini-Leak im OMS-Singleton.
W0-Rest (menschlich): VPS-Migration stockbot-User (Tor T1), Backup-Timer + age-Key aktivieren.

## Kritische Dateien für die Umsetzung

- `stockbot/execution/oms.py` — Risk-Context-Einspeisung, Kern von W1
- `stockbot/core/risk.py` — `pretrade_check` (alle Checks, brauchen nur Inputs)
- `stockbot/tgbot/bot.py` — OMS-Callsites, Scheduler-Jobs für W1.5/W2
- `stockbot/web/webapp.py` — OMS-Callsite Web, Kill-Switch-/Risk-UI
- `stockbot/core/allocator.py` — Slot-Auswahl (round-robin über Strategien, respektiert `max_open_positions`)
- `stockbot/broker/client.py` — Notional/Bruchteil-Orders, **kein Bracket** (Bot managt SL/TP selbst)
- `deploy/stockbot.service` — W0.3 systemd-Härtung (Vorlage für dashboard.service)

## Anmerkung Positions-Sizing (aus Diskussion 2026-07-15)

Bei kommissionsfreiem Alpaca-Handel ist eine kleine feste Positionsgröße (z. B. 50 €) **keine
Kostenfalle** — Spread/Slippage sind prozentual und größenunabhängig. Die eigentliche Einschränkung
fixer Größen: sie umgehen das **risikobasierte Sizing** (`risk.py` sizing/`risk_amount`), sodass jeder
Trade real unterschiedlich viel riskiert. Empfohlen: **Risiko-Budget pro Trade** (z. B. 1 % des Kontos)
statt fixer Notional-Größe → gibt dem Risk Service (W1) den Sizing-Hebel zurück. Selektion sollte
weiter über **Qualitätsschwelle (`MIN_SIGNAL_STRENGTH`, aktuell 55) + Diversifikation (Allocator) +
validierten Score (erst nach Phase 7)** laufen, nicht über reines "nur höchste Scores".
