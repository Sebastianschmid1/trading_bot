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
  (Auto-Accept-Anti-Spam außerhalb der regulären Sitzung) → 2026-07-23 `d4fc73e` (Style-Audit
  §32.3 + §32.4) → **2026-07-25 `af9e546` DEPLOYT: §32.5 + Testsuite-Hygiene/erste CI + UTC-Bugfix +
  6-Task-Audit-Abarbeitung (Charts/Glossar/Risiko-Wiring/Backtest/fail-closed) + PS-Lock. Maßgeblich
  ist immer der letzte deployte Eintrag = `af9e546`.**
- **Zuletzt (2026-07-27, DEPLOYT, VPS-main `324f52c`):** `5acf6cf` UI-Abstand
  „Position schließen"-Button (visuelle Abnahme) + `c1887c0` **Labor-Regressionsfix**: der
  `YFinanceResearchProvider` lieferte seit W5.1 (`0c877e8`, Umstellung von `yf.download` auf
  `yf.Ticker.history`) einen **tz-aware** Tages-Index → das Strategie-Labor crashte bei jedem Lauf mit
  `Cannot compare tz-naive and tz-aware timestamps` (der `daily_lab_optimization`-Cronjob war seit dem
  16.07. tot). Fix am Provider-Seam: neue Helferfunktion `_strip_tz_naive` (bewusst `tz_localize(None)`,
  erhält den lokalen Handelstag) in `get_bars`/`get_bars_batch` — analog zum Alpaca-Pfad
  (`_normalize_alpaca_bars`), damit beide demselben naiven Zeitvertrag folgen. `optimize/lab.py` selbst
  unangetastet. 5 neue Provider-/Regressionstests, gezielte Suites 80 passed. GitHub-`main` `3c4399d`.
  Deploy: kein Migration-/Dependency-Bedarf (Alembic-Head bleibt `d0e1f2a3b4c5`), Backup
  `stockbot_pre_labortzfix_20260727_1852.dump`, Smoke grün (Import-strip liefert naiv, Dashboard 200,
  Journal fehlerfrei).
- **⚠️ INFRA-BEFUND 2026-07-27 — VPS-`main` ≠ GitHub-`main` (divergiert an `af9e546`):** Der VPS
  `/root/stockbot` main trägt **15 zusätzliche „wedding"-Commits** (eine komplette Hochzeits-Foto-/
  Video-Galerie: `wedding.service`, eigene TLS-Domain/Caddy-Route, Gast-Zugang; HEAD vor diesem Deploy
  `30d8296`), die **nicht** auf GitHub-`main` liegen (nur als Branch `origin/claude/wedding-photo-gallery-upload-wokhvn`).
  Der VPS **kann nicht von GitHub fetchen** (`git@github.com: Permission denied (publickey)`), Deploy geht
  daher ausschließlich über „Branch von hier zum VPS-Repo pushen + `ssh` lokal mergen". **Folge für jeden
  künftigen Deploy: KEIN ff-merge — immer `git merge --no-ff` des gepushten Deploy-Branches in den VPS-main.**
  Trading- und Wedding-Dateien überschneiden sich nicht → Merge konfliktfrei; `systemctl restart stockbot`
  betrifft nur `run_bot.py`, nicht `wedding.service`. Postgres-Container heißt `stockbot-postgres-postgres-1`.
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
| T2 | W3.6 | Freigabe Exit-Policies (ändert Live-Trade-Verhalten), hinter `STRATEGY_EXITS_ENABLED`=false. **Korrektur 2026-08-23:** „Code fertig" stimmte nicht — der ATR-Trailing-Stop war strukturell tot (`_strategy_exit_reason` übergab weder `highest_price_since_entry` noch `atr`, `_trailing_stop` fiel immer auf HOLD), und `mean_reversion_exit` scheiterte am gemeinsamen Eingabesatz still im Exception-Handler. Beides behoben (`trades.high_water` + Fortschreibung im Monitor + Signatur-Filter im Dispatch, Alembic `a2b3c4d5e6f7`). Tor T2 = Flag einschalten + deployen — **jetzt erst wirksam**. |
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
   **(a) §32.3 ✅ ERLEDIGT** (`eefa106`+`b0db2c1`, s. u.); **(b) §32.4 ✅ ERLEDIGT** (`6982f0c`, s. u.);
   **(c) §32.5 ✅ ERLEDIGT** (`ad87da8`, s. u.); **(d) §32.6 ✅ ERLEDIGT** (`6004063`, 2026-08-09,
   s. u.) — §9.1-Navigation auf
   die echten Routen gemappt (`watchlist`/`history`/`backtest`/`lab` etc. dürfen nicht ungestylt
   bleiben); **(e) §32.7 ✅ ERLEDIGT** (`394ff30`) kategoriale, farbenblind-sichere Chart-Palette `--cat-1…6` (getrennt von
   Grün/Rot); **(f) §32.8 ✅ ERLEDIGT** (`394ff30`) gemeinsames matplotlib-Style-Mapping der Tokens, damit Backtest-/Report-PNGs
   nicht von den Web-Charts abweichen; **(g) §32.9 ✅ ERLEDIGT** (`5a98bdb`) ein Web↔Telegram-Glossar (DoD §30 verlangt
   Begriffs-Parität, Quelle fehlte). Kein Live-Trade-Verhalten außer (a)/(b), die **härten**.
   **Punkt 1b ist damit vollständig abgearbeitet — „Gate Style"-Rest geschlossen.**

**Worktree-Aufräumung 2026-08-09.** Die 13 alten Agent-Worktrees unter
`.claude/worktrees/` (172 MB) wurden entfernt und ihre gemergten `agent/*`-Branches
gelöscht. Zwei Befunde dabei: (1) im Worktree `agent-obs-001` lag **uncommittete**
Arbeit (149 Zeilen + `tests/test_obs_rejection.py`) — als Patch gesichert unter
`~/backups/trading_bot-worktree-obs-001-20260809/`, falls sie noch gebraucht wird.
(2) `agent/fix-yf-tz-naive` galt als ungemergt, war es inhaltlich aber nicht: der
tz-Fix kam über `c1887c0` (2026-07-27) in `main`, `git diff main <branch>` zeigt für
`stockbot/market/data_providers.py` **keinen** Unterschied. Der Branch trug nur einen
älteren Stand von `app.html` und wurde deshalb gelöscht — keine verlorene Arbeit.

**Style-Audit (d)–(g) erledigt (Stand 2026-08-09, NICHT deployt).** (e)/(f) `394ff30`
(`--cat-1…6` in `stockbot/web/static/tokens.css`, gemeinsame Quelle
`stockbot/core/chart_palette.py` für Web und matplotlib), (g) `5a98bdb`
(`stockbot/core/glossary.py`) — beide waren bereits in `main`, im Plan aber noch als
offen geführt; hier nachgetragen. (d) `6004063`: §9.1 auf die neun echten Seitenrouten
gemappt, reiner Doku-Task ohne Codeänderung. **Erster Task, der über `codex exec`
statt über einen Claude-Subagenten lief** (Handoff nach `agent-control/planner.md`,
Diff vom Lead reviewt, Suite gegengefahren: 1287 passed, 29 skipped).

**Style-Audit (a)+(b) erledigt (2026-07-23, `6982f0c`+`eefa106`+`b0db2c1`, DEPLOYT auf VPS `d4fc73e`).**
Deploy: Backup `/var/backups/stockbot/stockbot-20260723-215229.dump.age`, push→VPS+ff-merge, Restart.
**Keine Migration, keine neuen Deps** (Alembic-Head unverändert `c9d0e1f2a3b4`). Smoke grün: 12
Scheduler-Jobs, 0 Fehler im Journal des neuen Prozesses, `/api/v1/health` 200 mit `x-trace-id`.
⚠️ **Betriebsbefund beim Deploy:** `run_bot.py` startet das Dashboard selbst in einem Thread
(`bot._start_dashboard_thread`) und bindet damit Port 8000; das separate `dashboard.service` ist auf
dem VPS **disabled und lief noch nie**. Wer beide Units startet, erzeugt einen Port-Konflikt
(`Errno 98`) — der Bot läuft dann zwar weiter (Scheduler ok), aber ohne eigene Weboberfläche.
**Deploy-Regel bleibt: nur `systemctl restart stockbot`, `dashboard.service` nicht anfassen.**
(Passiert genau einmal beim Deploy 2026-07-23, sofort zurückgedreht.)
Zwei Claude-Subagenten (Worktree), sequentiell weil beide `web/templates/app.html` anfassen;
Manager-reviewt, Suite selbst gegengefahren (225 passed, 1 skipped auf gemergtem `main`).
- **(b) §32.4 Dialog- & Fokus-Verhalten** (`6982f0c`): `role="dialog"`/`aria-modal="true"`,
  expliziter Tab-Fokus-Trap, ESC + Fokus-Rückgabe auf den auslösenden Button. **Anti-Fehlklick:**
  Initialfokus hart auf „Abbrechen", **Enter auf dem Bestätigen-Button ist geblockt** (Space bleibt),
  Confirm-Button sperrt sich beim Klick gegen Doppel-Submit (ergänzt den `dataset.busy`-Pfad und die
  OMS-Doppelklick-Absicherung). Kein Backend-Eingriff, Nicht-JS-Fallback erhalten.
  Tests: 5 neue in `tests/test_web_style_phases.py`; Negativprobe des Subagenten macht ohne die
  Änderung genau diese 5 rot.
- **(a) §32.3 Datenaktualität** (`eefa106` Erstwurf + `b0db2c1` Manager-Korrektur): neues IO-freies
  Modul `stockbot/web/feed_status.py` (`FeedStatus`, `evaluate`, `is_stale`, `unknown`),
  `_feed_status_for` in `webapp.py`, Chip + `role="alert"`-Banner in `app.html`.
  **Zwei Blocker im Erstwurf, vom Subagenten selbst geflaggt, per `SendMessage` zurückgegeben:**
  1. *Exits wurden mitgesperrt* — ein alter Scan hätte das Schließen einer laufenden Position
     blockiert. Verschlechterung der Sicherheitslage, und die Datenbasis passt nicht (Status kommt
     aus dem Scan-Cache, das Alter der Trade-Kurse ist davon unabhängig). **Jetzt: nur Einstiege
     werden gesperrt, Exits nie** — im Template kommentiert, eigener Regressionstest.
  2. *Die Gate-Kopplung saß an der falschen Kante* — `max_quote_age_seconds`=60 s vs. `SCAN_TTL_S`=600 s
     hätte die Seite ~1 min nach jedem Scan selbst gesperrt, obwohl `risk_context.quote_context` beim
     Order-Versuch eine **frische** Quote holt. Die UI hätte systematisch blockiert, was das Backend
     durchlässt. **Auflösung: UI-Preisalter und Ausführungs-Quote-Alter sind zwei verschiedene Größen.**
     `fresh` endet exakt an der Gate-Grenze (ruft `data_quality.check_quote_age` real auf — keine zweite
     hartkodierte Zahl, per 5×7-Matrix-Test gepinnt) ⇒ „aktuell" wird nie für etwas behauptet, das das
     Ausführungs-Gate ablehnen würde; gesperrt wird erst ab eigener UI-Konstante `UI_STALE_SECONDS=180`.
     Faktisches Blockfenster damit 180–600 s (danach verfallen die Signalkarten ohnehin).
  Für Signale/Trades ohne Kurs-Zeitstempel wird **nichts geraten**: expliziter `unknown`-Zustand
  (`chip--caution`, blockiert nicht). Abrufzeit wird als `UTC` beschriftet, keine Umrechnung ohne
  Grundlage. Backend/Order-Pfad unberührt (`data_quality.py`, `risk.py`, `oms.py`, `risk_context.py`,
  `dashboard.py` nicht angefasst), keine neuen Dependencies, keine neuen CSS-Klassen.
  Tests: `tests/test_feed_status.py` (neu) + 4 neue in `tests/test_web_style_phases.py`.
- **Kein Live-Trade-Verhalten geändert** — beide Tasks härten nur die UI (Defense-in-Depth); das
  Backend-Gate bleibt der eigentliche Schutz.

**Style-Audit (c) + Testsuite-Hygiene + UTC-Bugfix erledigt (2026-07-24, auf `main`, NOCH NICHT deployt).**
Ausgelöst durch einen externen Fabel-Audit des Projekts (Analyse s. u. „Audit-Befunde").
- **(c) §32.5 „Daten unsicher/degradiert"** (`ad87da8`, Claude-Subagent, Manager-reviewt): eigener
  UI-Zustand `degraded` in `feed_status.py` (Konstruktor `degraded(detail)`, `blocks_orders=True`,
  `chip--caution`). Ausgelöst durch die reale Fallback-Stelle `dashboard.py::_current_price`, die bei
  fehlgeschlagenem Kursabruf **den Einstiegskurs** als „aktuell" zeigte (⇒ „$300 → $300, +0.00 €",
  ein erfundener Wert). Jetzt: NaN-Sentinel ⇒ `None` ⇒ „nicht verfügbar"/„—" + `alert2--warning`-Banner
  (`role="alert"`), das die betroffenen Ticker nennt; Einstiege gesperrt, **Exits nie**. Abgrenzung
  `unknown` (Alter unbekannt, Wert plausibel → warnt) vs. `degraded` (Quelle ausgefallen → sperrt).
  Backend/Order-Pfad unberührt (fail-open in `risk_context.py` bewusst NICHT angefasst — separate
  Entscheidung). Tests: 4+4 neu. **Betriebshinweis:** der Zustand sperrt neue Einstiege seitenweit,
  sobald für EINE angezeigte Position kein Kurs abrufbar ist (bewusst über-blockierend, heilt selbst).
- **Testsuite-Hygiene** (`a4ed426`+`b6498b6`+`dd28789`, Claude-Subagent + Manager-CI-Tweak):
  (1) roter Test `test_roundup_queue.py::…outside_regular_session` auf das `8917b82`-Anti-Spam-Verhalten
  gezogen (umgebaut, nicht weggeworfen — der „kein Auto-Start"-Kern blieb, den `test_signal_suppression`
  NICHT abdeckt). (2) **`tests/conftest.py`** neu: setzt Import-Defaults (Dummy-`ENCRYPTION_KEY`,
  `DASHBOARD_BASE_URL`) via `setdefault` ⇒ frischer Checkout sammelt jetzt 1237 Tests / **0 Errors**
  (vorher 69 Collection-Errors). Sicherheits-Defaults unverschoben. (3) **`.github/workflows/tests.yml`**
  neu: erste CI (`push`/`pull_request` auf `main`), **Python 3.12** (Prod-Parität mit dem VPS, nicht
  Repo-Minimum 3.11), Install aus `requirements.lock`. **Erster CI-Lauf (`30074981868`, push `cf4e3a7`)
  grün** — `requirements.lock` installiert unter 3.12 sauber, volle Suite läuft auf frischem Runner
  ohne Secrets durch (dank `conftest.py`). Der Push brauchte einmalig `gh auth refresh -s workflow`
  (Token-Scope für die Workflow-Datei).
- **UTC-Datum-Bugfix** (`8aaddaa`, Claude-Subagent — Spend-Limit mitten im Abschluss, Manager hat die
  fertige, uncommittete Worktree-Arbeit reviewt + committet + gemergt): `_send_autoaccept_daily_report`
  bildete den Abfragetag mit `date.today()` (Server-Lokalzeit), während `trade_date` via `db._today()`
  in UTC gestempelt wird ⇒ auf Nicht-UTC-Maschinen „Gekauft (0)" trotz Kauf. **Prod (VPS `Etc/UTC`)
  nicht betroffen**, aber die Suite war dadurch zeitabhängig rot. Zentrale `db.today_utc_date()`/
  `today_utc()` als einzige Wahrheit, DB-vergleichende Aufrufer gezogen (Tagesreport, `_trade_age_days`,
  Kandidaten-Cache, `dashboard` days-cutoff, sltp-Warn-Key); `broker/client.py` (Options-Verfallsfenster,
  Broker-Kalender) bewusst gelassen. Deterministische Regressionstests, grün über TZ=UTC/Auckland/LA.
- **`main` damit wieder vollständig grün** (voller Suite-Lauf nach dem Merge).

**Audit-Abarbeitung + Style-Rest (d)–(g) erledigt (2026-07-25, auf `main`, NOCH NICHT deployt).**
Sechs parallele Claude-Subagenten (unabhängige Dateien), Manager-reviewt + einzeln gemergt, volle Suite
nach den Merges grün (**1271 passed, 29 skipped**). §32.6 fiel weg (Nav mappt bereits auf echte Routen,
alle Templates `extends base.html` — Rest ist visuelle Abnahme).
- **Deploy-Lock + Doc-Rot** (`25b6d39`): `upload.ps1`/`update.ps1` installieren jetzt `requirements.lock`
  statt `requirements.txt` (schließt das Supply-Chain-Leck, das `deploy/*.sh` schon zu hatte, in der
  PowerShell-Hintertür). Doc-Rot in `data_quality.py`/`market_data.py` korrigiert (die „von keinem
  Live-Pfad genutzt"-Lügen). Nur Docstrings/Dateinamen, kein Verhaltenscode.
- **§32.7 + §32.8 Charts** (`394ff30`): Token-Gruppe `--cat-1…6` (farbenblind-sicher, dataviz-validiert,
  ΔE-Prüfung bestanden) in `tokens.css` + neue Python-Quelle `core/chart_palette.py`, aus der `report.py`
  seine Matplotlib-Farben zieht (keine Hex-Literale mehr). Parität Token↔Python per Test festgenagelt.
  Web-Mehrserien-Chart (`strengthChart`) auf `--cat-1…6` gezogen. **Review-Befund akzeptiert:** zwei
  Report-Balken waren Grün/Rot-codiert für Kategorien (§15.3-Missbrauch) → auf `--cat-1/2` umgestellt,
  Grün/Rot-Semantik unberührt (sichtbare Farbänderung an einem Report-PNG).
- **§32.9 Glossar** (`5a98bdb`): `core/glossary.py` als einzige Quelle für Status-/Modus-/Aktionsbegriffe;
  `web/dashboard.py` re-exportiert (Aufrufer unverändert), `bot.py` zieht `broker_status_label`. **Gefundene
  Divergenz behoben:** Telegram zeigte rohe englische Broker-Codes („rejected"), Web deutsche §25.2-Labels
  → auf die Web-Formulierung vereinheitlicht. Reine Anzeige-Strings, kein Verhaltenscode.
- **Risiko-Verdrahtung** (`051c67f`, Alembic-Head `c9d0e1f2a3b4` → **`d0e1f2a3b4c5`**): `db.get_realized_pnl_today`
  + persistiertes `risk_profiles` (Tabelle in SCHEMA_SQL UND Alembic). **Inert-by-default bewährt** — der
  Subagent sah selbst, dass Default-`daily_loss_limit_pct=1.00` bei bedingungslosem Durchreichen alle
  Bestandsnutzer sofort scharf schaltet; deshalb wird `realized_pnl_today` NUR bei gespeichertem Profil
  gesetzt, ohne Profil exakt das alte Verhalten (per OMS-Gate-Test bewiesen). `save_risk_profile` gebaut,
  aber noch keine UI (separates Ticket). **Postgres-Contract-Test der neuen Tabelle am VPS gegenzuverifizieren.**
- **Backtest-Ehrlichkeit** (`fa2c03f`, Audit-Punkt 1): (A) `resolve_backtest_universe` + `universe_history`
  verdrahtet — Point-in-Time-Liste ist Default, sobald `data/universe_history/<region>.json` existiert;
  **die Daten fehlen im Repo**, also greift der ehrliche Degradations-Pfad (heutige Liste + sichtbare
  `SURVIVORSHIP_WARNING` im Report statt stiller Schönung). (B) `_walk_exit` gap-realistisch: Bar öffnet per
  Gap jenseits des Levels → Fill am Open (Risiko-Seite schlechter = konservativ), sonst intrabar am Level;
  „SL vor TP" bleibt. (C) `slippage_spread_fraction` Default `0.0 → 0.5` (Kosten eher über- als
  unterschätzen). **Kein Live-Handelspfad berührt** — reine Backtest-Auswertung, aber Tor-T5-relevant.
- **fail-closed** (`93cad46`, hinter Flag `RISK_FAIL_CLOSED_ON_QUOTE`, **Default AUS = heutiges Verhalten**):
  `quote_context` gibt bei nicht abrufbarer Quote (Exception/`None`) nur bei gesetztem Flag das additive
  Sentinel `{"quote_required": True}` zurück; `pretrade_check` bekommt additiv `quote_required=False` und
  blockt bei `quote_required and quote is None` mit `quote_unavailable` (analog `order_context_missing`,
  Kernlogik unangetastet). Flag wirkt global (Paper wie Live), Empfehlung „für Live-Konten AN" im Docstring.
  **Aktivierung = freigabepflichtig (Live-Verhalten).** Der fail-closed-Branch hing an `ee96fdc` (vor dem
  Risiko-Merge) → Manager hat beim Rebase den `risk_context.py`-Konflikt von Hand aufgelöst, sodass
  **beide** Features erhalten bleiben (Profil-Laden + Sentinel); per Suite bestätigt.

**DEPLOYT 2026-07-25: VPS auf `af9e546`** (Backup `/var/backups/stockbot/stockbot-20260725-111541.dump.age`,
push→VPS+ff-merge, **`alembic upgrade head` c9d0e1f2a3b4 → `d0e1f2a3b4c5`** [risk_profiles], nur
`systemctl restart stockbot`). Smoke grün: 0 Fehler im neuen Prozess, 12 Scheduler-Jobs, `/api/v1/health`
200 mit `x-trace-id`, Live-Flags unset (fail-closed + Exit-Policies AUS). **Postgres-Gegenverifikation der
neuen Tabelle:** `db.get_risk_profile(0)`→None, `db.get_realized_pnl_today(0)`→0.0 (kein Typvertrag-Verstoß).
Keine neuen Deps. `dashboard.service` unangetastet (inactive, Ops-Regel).

**Audit-Status danach:** Punkt 1 (Backtest) ✅, Punkt 3 realized_pnl/Profil ✅ + fail-closed ✅ (beide
freigabe-/opt-in-gated; Exposure-Default-Bindung kommt mit einer künftigen Profil-UI), Punkt 5 (CI/Suite) ✅,
Punkt 6 (PS-Lock) ✅, Doc-Rot ✅. **Offen:** OAuth/Telegram-Secret-Transport (braucht externe
Alpaca-OAuth-App-Registrierung — menschlich), Punkt 7 Web-Kleinigkeiten (Token-in-URL,
`utcnow()`-Deprecations, Monolithen), `pf_key`-Sortierung.

### Audit-Befunde (externer Fabel-Audit, 2026-07-24) — Manager-Bewertung gegen den echten Code
6 von 7 Blöcken treffen zu, einer ist überholt:
- **Punkt 2 „Live-Pfad hängt an yfinance" = ÜBERHOLT** (beschreibt den Stand vor W3.2). `db.py:56`
  `yf = _SignalQuoteSource()` ist ein Alpaca-Shim (Name nur aus Test-Kompat), `analyzer` läuft über
  `provider_factory.get_signal_provider()`. Verbliebene `yf.download` sind Research-Tier (Sparklines,
  factor_history, llm_ranker). **ABER echter Teilbefund:** Docstrings lügen — `data_quality.py` sagt
  „von KEINEM Live-Codepfad genutzt", während `risk.py:121` es aufruft. → Doc-Rot als eigenes Ticket.
- **Punkt 1 Backtest ≠ Live** (bestätigt, hohe Priorität für Tor T5): 1d-only vs. Multi-TF-Confluence;
  `universe_history.py` hat 0 Aufrufer (Survivorship-Bias); `_walk_exit` füllt exakt am Level (Gap-blind);
  Kosten-Defaults Spread/Slippage=0; `pf_key` sortiert `PF=None→inf` nach oben.
- **Punkt 3 Risiko teils tot verdrahtet** (bestätigt, aus Manager-Sicht schwerster Punkt):
  `quote_context` fail-open ⇒ `{}` ⇒ Frische/Spread still übersprungen; `realized_pnl_today` nie gesetzt
  ⇒ Tagesverlustlimit inaktiv; RiskProfile-Defaults (max_position_pct=100, Exposure je 100 %) binden nicht.
- **Punkt 4** Broker-Secrets über Telegram-Chat + `broker_oauth.py` gebaut aber 0 Aufrufer (bestätigt).
- **Punkt 5** Suite rot + keine CI (bestätigt — **jetzt behoben**, s. o.).
- **Punkt 6** `update.ps1:56`/`upload.ps1:82` installieren `requirements.txt` statt `.lock` (bestätigt).
- **Punkt 7** Web-Kleinigkeiten (Token in URLs, toleranter CSRF, Monolithen, `utcnow()`-Deprecations).
- Rechtlicher Hinweis (Multi-User-Anlageberatung) deckt sich mit **Tor T4** (schon als extern/gated notiert).
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

### Labor-Divergenz: warum `ai_adaptive` live verliert (Befund 2026-08-23)

Der Reality-Check des Labors meldete am 20.08. `divergent`: live 0 % Trefferquote und −6,6 %
je Trade gegen eine Backtest-Erwartung von 41,8 % und +2,27 %. Die Bilanz aller geschlossenen
Trades bestätigt das — `ai_adaptive` ist mit Ø −6,03 % (1 Gewinner aus 13, −19,61 $) die mit
Abstand schlechteste Strategie, während `breakout`/`standard` bei ±0 liegen.

Drei Ursachen, alle am Code belegt, keine davon ein Fehler des Gates:

1. **Universum-Mismatch.** `run_cycle` lud hart `DEFAULT_REGION` ('sp500'), gehandelt wurde
   `sp500,msci_world,emerging`. Die geschlossenen Trades waren fast durchweg EM-ADRs (VALE,
   BSBR, CIG, ZTO, UMC, PAGS, STNE, …). Behoben: `lab._live_regions()` folgt den Regionen der
   Nutzer mit `broker_exec`, Rückfall auf `DEFAULT_REGION`.
2. **Toter Trailing-Stop.** Das Labor optimiert `trail_mult` und rechnet den Trailing-Stop in
   die Erwartung ein; live existierte er nicht (siehe Tor T2). Behoben.
3. **Folge aus 1+2:** Praktisch jeder Verlust wurde exakt am Stop-Loss geschlossen, kein
   einziges Take-Profit erreicht (STNE 9,43/9,52 · BSBR 5,61/5,607 · GRAB 3,52/3,524 ·
   TCOM 44,50/44,498 · ZTO 22,32/22,355 · VALE 13,73/13,7316 · ABEV 2,88/2,8826).
   `tp_mult` 10,0 ATR entspricht bei diesen Titeln +29 % bis +86 % — in der Haltedauer
   unerreichbar, während 3,0 ATR nach unten regelmäßig getroffen werden.

Das Gate selbst arbeitete korrekt (`pending.json` leer, letzte Entscheidung „gewinnt nur 1/3
OOS-Folds"). Der Reality-Check hat die Divergenz selbst gemeldet — sie wurde nur nicht gelesen.

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
