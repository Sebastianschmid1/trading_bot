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

## W1-Teilstand (2026-07-16, `3c3b6c5`, GitHub `main`, NICHT deployt)

W1.1/W1.4/W1.5 gebaut, reviewt, gemergt, Suite grün (893 passed, 27 skipped). Offen im
Kernstrang: **W1.2** (Quote-Frische), **W1.3** (Kill-Switch persistent+UI), **W1.6**
(Determinismus-Beweis) — bewusst nach W1.1 sequenziert (gleicher OMS-Submit-Pfad).

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

| Welle | Ziel | Schließt Gate |
|---|---|---|
| **W0** Betriebsschutz (sofort) | Prod-Postgres & Deployment absichern, ohne den Trading-Pfad im Burn-in anzufassen | Teile P9 |
| **W1** Risk-Wiring | `pretrade_check` mit echten Inputs live; Kill-Switch & Audit persistent | **P3, P1.1**, P2-Quote |
| **W2** OMS-Orchestrierung | Broker-Events, Reconciliation-Alarm, Partial-Fill-Handling live | **P4** |
| **W3** Daten & Versionen | yfinance raus aus Prod-Pfad, Strategieversion je Signal, Mode-Dashboards, Scheibe 9 | **P2, P5, P6** |
| **W4** Observability & Platform | JSON-Logging, Metriken/Alarme, Secrets, OAuth | **P9** Rest |
| **W5** Backtest-Härtung | gemeinsamer Strategiecode, Kostenmodell, Validierung, Reproduzierbarkeit | **P7** |
| **W6** Labor begrenzen | Champion/Candidate, Promotion-Gates, Holdout-Schutz | **P8** |
| **W7** UI/Design & Querschnitt | Style-Phasen 2–5, Web-/Telegram-Umbau, API v1, Pakete B/C/D | **Gate Style** |
| **W8** Test & Paper-Freigabe | Testsuiten + Paper-Burn-in + Go/No-Go | **P10** |

---

## W0 — Betriebsschutz (JETZT starten, alle parallel)

Kein Eingriff in den Trading-Codepfad → stört den Postgres-Burn-in nicht.

| # | Task (Sol-tauglich) | Scope | Aufwand | Abh. | Parallel? |
|---|---|---|---|---|---|
| W0.1 | **PLAT-009 Postgres-Backups** | `pg_dump` verschlüsselt (age/gpg) per Timer, Aufbewahrungsplan, Restore-Test-Skript + dokumentierter Test gegen Wegwerf-DB. **Höchste Dringlichkeit: Prod-DB hat aktuell kein Backup.** | M | — | ✅ |
| W0.2 | **PLAT-006a Deps pinnen** | `requirements.lock` (pip-tools/freeze), `pip-audit`, yfinance-tz-Cache-FD-Leck prüfen (todo.md A2), Dependabot-Konfig. | S | — | ✅ |
| W0.3 | **PLAT-008 systemd-Härtung** | Units umschreiben (eigener User `stockbot`, `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict` + `ReadWritePaths`, Limits), Pfad-Anpassung in `deploy/*.sh`/`upload.ps1`. Sol schreibt Units + Migrationsanleitung; **VPS-Migration = menschlicher Deploy-Schritt (Tor T1).** | M | — | ✅ |
| W0.4 | **Paket A Konfig/Flags** | Typisierte Settings-Klasse um `config.py`, Modusvalidierung beim Start, Start-Verweigerung bei riskanter Fehlkonfig. | M | — | ✅ |

## W1 — Risk-Wiring (Kern-Sicherheitswelle)

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

## W2 — OMS-Live-Orchestrierung (Gate P4)

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
| W4.1 | PLAT-004 strukturiertes JSON-Logging (trace_id, pseudonymisierte user_id, keine PII/Keys) | M | ✅ |
| W4.2 | PLAT-005 Metriken + Alarmregeln (Quote-Alter, Reject/Fill-Rate, Reconciliation-Fehler, Positionen ohne Stop, Kill-Switch-Status) | M/L | ✅ (nutzt W1.5/W2.2) |
| W4.3 | PLAT-006b Secrets (systemd-Credentials, Rotation, kein Secret in Logs) | M | ✅ |
| W4.4 | PLAT-007 Alpaca OAuth (Scopes, Token verschlüsselt, Revoke, Paper/Live getrennt) | L | ✅ |
| W4.5 | Pakete B/C/D (Domain Events, Outbox, Notifications als Consumer) | L | ✅ (füttert W4.2-Alarme) |

## W5 — Backtest-Härtung (Gate P7; Research-Strang, parallel zu W2–W4)

Blockiert nicht den Paper-Burn-in, wohl aber Canary. Kann früh parallel anlaufen.

| # | Task | Aufwand | Parallel? |
|---|---|---|---|
| W5.1 | Gemeinsamer Strategiecode + Clock/Data-Abstraktion (Backtest nutzt Prod-Strategiemodule) | L | Seriell (Fundament) |
| W5.2 | Multi-Timeframe-Korrektheit / kein Look-ahead (Tests) | M | nach W5.1 |
| W5.3 | RES-004 Kostenmodell (Kommission/Spread/Slippage/SEC/FINRA/Teilfüllung/Market-Impact) | M | ✅ parallel zu W5.2 |
| W5.4 | Universen point-in-time + Survivorship-Messung | L | ✅ |
| W5.5 | RES-003 Reproduzierbarkeit (Run-ID, Commit, Seed, Deps) | M | ✅ |
| W5.6 | Validierung (Nested Walk-forward, Purging, Embargo, Holdout-Sperre, Bootstrap, Regime) | L | nach W5.2/W5.5 |

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

**W1.1→W1.2 (Risk-Wiring) → W2.1→W2.3 (OMS-Orchestrierung) → W3.1→W3.2 (yfinance-Ablösung) → W8
(Burn-in-Kalenderzeit).** Alles andere (W4, W5–W6, W7) hängt parallel daneben. Der Burn-in kann formal
erst "zählen", wenn W1–W3 deployt sind — je früher W1 live ist, desto früher startet die Uhr.

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

## Empfehlung: Jetzt starten mit W0 + W1 (versetzt)

Sofort **3–4 parallele Sol-Worker auf W0.1/W0.2/W0.3/W0.4** — reine Betriebsschutz-Tasks, die den
Trading-Codepfad nicht berühren und den laufenden Postgres-Burn-in nicht kontaminieren. Das Backup
(W0.1) ist nicht verhandelbar dringend. **Gleichzeitig W1.3/W1.4/W1.5 als parallele Branches
entwickeln** (eigene Tabellen/Jobs, konfliktfrei) und **W1.1 als Kernstrang**; Deploy der W1-Ergebnisse
erst nach 2–3 stabilen Postgres-Markttagen bündeln. So wird die Beobachtungsphase produktiv genutzt,
und direkt danach sind Gate P3 + P1.1 geschlossen — Voraussetzung, damit die Burn-in-Uhr für Gate P10
sinnvoll tickt. Parallel **T4 (regulatorische Einordnung) anstoßen**, da extern und lang laufend.

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
