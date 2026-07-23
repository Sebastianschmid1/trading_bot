# Umsetzungsplan — trading_bot (Wellen ab 2026-07-15)

> Sequenzierter Fahrplan für das offene Backlog aus [PLAN_CHECKLIST.md](PLAN_CHECKLIST.md).
> Erstellt am 2026-07-15 (Fabel-Plan-Architekt, gegen den echten Code kalibriert).
> **Arbeitsmodell:** Engineering-Manager (Claude) plant/reviewt, alle Coding-Aufgaben gehen an
> parallele **Claude-Implementierungs-Subagenten** (Agent-Tool, isolierter Worktree je Task).
> Unabhängige Pakete → mehrere Subagenten gleichzeitig. (Ältere Einträge nennen die Rolle
> „Sol"/Codex CLI — historisch; seit 2026-07-22 sind es Claude-Subagenten.)

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
- **DEPLOY 2026-07-19 (Sonntag, Markt zu): VPS auf `4b99f65` — W0–W7-Backend inkl. GANZ W3 ist
  jetzt PRODUKTIV.** Ablauf: `pg_dump`-Backup (`/root/backups/stockbot_pre_w1-w7_20260719_1317.sql.gz`),
  ff-merge, `alembic upgrade head` (8 Migrationen sauber, Head `c9d0e1f2a3b4`), restart. Smoke grün:
  Service aktiv, alle neuen Scheduler-Jobs registriert (shadow_signals, broker_order_poll,
  post_trade_risk_scan, periodic_oms_reconciliation, daily_lab_optimization), Telegram ok,
  Dashboard 200, keine Warnungen. `STRATEGY_EXITS_ENABLED` nicht gesetzt → Default AUS (Tor T2 zu).
  Ältere „NICHT deployt"-Vermerke in den Wellen-Abschnitten unten sind damit historisch.
  **Montag (erster Markttag) beobachten:** Risk-Gates scharf (Ablehnungen mit Default-RiskProfile
  möglich), Signalpfad jetzt Alpaca statt yfinance.
- **Deploy-Historie (eine Zeile, damit die Commit-Angaben unten nicht auseinanderlaufen):**
  2026-07-19 `4b99f65` (W0–W7-Backend) → am selben Tag `3469052` (Telegram-Hauptmenü) →
  2026-07-20 `47672dc`/`f601184` (W7-Visual + W8-Suiten) → 2026-07-20 abends `8d16547`
  (Nebenbefund-Fixes `cf3074a`) → 2026-07-21 `3c56f87`/`d107f97` (systemd-Credentials +
  Betreiber-Key-Trennung + Log-Härtung) → `0104d94` (W4.5-Outbox) → 2026-07-23 `b531b33`
  (tz-aware/naive-Bugfix + Betriebsmodell-Doku Codex→Claude-Subagenten) → **2026-07-23 `719c1af`
  (Auto-Accept-Anti-Spam außerhalb der regulären Sitzung). Maßgeblich ist immer der letzte Eintrag.**
- **Prod-Befund 2026-07-20 „keine Marktdaten" — BEHOBEN am selben Abend.** Auf dem VPS fehlten
  `ALPACA_API_KEY`/`ALPACA_API_SECRET`; seit W3.2 ist der Signalpfad Alpaca-only, also lief
  jede Minute „Bars … nicht abrufbar" und es entstanden **0 Orders seit dem 19.07-Deploy**
  (davor 11 in 7 Tagen) — der Paper-Burn-in sammelte keine Evidenz.
  **Lösung: systemd-Credentials statt `.env`** (PLAT-006b/W4.3 wird damit erstmals produktiv
  genutzt): `/etc/stockbot/credentials/*.cred` via `systemd-creds encrypt`, geladen über das
  Drop-in `deploy/stockbot-credentials.conf` → `/etc/systemd/system/stockbot.service.d/`.
  `config._secret()` liest sie aus `$CREDENTIALS_DIRECTORY` (Präzedenz Credential > Env > .env).
  Verifiziert: Warnungen weg, `get_quote("AAPL")` und `get_bars` liefern echte Daten.
  **Bewusste Trennung:** der hinterlegte Key ist ein **Betreiber-Datenzugang** für den globalen
  Scan, NICHT ein Trading-Key; Nutzer-Broker-Credentials bleiben pro Nutzer in der DB.
- **Sicherheitsfolge des globalen Keys — GESCHLOSSEN (2026-07-21, `d107f97`, deployt).**
  `config.ALPACA_ENABLED` wurde durch die Credentials `True`; darüber lieferte
  `_alpaca_ready(user)` für **jeden** Nutzer `True` und `_alpaca_client(user)` fiel ohne eigene
  Credentials auf `broker._get_client()` (Betreiber-Key) zurück — ein Nutzer ohne eigene
  Anbindung hätte „echte Broker-Order" aktivieren und über das Betreiberkonto handeln können
  (real betroffen: 0). Jetzt: **Order-Ausführung ausschließlich mit eigenen Nutzer-Credentials**
  in `web/webapp.py` und `tgbot/bot.py`; `_alpaca_keys` (Options-**Marktdaten**) behält den
  globalen Rückfall bewusst. Regressionstests: `tests/test_broker_key_isolation.py` (4).
  Zwei Alt-Tests kodierten das alte Modell und wurden auf die neue Regel gezogen.
  **Live-Verhalten geändert: restriktiver, kein neuer Handelspfad.**
- **Secret-Leck im Journal — GESCHLOSSEN (2026-07-21, `d107f97`, deployt).** Das
  Telegram-Bot-Token stand im Klartext in jeder Log-Zeile (httpx loggt die volle URL auf INFO,
  das Token ist Teil des Pfades; gemessen 60 Zeilen/10 min). Zusätzlich lief `_redact` **nur**
  im `JsonFormatter` — Produktion nutzt das Textformat, dort wurde nie etwas geschwärzt. Jetzt
  schwärzt ein `RedactingFilter` formatunabhängig am Handler (Muster für `/bot<id>:<secret>`),
  httpx/httpcore zusätzlich auf WARNING. **Verifiziert: 0 Treffer in 90 s nach Neustart.**
  ⚠️ **Offen (menschlich): Token rotieren** — er steht weiterhin in den alten Journal-Daten.
- **Prozess-Härtung aktiv (2026-07-21):** `deploy/stockbot-hardening.conf` →
  `/etc/systemd/system/stockbot.service.d/hardening.conf`: `NoNewPrivileges`, `PrivateTmp`,
  `PrivateDevices`, `ProtectSystem=strict` + `ReadWritePaths`, `ProtectKernel*`,
  `RestrictSUIDSGID/Namespaces/Realtime`, `LockPersonality`. Vorher stand **alles** davon auf
  `no`. `ProtectHome` und der unprivilegierte Nutzer bleiben Tor T1 (Dienst läuft als root aus
  `/root/stockbot`). Nach Neustart keine Permission-Fehler, alle 11 Scheduler-Jobs da.
- **Postgres-Gegenverifikation (2026-07-21):** Die 10 Contract-Tests, die lokal mangels Postgres
  skippen, liefen erstmals gegen die echte Instanz (`tests/test_db_backend_users.py`: 47 passed).
  Zusätzlich Read-only-Smoke aller selten benutzten Auswertungs-Abfragen (`burn_in_order_stats`,
  Audit-Log, Shadow-Snapshots, Strategieversionen, Order-Events, Dashboard-Aggregat inkl.
  `mode_reports`) — **keine weiteren Typvertrag-Verstöße**. Strategieversionen `standard`/
  `bb_revert`/`ai_adaptive` lösen korrekt auf.
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

**⚠️ Codex/Sol-Usage-Limit erreicht (2026-07-17, historisch)** — der damalige Codex-Worker
konnte bis zum Reset keine neuen Coding-Tasks fahren. **Nutzer-Override für jene Session: der
Manager implementierte die restlichen ungateten Tasks selbst** (mit Tests + Review-Sorgfalt).
*(Obsolet seit dem Wechsel auf Claude-Subagenten 2026-07-22 — kein externes Usage-Limit mehr.)*
Manager-implementiert: **W5.5 ✅** (`a1d74a7`), **W5.4 ✅** (`05a17fd`), **W5.6 Validierung ✅**
(`d1f53d4`) → **ganz W5 fertig (Gate P7)**, **W4.4 Alpaca-OAuth-Seam ✅** (`2bb1636`, Alembic-Head
`e5f6a7b8c9d0`), **W4.5 Pakete B/C/D ✅** (`85ecf6c`, Alembic-Head `f6a7b8c9d0e1`) → **ganz W4 komplett**.
**W6 Labor ✅** (`43c9b67`, Gate P8, Framework). **W7 (UI/Design) GESTARTET** — Style-Phase 2
Kernkomponenten (`static/components.css` + Makros, `bf4d593`) fertig + Gallery zur Abnahme.
**W7 ABGENOMMEN (2026-07-18):** Der Nutzer hat das Komponentensystem (Gallery) + die backend-
testbaren Teile abgenommen: Style-Phase-2-Komponenten (`components.css` + Makros, `bf4d593`),
**Telegram-Callback-Sicherheit** (`callback_tokens`-Tabelle Alembic `a7b8c9d0e1f2`, opaque/
nutzergebunden/einmalig, `85b160e`), **API v1** (`web/api_v1.py`: Idempotency-Header, RBAC,
Pydantic, Trace-ID je Antwort, `bb3f36e`). **Verbleibender Integrationsschritt (kein neuer Design-
Entwurf mehr):** die bestehenden Seiten-Templates schrittweise auf die abgenommenen Komponenten
umziehen (Style-Phasen 3–5 inkl. Pflicht-Bestätigungsdialog + A11y) und die Seams (`api_v1_router`
→ `webapp.py`, `callback_security` → `bot.py`-Handler) einhängen — am laufenden App-Stand,
verifiziert im Browser. **W1/W2/W4/W5 Deploy weiterhin gebündelt freigabe-pflichtig; auf
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
| **W3** Daten & Versionen | yfinance raus aus Prod-Pfad, Strategieversion je Signal, Mode-Dashboards, Scheibe 9 | **P2, P5, P6** | ✅ code-komplett (Tor T0 ✅; W3.1 ✅; **W3.2 ✅ [P2]; W3.3 ✅ [P5]; W3.4 ✅ [P6/RES-002]; W3.5 ✅; W3.6 Exit-Policies ✅ CODE [Flag Default AUS, Aktivierung = Tor T2]**) |
| **W4** Observability & Platform | JSON-Logging, Metriken/Alarme, Secrets, OAuth | **P9** Rest | ✅ erledigt (W4.1–W4.5); Secrets-Pfad und Outbox seit 2026-07-21 tatsächlich **in Betrieb**, nicht nur gebaut |
| **W5** Backtest-Härtung | gemeinsamer Strategiecode, Kostenmodell, Validierung, Reproduzierbarkeit | **P7** | ✅ erledigt (W5.1–W5.6 komplett; Gate P7 im Wesentlichen erfüllt) |
| **W6** Labor begrenzen | Champion/Candidate, Promotion-Gates, Holdout-Schutz | **P8** | ✅ erledigt (`research/lab.py` Framework, Gate P8; reale Promotion = Tor T3; Manager-implementiert) |
| **W7** UI/Design & Querschnitt | Style-Phasen 2–5, Web-/Telegram-Umbau, API v1 | **Gate Style** | ✅ **erledigt** (2026-07-20): Komponentensystem `bf4d593` + Callback-Sicherheit `85b160e` + API v1 `bb3f36e` (abgenommen 2026-07-18), Seams verdrahtet `1867469`, Style-Phasen 3–5 + Mode-Report-Panel `48fc42c`. **Stylekonzept-Audit v1.1 (2026-07-22, `docs/Stylekonzept.md` §32):** Tokens/Kernkomponenten bestätigt 1:1, Kontrast WCAG-AA verifiziert; Style-Rest-Tasks s. „Was jetzt" Punkt 1b |
| **W8** Test & Paper-Freigabe | Testsuiten + Paper-Burn-in + Go/No-Go | **P10** | ✅ code-komplett (`5d38d5d`: Replay-, Failure-Injection-Suite, `core/burn_in.py`, `docs/GO_NO_GO.md`) — offen: **Burn-in-Kalenderzeit + Tor T5** |

---

## W0 — Betriebsschutz — ✅ ERLEDIGT (`c0e43bd`)

> Alle vier Tasks gebaut, reviewt, gemergt, gepusht. Detail-Status siehe oben (Projektstand).
> Die Tabelle unten ist die ursprüngliche Planung (historisch).

Kein Eingriff in den Trading-Codepfad → stört den Postgres-Burn-in nicht.

| # | Task (Subagent-tauglich) | Scope | Aufwand | Abh. | Parallel? |
|---|---|---|---|---|---|
| W0.1 | **PLAT-009 Postgres-Backups** | `pg_dump` verschlüsselt (age/gpg) per Timer, Aufbewahrungsplan, Restore-Test-Skript + dokumentierter Test gegen Wegwerf-DB. **Höchste Dringlichkeit: Prod-DB hat aktuell kein Backup.** | M | — | ✅ |
| W0.2 | **PLAT-006a Deps pinnen** | `requirements.lock` (pip-tools/freeze), `pip-audit`, yfinance-tz-Cache-FD-Leck prüfen (todo.md A2), Dependabot-Konfig. | S | — | ✅ |
| W0.3 | **PLAT-008 systemd-Härtung** | Units umschreiben (eigener User `stockbot`, `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict` + `ReadWritePaths`, Limits), Pfad-Anpassung in `deploy/*.sh`/`upload.ps1`. Der Subagent schreibt Units + Migrationsanleitung; **VPS-Migration = menschlicher Deploy-Schritt (Tor T1).** | M | — | ✅ |
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
| W3.1 | **PLAT-001 Scheibe 9** | **✅ ERLEDIGT (`cba7380`, Scope A):** Produktion Postgres-only erzwungen (`settings.assert_postgres_backend` an bot.main/dashboard.run, `ALLOW_SQLITE_RUNTIME`-Opt-in). Dual-Backend-Seam bleibt bewusst fürs Offline-Test-Harness (Scope vom Nutzer gewählt). | S | Tor T0 ✅ | Seriell (zuerst) |
| W3.2 | **yfinance raus aus Prod-Signalpfad** | **✅ ERLEDIGT (5 Commits):** Provider-Seam `provider_factory.get_signal_provider()` (Alpaca, nie yfinance, degradiert sauber ohne Keys) + `MarketDataProvider.get_bars_batch` (yfinance-förmig normalisiert). Umgestellt: `analyzer._download_all_timeframes` (Signal-Indikatoren), `evaluator` (Kurs+Tagesspanne), `db`-Aktivierungskurs (via Alpaca-Adapter, `db.yf`-Test-Naht bewahrt). **Smart-Money/Lookup/LLM-Ranker/`factor_history`/`price_history_batch` als Research/Anzeige klassifiziert** (yfinance dort bewusst erlaubt — Alpaca hat keine Eigentümer-/Insider-/Fundamentaldaten; erzeugen nie allein einen Trade). Deterministischer Preissignalpfad = Alpaca-only. **Gate P2 erfüllt.** 312 gezielte Tests grün. | L | W3.1 | ✅ eigener Strang |
| W3.3 | **Strategieversion an jedes Signal** | **✅ ERLEDIGT:** Tabelle `strategy_versions` (SCHEMA_SQL + Alembic `b8c9d0e1f2a3`, append-only, idempotent per content_hash); `db.publish/get/resolve_strategy_version_id` + `ensure_strategy_versions_published` (bootet die 3 produktiven V1-Strategien). `add_pending` stampt jedes Signal mit `strategy_version_id`; Bootstrap am Start. **Gate P5 erfüllt.** | M | W3.1 | ✅ |
| W3.4 | **Mode-Reports in Dashboards** | **✅ ERLEDIGT (Daten-/Report-Ebene):** Adapter `core/mode_reporting.py` → `build_mode_report` (Paper aus Legacy-Trades, Shadow separat, strukturell nie vermischt); `build_dashboard_data` exponiert `mode_reports` (paper/shadow) im Dashboard-JSON → **Gate P6/RES-002 Mode-Isolation erfüllt**. Frontend-Panel-Render = Teil der Visual-Integration (Browser, mit W7-Visual gebündelt). | M | W3.1 | ✅ |
| W3.5 | **Shadow-Scheduler + Rohdatenarchiv-Metadaten** | **✅ ERLEDIGT:** Tabellen `raw_data_archive` + `shadow_snapshots` (Alembic `c9d0e1f2a3b4`); `db.record_raw_data_archive_entry`/`record_shadow_snapshot`/`get_shadow_snapshots` (idempotent, Mode-isoliert); `research/shadow_scheduler.py` als marktzeit-gated `run_repeating`-Job; Dashboard-Shadow-Report liest persistierte Snapshots. | S | W3.1 | ✅ |
| W3.6 | **Exit-Policies verdrahten** | **✅ CODE ERLEDIGT (deploy-/aktivierungs-gated):** `evaluate_strategy_exit` (STRAT-005) aus `evaluate_active_trade` aufgerufen, Bars über Prod-Signalprovider + `minutes_to_close`. Hinter Flag `STRATEGY_EXITS_ENABLED` (**Default AUS**) — Liquidation/SL/TP bleiben maßgeblich. **⚠️ Einschalten = Tor T2 (ändert Live-Trade-Verhalten), erst nach menschlicher Freigabe.** | M | W3.1 | ✅ Code (Aktivierung gated) |

## W4 — Observability & Platform (Rest P9; parallel zu W5)

| # | Task | Aufwand | Parallel? |
|---|---|---|---|
| W4.1 | PLAT-004 strukturiertes JSON-Logging (trace_id, pseudonymisierte user_id, keine PII/Keys) — **✅ ERLEDIGT** (`logging_setup.py`, LOG_FORMAT opt-in, HMAC-Pseudonym., Redaction) | M | ✅ |
| W4.2 | PLAT-005 Metriken + Alarmregeln (Quote-Alter, Reject/Fill-Rate, Reconciliation-Fehler, Positionen ohne Stop, Kill-Switch-Status) — **✅ ERLEDIGT** (`core/metrics.py` Registry + `core/alerts.py`, 9 Kennzahlen, schlanke Emits an OMS/Reconcile/Poll/Kill-Switch/Post-Trade/Heartbeat; Quote-Age-Emit try/except-gehärtet) | M/L | ✅ (nutzt W1.5/W2.2) |
| W4.3 | PLAT-006b Secrets (systemd-Credentials, Rotation, kein Secret in Logs) — **✅ ERLEDIGT** (`_secret()` Präzedenz cred>env>.env, Settings-Maskierung, LoadCredential-Unit-Vorlage) | M | ✅ |
| W4.4 | PLAT-007 Alpaca OAuth (Scopes, Token verschlüsselt, Revoke, Paper/Live getrennt) — **✅ ERLEDIGT** (`broker_oauth_connections`-Tabelle, Alembic `e5f6a7b8c9d0`; `execution/broker_oauth.py` additiv/opt-in, Fernet-verschlüsselte Token, Paper/Live getrennt, injizierbarer Revoke; API-Key-Pfad+TSAFE unberührt; Manager-implementiert) | L | ✅ |
| W4.5 | Pakete B/C/D (Domain Events, Outbox, Notifications als Consumer) — **✅ VERDRAHTET (2026-07-21)**: `OMS._emit_domain_event` reiht jeden Statuswechsel (submitted/filled/partial/rejected/cancelled) als versioniertes Event in die Outbox (fail-open — ein Outbox-Fehler bricht nie den Handelspfad), Scheduler-Job `outbox_delivery` (60 s) stellt an den neuen `ObservabilityConsumer` zu. **Bewusst NICHT der `NotificationConsumer`:** Telegram-Nachrichten verschickt `bot.py` weiterhin direkt am Handelspfad, ein zweiter Weg über die Outbox erzeugte Dubletten. Damit misst `burn_in.dead_letter_events` erstmals etwas Echtes. Tests: `tests/test_outbox_wiring.py` (5). Historie: bis dahin galt — (`core/events.py` versioniert, `core/outbox.py` Worker mit Retry/Dead-Letter/Backlog + `outbox_events`-Tabelle Alembic `f6a7b8c9d0e1`, `core/event_consumers.py` DedupConsumer + NotificationConsumer; Manager-implementiert). **Verifiziert 2026-07-21:** `outbox`/`events`/`event_consumers` kommen in `tgbot/`, `web/`, `execution/` **kein einziges Mal** vor — es wird nie ein Event erzeugt, nie zugestellt, und kein Scheduler ruft `deliver_due` auf. Folge: `burn_in.dead_letter_events` ist strukturell immer 0, das Gate-P10-Kriterium läuft ins Leere. **Offen: verdrahten (welche Events, welcher Consumer, Scheduler-Job) — bewusst nicht nebenbei gemacht, weil es den Live-Benachrichtigungspfad berührt.** | L | ⚠️ |

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
menschliches Tor T3, kein Subagent-Task.**

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

**~~W1 (Risk-Wiring) → W2 (OMS-Orchestrierung) → W3 (Daten & Versionen)~~ ✅ deployt (2026-07-19)
→ W8 Burn-in-Kalenderzeit (läuft) → Tor T5.** Alles andere (W4, W5–W6, W7 ✅) ist erledigt. Der
Burn-in zählt seit dem Deploy; ausgewertet wird er mit `stockbot/core/burn_in.py` gegen
`docs/GO_NO_GO.md`. W3.6 (Exit-Policies)
bleibt zusätzlich hinter `STRATEGY_EXITS_ENABLED` (Default AUS) — Einschalten = separate Tor-T2-Freigabe.

## Quick Wins (größter Nutzen pro Aufwand)

1. **W0.1 Postgres-Backup** — Prod-DB ist Quelle der Wahrheit und ungesichert. Größtes reales Risiko.
2. **W0.2 Deps pinnen** — S-Aufwand, schließt bekanntes Supply-Chain-/FD-Leck-Risiko.
3. **W1.1 Risk-Context-Loader** — schaltet ~10 fertige, getestete Risk-Checks scharf; fast reine Verdrahtung.
4. **W1.5 Post-Trade-Scan** — S-Aufwand, erkennt ungeschützte Positionen (P9-Kriterium nebenbei).

## Menschliche Entscheidungs-Tore (keine Subagent-Tasks)

| Tor | Wann | Inhalt |
|---|---|---|
| T0 | ~~nach ~3–5 Markttagen~~ | ✅ **abgenommen 2026-07-19** (Postgres ~4 Markttage stabil seit Cutover) → Scheibe 9 (W3.1) freigegeben+erledigt |
| T1 | W0.3 | VPS-Migration auf Nicht-Root-User durchführen/abnehmen |
| T2 | W3.6 | Freigabe Exit-Policies (ändert Live-Trade-Verhalten). **Code fertig, hinter `STRATEGY_EXITS_ENABLED`=false** — Tor T2 = Flag einschalten + deployen. |
| T3 | laufend ab W6 | Jede Strategie-Promotion (Gate P8) |
| T4 | **jetzt anstoßen** | **Regulatorische Einordnung** (extern, lange Vorlaufzeit — blockiert P11/P12) |
| T5 | Ende W8 | Paper-Go/No-Go-Abzeichnung |
| T6 | P11 | Canary-Live-Entscheidung (separat) |

## Was jetzt (Stand 2026-07-20, W7 komplett + W8 code-komplett)

**W0–W7 sind komplett** (Gates P1.1/P2/P3/P4/P5/P6/P7/P8-Framework/P9 geschlossen); der Backend-Stand
ist seit 2026-07-19 auf dem VPS deployt (`3469052`, Alembic `c9d0e1f2a3b4`). **W8 ist code-komplett**
— offen bleibt nur die Kalenderzeit und die menschliche Abzeichnung.

**2026-07-20 dazugekommen — und am selben Tag DEPLOYT (VPS auf `47672dc`, Alembic-Head unverändert `c9d0e1f2a3b4`, Backup `/root/backups/stockbot_pre_w7visual_w8_20260720_1159.dump`; Smoke grün: purge_callback_tokens-Job registriert, /api/v1/health 200 mit x-trace-id, /static/components.css 200, unauth /api/v1/signals 401):**

- **W7 abgeschlossen** (`1867469` Seams, `48fc42c` Visual): API-v1-Router + Trace-ID-Middleware in
  `dashboard.py`, Callback-Security in `bot.py` (opake Einmal-Tokens + Purge-Job), Style-Phasen 3–5
  auf den echten Seiten (`components.css` wird jetzt tatsächlich geladen, „Trade prüfen" statt grünem
  Kaufbutton, Pflicht-Bestätigungsdialog in fester Feldreihenfolge, Kill-Switch mit Zustands-Chip +
  Rückfrage, Skip-Link/Fokus/Bottom-Nav/44px-Touchziele), **Mode-Report-Panel Paper/Shadow im
  Dashboard** aus dem vorhandenen `/data`-JSON. Tests: `tests/test_web_style_phases.py`.
- **W8 code-komplett** (`5d38d5d`): Replay-Suite (`tests/test_replay_suite.py`, 7),
  Failure-Injection-Suite (`tests/test_failure_injection.py`, 11), Burn-in-Auswertung
  (`stockbot/core/burn_in.py` + `db.burn_in_order_stats`, `tests/test_burn_in.py`, 6) und die
  abzeichenbare Checkliste **`docs/GO_NO_GO.md`**.
- **Backup-Timer auf dem VPS aktiv** (W0-Rest): `pg-backup.timer`/`.service` installiert,
  `AGE_RECIPIENTS` gesetzt, Backup erzeugt **und restore-verifiziert** (`pg_restore --list`).

Was noch offen ist (Stand 2026-07-21 abends, VPS auf `d107f97`):

0. **Token rotieren** (menschlich, klein): Das Telegram-Bot-Token steht in den *alten*
   Journal-Daten im Klartext. Das Leck selbst ist geschlossen, der bereits exponierte Wert
   nicht. Optional zusätzlich `journalctl --rotate --vacuum-time=…` auf dem VPS.
0b. ~~W4.5 verdrahten oder streichen~~ → **erledigt 2026-07-21** (Events aus dem OMS, Zustelljob
   `outbox_delivery`, `ObservabilityConsumer`; kein doppelter Telegram-Versand). Offen bleibt die
   *fachliche* Frage, ob der direkte Nachrichtenversand später ganz auf die Outbox umziehen soll —
   das wäre eine Änderung am Benachrichtigungsverhalten und gehört separat entschieden.

1. **Visuelle Abnahme im Browser** (nur dort prüfbar): Mode-Report-Panel im Dashboard und der
   Pflicht-Bestätigungsdialog auf der Signalseite.
1b. **Style-Nacharbeit aus dem Stylekonzept-Audit v1.1** (`docs/Stylekonzept.md` §32, 2026-07-22).
   Das Konzept wurde gegen die W7-Umsetzung auditiert: Tokens (§27→`tokens.css`) und Kernkomponenten
   sind 1:1 umgesetzt, **Kontrast durchgehend WCAG-AA verifiziert** (nur `--text-disabled` fällt
   zulässig durch), der Locale-Widerspruch in §6.3 (Punkt/Komma gemischt) ist im Konzept gefixt. Als
   **normative Präzisierungen** neu bzw. offen (kleine, gut abgrenzbare Subagent-Tasks, „Gate Style"-Rest):
   (a) §32.3 Feed-Staleness dreistufig **an das Quote-Freshness-Gate (P2-quote) gekoppelt** —
   `veraltet` blockiert orderrelevante Buttons sichtbar; Zeitzonen immer beschriftet (Marktzeit `ET`
   vs. System `UTC`); (b) §32.4 Trade-Bestätigungsdialog: Fokus-Trap/ESC/Fokus-Rückgabe **und
   Anti-Fehlklick** (Confirm nicht initial fokussiert, nicht per Enter auslösbar) — betrifft
   Live-Sicherheit, vor Aktivierung eines Live-Pfads Pflicht; (c) §32.5 eigener „Daten unsicher"-Zustand
   (warning-Banner + disabled Controls, kein optimistischer Schätzwert); (d) §32.6 §9.1-Navigation auf
   die echten Routen mappen (`watchlist`/`history`/`backtest`/`lab` etc. dürfen nicht ungestylt
   bleiben); (e) §32.7 kategoriale, farbenblind-sichere Chart-Palette `--cat-1…6` (getrennt von
   Grün/Rot); (f) §32.8 gemeinsames matplotlib-Style-Mapping der Tokens, damit Backtest-/Report-PNGs
   nicht von den Web-Charts abweichen; (g) §32.9 ein Web↔Telegram-Glossar (DoD §30 verlangt
   Begriffs-Parität, Quelle fehlte). Kein Live-Trade-Verhalten außer (a)/(b), die **härten**. Reihenfolge:
   (a)+(b) zusammen mit der visuellen Abnahme (Punkt 1), Rest nach Bedarf.
2. **Tor T2 — W3.6 Exit-Policies aktivieren** (`STRATEGY_EXITS_ENABLED=true`). Code deployt, Flag AUS →
   ohne diese ausdrückliche Freigabe ändert sich nichts am Live-Trade-Verhalten.
3. **W8 Burn-in — Kalenderzeit. Zählt effektiv erst ab 2026-07-20 abends** (davor fehlten die
   Marktdaten-Credentials, es entstanden keine Signale/Orders — die Tage seit dem 19.07-Deploy
   sind für Tor T5 wertlos). Erster echter Markttag ist damit **Dienstag, 2026-07-21**.
   Danach mehrere Marktwochen inkl. ≥1 Feiertag (Labor Day 2026-09-07). Auswertung mit
   `burn_in.build_burn_in_report`, Abzeichnung nach `docs/GO_NO_GO.md` (**Tor T5**).
   *Am VPS gegenverifiziert 2026-07-20:* die Auswertung lief unter Postgres zunächst auf einen
   Typfehler (`created_at` ist TEXT, `datetime`-Parameter → `text >= timestamptz`); behoben in
   `burn_in._as_utc_text` samt Regressionstest — genau die Klasse Bug, die SQLite-Tests verbergen.
4. **Tor T1 (menschlich):** VPS-Migration auf `stockbot`-User/`/opt/stockbot` (docs/DEPLOY_HARDENING.md).
5. **T4 (regulatorische Einordnung)** — extern/langläufig, blockiert P11/P12.

**Nebenbefunde erledigt (2026-07-20, `cf3074a`, deployt am selben Tag mit `8d16547`):** alle drei
dokumentierten Altlasten behoben — (1) `db._today()` folgt jetzt UTC statt Server-Lokalzeit
(`trade_date` und `created_at` liefen auf Maschinen mit Offset nahe Mitternacht auseinander;
`_trade_age_days` in bot.py mitgezogen; VPS ist `Etc/UTC` → prod unverändert), (2) der
Signal-Ablaufcheck im OMS respektiert das `now` aus dem Risk-Kontext statt der Wanduhr
(Prod übergibt keins → unverändert), (3) `OMS._audit_contexts` wird beim Übergang in einen
Endzustand freigegeben (`is_terminal_order_status`, Cache ist read-through). Regressionstests
zu allen dreien; der Zeitvertrag-Test macht den alten `date.today()`-Code nachweislich rot.
**Volle Suite grün: 1048 passed + 80 Backtest-Tests.**

**tz-aware/naive-Bug behoben (2026-07-22, `57fad48`+`05a3125`, DEPLOYT 2026-07-23 auf VPS `b531b33`):**
Laufzeitfehler `Cannot compare tz-naive and tz-aware timestamps` — Provider-Swap-Fallout (W3.2).
Alpaca-Bars kommen tz-aware UTC, der „yfinance-förmige" Signalpfad + DB-Zeitvertrag erwarten aber
naive UTC. Fix am Choke-Point `market/data_providers.py::_normalize_alpaca_bars` (Index nach
naive UTC ziehen: `tz_convert("UTC").tz_localize(None)`) + Regressionstests (tz-aware→naiv, inkl.
MultiIndex). Zweitbefund: `tgbot/bot.py::_strategy_exit_reason` setzte `now` tz-aware → latenter
`now - opened_at`-Crash (hinter `STRATEGY_EXITS_ENABLED`=off); jetzt naive UTC. Umgesetzt per
Claude-Subagent (Worktree), Manager-reviewt, 74 gezielte Tests grün. **Deployt 2026-07-23**
(Backup `stockbot-20260723-114559.dump.age`, ff-merge, Restart, Smoke grün: 12 Scheduler-Jobs,
Health 200 mit x-trace-id, keine Fehler im Journal; Alembic-Head unverändert `c9d0e1f2a3b4`,
keine Migration/Deps). Final beweist sich der Fix im `intraday_signals`-Lauf ab Marktöffnung.

**Auto-Accept-Anti-Spam (2026-07-23, `8917b82`, DEPLOYT auf VPS `719c1af` — LIVE-VERHALTEN, freigegeben):**
Nutzeranforderung: Bei aktiviertem „automatische Trades annehmen" (`auto_accept`) sollen außerhalb
der regulären US-Sitzung KEINE Signal-Karten mehr in den Telegram-Chat gepusht werden (Spam, v. a.
via `EXTENDED_HOURS`-Intraday-Scan pre-market). Fix am Choke-Point `tgbot/bot.py::send_signal`:
neuer Guard `_suppress_auto_accept_out_of_session(auto_accept, _us_market_open(extended=False))` →
außerhalb der regulären Sitzung nur loggen + `return False` (kein Versand, kein Website-`notify`).
Kauf bleibt unverändert: der In-Sitzung-Auto-Accept-Zweig + der Eröffnungs-/Intraday-Scan bewerten
zur Öffnung frisch neu und kaufen dann automatisch. Nicht-`auto_accept`-Nutzer unverändert.
`tests/test_signal_suppression.py` (4) + Regression `test_signals`/`test_signal_retention` (21) grün.
**Deploy approval-gated** (ändert Live-Trade-/Benachrichtigungsverhalten).

W0-Rest (menschlich): VPS-Migration stockbot-User (Tor T1). Der Backup-Timer ist seit
2026-07-20 aktiv (PLAT-009 zu).

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
