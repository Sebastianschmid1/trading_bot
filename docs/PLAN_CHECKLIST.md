# Umsetzungs-Checkliste: Konzept_v1

*Abarbeitbare Checkliste zum [Plan.md](Plan.md) / [KONZEPT_v1.md](KONZEPT_v1.md). Stand: 2026-07-11.*

**So wird sie benutzt:** Jede Zeile ist ein konkreter, abhakbarer Schritt. Reihenfolge = Empfehlung
aus Plan §28. Ein **Phasen-Gate** (Abnahme) am Ende jeder Phase muss erfüllt sein, bevor die nächste
Phase live-relevant wird. Prioritäten: **P0** (kritisch, vor jedem Live-Test) · **P1** (vor stabilem Paper)
· **P2** (vor Canary Live) · **P3** (nach V1).

**Legende:** `[ ]` offen · `[~]` in Arbeit · `[x]` erledigt · `[!]` blockiert/Entscheidung nötig.

---

## Phase 0 — Sofortige Stabilisierung `P0` · Epic: TSAFE

Ziel: Alle besonders riskanten Funktionen sind deaktiviert oder technisch blockiert.

- [x] **TSAFE-001** Globaler Live-Kill-Switch: Feature-Flags einführen
      (`TRADING_MODE=paper`, `ALLOW_LIVE_TRADING=false`, `ALLOW_MARGIN=false`,
      `ALLOW_OPTIONS=false`, `ALLOW_SHORTS=false`, `MAX_LEVERAGE=1`) — in `config.py`, inkl. erzwungenem Paper-Modus solange Live nicht frei
- [x] **TSAFE-001** Live-Orderversuche serverseitig ablehnen (nicht nur UI-Schalter) — Guard `_live_block_reason` in `broker/client.py` auf `submit_buy`/`submit_option_buy`
- [x] **TSAFE-001** Telegram kann Live nicht aktivieren; nur protokollierte Admin-Konfig kann es später — Telegram steuert nur `broker_exec` (Ausführung an/aus); Paper/Live ausschließlich über Config-Flags
- [ ] **TSAFE-002** Hebel hart blockieren: UI-Auswahl entfernen (Web + Telegram), Backend validiert `leverage == 1`
- [ ] **TSAFE-002** Migration: gespeicherte Hebelwerte > 1 auf 1 setzen; Orders mit Hebel > 1 ablehnen
- [ ] **TSAFE-003** Optionen deaktivieren: Long-Call-Pfad aus Produktion entfernen, UI ausblenden,
      Brokeradapter blockiert Optionssymbole, Optionskonfig als deprecated markieren
- [ ] **TSAFE-004** Budgetüberschreitung entfernen: `SHARE_ROUNDUP_FACTOR`-Aufrunden (1.5×) raus →
      nie über verfügbares Budget; Fractional nutzen oder Trade verkleinern/ablehnen
- [ ] **TSAFE-005** Score-Exit deaktivieren: automatisches Schließen bei `SIGNAL_CLOSE_THRESHOLD` (< 35) entfernen
- [ ] **TSAFE-005** Explizite strategiebezogene Exits an dessen Stelle setzen; Alt-Trades mit Exitgrund markieren
- [ ] **TSAFE-006** Direkte Brokeraufrufe inventarisieren: Repo nach `submit_order`, `close_position`,
      `cancel_order`, Alpaca-Client-Aufrufen, Orderneuerzeugung in Telegram/Web/Scheduler durchsuchen + Liste dokumentieren
- [ ] **TSAFE-007** EOD-22:15-Schließung deaktivieren; neue Positionen nur in definierten Sessions (vorläufig Exchange-Kalender)
- [ ] **TSAFE-007** Zentrale Risk-/Order-Vorprüfung einführen (Platzhalter, den Phase 3/4 füllt)

**Gate P0 (Abnahme):**
- [ ] Kein Codepfad kann eine Live-Order senden
- [ ] Jede Order mit Hebel > 1 / jede Optionsorder wird abgelehnt
- [ ] Keine Order überschreitet Budget/Buying Power
- [ ] Kein Trade wird allein wegen globalem Score geschlossen
- [ ] Tests beweisen: Telegram und Web senden keine direkten Brokerorders

---

## Phase 1 — Domänenmodell & PostgreSQL `P1` · Epic: PLAT

Ziel: Belastbares Zustands- und Datenmodell.

- [ ] **PLAT-001** PostgreSQL lokal + Staging bereitstellen; Alembic (o.ä.) einführen; Connection Pool + Transaktionsgrenzen
- [ ] **PLAT-001** Bestehendes SQLite-Schema dokumentieren + einfrieren; read-only Export aufbewahren
- [ ] **PLAT-001** Datenmigration schreiben; Testmigration auf Kopie; Zeilen/Summen vergleichen; Paper auf PostgreSQL umstellen
- [ ] Domänenobjekte definieren: User, RiskProfile, BrokerConnection, Strategy, StrategyVersion, Signal,
      SignalCandidate, TradeIntent, RiskDecision, Order, OrderEvent, Fill, Position, PositionEvent, KillSwitch, AuditEvent
- [ ] Zustandsmaschine **Signal** (generated→filtered→published→accepted/rejected/expired/blocked_by_risk→order_created)
- [ ] Zustandsmaschine **Order** (created→validated→submitted→accepted_by_broker→partially_filled→filled / cancel_requested→cancelled / rejected / expired)
- [ ] Zustandsmaschine **Position** (pending_open→open→pending_close→closed / reconciliation_required)
- [ ] Zentrale Validierung: ungültige Zustandsübergänge werden abgelehnt
- [ ] **PLAT-002** Audit-Log append-only (Event-ID, Timestamp, User, Actor, Entity-Typ/-ID, Aktion, alt/neu, Trace-ID, Quellkanal, Metadaten)

**Gate P1.1 (Abnahme):**
- [ ] Nutzer + Trades migrierbar; Zustandsübergänge getestet; ungültige abgelehnt
- [ ] Jede Brokeraktion erzeugt Audit-Event; alles über Trace-IDs nachvollziehbar

---

## Phase 2 — Exchange-Kalender & Marktdaten `P1` · Epic: DATA

- [ ] **DATA-001** Exchange-Kalender-Bibliothek wählen; NYSE/Nasdaq integrieren
- [ ] **DATA-001** Funktionen: `is_trading_day`, `market_open/close`, `is_market_open`, `next_market_open`, `minutes_to_close`, `is_early_close`
- [ ] **DATA-002** Scheduler umstellen: feste Berlin-Zeiten → relativ zu Open/Close; Reports separat in Europe/Berlin
- [ ] **DATA-003** `MarketDataProvider`-Interface (`get_bars/get_quote/stream_quotes/stream_trades/get_corporate_actions/get_market_status`)
- [ ] **DATA-003** Implementierungen: `YFinanceResearchProvider`, `AlpacaPaperMarketDataProvider` (später `LicensedProductionProvider`)
- [ ] **DATA-005** Datenherkunft je Berechnung speichern (Provider, Feed, Abrufzeit, Exchange-Zeit, Datenversion, Qualitätsstatus)
- [ ] **DATA-004** Datenqualitäts-Gates: Quote-Alter, Spread, Bar-Vollständigkeit, keine NaN, Symbol aktiv, kein Halt, Corporate Actions
- [ ] Rohdatenarchiv (Metadaten in PostgreSQL, Rohdaten als Parquet, Partition nach Symbol/Datum/Timeframe; Rohdaten getrennt von Features)

**Gate P2 (Abnahme):**
- [ ] DST-Wechsel + Half Days korrekt; keine Intraday-Position nach Entry-Cutoff
- [ ] Signal speichert Provider + Datenversion; veraltete Quotes blockieren Orders

---

## Phase 3 — Risk Service & Position Sizing `P1` · Epic: RISK

- [ ] **RISK-001** `RiskProfile`-Modell (account_risk_per_trade_pct, daily_loss_limit_pct, max_open_positions,
      max_position_pct, max_sector/correlated_exposure_pct, max_daily_new_exposure_pct, max_spread_bps,
      max_quote_age_seconds, min_average_dollar_volume, earnings_blackout_days, allow_overnight, allowed_strategies)
- [ ] **RISK-002** Sizing-Service: `Risikobetrag = Kontowert × Risiko_pro_Trade`, `Stückzahl = Risikobetrag / Stop-Abstand` + Caps
- [ ] **RISK-002** Konservative Defaults: 0,25 %/Trade, 1,00 % Tagesverlust, max 5 Positionen, 1× Exposure
- [ ] **RISK-003** Pre-Trade-Checks in fester Reihenfolge (Kill-Switch → Modus → Signal gültig → Strategie erlaubt →
      Markt offen → Quote frisch → Spread → Liquidität → Tagesverlustlimit → max Positionen → bestehende Ticker-Position →
      Exposure/Sektor → Sizing → Buying Power → Brokerstatus)
- [ ] **RISK-004** Tagesverlustlimit laufend fortschreiben; blockiert neue Positionen
- [ ] **RISK-005** Exposure-Limits (Einzel/Sektor/korreliert/täglich neu); Post-Trade: offene Position ohne Schutzorder erkennen
- [ ] **RISK-006** Kill-Switch-Service (`activate/deactivate_global`, `activate/deactivate_user`,
      `is_new_position_allowed`, `is_protective_exit_allowed`)
- [ ] UI für Risikoeinstellungen; Ablehnungsgründe im UI sichtbar

**Gate P3 (Abnahme):**
- [ ] Keine Order umgeht den Risk Service; gleiche Inputs → gleiche RiskDecision
- [ ] Keine Order überschreitet Kontorisiko; Tagesverlustlimit blockiert; Schutz-Exits trotz Kill-Switch erlaubt

---

## Phase 4 — Order Management System & Brokerzustände `P1` · Epic: OMS

- [ ] **OMS-001** `TradeIntent`-Modell (user_id, signal_id, requested_action, accepted_exit_policy, source_channel, created_at, idempotency_key)
- [ ] **OMS-002** OMS-Pipeline (Intent laden → Idempotency → Signal → Risk → Orderplan → persistieren → Broker senden → Broker-ID → Events → Nutzer informieren)
- [ ] **OMS-003** Idempotency: Key pro Nutzeraktion, DB-Unique-Constraint, doppelte Callbacks/Requests erzeugen keine 2. Order; Client-Order-ID aus interner ID
- [ ] **OMS-004** `BrokerAdapter`-Interface (submit/cancel/replace_order, close_position, get_order, list_open_orders, list_positions, stream_order_events, get_account)
- [ ] **OMS-005** Broker-Event-Worker (accepted, rejected, partial_fill, fill, cancelled, expired, replaced) — dedupliziert, persistiert, Zustandsübergang geprüft, in Positionen übertragen, auditiert, an Notification
- [ ] **OMS-006** Partial Fills: Teilposition, Stopgröße an Fillmenge anpassen, Restorder-Timeout, keine doppelte Schutzorder
- [ ] **OMS-007** Reconciliation: Echtzeit (Stream primär) + periodisch (5–15 min: Orders/Positionen/Cash/Buying Power, unbekannte/fehlende Positionen) + täglicher Voll-Abgleich + Report

**Gate P4 (Abnahme):**
- [ ] Doppelklicks → keine doppelten Orders; Orderstatus = Brokerereignisse
- [ ] Partial Fills korrekt; Abweichungen erkannt + alarmiert; jede Order hat vollständige Ereignishistorie

---

## Phase 5 — Strategien & Signal-Engine vereinfachen `P2` · Epic: STRAT

- [ ] **STRAT-001** Strategie-Inventur je Strategie (Zielidee, Timeframe, Haltedauer, Entry, Exit, Universum, Kostenempfindlichkeit, #Backtest-Trades, OOS-Status, Abhängigkeiten)
- [ ] **STRAT-002** Klassifizieren + max. 3 V1-Familien wählen: **Intraday Momentum**, **Swing Trend**, **Mean Reversion** (Rest → research-only/deprecated)
- [ ] **STRAT-004** Globalen 0–100-Score aus Entscheidungspfad entfernen; strategiespezifische Rohscores; UI-Beschriftung anpassen (keine Wahrscheinlichkeit ohne Kalibrierung)
- [ ] **STRAT-003** Strategieversionierung (Parameter, Feature-Version, Universum, Entry/Exit-Regeln, Kostenmodell, Release-Status, Code-Commit — unveränderlich veröffentlicht)
- [ ] **STRAT-005** Strategiebezogene Exits je Familie (Momentum: Stop/Trailing/Momentumbruch/Timeout/Close-Exit · Swing: Stop/Strukturbruch/Trailing/Max-Haltedauer/Eventfilter · Mean Reversion: Mittelwert-Rückkehr/Stop/Zeit-Exit/Regimebruch)

**Gate P5 (Abnahme):**
- [ ] Max. 3 produktive Familien; kein globaler Score entscheidet über Entry/Exit
- [ ] Jedes Signal referenziert unveränderliche Strategieversion mit dokumentierter Entry/Exit-Logik

---

## Phase 6 — Portfolio-Allocator, Shadow & Reporting `P2` · Epic: STRAT/RES

- [ ] **STRAT-006** Portfolio-Allocator (Inputs: Candidates, Positionen, offene Orders, Risikoprofil, Sektor/Korrelation, Kosten, Prioritäten → Auswahl + Ablehnungsgründe + reserviertes Budget)
- [ ] **RES-001** Shadow-Modus: Signale auf Live-Daten, nicht ausführbar, simulierte Entry/Exit, getrennt ausgewertet
- [ ] **RES-002** Moduskennzeichnung `backtest|shadow|paper|live` Pflicht auf Signal, Intent, Order, Fill, Position, Performance-Snapshot
- [ ] **RES-002** Getrennte Reports/Dashboards je Modus (Netto-P&L, Kosten, Slippage, Drawdown, #Trades, Profitfaktor, Erwartungswert, offene Risiken, Strategieversion)
- [ ] Signaltreue: alle Signale (veröffentlicht/abgelehnt/abgelaufen/risk-blockiert/nicht gefüllt) bleiben gespeichert — keine nachträgliche Löschung aus Performance

**Gate P6 (Abnahme):**
- [ ] Position wird nicht mehreren Strategien zugerechnet; Shadow ≠ Paper getrennt
- [ ] Backtestdaten tauchen nicht in Live-Kennzahlen auf; abgelehnte/abgelaufene Signale bleiben sichtbar

---

## Phase 7 — Backtest-Härtung `P2` · Epic: RES

- [ ] Gemeinsamer Strategiecode: Backtest nutzt produktives Strategiemodul (keine abweichende Implementierung); Clock-/Data-Provider abstrahiert
- [ ] Multi-Timeframe-Korrektheit: nur abgeschlossene Bars, keine Tagesbar vor Tagesende, Resampling-Grenzen, Zeitzonen konsistent, Entry frühestens nach vollständiger Signalinfo
- [ ] **RES-004** Kostenmodell: Kommissionen, Spread, Slippage, SEC/FINRA-Gebühren, Teilfüllung, Liquiditätsgrenzen, Market-Impact-Näherung
- [ ] Universen: historische Zusammensetzung (point-in-time), Delistings, Survivorship-Bias messen + dokumentieren, Universums-Version speichern
- [ ] Validierung: Nested Walk-forward, Purging, Embargo, finaler Holdout, Sensitivitätsanalyse, Bootstrap-Konfidenzintervalle, Regimeauswertung
- [ ] **RES-003** Reproduzierbarkeit: je Run Run-ID, Git-Commit, Strategie-/Datenversion, Universum, Parameter, Kostenmodell, Zeitraum, Seed, Dependency-Versionen

**Gate P7 (Abnahme):**
- [ ] Gleicher Run + gleiche Version → gleiches Ergebnis; kein Look-ahead in Tests
- [ ] Kosten/Slippage separat sichtbar; finaler Holdout technisch gesperrt; Report nennt #getesteter Kandidaten

---

## Phase 8 — Strategie-Labor begrenzen `P2` · Epic: RES

- [ ] **RES-006** Champion-Candidate-Modell: genau 1 Champion je Strategie, mehrere Candidates (Backtest → Shadow → optional Paper)
- [ ] Suchraum: explizite Parametergrenzen, max. Kandidaten/Zyklus, keine unbegrenzte Feature-Erzeugung, LLM nur für beschreibende Hypothesen (hart validiert)
- [ ] Promotion-Gates: ausreichende Tradezahl, OOS-Mehrheit, keine starke DD-Verschlechterung, positive Netto-Performance, Sensitivitätsstabilität, Shadow-Bestätigung, **menschliche Freigabe**
- [ ] **RES-005** Holdout-Schutz: Zugriff protokollieren, Kandidaten nicht wiederholt am Holdout testen, neuer Holdout erst nach Releasezyklus
- [ ] Pending-Workflow (generated→validated→backtested→shadow→pending_review→approved/rejected→archived); kein direkter Live-Parameter-Schreibzugriff

**Gate P8 (Abnahme):**
- [ ] Labor kann keine Live-Strategie direkt ändern; jeder Kandidat reproduzierbar
- [ ] Jede Promotion menschlich bestätigt; LLM-Ausgabe setzt keinen Produktionscode/Live-Parameter

---

## Phase 9 — Sicherheit, Deployment & Observability `P2` · Epic: PLAT

- [ ] **PLAT-008** Systemd-Härtung je Dienst: eigener Nutzer (kein Root), `NoNewPrivileges`, `PrivateTmp`,
      `ProtectSystem=strict`, `ProtectHome`, `RestrictAddressFamilies`, nur nötige Schreibpfade, Restart-Policy, Resource Limits *(→ todo.md A1)*
- [ ] **PLAT-006** Dependencies pinnen (Lockfile/Constraints), `pip-audit`, Dependabot, Upgrade-Tests *(→ todo.md A2, inkl. yfinance-FD-Leck-Fix)*
- [ ] **PLAT-006** Secrets: `.env` nur lokal; Staging/Prod systemd-Credentials/Secret Store; Rotation; getrennte Schlüssel; kein Secret in Logs/Exceptions
- [ ] **PLAT-007** Alpaca OAuth (minimale Scopes, Token verschlüsselt, Disconnect + Revoke, Paper/Live getrennt)
- [ ] **PLAT-004** Strukturiertes JSON-Logging (timestamp, service, severity, trace_id, user_id pseudonymisiert, entity_id, event_type) — keine Keys/PII
- [ ] **PLAT-005** Monitoring-Metriken (Verfügbarkeit, Feed-Latenz, Quote-Alter, Orderlatenz, Reject/Fill-Rate, Reconciliation-Fehler, Queue-Lag, Positionen ohne Stop, Kill-Switch-Status) + Alarmregeln
- [ ] **PLAT-009** Verschlüsselte PostgreSQL-Backups, Aufbewahrungsplan, regelmäßiger Restore-Test, Recovery-Ziele

**Gate P9 (Abnahme):**
- [ ] Kein Dienst läuft als Root; keine Prod-Secrets in `.env`; kritische Fehler → Alarm
- [ ] Restore-Test erfolgreich; Nutzer kann Brokerzugriff sofort widerrufen; Position ohne Schutz wird erkannt

---

## Phase 10 — Teststrategie & Paper-Freigabe `P2`

- [ ] Unit-Tests: Sizing, Risk-Limits, Kalender, Strategiebedingungen, Zustandsübergänge, Idempotency, Berechtigungen, Kill-Switch
- [ ] Integrationstests: Signal→Freigabe→Risk→Order, doppelte Freigabe, abgelaufenes Signal, veralteter Quote, Risk-Block, Partial Fill, Broker-Reject, Cancel/Replace, Reconciliation-Abweichung, Schutzorder
- [ ] Replay-Tests: normaler Fill, Event doppelt/verspätet, Partial Fill, Fill nach Cancel, unbekannte Order, externe Position
- [ ] Failure-Injection: Feed offline, Broker-Timeout, DB-Unterbrechung, Queue-Retry, Worker-Neustart, doppelter Callback/Request, Clock-Skew, veraltete Marktdaten
- [ ] **Paper-Burn-in**: mehrere Marktwochen, ≥1 Feiertag/Half-Day, versch. Regime, dokumentierte Fehlerquote, keine ungeklärten Reconciliation-Abweichungen, keine doppelten Orders, keine Budgetüberschreitung
- [ ] Go/No-Go-Checkliste erstellt + abgezeichnet

**Gate P10 (Abnahme):**
- [ ] Keine doppelten Orders in allen Wiederholungstests; keine unkontrollierte Order bei Feed-/Brokerfehler
- [ ] Alle kritischen Failure-Fälle dokumentiert; keine ungeklärten Positionsabweichungen; Kill-Switch in Integrationstests bestätigt

---

## Phase 11 — Canary Live `P2→P3` (separate Entscheidung)

- [ ] Voraussetzungen: regulatorische Einordnung, alle P0/P1/P2 fertig, Paper-Burn-in dokumentiert, OAuth funktioniert, Kill-Switches + Reconciliation getestet, Incident-Prozess definiert
- [ ] Begrenzungen: nur freigegebenes Konto, sehr kleines Risikobudget, max. 1 Strategie, 1–2 Positionen, nur liquide Aktien/ETFs, keine Overnight-Position, keine Auto-Skalierung
- [ ] Canary-Metriken erfassen (Signal→Order/Order→Fill-Latenz, Slippage, Fill/Reject-Rate, Spread, Positionsabweichungen, Schutzorderstatus, manuelle Eingriffe, Incidents)
- [ ] Abbruchkriterien scharf schalten (doppelte Order, falsche Größe, fehlende Schutzorder, unerklärte Abweichung, Verlustlimit, veraltete Daten, falsche Handelszeit, nicht-auditierbare Aktion → Sofort-Stop)
- [ ] Deliverables: Canary-Konfig + Risk-Profil, täglicher Report, Incident-Log, Abschlussentscheidung

---

## Phase 12 — Regulatorischer & kommerzieller Modus `P3`

- [ ] User Journeys prüfen (Signal generisch/personalisiert, Telegram-Freigabe, Orderübermittlung, Auto-Exits, Strategieauswahl, Rankings/Werbung, Gebühren, Affiliate/Vergütung)
- [ ] Dokumente erstellen (Produktbeschreibung, User Journey, Rollenmodell, Datenflussdiagramm, Brokerbeziehung, Vergütung, Konfliktregister, Risikohinweise, Performance-Darstellung, Datenschutz, Aufbewahrung, Beschwerde-/Incident-Prozess)
- [ ] Go/No-Go: kein öffentlicher Live-Launch ohne schriftliche Einordnung + umgesetzte Anforderungen

---

## Querschnitts-Arbeitspakete (parallel, wo passend)

- [ ] **Paket A – Konfig/Flags:** zentrale typisierte Settings-Klasse, sichere Defaults, Modusvalidierung beim Start, Start verweigern bei riskanter Fehlkonfig
- [ ] **Paket B – Domain Events:** versioniertes Event-Set (SignalGenerated…KillSwitchActivated), Schema dokumentiert, Consumer idempotent, Trace-ID durchgängig
- [ ] **Paket C – Outbox:** Outbox-Tabelle, atomare Speicherung mit Domänenänderung, Auslieferungs-Worker, Retry, Dead-Letter, Rückstand-Monitoring
- [ ] **Paket D – Notifications:** Telegram + Web-SSE als Consumer, keine Handelslogik im Notification-Code, Templates (Signal/Fill/Reject/Kill-Switch), Retry + Dedup
- [ ] **DB-Migrationsmapping:** trades→positions+position_events · signals→signals+signal_candidates · broker keys→broker_connections · strategy name→strategies+strategy_versions · status text→Enums · P&L→fills+performance_snapshots
- [ ] **API v1** (siehe Plan §23): Idempotency-Header bei mutierenden Trade-Aktionen, rollenbasierte Autorisierung, Pydantic-Schemas, Trace-ID je Antwort
- [ ] **Telegram-Umbau:** direkte Brokeraufrufe/Hebel/Optionen/komplexe Settings raus; behalten: Signale, Annehmen/Ablehnen, Positionen, Kill-Switch, Web-Link; Callback-Sicherheit (opaque ID, serverseitige Auflösung, Ablauf, Nutzerbindung, Einmal-/idempotent) — Nachrichtenaufbau nach [Stylekonzept.md](Stylekonzept.md) §26 (Modus-Präfix, `LIVE · ECHTES GELD`, keine Raketen/Feuer-Emojis)
- [ ] **Web-App-Umbau:** Signalansicht (Entry-Zone/Stop/Risiko/Größe/Kosten/Regime/Datenstatus), Pflicht-Bestätigungsdialog, Risikoübersicht, deutliche Moduskennzeichnung (BACKTEST/SHADOW/PAPER/LIVE, LIVE mit Warnung) — visuelle Umsetzung nach Design-System (siehe unten)

---

## Design-System (Stylekonzept) `P2/P3` · Ref: [Stylekonzept.md](Stylekonzept.md)

Visuelle Ebene über Web-App, Dashboard und Telegram. Dark Mode, risikoorientiert, keine Gamification.
Reihenfolge = Stylekonzept §29. Läuft parallel zu Phase 5/6 und den Web-/Telegram-Umbau-Paketen.

**Style-Phase 1 — Designgrundlage:**
- [ ] Design-Tokens zentral als CSS `:root` einbinden (Stylekonzept §27: `--bg-*`, `--text-*`, `--primary*`, semantische Farben, Border, Spacing, Radien, Schatten)
- [ ] Typografie: Inter (UI) + JetBrains Mono (Zahlen/Kurse/IDs/Timestamps); Größenskala §6.2
- [ ] Spacing- (4px-Basis), Radien- und Schatten-System verankern
- [ ] Icon-Set festlegen (Lucide/Heroicons Outline) — **keine Emoji als Interface-Icons**
- [ ] Statussystem + Modus-Badges (BACKTEST=Violett, SHADOW=Blau, PAPER=Gelb, LIVE=Rot; immer Text+Icon, nie nur Farbe)

**Style-Phase 2 — Kernkomponenten:**
- [ ] Button (Primär/Sekundär/Destruktiv/**Live-Order**), alle Zustände (Default/Hover/Active/Focus/Disabled/Loading/Success/Error; Loading blockt Doppelausführung)
- [ ] Input, Select, Dialog, Alert, Card, Status-Chip, Tabelle, Tooltip, Tabs — alle aus Tokens

**Style-Phase 3 — Hauptseiten:**
- [ ] Dashboard/Übersicht mit Info-Hierarchie Risiko→Status→Aktionen→Performance→Historie (Gewinn nicht größte Fläche)
- [ ] Signale (Signalkarte §11.2: „Trade prüfen" statt grüner Kaufen-Button, keine „Top Pick"/Dringlichkeit)
- [ ] Positionen, Orders, Performance (Modi getrennt, nie in einer Linie), Einstellungen

**Style-Phase 4 — Risikointeraktionen:**
- [ ] Trade-Bestätigungsdialog (feste Reihenfolge §18.1; Live: roter Hinweis + „Echtes Geld" + Volltext-Button)
- [ ] Kill-Switch-UI, Brokerstatus, Risk-Profile-Editor (Slider/Presets statt freier Limits), Fehler-/Unsicherheitszustände
- [ ] Microcopy nach §25 (sachlich; „Durch Risikoregel blockiert" statt „Jetzt zuschlagen")

**Style-Phase 5 — Responsive & Accessibility:**
- [ ] Mobile Layout (Bottom-Nav; Signal-Freigabe §23.4 ohne Horizontal-Scroll: Modus/Ticker/Entry/Stop/Risiko/Größe/Ablauf)
- [ ] Tastaturbedienung + sichtbarer Fokus, Screenreader/semantisches HTML, Touch-Ziele ≥ 44×44 px
- [ ] Kontrastprüfung (WCAG 2.1 AA), `prefers-reduced-motion` respektiert, Charts mit textlicher Alternative

**Gate Style (DoD Style-System, §30):**
- [ ] Alle Hauptseiten nutzen dieselben Tokens; Dark Mode durchgängig
- [ ] Keine kritische Info nur farblich; Betriebsmodus auf jeder relevanten Seite sichtbar; Live-Aktionen eindeutig gekennzeichnet
- [ ] Kernkomponenten dokumentiert; Desktop/Tablet/Mobile unterstützt; Fokus sichtbar; Kontrast geprüft
- [ ] Telegram-Nachrichten nutzen dieselbe Terminologie wie die Web-App

---

## Definition of Done — Version 1

- [ ] **Produkt:** nur US-Aktien + US-Aktien-ETFs, Long-only, 1×, keine Optionen, Paper Standard, ≤ 3 Strategiefamilien
- [ ] **Daten:** Exchange-Kalender integriert, Datenprovider abstrahiert, Produktionssignale nicht von yfinance abhängig, Datenherkunft gespeichert
- [ ] **Risiko:** zentraler Risk Service, risikobasiertes Sizing, Tagesverlustlimit, Exposure-Limits, Kill-Switches, keine Budgetüberschreitung
- [ ] **Execution:** zentrales OMS, Idempotency, Broker-Event-Verarbeitung, Partial-Fill-Logik, Reconciliation, vollständiges Audit
- [ ] **Research:** Backtest/Shadow/Paper/Live getrennt, Strategieversionierung, reproduzierbare Backtests, Labor ohne direkte Live-Änderungen
- [ ] **Betrieb:** PostgreSQL, dedizierte Systemnutzer, gepinnte Dependencies, sichere Secrets, Monitoring, Backups, Restore-Test
- [ ] **Qualität:** Unit-/Integrations-/Replay-/Failure-Tests, Paper-Burn-in erfolgreich, keine offenen P0/P1, dokumentiertes Go/No-Go

---

## Dauerhafte Leitplanken (immer gültig)

- [ ] Paper-Modus bleibt Standard · Live nur mit separater Freigabe
- [ ] Neue Positionen brauchen Human Gate · Schutz-Exits dürfen nach Zustimmung automatisch laufen
- [ ] Kein Hebel/keine Optionen in V1 · kein yfinance im Produktionssignalpfad · keine Budgetüberschreitung
- [ ] Keine direkte Order aus Telegram/Web · keine Vermischung von Backtest/Shadow/Paper/Live
- [ ] Keine direkte Live-Änderung durch den Optimizer · jede Entscheidung reproduzierbar · jede Brokeraktion auditierbar
- [ ] Bei unklarer Daten-/Brokerlage werden keine neuen Positionen eröffnet
