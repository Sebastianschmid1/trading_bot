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
- [x] **TSAFE-002** Hebel hart blockieren: UI-Auswahl entfernen (Web + Telegram), Backend validiert `leverage == 1`
- [x] **TSAFE-002** Migration: gespeicherte Hebelwerte > 1 auf 1 setzen; Orders mit Hebel > 1 ablehnen
      (`db._migrate_leverage_values` klemmt bei jedem Start `users.leverage` sowie den Hebel im
      `signal_json` noch offener Trades auf `MAX_LEVERAGE`; `webapp._execute_broker_order_for_web`
      und `bot._maybe_broker_order` lehnen echte Broker-Orders mit Hebel > `MAX_LEVERAGE` jetzt
      explizit ab, statt sie still auf Aktien/1x herabzustufen — schließt auch die Lücke, dass
      Telegram bislang bei Hebel > 1 einen echten Optionskontrakt wählen/kaufen konnte.)
- [x] **TSAFE-003** Optionen deaktivieren: `broker.submit_option_buy` lehnt bei `ALLOW_OPTIONS=false`
      serverseitig ab; Optionskonfig als deprecated markiert. Optionen ohnehin über Hebel-Deckel
      (TSAFE-002) unerreichbar; separate Options-UI existiert nicht (war an Hebel-Auswahl gekoppelt, entfernt).
- [x] **TSAFE-004** Budgetüberschreitung entfernt: Live-Pfad übergibt `roundup_factor=1.0` (kein Aufrunden
      auf ganze Aktie über Budget); zu teure Aktie → Bruchteil/Notional bzw. Vormerkung. `SHARE_ROUNDUP_FACTOR` deprecated (Default 1.0).
- [x] **TSAFE-005** Score-Exit deaktiviert: `bot.evaluate_active_trade` schließt nicht mehr bei
      `strength < SIGNAL_CLOSE_THRESHOLD`; Tests angepasst.
- [x] **TSAFE-005** Explizite Exits bleiben: Liquidation/Stop-Loss/Take-Profit + Höchsthaltedauer/EOD.
      Voll strategiespezifische Exits folgen in Phase 5; Alt-Trades tragen ihren Close-Grund unverändert (self-identifizierend).
- [x] **TSAFE-006** Direkte Brokeraufrufe inventarisiert → `docs/BROKER_CALLS_INVENTORY.md`
      (alle `submit_*`/`close_position`/`cancel_order`-Stellen in Telegram/Web/Scheduler + bestehende Gates).
- [x] **TSAFE-007** EOD-22:15-Schließung: `DEFAULT_EOD_CLOSE=False` (feste Berlin-Zeit war session-blind;
      session-relative Schließung folgt Phase 2/DATA-002). Interim: neue Positionen entstehen nur über die
      Signal-Jobs während der US-Sitzung; hartes Session-Gate über Exchange-Kalender in Phase 2.
- [x] **TSAFE-007** Zentrale Risk-/Order-Vorprüfung eingeführt: `stockbot/core/risk.py::pretrade_check`
      (Seam für Phase 3) — prüft Live-Gate, Hebel-Deckel, Optionsverbot; volles Risikomodell in Phase 3.

**Gate P0 (Abnahme):**
- [x] Kein Codepfad kann eine Live-Order senden — Live-Guard + erzwungener Paper-Modus
- [x] Jede Order mit Hebel > 1 / jede Optionsorder wird abgelehnt (TSAFE-002 / TSAFE-003)
- [x] Keine Order überschreitet Budget/Buying Power (TSAFE-004 + bestehender Buying-Power-Check)
- [x] Kein Trade wird allein wegen globalem Score geschlossen (TSAFE-005)
- [x] Tests beweisen: Telegram und Web senden keine direkten Brokerorders — Live-Orders bereits
      serverseitig geblockt; `tests/test_no_direct_broker_access.py` beweist statisch, dass
      `bot.py`/`webapp.py` das Alpaca-SDK nie direkt aufrufen und nur die gate-geprüften
      `broker.*`-Funktionen nutzen. Der Idempotency-/Zustandsmaschinen-Nachweis kommt mit dem OMS (Phase 4).

---

## Phase 1 — Domänenmodell & PostgreSQL `P1` · Epic: PLAT

Ziel: Belastbares Zustands- und Datenmodell.

- [!] **PLAT-001** PostgreSQL lokal + Staging bereitstellen; Alembic (o.ä.) einführen; Connection Pool + Transaktionsgrenzen
      (Lokaler Teil erledigt + verifiziert: `docker-compose.yml` für lokales Postgres,
      `alembic.ini`/`migrations/` mit initialer Schema-Migration [Spiegel von
      `docs/DB_SCHEMA_SQLITE.md`, 7 Tabellen] und `stockbot/core/db_pool.py`
      [SQLAlchemy-Connection-Pool + `session_scope`-Transaktionsgrenze]; `tests/test_migrations.py`/
      `tests/test_db_pool.py` laufen sowohl gegen SQLite als auch — wenn erreichbar — gegen ein
      echtes lokales PostgreSQL unter `config.POSTGRES_DSN` [übersprungen, wenn keins läuft].
      Modul wird von keinem Live-Codepfad genutzt — SQLite bleibt bis zum dokumentierten Cutover
      Quelle der Wahrheit. Blockiert: eine echte Staging-Instanz [persistenter Server außerhalb
      dieser Sandbox] bereitzustellen ist eine Infra-/Ops-Entscheidung, die ein Mensch mit
      VPS-/Hosting-Zugriff treffen und ausführen muss — kein Code-Task und durch die
      Kein-Deploy-Leitplanke dieser Session dauerhaft ausgeschlossen.)
- [x] **PLAT-001** Bestehendes SQLite-Schema dokumentieren + einfrieren; read-only Export aufbewahren
      (`docs/DB_SCHEMA_SQLITE.md` friert den Stand ein; `stockbot/core/db_export.py` /
      `tools/export_sqlite_snapshot.py` schreiben einen read-only-Snapshot aller Tabellen
      als JSON für den späteren Zeilen/Summen-Vergleich nach der Postgres-Migration.)
- [!] **PLAT-001** Datenmigration schreiben; Testmigration auf Kopie; Zeilen/Summen vergleichen; Paper auf PostgreSQL umstellen
      (`stockbot/core/db_migrate.py` schreibt einen `db_export`-Snapshot in eine per Alembic
      angelegte Zielengine und vergleicht danach Zeilenzahlen + Summen je Tabelle
      [`tests/test_db_migrate.py`, Testmigration gegen eine Kopie]. Läuft zusätzlich zur
      SQLite-Kopie gegen ein echtes lokales PostgreSQL, wenn erreichbar — hat dabei einen
      echten Treiberunterschied aufgedeckt und fixiert [psycopg2 liefert `BYTEA` als
      `memoryview`, nicht `bytes`]. Blockiert wie der Punkt darüber: echtes Staging-Postgres
      befüllen (Schritt 6) + Paper-Laufzeit auf Postgres umstellen [Schritt 7] brauchen eine
      echte, von einem Menschen bereitgestellte Staging-Instanz — kein Deploy aus dieser Session.)
- [x] Domänenobjekte definieren: User, RiskProfile, BrokerConnection, Strategy, StrategyVersion, Signal,
      SignalCandidate, TradeIntent, RiskDecision, Order, OrderEvent, Fill, Position, PositionEvent, KillSwitch, AuditEvent
      (`stockbot/core/domain.py`: reine, IO-freie Dataclasses + Status-Enums nach Plan.md §9.2/§9.4/§11.1/§12.1;
      noch nicht an ORM/DB gebunden — das ist der nächste Schritt zusammen mit den Zustandsmaschinen.
      `RiskDecision` bleibt bewusst der bestehende Typ aus `stockbot/core/risk.py`, keine Dopplung.)
- [x] Zustandsmaschine **Signal** (generated→filtered→published→accepted/rejected/expired/blocked_by_risk→order_created)
      (`stockbot/core/state_machine.py::signal_transition_allowed`/`assert_signal_transition`;
      Interpretation: `filtered` kann direkt nach `rejected`/`expired` wechseln, wenn der Filter
      das Signal aussortiert, ohne es zu veröffentlichen; `order_created` folgt nur aus `accepted`.)
- [x] Zustandsmaschine **Order** (created→validated→submitted→accepted_by_broker→partially_filled→filled / cancel_requested→cancelled / rejected / expired)
      (`stockbot/core/state_machine.py::order_transition_allowed`/`assert_order_transition`;
      `cancel_requested` erlaubt zusätzlich `partially_filled`/`filled`, weil ein Fill in-flight
      sein kann, bevor die Stornierung beim Broker wirksam wird.)
- [x] Zustandsmaschine **Position** (pending_open→open→pending_close→closed / reconciliation_required)
      (`stockbot/core/state_machine.py::position_transition_allowed`/`assert_position_transition`;
      `reconciliation_required` ist von `pending_open`/`open`/`pending_close` aus erreichbar und
      löst sich nach Klärung zu `open` oder `closed` auf, statt ein eigener linearer Schritt zu sein.)
- [x] Zentrale Validierung: ungültige Zustandsübergänge werden abgelehnt
      (`stockbot/core/state_machine.py::assert_transition`/`transition_allowed` dispatchen anhand
      des Enum-Typs von `from_status` an Signal-/Order-/Position-Zustandsmaschine — ein Einstiegspunkt
      für alle drei, wie in Plan.md §9.3 gefordert; lehnt zusätzlich Typ-Mismatches ab.)
- [x] **PLAT-002** Audit-Log append-only (Event-ID, Timestamp, User, Actor, Entity-Typ/-ID, Aktion, alt/neu, Trace-ID, Quellkanal, Metadaten)
      (`stockbot/core/audit_log.py::AuditLog` — reiner In-Prozess-Store für `AuditEvent`
      [alle Plan.md-§9.4-Felder], erzwingt Append-only strukturell durch Fehlen von
      update()/delete(); persistente Anbindung folgt mit dem Postgres-Cutover.)

**Gate P1.1 (Abnahme):**
- [ ] Nutzer + Trades migrierbar; Zustandsübergänge getestet; ungültige abgelehnt
- [ ] Jede Brokeraktion erzeugt Audit-Event; alles über Trace-IDs nachvollziehbar

---

## Phase 2 — Exchange-Kalender & Marktdaten `P1` · Epic: DATA

- [x] **DATA-001** Exchange-Kalender-Bibliothek wählen; NYSE/Nasdaq integrieren
      (`pandas_market_calendars` gewählt — baut auf der bereits vorhandenen `pandas`-Abhängigkeit
      auf, deckt NYSE/Nasdaq-Feiertage + Frühschluss-Tage korrekt ab; `stockbot/core/exchange_calendar.py`.)
- [x] **DATA-001** Funktionen: `is_trading_day`, `market_open/close`, `is_market_open`, `next_market_open`, `minutes_to_close`, `is_early_close`
      (alle in `stockbot/core/exchange_calendar.py`, DST-robust [Sitzungsdauer-Vergleich statt fester
      Uhrzeit für `is_early_close`]. Noch von keinem Live-Codepfad genutzt — Umstellung von
      `bot.py::_us_market_open`/Scheduler ist DATA-002.)
- [x] **DATA-002** Scheduler umstellen: feste Berlin-Zeiten → relativ zu Open/Close; Reports separat in Europe/Berlin
      (Erledigt: `bot._us_market_open` nutzt `stockbot/core/exchange_calendar` statt reinem
      Wochentag+ET-Fenster-Check — Feiertage [z. B. Thanksgiving] und Frühschluss-Tage [z. B. Black
      Friday, 9:30–13:00 ET] schließen den Markt jetzt korrekt. Betrifft alle Aufrufer: Auto-Accept,
      `evaluate_active_trade`, Order-Gates, `refill_pending`, `run_daily_lab_optimization`.
      Zusätzlich: „Signalerzeugung relativ zum Open" + „Intraday-Exit relativ zum Close" (Plan.md §10.1)
      umgesetzt — `send_daily_signals`/`close_and_evaluate` feuern nicht mehr über `job_queue.run_daily`
      zu einer festen Berlin-Uhrzeit, sondern über einen neuen `_session_scheduler_tick`
      [alle `SESSION_TICK_INTERVAL_SEC`], der `exchange_calendar.market_open/close` des jeweiligen
      Handelstags + Offset [`SIGNAL_OPEN_OFFSET_MIN`/`CLOSE_AFTER_CLOSE_OFFSET_MIN`] auswertet — robust
      gegenüber Feiertagen und den ca. 1–3 Wochen/Jahr, in denen EU-/US-Sommerzeitumstellung nicht
      synchron sind. Toleranzfenster [`_SESSION_FIRE_WINDOW_MIN`] verhindert Nachfeuern Stunden nach
      einem Neustart. `SMARTMONEY_SCAN_HOUR`/`BROKER_RECONCILE_HOUR`/`LAB_DAILY_HOUR` bleiben bewusst
      auf fester Berlin-Zeit [nicht direkt session-gated bzw. bereits intern über `_us_market_open`
      abgesichert — geringeres Risiko]. Außerdem „Entry-Sperre relativ zum Close" (Plan.md §10.1,
      Gate P2 „keine Intraday-Position nach Entry-Cutoff") umgesetzt: `exchange_calendar.
      is_past_entry_cutoff` + `ENTRY_CUTOFF_BEFORE_CLOSE_MIN` [Default 15 Min.] zentral in
      `services/trades.py::accept_trade` — gilt für Telegram-Auto-Accept, Telegram-Button-Accept UND
      Web gleichermaßen [ein Seam für beide Kanäle]; betrifft nur neue Einstiege, nie Schutz-Exits.
      Zuletzt: „Reports separat in Europe/Berlin" — der `close_and_evaluate`-Tagesreport-Header zeigte
      bisher den statischen Anzeigewert `CLOSE_TIME_HOUR:CLOSE_TIME_MIN`, obwohl der Job jetzt
      session-relativ [variabel] feuert; zeigt jetzt die tatsächliche `Europe/Berlin`-Uhrzeit im
      Moment der Auswertung. Damit sind alle vier Punkte aus Plan.md §10.1 „Scheduler-Umstellung"
      umgesetzt.)
- [x] **DATA-003** `MarketDataProvider`-Interface (`get_bars/get_quote/stream_quotes/stream_trades/get_corporate_actions/get_market_status`)
      (`stockbot/core/market_data.py`: `MarketDataProvider` als `ABC` mit den sechs Methoden aus
      Plan.md §10.2 + Wertobjekten `Quote`/`CorporateAction`/`MarketStatus` [Provider/Feed/Abrufzeit/
      Exchange-Zeit-Felder für DATA-005]. `get_bars` liefert bewusst ein `pandas.DataFrame`
      [OHLCV, `DatetimeIndex`] — dasselbe Format, das die bestehende Indikator-Berechnung schon
      erwartet, damit die spätere Umstellung bestehender Aufrufer keinen Format-Bruch erzwingt.
      Reines, IO-freies Interface nach dem Muster von `exchange_calendar.py`/`domain.py` — noch von
      KEINEM Live-Codepfad genutzt; die Umstellung von `analyzer.py`/`evaluator.py`/`smartmoney.py`/
      `lookup.py`/`db.py` [aktuell direktes `yfinance`] folgt mit den konkreten Implementierungen.)
- [x] **DATA-003** Implementierungen: `YFinanceResearchProvider`, `AlpacaPaperMarketDataProvider` (später `LicensedProductionProvider`)
      (Beide in `stockbot/market/data_providers.py`. `YFinanceResearchProvider` bündelt die
      bislang verstreuten `yfinance`-Aufrufe [`Ticker.history`/`fast_info`/`splits`/`dividends`]
      hinter dem `MarketDataProvider`-Interface; `get_market_status` nutzt den bestehenden
      Exchange-Kalender statt eigener Zeitfensterlogik. `AlpacaPaperMarketDataProvider` nutzt die
      bereits vorhandene `alpaca-py`-Abhängigkeit [`StockHistoricalDataClient`/
      `CorporateActionsClient`/`TradingClient.get_clock()`, dieselben Zugangsdaten wie
      `broker/client.py`, aber ein eigener IO-isolierter Marktdaten-Client] — Order-Ausführung und
      Marktdaten bleiben getrennte Verantwortlichkeiten. Beide Provider sind Pull-only;
      `stream_quotes`/`stream_trades` lehnen bewusst mit `NotImplementedError` ab [Alpaca-
      Echtzeit-Streaming via `alpaca.data.live.StockDataStream` folgt separat, falls benötigt].
      `data_client`/`corporate_actions_client`/`trading_client` sind injizierbar, damit Tests ohne
      echte Alpaca-Keys/Netzwerk auskommen [gleiches Prinzip wie `tests/test_broker.py`].
      `LicensedProductionProvider` bleibt bewusst offen — noch keine konkrete lizenzierte
      Datenquelle ausgewählt. Noch von KEINEM Live-Codepfad genutzt; DATA-003 damit abgeschlossen,
      soweit ohne echte Alpaca-Keys/Lizenzentscheidung möglich.)
- [x] **DATA-005** Datenherkunft je Berechnung speichern (Provider, Feed, Abrufzeit, Exchange-Zeit, Datenversion, Qualitätsstatus)
      (`stockbot/core/domain.py::Signal` trug bereits `data_provider`/`data_version` [PLAT-001];
      ergänzt um die restlichen vier Plan.md-§10.3-Felder: `data_feed`, `data_fetched_at`
      [Abrufzeit], `data_exchange_time` [Exchange-Zeit], `data_quality_status` — deckt jetzt alle
      sechs geforderten Felder ab. Bewusst als reine Datenfelder ohne Mapping-Hilfsfunktion, damit
      `domain.py` sein bestehendes Muster [„reine, IO-freie Datencontainer", keine Funktionen]
      nicht bricht; `stockbot/core/market_data.py::Quote` trägt dieselben Provider-/Feed-/
      Zeit-Felder bereits auf Roh-Quote-Ebene [DATA-003]. Wie der Rest von `domain.py` noch NICHT
      an eine DB gebunden und von keinem Live-Codepfad genutzt — das ist der dokumentierte,
      bereits bekannte nächste Schritt für das gesamte Domänenmodell, kein neuer Rückstand dieses
      Punkts.)
- [x] **DATA-004** Datenqualitäts-Gates: Quote-Alter, Spread, Bar-Vollständigkeit, keine NaN, Symbol aktiv, kein Halt, Corporate Actions
      (`stockbot/core/data_quality.py`: `QualityDecision`-Dataclass [`ok`/`reason`/`code`] nach
      demselben Muster wie `risk.py::pretrade_check`; einzelne `check_*`-Funktionen für alle
      Plan.md-§10.4-Punkte [inkl. „Marktstatus korrekt", das die Checklisten-Kurzfassung nicht
      nennt] + `evaluate_quality(...)` als Bündelung in fester Reihenfolge [Symbol aktiv → Halt →
      Marktstatus → Quote-Alter → Spread → Bars/NaN → Corporate Actions]. Jeder Einzel-Check läuft
      nur, wenn die dafür nötige Eingabe übergeben wurde — kein Check blockiert allein wegen
      fehlender Eingabe. Baut auf den DATA-003-Wertobjekten [`Quote`/`MarketStatus`/
      `CorporateAction`] auf. Noch von KEINEM Live-Codepfad genutzt — Verdrahtung in den
      Produktionssignalpfad [Gate P2: „veraltete Quotes blockieren Orders"] setzt die noch
      offene Migration bestehender Aufrufer auf `MarketDataProvider` voraus.)
- [!] Rohdatenarchiv (Metadaten in PostgreSQL, Rohdaten als Parquet, Partition nach Symbol/Datum/Timeframe; Rohdaten getrennt von Features)
      (`stockbot/core/raw_data_archive.py`: `write_bars`/`read_bars` schreiben/lesen unveränderte
      OHLCV-Rohdaten als Parquet [neue Abhängigkeit `pyarrow`], partitioniert nach
      `<Symbol>/<Datum>/<Timeframe>.parquet` unter `data/raw_archive/` — getrennt von den
      bestehenden Feature-/Indikator-Berechnungen. Ein erneuter Schreibvorgang derselben Partition
      ersetzt den alten Snapshot statt zu duplizieren. `write_bars` liefert eine
      `RawDataArchiveEntry` [Provider/Abrufzeit/Zeilenanzahl/Dateipfad — dieselben
      Provenance-Felder wie `Quote`/`Signal`, DATA-003/DATA-005] zurück. Blockiert wie die beiden
      PLAT-001-Punkte oben: „Metadaten in PostgreSQL" braucht eine echte, von einem Menschen
      bereitgestellte Staging-Instanz — kein Code-Task dieser Session und durch die
      Kein-Deploy-Leitplanke dauerhaft ausgeschlossen; sobald sie existiert, kann
      `RawDataArchiveEntry` über den bestehenden `db_pool`-Seam persistiert werden. Noch von
      KEINEM Live-Codepfad genutzt.)

**Gate P2 (Abnahme):**
- [ ] DST-Wechsel + Half Days korrekt; keine Intraday-Position nach Entry-Cutoff
- [ ] Signal speichert Provider + Datenversion; veraltete Quotes blockieren Orders

---

## Phase 3 — Risk Service & Position Sizing `P1` · Epic: RISK

- [x] **RISK-001** `RiskProfile`-Modell (account_risk_per_trade_pct, daily_loss_limit_pct, max_open_positions,
      max_position_pct, max_sector/correlated_exposure_pct, max_daily_new_exposure_pct, max_spread_bps,
      max_quote_age_seconds, min_average_dollar_volume, earnings_blackout_days, allow_overnight, allowed_strategies)
      (Bereits mit PLAT-001 umgesetzt: `stockbot/core/domain.py::RiskProfile` trägt exakt diese
      Felder [Plan.md §11.1] inkl. der konservativen Defaults aus RISK-002 [`account_risk_per_trade_pct
      =0.25`, `daily_loss_limit_pct=1.00`, `max_open_positions=5`] — `tests/test_domain.py::
      test_risk_profile_has_all_plan_fields` deckt es ab. War beim Schreiben von PLAT-001 als
      Domänenobjekt bereits vollständig, wurde hier nur nachträglich als RISK-001 abgehakt statt neu
      implementiert. Wie der Rest von `domain.py` noch NICHT an eine DB gebunden/an keinen Live-
      Codepfad angebunden — das Sizing/die Pre-Trade-Prüfung, die es tatsächlich NUTZT, ist RISK-002/
      RISK-003.)
- [x] **RISK-002** Sizing-Service: `Risikobetrag = Kontowert × Risiko_pro_Trade`, `Stückzahl = Risikobetrag / Stop-Abstand` + Caps
      (`stockbot/core/risk_sizing.py::size_position` — reine Formel nach Plan.md §11.2, gedeckelt
      durch `risk_profile.max_position_pct` [verhindert, dass ein sehr enger Stop eine
      unverhältnismäßig große Position ergibt]. Bewusst getrennt von `stockbot/broker/sizing.py`
      [das plant eine Order für ein bereits FESTES Euro-Budget; hier wird die Positionsgröße erst
      aus dem Kontorisiko berechnet]. `SizingResult`-Dataclass nach demselben `ok`/`reason`/`code`-
      Muster wie `risk.py::pretrade_check`/`data_quality.py`. Noch von KEINEM Live-Codepfad
      genutzt — Verdrahtung folgt mit RISK-003.)
- [x] **RISK-002** Konservative Defaults: 0,25 %/Trade, 1,00 % Tagesverlust, max 5 Positionen, 1× Exposure
      (Bereits mit RISK-001/PLAT-001 in `domain.RiskProfile` gesetzt und getestet
      [`account_risk_per_trade_pct=0.25`, `daily_loss_limit_pct=1.00`, `max_open_positions=5`];
      „1× Exposure" ist keine RiskProfile-Feld-Konstante, sondern die Summenwirkung der Caps —
      wird durch `risk_sizing.size_position`s `max_position_pct`-Deckel je Einzelposition erzwungen,
      ein aggregierter Portfolio-Exposure-Deckel über mehrere offene Positionen folgt mit RISK-005.)
- [~] **RISK-003** Pre-Trade-Checks in fester Reihenfolge (Kill-Switch → Modus → Signal gültig → Strategie erlaubt →
      Markt offen → Quote frisch → Spread → Liquidität → Tagesverlustlimit → max Positionen → bestehende Ticker-Position →
      Exposure/Sektor → Sizing → Buying Power → Brokerstatus)
      (`stockbot/core/risk.py::pretrade_check` erweitert: nach Kill-Switch/Hebel/Optionen [Phase 0]
      jetzt zusätzlich Signal gültig/nicht abgelaufen [`signal_status`/`signal_expires_at`],
      Strategie erlaubt [`strategy_key`/`allowed_strategies`, leere Liste blockiert nichts —
      RiskProfile-Default], Markt offen [DATA-002], Quote frisch/Spread [DATA-004, delegiert an
      `data_quality.check_quote_age`/`check_spread`] und Liquidität [`average_dollar_volume`/
      `min_average_dollar_volume`] — feste Reihenfolge nach Plan.md §11.3, jeder Check optional
      [nur geprüft, wenn die nötige Eingabe übergeben wurde]. Bewusst weiterhin broker-/IO-frei;
      neue Tests in `tests/test_risk.py`. Noch offen: Tagesverlustlimit/max Positionen/bestehende
      Ticker-Position/Exposure-Sektor [RISK-004/RISK-005, brauchen eine Live-Kontoabfrage —
      offene Positionen/Tages-P&L —, die den bislang reinen IO-freien Charakter dieses Seams
      sprengen würde], Sizing-Integration [RISK-002 `risk_sizing.size_position` existiert,
      ist aber noch nicht in `pretrade_check` verdrahtet], Buying Power/Brokerstatus [Live-
      Broker-Abfrage]. Noch von KEINEM Live-Codepfad genutzt.)
- [x] **RISK-004** Tagesverlustlimit laufend fortschreiben; blockiert neue Positionen
      (`stockbot/core/daily_loss_limit.py::check_daily_loss_limit` — reine, zustandslose
      Entscheidungsfunktion nach demselben `ok`/`reason`/`code`-Muster; „laufend fortschreiben"
      bedeutet hier: der Aufrufer übergibt bei jeder Prüfung den aktuellen realisierten
      Tages-P&L [z. B. aus `db.get_all_trades_between(user_id, heute, heute)`], die Funktion
      selbst hält keinen eigenen State. Zählt bewusst nur REALISIERTE P&L geschlossener Trades,
      nicht unrealisierte Verluste offener Positionen. In `risk.py::pretrade_check` verdrahtet
      [neue optionale Parameter `realized_pnl_today`/`account_value`/`risk_profile`, Schritt 10
      der Plan.md-§11.3-Reihenfolge — nur geprüft, wenn alle drei übergeben wurden]. Der
      Aufrufer [Telegram/Web] muss den heutigen realisierten P&L weiterhin selbst live ermitteln
      und übergeben — das bleibt ein separater Wiring-Schritt an den konkreten Aufrufstellen.
      Noch von KEINEM Live-Codepfad genutzt.)
- [~] **RISK-005** Exposure-Limits (Einzel/Sektor/korreliert/täglich neu); Post-Trade: offene Position ohne Schutzorder erkennen
      (Exposure-Limits erledigt: `stockbot/core/exposure.py` — vier reine `check_*`-Funktionen
      [Einzel/Sektor/Korreliert/Täglich neu, je gegen die passenden `domain.RiskProfile`-Deckel]
      + `evaluate_exposure(...)` als Bündelung in fester Reihenfolge. Sektor-/Korrelationsgruppe
      sind ein Eingabe-String je Position [`ExposurePosition.sector`/`correlation_group`] — das
      Modul berechnet KEINE Sektor-Klassifikation/Kurskorrelation selbst, das bleibt ein
      separater Wiring-Schritt [z. B. `yfinance` `.info['sector']`]. Fehlt die Angabe für den
      Kandidaten, wird der jeweilige Check übersprungen statt fälschlich zu blockieren. Noch
      offen: „Post-Trade: offene Position ohne Schutzorder erkennen" [eigenes, separates Thema]
      und die Verdrahtung in `risk.py::pretrade_check`. Noch von KEINEM Live-Codepfad genutzt.)
- [x] **RISK-006** Kill-Switch-Service (`activate/deactivate_global`, `activate/deactivate_user`,
      `is_new_position_allowed`, `is_protective_exit_allowed`)
      (`stockbot/core/kill_switch.py::KillSwitchService` — reiner In-Prozess-Store für
      `domain.KillSwitch` nach demselben Muster wie `audit_log.py::AuditLog`; alle vier
      Plan.md-§11.5-Methoden plus `global_status`/`user_status` zum Auslesen. Globaler Kill-Switch
      blockiert alle Nutzer, User-Kill-Switch nur den betroffenen; `is_new_position_allowed`
      prüft beide. `is_protective_exit_allowed` liefert bewusst immer `True` [Konzept §17.4:
      Schutz-Exits bleiben immer erlaubt] — kein Platzhalter für eine künftige Sperre, sondern
      der explizite, benannte Gegenpol. Noch NICHT an eine DB gebunden [In-Prozess-State geht bei
      Neustart verloren — persistente Anbindung folgt mit dem Postgres-Cutover] und von KEINEM
      Live-Codepfad genutzt.)
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
