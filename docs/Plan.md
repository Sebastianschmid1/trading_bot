# Plan: Umsetzung von Konzept_v1

**Version:** 1.0  
**Stand:** 11. Juli 2026  
**Ziel:** Umbau der bestehenden Trading-Bot-App zu einem kontrollierten Trading Research & Execution Assistant

---

## 1. Zweck dieses Plans

Dieser Plan beschreibt die technische und organisatorische Umsetzung des Zielkonzepts.

Er ist so aufgebaut, dass die bestehende App schrittweise umgebaut werden kann, ohne unnötig alle vorhandenen Komponenten neu zu schreiben.

Die Umsetzung erfolgt nach dem Grundsatz:

> Erst Risiken und Zustände kontrollieren, danach Strategien und Komfortfunktionen erweitern.

Die höchste Priorität haben:

1. Verhinderung unkontrollierter Live-Orders,
2. korrekte Handelszeiten,
3. belastbare Order- und Brokerzustände,
4. risikobasiertes Sizing,
5. Trennung von Backtest, Shadow, Paper und Live,
6. geeignete Produktionsmarktdaten,
7. Reduktion der Produktkomplexität.

---

## 2. Annahmen

Der Plan basiert auf folgenden Annahmen:

- die bestehende Anwendung ist in Python implementiert,
- FastAPI wird weiterhin für die Web-App verwendet,
- Telegram bleibt als Benachrichtigungs- und Freigabekanal bestehen,
- Alpaca bleibt zunächst der primäre Broker,
- der bestehende Code kann schrittweise refaktoriert werden,
- Paper-Trading ist während des Umbaus der einzige aktive Handelsmodus,
- Live-Trading wird bis zum Abschluss der sicherheitskritischen Phasen deaktiviert,
- die bestehenden Strategien und Backtests werden nicht ungeprüft übernommen.

---

## 3. Nicht-Ziele des ersten Umbaus

Nicht Bestandteil der ersten Umsetzung sind:

- Optionen,
- Hebel,
- Margin,
- Short Selling,
- Krypto,
- Rohstoffe,
- weitere Broker,
- mobile Native App,
- öffentliches Copy Trading,
- vollautomatische Strategie-Promotion,
- umfassende Microservice-Infrastruktur mit Kubernetes.

Die Architektur soll modular sein, aber der erste Umbau darf als gut strukturierter modularer Monolith mit getrennten Workern umgesetzt werden.

---

## 4. Prioritätsstufen

### P0 – Kritisch

Muss vor jedem weiteren Live-Test abgeschlossen sein.

- Live-Modus global deaktivieren.
- Hebel und Optionen entfernen oder hart blockieren.
- Budgetüberschreitung entfernen.
- feste Berliner Handelszeiten entfernen.
- Score-Exit unter 35 deaktivieren.
- direkte Brokerorders aus Telegram und Web verhindern.
- zentrale Risk- und Orderprüfung einführen.

### P1 – Hoch

Muss vor einem stabilen Paper-Betrieb abgeschlossen sein.

- PostgreSQL,
- Exchange-Kalender,
- sauberes Order-State-Modell,
- Broker-Event-Verarbeitung,
- Reconciliation,
- Idempotency,
- Audit-Log,
- getrennte Modi und Reports,
- risikobasiertes Sizing.

### P2 – Mittel

Muss vor Canary Live abgeschlossen sein.

- geeigneter Produktionsdatenfeed,
- Portfolio-Allocator,
- Strategieversionierung,
- Shadow-Modus,
- Observability,
- Failure-Tests,
- OAuth,
- dedizierter Secret Store,
- Security Hardening.

### P3 – Nachgelagert

Nach stabiler Version 1.

- weitere Strategien,
- Strategie-Labor erweitern,
- zusätzliche Märkte,
- kommerzieller Multi-User-Modus,
- zusätzliche Broker.

---

## 5. Zielarchitektur

Die erste Zielarchitektur ist ein modularer Monolith mit getrennten Prozessen.

```text
Web / Telegram
      |
      v
Application Services
      |
      +----------------------+
      |                      |
      v                      v
Signal Service         Portfolio Service
      |                      |
      +----------+-----------+
                 |
                 v
             Risk Service
                 |
                 v
        Order Management System
                 |
                 v
          Alpaca Broker Adapter
                 |
                 v
       Broker Events / Reconcile
```

Zusätzliche Komponenten:

```text
Market Data Worker
Strategy Worker
Scheduler Worker
Notification Worker
Research / Backtest Worker
PostgreSQL
Queue oder DB-Outbox
Audit Event Store
Monitoring
```

---

## 6. Empfohlene Repository-Struktur

```text
stockbot/
  api/
    web/
      routes/
      schemas/
      dependencies/
    telegram/
      handlers/
      keyboards/
      middleware/

  application/
    users/
    signals/
    portfolio/
    risk/
    orders/
    broker/
    notifications/
    reporting/

  domain/
    users/
    strategies/
    signals/
    risk/
    orders/
    positions/
    broker/
    common/

  market/
    data/
    calendars/
    universes/
    features/
    strategies/

  execution/
    oms/
    broker_adapters/
    reconciliation/

  research/
    backtest/
    experiments/
    optimize/
    reports/

  infrastructure/
    db/
    migrations/
    queue/
    outbox/
    audit/
    secrets/
    monitoring/

  workers/
    market_data_worker.py
    signal_worker.py
    order_event_worker.py
    notification_worker.py
    scheduler_worker.py

  tests/
    unit/
    integration/
    replay/
    failure/
    security/
```

---

## 7. Phasenübersicht

| Phase | Ziel | Ergebnis |
|---|---|---|
| 0 | System einfrieren und absichern | Kein unkontrollierter Live-Handel |
| 1 | Domänenmodell und Datenbank | Saubere Zustände und Migration |
| 2 | Kalender und Marktdaten | Korrekte Marktzeit und Datenbasis |
| 3 | Risk Service und Sizing | Jede Order innerhalb definierter Limits |
| 4 | OMS und Brokerzustände | Idempotente, nachvollziehbare Orders |
| 5 | Signale und Strategien vereinfachen | Maximal drei klare Strategiefamilien |
| 6 | Shadow, Paper und Reporting | Saubere Performance-Trennung |
| 7 | Backtest-Härtung | Reproduzierbare, realistischere Forschung |
| 8 | Strategie-Labor begrenzen | Kontrollierter Champion-Candidate-Prozess |
| 9 | Sicherheit und Betrieb | Produktionsfähiger Paper-Betrieb |
| 10 | Canary Live | Sehr begrenzter Live-Test |
| 11 | Kommerzieller Modus | Erst nach regulatorischer Freigabe |

---

# Phase 0: Sofortige Stabilisierung

## 8. Ziel

Alle besonders riskanten Funktionen werden deaktiviert oder technisch blockiert.

## 8.1 Aufgaben

### 8.1.1 Globalen Live-Kill-Switch einführen

Neue Konfiguration:

```env
TRADING_MODE=paper
ALLOW_LIVE_TRADING=false
ALLOW_MARGIN=false
ALLOW_OPTIONS=false
ALLOW_SHORTS=false
MAX_LEVERAGE=1
```

Anforderungen:

- Live-Orderversuche werden serverseitig abgelehnt.
- Frontend-Schalter allein dürfen Live nicht aktivieren.
- Telegram darf Live nicht aktivieren.
- Nur eine administrative, protokollierte Konfigurationsänderung darf später Live ermöglichen.

### 8.1.2 Hebel entfernen

- Hebeloptionen aus Web und Telegram entfernen.
- Backend validiert `leverage == 1`.
- bestehende gespeicherte Werte über 1 auf 1 migrieren.
- jede Order mit Leverage größer 1 ablehnen.

### 8.1.3 Optionen deaktivieren

- Long-Call-Logik aus produktivem Pfad entfernen.
- UI-Elemente ausblenden.
- Brokeradapter blockiert Optionssymbole.
- vorhandene Optionskonfiguration als deprecated markieren.

### 8.1.4 Budgetüberschreitung entfernen

Alte Logik:

```text
bis 1,5× Budget aufrunden
```

Neue Logik:

- nie über verfügbares Budget,
- Fractional Shares verwenden, wenn erlaubt,
- ansonsten Trade ablehnen oder verkleinern.

### 8.1.5 Score-Exit deaktivieren

- automatisches Schließen bei Score kleiner 35 entfernen,
- existierende Strategie-Exits explizit implementieren,
- historische Trades mit diesem Exitgrund markieren.

### 8.1.6 Zeitplan entschärfen

- EOD-Schließung um 22:15 Berlin deaktivieren,
- neue Positionen nur in klar definierten Sessions zulassen,
- vorläufig Marktzeiten über Exchange-Kalender ermitteln.

### 8.1.7 Direkte Brokeraufrufe inventarisieren

Suche im Repository nach:

- `submit_order`,
- `close_position`,
- `cancel_order`,
- Alpaca-Client-Aufrufen,
- Ordererzeugung in Telegram,
- Ordererzeugung in Web-Routen,
- Ordererzeugung in Scheduler-Jobs.

Alle direkten Aufrufe dokumentieren.

## 8.2 Deliverables

- Feature-Flag-Konfiguration,
- globaler Kill-Switch,
- entfernte Hebel- und Optionspfade,
- entfernte Budgetüberschreitung,
- entfernte Score-Exit-Regel,
- Liste aller direkten Brokeraufrufe,
- Sicherheits-Migrationsskript.

## 8.3 Akzeptanzkriterien

- Kein Codepfad kann eine Live-Order senden.
- Jede Order mit Hebel größer 1 wird abgelehnt.
- Keine Optionsorder ist möglich.
- Keine Order überschreitet Budget oder Buying Power.
- Kein Trade wird allein wegen eines globalen Scores geschlossen.
- Tests beweisen, dass Telegram und Web keine direkten Brokerorders senden.

---

# Phase 1: Domänenmodell und PostgreSQL

## 9. Ziel

Die Anwendung erhält ein belastbares Zustands- und Datenmodell.

## 9.1 PostgreSQL einführen

### Aufgaben

- PostgreSQL lokal und auf Staging bereitstellen.
- Alembic oder vergleichbares Migrationswerkzeug einführen.
- bestehende SQLite-Struktur dokumentieren.
- Datenmigration schreiben.
- Read-only Export der alten SQLite-Daten aufbewahren.
- Transaktionsgrenzen definieren.
- Connection Pool konfigurieren.

### Migrationsstrategie

1. SQLite-Schema einfrieren.
2. Exportskript erstellen.
3. Transformationsregeln definieren.
4. Testmigration auf Kopie durchführen.
5. Zeilenanzahlen und Summen vergleichen.
6. Staging migrieren.
7. Paper-Betrieb auf PostgreSQL umstellen.
8. SQLite nur noch als Archiv behandeln.

## 9.2 Domänenobjekte definieren

Mindestens:

- User,
- RiskProfile,
- BrokerConnection,
- Strategy,
- StrategyVersion,
- Signal,
- SignalCandidate,
- TradeIntent,
- RiskDecision,
- Order,
- OrderEvent,
- Fill,
- Position,
- PositionEvent,
- KillSwitch,
- AuditEvent.

## 9.3 Zustandsmaschinen definieren

### Signal

```text
generated
  -> filtered
  -> published
  -> accepted
  -> rejected
  -> expired
  -> blocked_by_risk
  -> order_created
```

### Order

```text
created
  -> validated
  -> submitted
  -> accepted_by_broker
  -> partially_filled
  -> filled
  -> cancel_requested
  -> cancelled
  -> rejected
  -> expired
```

### Position

```text
pending_open
  -> open
  -> pending_close
  -> closed
  -> reconciliation_required
```

Übergänge werden zentral validiert.

## 9.4 Audit-Log

Jede relevante Aktion speichert:

- Event-ID,
- Timestamp,
- User-ID,
- Actor,
- Entity-Typ,
- Entity-ID,
- Aktion,
- alter Zustand,
- neuer Zustand,
- Trace-ID,
- Quellkanal,
- technische Metadaten.

Audit-Daten werden append-only behandelt.

## 9.5 Deliverables

- PostgreSQL-Schema,
- Migrationsskripte,
- Domänenmodelle,
- Zustandsmaschinen,
- Audit-Event-Modell,
- Datenmigrationsreport.

## 9.6 Akzeptanzkriterien

- bestehende Nutzer und Trades lassen sich migrieren,
- Zustandsübergänge sind getestet,
- ungültige Übergänge werden abgelehnt,
- jede Brokeraktion erzeugt ein Audit-Event,
- alle Transaktionen sind über Trace-IDs nachvollziehbar.

---

# Phase 2: Exchange-Kalender und Marktdaten

## 10. Ziel

Handelslogik verwendet korrekte Börsenzeiten und eine definierte Datenbasis.

## 10.1 Exchange-Kalender

### Aufgaben

- Bibliothek für Exchange-Kalender auswählen.
- NYSE/Nasdaq-Handelskalender integrieren.
- folgende Funktionen bereitstellen:
  - `is_trading_day(date)`,
  - `market_open(date)`,
  - `market_close(date)`,
  - `is_market_open(timestamp)`,
  - `next_market_open(timestamp)`,
  - `minutes_to_close(timestamp)`,
  - `is_early_close(date)`.

### Scheduler-Umstellung

Alte feste Berlin-Zeiten ersetzen durch:

- Signalerzeugung relativ zum Open,
- Entry-Sperre relativ zum Close,
- Intraday-Exit relativ zum Close,
- Reports separat in Europe/Berlin.

## 10.2 Datenprovider-Abstraktion

Interface definieren:

```python
class MarketDataProvider:
    def get_bars(...)
    def get_quote(...)
    def stream_quotes(...)
    def stream_trades(...)
    def get_corporate_actions(...)
    def get_market_status(...)
```

Implementierungen:

- `YFinanceResearchProvider`,
- `AlpacaPaperMarketDataProvider`,
- später `LicensedProductionProvider`.

## 10.3 Datenherkunft speichern

Jede Berechnung speichert:

- Provider,
- Feed,
- Abrufzeit,
- Exchange-Zeit,
- Datenversion,
- Qualitätsstatus.

## 10.4 Datenqualitäts-Gates

- Quote nicht älter als Grenzwert,
- Spread nicht größer als Grenzwert,
- Bar vollständig,
- keine NaN-Kernwerte,
- Symbol aktiv,
- Marktstatus korrekt,
- kein Halt,
- Corporate Actions berücksichtigt.

## 10.5 Rohdatenarchiv

Minimalversion:

- Metadaten in PostgreSQL,
- größere Rohdaten als Parquet,
- Partitionierung nach Symbol, Datum und Timeframe,
- unveränderte Rohdaten getrennt von Features.

## 10.6 Deliverables

- Exchange-Calendar-Service,
- neue Scheduler-Logik,
- Provider-Interface,
- mindestens ein Paper-Provider,
- Datenqualitätsservice,
- Rohdaten- und Metadatenmodell.

## 10.7 Akzeptanzkriterien

- Sommerzeitwechsel verursachen keine falschen Handelszeiten,
- Half Days werden korrekt behandelt,
- keine neue Intraday-Position nach Entry-Cutoff,
- Signal speichert Datenprovider und Datenversion,
- veraltete Quotes blockieren Orders.

---

# Phase 3: Risk Service und Position Sizing

## 11. Ziel

Jeder Trade wird anhand eines zentralen, reproduzierbaren Risikomodells geprüft.

## 11.1 RiskProfile

Felder:

- account_risk_per_trade_pct,
- daily_loss_limit_pct,
- max_open_positions,
- max_position_pct,
- max_sector_exposure_pct,
- max_correlated_exposure_pct,
- max_daily_new_exposure_pct,
- max_spread_bps,
- max_quote_age_seconds,
- min_average_dollar_volume,
- earnings_blackout_days,
- allow_overnight,
- allowed_strategies.

## 11.2 Sizing-Service

Inputs:

- Kontowert,
- Cash,
- Buying Power,
- Entry,
- Stop,
- Spread,
- erlaubtes Risiko,
- bestehende Exposure,
- Mindeststückgröße,
- Fractional-Unterstützung.

Outputs:

- Stückzahl,
- erwartetes Risiko,
- erwartete Kosten,
- abgelehnte oder reduzierte Größe,
- Begründung.

## 11.3 Pre-Trade-Risk-Checks

Reihenfolge:

1. globaler Kill-Switch,
2. Nutzer-Kill-Switch,
3. Modus erlaubt,
4. Signal gültig und nicht abgelaufen,
5. Strategie erlaubt,
6. Markt geöffnet,
7. Quote aktuell,
8. Spread akzeptabel,
9. Liquidität ausreichend,
10. Tagesverlustlimit,
11. maximale Positionen,
12. bestehende Tickerposition,
13. Gesamt- und Sektorexposure,
14. risikobasiertes Sizing,
15. Buying Power,
16. Brokerstatus.

## 11.4 Post-Trade-Risk

- aktive Stops vorhanden,
- offene Position ohne Schutzorder erkennen,
- tägliches Verlustlimit aktualisieren,
- Exposure laufend berechnen,
- Abweichungen alarmieren.

## 11.5 Kill-Switch-Service

Funktionen:

- activate_global(reason),
- deactivate_global(actor),
- activate_user(user_id, reason),
- deactivate_user(user_id, actor),
- is_new_position_allowed(user_id),
- is_protective_exit_allowed(user_id).

## 11.6 Deliverables

- RiskProfile-Modell,
- zentraler Risk Service,
- neuer Sizing-Service,
- Kill-Switch-Service,
- Risikoentscheidungsprotokoll,
- UI für Risikoeinstellungen.

## 11.7 Akzeptanzkriterien

- keine Order kann Risk Service umgehen,
- gleiche Inputs erzeugen gleiche Risk Decision,
- keine Order überschreitet Kontorisiko,
- Tagesverlustlimit blockiert neue Positionen,
- Schutz-Exits bleiben trotz Kill-Switch erlaubt,
- Ablehnungsgründe sind im UI sichtbar.

---

# Phase 4: Order Management System und Brokerzustände

## 12. Ziel

Orders werden idempotent, nachvollziehbar und robust verarbeitet.

## 12.1 Trade Intent

Web und Telegram erzeugen nur:

```text
TradeIntent
- user_id
- signal_id
- requested_action
- accepted_exit_policy
- source_channel
- created_at
- idempotency_key
```

## 12.2 OMS-Pipeline

1. Intent laden.
2. Idempotency prüfen.
3. Signal prüfen.
4. Risk Service aufrufen.
5. Orderplan erstellen.
6. Order persistieren.
7. Brokerorder senden.
8. Broker-ID speichern.
9. Statusereignisse verarbeiten.
10. Nutzer informieren.

## 12.3 Idempotency

- Idempotency Key pro Nutzeraktion.
- Unique Constraint in DB.
- Wiederholte Telegram-Callbacks liefern dasselbe Ergebnis.
- Wiederholte HTTP-Requests erzeugen keine zweite Order.
- Broker-Client-Order-ID aus interner Order-ID ableiten.

## 12.4 Broker Adapter

Interface:

```python
class BrokerAdapter:
    def submit_order(...)
    def cancel_order(...)
    def replace_order(...)
    def close_position(...)
    def get_order(...)
    def list_open_orders(...)
    def list_positions(...)
    def stream_order_events(...)
    def get_account(...)
```

## 12.5 Broker-Event-Worker

Verarbeitet:

- accepted,
- rejected,
- partial_fill,
- fill,
- cancelled,
- expired,
- replaced.

Jedes Ereignis wird:

- dedupliziert,
- persistiert,
- auf gültigen Zustandsübergang geprüft,
- in Positionen übertragen,
- auditiert,
- an Nutzerbenachrichtigung weitergegeben.

## 12.6 Partial Fills

Regeln definieren:

- Teilposition anlegen,
- Stopgröße an Fillmenge anpassen,
- Restorder offen lassen oder nach Timeout canceln,
- keine doppelte Schutzorder,
- Nutzerstatus aktualisieren.

## 12.7 Reconciliation

### Echtzeit

Brokerstream ist primäre Eventquelle.

### Periodisch

Zum Beispiel alle 5 bis 15 Minuten:

- offene Orders vergleichen,
- Positionen vergleichen,
- Cash und Buying Power vergleichen,
- unbekannte Brokerpositionen erkennen,
- fehlende interne Positionen erkennen.

### Täglich

Vollständiger Audit-Abgleich und Report.

## 12.8 Deliverables

- TradeIntent-Modell,
- OMS-Service,
- Brokeradapter,
- Order-Event-Worker,
- Partial-Fill-Logik,
- Reconciliation-Service,
- Duplicate-Order-Tests.

## 12.9 Akzeptanzkriterien

- doppelte Klicks erzeugen keine doppelten Orders,
- Orderstatus stimmen mit Brokerereignissen überein,
- Partial Fills werden korrekt behandelt,
- Abweichungen werden erkannt und alarmiert,
- jede Order besitzt eine vollständige Ereignishistorie.

---

# Phase 5: Strategien und Signal-Engine vereinfachen

## 13. Ziel

Die produktive Engine wird auf wenige, verständliche Strategiefamilien reduziert.

## 13.1 Strategie-Inventur

Für jede vorhandene Strategie erfassen:

- Zielidee,
- Timeframe,
- Haltedauer,
- Entry,
- Exit,
- Universum,
- Kostenempfindlichkeit,
- Anzahl Backtest-Trades,
- OOS-Status,
- Abhängigkeit zu anderen Strategien.

## 13.2 Strategien klassifizieren

Zuordnen zu:

- Intraday Momentum,
- Swing Trend,
- Mean Reversion,
- Research only,
- Deprecated.

## 13.3 Auswahl der V1-Strategien

Maximal eine Strategieversion pro Familie zunächst produktiv sichtbar.

Auswahlkriterien:

- klare ökonomische Hypothese,
- genügend Trades,
- stabile OOS-Ergebnisse,
- nachvollziehbarer Exit,
- realistische Kosten,
- geringe Komplexität,
- geeignete Marktdaten verfügbar.

## 13.4 Globalen Score entfernen

- universellen 0–100-Score aus Entscheidungspfad entfernen,
- strategespezifische Rohscores einführen,
- UI-Beschriftung anpassen,
- keine Wahrscheinlichkeit ohne Kalibrierung.

## 13.5 Strategieversionierung

Jede Strategieversion speichert:

- Parameter,
- Feature-Version,
- Universum,
- Entry- und Exitregeln,
- Kostenmodell,
- Release-Status,
- Code-Commit.

## 13.6 Strategiebezogene Exits

Beispiele:

### Intraday Momentum

- Stop,
- Trailing Stop,
- Momentumbruch,
- Entry-Timeout,
- Market-Close-Exit.

### Swing Trend

- Stop,
- Strukturbruch,
- Trailing Stop,
- maximale Haltedauer,
- Eventfilter.

### Mean Reversion

- Rückkehr zum Mittelwert,
- Stop,
- Zeit-Exit,
- Regimebruch.

## 13.7 Deliverables

- Strategie-Inventur,
- V1-Strategieauswahl,
- Strategie-Registry mit Versionen,
- getrennte Rohscores,
- strategiebezogene Exitlogik,
- Deprecation-Liste.

## 13.8 Akzeptanzkriterien

- maximal drei produktive Strategiefamilien,
- kein globaler Score entscheidet über Entry oder Exit,
- jedes Signal referenziert eine unveränderliche Strategieversion,
- jede Strategie besitzt dokumentierte Entry- und Exitlogik.

---

# Phase 6: Portfolio-Allocator, Shadow und Reporting

## 14. Ziel

Konkurrierende Signale werden kontrolliert ausgewählt und alle Betriebsmodi sauber getrennt.

## 14.1 Portfolio-Allocator

Inputs:

- SignalCandidates,
- bestehende Positionen,
- offene Orders,
- Nutzer-Risikoprofil,
- Sektor- und Korrelationsdaten,
- erwartete Kosten,
- Strategieprioritäten.

Outputs:

- ausgewählte Signale,
- verworfene Signale,
- Ablehnungsgründe,
- reserviertes Risikobudget.

## 14.2 Shadow-Modus

Shadow-Signale:

- werden auf Live-Daten erzeugt,
- sind nicht durch Nutzer ausführbar,
- erhalten simulierte Entry- und Exitwerte,
- werden getrennt ausgewertet.

## 14.3 Moduskennzeichnung

Jede relevante Entität erhält:

- `backtest`,
- `shadow`,
- `paper`,
- `live`.

Das Feld ist Pflicht für:

- Signal,
- Intent,
- Order,
- Fill,
- Position,
- Performance Snapshot.

## 14.4 Reporting

Getrennte Dashboards für:

- Backtest,
- Shadow,
- Paper,
- Live.

Pflichtanzeigen:

- Netto-P&L,
- Kosten,
- Slippage,
- Drawdown,
- Anzahl Trades,
- Profitfaktor,
- Erwartungswert,
- offene Risiken,
- Strategieversion.

## 14.5 Signaltreue

Alle Signale speichern:

- veröffentlicht,
- abgelehnt,
- abgelaufen,
- durch Risk blockiert,
- nicht gefüllt.

Keine nachträgliche Löschung aus Performance-Analysen.

## 14.6 Deliverables

- Portfolio-Allocator,
- Shadow-Modus,
- Modusmigration,
- getrennte Reports,
- vollständige Signalhistorie,
- Strategieattribution.

## 14.7 Akzeptanzkriterien

- dieselbe Position wird nicht mehreren Strategien zugerechnet,
- Shadow und Paper sind getrennt,
- Backtestdaten tauchen nicht in Live-Kennzahlen auf,
- alle abgelehnten und abgelaufenen Signale bleiben sichtbar.

---

# Phase 7: Backtest-Härtung

## 15. Ziel

Backtests werden reproduzierbarer und näher an der späteren Execution.

## 15.1 Gemeinsamer Strategiecode

- Signal- und Exitregeln aus produktivem Strategiemodul nutzen.
- Keine separate, abweichende Backtest-Implementierung.
- Zeit und Datenzugriff über abstrahierten Clock- und Data-Provider.

## 15.2 Multi-Timeframe-Korrektheit

Prüfen:

- nur abgeschlossene Bars,
- keine Tagesbar vor Tagesende,
- korrekte Resampling-Grenzen,
- Zeitzonen konsistent,
- Entry frühestens in Folgebarkeit oder nach vollständiger Signalinformation.

## 15.3 Kostenmodell

Mindestens:

- Kommissionen,
- Spread,
- Slippage,
- SEC/FINRA-bezogene Gebühren, soweit relevant,
- Teilfüllungsannahmen,
- Liquiditätsgrenzen,
- Market-Impact-Näherung für größere Orders.

## 15.4 Universen

- historische Indexzusammensetzungen verwenden, wenn verfügbar,
- Delistings berücksichtigen,
- Survivorship Bias messen und dokumentieren,
- Universums-Version speichern.

## 15.5 Validierung

- Nested Walk-forward,
- Purging,
- Embargo,
- finaler Holdout,
- Parameter-Sensitivitätsanalyse,
- Bootstrap-Konfidenzintervalle,
- Regimeauswertung.

## 15.6 Reproduzierbarkeit

Jeder Backtest speichert:

- Run-ID,
- Git-Commit,
- Strategieversion,
- Datenversion,
- Universum,
- Parameter,
- Kostenmodell,
- Zeitraum,
- Random Seed,
- Python- und Dependency-Versionen.

## 15.7 Deliverables

- gehärtete Backtest-Engine,
- Daten- und Codeversionierung,
- Kostenmodell,
- Walk-forward-Framework,
- reproduzierbare Reports,
- Benchmark-Vergleich.

## 15.8 Akzeptanzkriterien

- gleicher Run mit gleicher Version erzeugt gleiche Resultate,
- kein Look-ahead in automatisierten Tests,
- Kosten und Slippage sind separat sichtbar,
- finaler Holdout ist technisch gesperrt,
- Report enthält Anzahl aller getesteten Kandidaten.

---

# Phase 8: Strategie-Labor begrenzen

## 16. Ziel

Das Labor erzeugt kontrollierte Kandidaten, ohne Live-Systeme direkt zu verändern.

## 16.1 Champion-Candidate-Modell

- genau eine freigegebene Champion-Version je Strategie,
- mehrere Candidate-Versionen,
- Kandidaten zuerst im Backtest,
- danach Shadow,
- danach optional Paper.

## 16.2 Suchraum

- explizit definierte Parametergrenzen,
- maximale Kandidaten je Zyklus,
- keine unbegrenzte Feature-Erzeugung,
- LLM nur für beschreibende Hypothesen,
- technische Validierung aller Vorschläge.

## 16.3 Promotion-Gates

Mindestens:

- ausreichende Tradezahl,
- OOS-Mehrheit,
- keine starke Drawdown-Verschlechterung,
- positive Nettoperformance nach Kosten,
- Sensitivitätsstabilität,
- Shadow-Bestätigung,
- menschliche Freigabe.

## 16.4 Holdout-Schutz

- Holdout-Datenzugriff protokollieren,
- Kandidaten nicht wiederholt am finalen Holdout testen,
- neuer Holdout erst nach klar definiertem Releasezyklus.

## 16.5 Pending Workflow

Status:

```text
generated
-> validated
-> backtested
-> shadow
-> pending_review
-> approved
-> rejected
-> archived
```

Kein direkter Schreibzugriff auf Live-Parameter.

## 16.6 Deliverables

- Champion-Candidate-Datenmodell,
- begrenzter Suchraum,
- Promotion-Pipeline,
- Pending-Review-UI,
- Holdout-Zugriffsschutz,
- vollständige Experimenthistorie.

## 16.7 Akzeptanzkriterien

- Labor kann keine Live-Strategie direkt verändern,
- jeder Kandidat ist reproduzierbar,
- jede Promotion ist menschlich bestätigt,
- LLM-Ausgabe kann keinen Produktionscode oder Live-Parameter direkt setzen.

---

# Phase 9: Sicherheit, Deployment und Observability

## 17. Ziel

Der Paper-Betrieb wird sicher und betrieblich beherrschbar.

## 17.1 Systemd-Härtung

Für jeden Dienst:

- eigener Benutzer,
- kein Root,
- `NoNewPrivileges=true`,
- `PrivateTmp=true`,
- `ProtectSystem=strict`,
- `ProtectHome=true`,
- `RestrictAddressFamilies`,
- nur notwendige Schreibpfade,
- Restart-Policy,
- Resource Limits.

## 17.2 Dependency Management

- alle Dependencies pinnen,
- Lockfile oder Constraints-Datei,
- Dependabot oder vergleichbare Updates,
- `pip-audit`,
- regelmäßige Upgrade-Tests.

## 17.3 Secrets

- lokale Entwicklung: `.env`,
- Staging/Produktion: systemd-Credentials oder Secret Store,
- Rotationsverfahren,
- getrennte Schlüssel,
- kein Secret in Exceptions oder Logs.

## 17.4 OAuth

- Alpaca OAuth Flow implementieren,
- Scopes minimieren,
- Token verschlüsseln,
- Disconnect-Funktion,
- Revoke-Flow,
- Paper und Live getrennt kennzeichnen.

## 17.5 Logging

Strukturiertes JSON-Logging mit:

- timestamp,
- service,
- severity,
- trace_id,
- user_id pseudonymisiert,
- entity_id,
- event_type.

Keine API-Keys, Tokens oder vollständigen personenbezogenen Daten.

## 17.6 Monitoring

Metriken:

- Service-Verfügbarkeit,
- Datenfeed-Latenz,
- Quote-Alter,
- Orderlatenz,
- Reject Rate,
- Fill Rate,
- Reconciliation-Fehler,
- Queue-Lag,
- offene Positionen ohne Stop,
- Kill-Switch-Status.

## 17.7 Backups

- verschlüsselte PostgreSQL-Backups,
- Aufbewahrungsplan,
- regelmäßiger Restore-Test,
- dokumentierte Recovery-Ziele.

## 17.8 Deliverables

- gehärtete systemd Units,
- gepinnte Dependencies,
- Secret-Management,
- OAuth,
- strukturierte Logs,
- Monitoring-Dashboard,
- Alarmierungsregeln,
- Backup- und Restore-Runbook.

## 17.9 Akzeptanzkriterien

- kein Dienst läuft als Root,
- keine Produktionssecrets in `.env`,
- kritische Fehler erzeugen Alarm,
- Restore-Test erfolgreich,
- Nutzer kann Brokerzugriff sofort widerrufen,
- offene Position ohne Schutz wird erkannt.

---

# Phase 10: Teststrategie und Freigabe für Paper

## 18. Ziel

Das Gesamtsystem wird vor Canary Live intensiv geprüft.

## 18.1 Unit Tests

Pflichtabdeckung:

- Sizing,
- Risk Limits,
- Kalender,
- Strategiebedingungen,
- Zustandsübergänge,
- Idempotency,
- Berechtigungen,
- Kill-Switch.

## 18.2 Integrationstests

Szenarien:

- Signal → Freigabe → Risk → Order,
- doppelte Freigabe,
- abgelaufenes Signal,
- veralteter Quote,
- Risk Block,
- Partial Fill,
- Broker Reject,
- Cancel/Replace,
- Reconciliation-Abweichung,
- Schutzorder.

## 18.3 Replay-Tests

Brokerereignisse in definierter Reihenfolge wiedergeben:

- normaler Fill,
- Event doppelt,
- Event verspätet,
- Partial Fill,
- Fill nach Cancel Request,
- unbekannte Order,
- externe Position.

## 18.4 Failure Injection

- Datenfeed offline,
- Broker timeout,
- DB-Verbindung unterbrochen,
- Queue-Wiederholung,
- Worker-Neustart,
- Telegram-Callback doppelt,
- Web-Request doppelt,
- Clock-Skew,
- veraltete Marktdaten.

## 18.5 Paper-Burn-in

Mindestinhalte:

- mehrere vollständige Marktwochen,
- mindestens ein Feiertag oder verkürzter Handelstag, wenn zeitlich möglich,
- unterschiedliche Marktregime,
- dokumentierte Fehlerquote,
- keine ungeklärten Reconciliation-Abweichungen,
- keine doppelten Orders,
- keine Budgetüberschreitung.

Die Dauer wird nicht allein nach Kalender definiert. Entscheidend ist eine ausreichende Anzahl realer Prozessereignisse.

## 18.6 Deliverables

- automatisierte Test-Suite,
- Replay-Fixtures,
- Failure-Test-Bericht,
- Paper-Burn-in-Report,
- Go/No-Go-Checkliste.

## 18.7 Akzeptanzkriterien

- keine doppelten Orders in allen Wiederholungstests,
- keine unkontrollierte Order bei Feed- oder Brokerfehler,
- alle kritischen Failure-Fälle dokumentiert,
- keine ungeklärten Positionsabweichungen,
- Kill-Switch funktioniert in Integrationstests.

---

# Phase 11: Canary Live

## 19. Ziel

Sehr begrenzter Live-Test nach erfolgreichem Paper-Betrieb.

## 19.1 Voraussetzungen

- regulatorische Einordnung für den konkreten internen Test,
- alle P0-, P1- und P2-Punkte abgeschlossen,
- dokumentierter Paper-Burn-in,
- funktionierende OAuth-Verbindung,
- getestete Kill-Switches,
- getestete Reconciliation,
- definierter Incident-Prozess.

## 19.2 Begrenzungen

- nur internes oder ausdrücklich freigegebenes Konto,
- sehr kleines Risikobudget,
- maximal eine Strategie,
- maximal eine oder zwei parallele Positionen,
- nur liquide Aktien oder ETFs,
- keine Overnight-Positionen im ersten Canary-Schritt,
- keine automatische Skalierung.

## 19.3 Canary-Metriken

- Signal-to-Order-Latenz,
- Order-to-Fill-Latenz,
- Slippage,
- Fill Rate,
- Reject Rate,
- Spread,
- Positionsabweichungen,
- Schutzorderstatus,
- manuelle Eingriffe,
- Incident-Anzahl.

## 19.4 Abbruchkriterien

Canary wird sofort gestoppt bei:

- doppelter Order,
- falscher Positionsgröße,
- fehlender Schutzorder,
- nicht erklärbarer Brokerabweichung,
- Verlustlimitverletzung,
- veralteten Marktdaten,
- falscher Handelszeit,
- nicht auditierbarer Aktion.

## 19.5 Deliverables

- Canary-Konfiguration,
- Canary-Risk-Profil,
- täglicher Canary-Report,
- Incident-Log,
- Abschlussentscheidung.

---

# Phase 12: Regulatorischer und kommerzieller Modus

## 20. Ziel

Bewertung, ob und wie ein öffentliches Multi-User-Produkt betrieben werden darf.

## 20.1 Zu prüfende User Journeys

- allgemeines Signal ohne Nutzerprofil,
- personalisiertes Signal,
- Nutzerfreigabe per Telegram,
- Orderübermittlung an Broker,
- automatische Exits,
- Strategieauswahl,
- Ranglisten und Performancewerbung,
- Gebührenmodell,
- Affiliate- oder Brokervergütung.

## 20.2 Erforderliche Dokumente

- Produktbeschreibung,
- vollständige User Journey,
- Rollenmodell,
- Datenflussdiagramm,
- Brokerbeziehung,
- Vergütungsmodell,
- Konfliktregister,
- Risikohinweise,
- Performance-Darstellung,
- Datenschutzkonzept,
- Aufbewahrungskonzept,
- Beschwerdeprozess,
- Incident- und Kommunikationsprozess.

## 20.3 Go/No-Go

Kein öffentlicher Live-Launch ohne schriftliche Einordnung und umgesetzte Anforderungen.

---

## 21. Technische Arbeitspakete

## 21.1 Paket A – Konfiguration und Feature Flags

### Aufgaben

- zentrale Settings-Klasse,
- strikt typisierte Umgebungsvariablen,
- sichere Defaults,
- Modusvalidierung beim Start,
- Start verweigern bei riskanter Fehlkonfiguration.

### Definition of Done

- Produktion startet nicht mit Live=true und fehlender Freigabe,
- Tests für alle Sicherheitsflags,
- Konfiguration wird ohne Secrets protokolliert.

## 21.2 Paket B – Domain Events

Events:

- SignalGenerated,
- SignalPublished,
- TradeIntentCreated,
- RiskApproved,
- RiskRejected,
- OrderSubmitted,
- OrderPartiallyFilled,
- OrderFilled,
- OrderRejected,
- PositionOpened,
- PositionClosed,
- ReconciliationMismatch,
- KillSwitchActivated.

### Definition of Done

- Events versioniert,
- Schema dokumentiert,
- Consumer idempotent,
- Trace-ID durchgängig.

## 21.3 Paket C – Outbox

### Aufgaben

- Outbox-Tabelle,
- atomare Speicherung mit Domänenänderung,
- Worker für Auslieferung,
- Retry,
- Dead-Letter-Status.

### Definition of Done

- kein verlorenes Event bei Prozessabsturz,
- wiederholte Auslieferung sicher,
- Monitoring für Rückstand.

## 21.4 Paket D – Notifications

### Aufgaben

- Telegram und Web-SSE als Consumer,
- keine Handelslogik im Notification-Code,
- Templates für Signal, Fill, Reject, Kill-Switch,
- Retry und Dedup.

### Definition of Done

- Benachrichtigungsfehler beeinflusst Orderzustand nicht,
- doppelte Events erzeugen keine Spam-Nachrichten,
- kritische Ereignisse separat markiert.

---

## 22. Datenbank-Migrationsplan

## 22.1 Vorbereitung

- aktuelle Tabellen und Felder inventarisieren,
- Datenvolumen bestimmen,
- inkonsistente Datensätze identifizieren,
- Backup erzeugen.

## 22.2 Mapping

Beispiel:

| Alt | Neu |
|---|---|
| trades | positions + position_events |
| signals | signals + signal_candidates |
| broker keys | broker_connections |
| strategy name | strategies + strategy_versions |
| status text | typisierte Enums |
| P&L fields | fills + performance snapshots |

## 22.3 Datenbereinigung

- doppelte Trades markieren,
- fehlende Strategy IDs ergänzen,
- Modus ableiten,
- historische Brokerabweichungen kennzeichnen,
- unbekannte Zustände als `legacy_unknown`.

## 22.4 Validierung

- Nutzeranzahl,
- Signalanzahl,
- Tradeanzahl,
- Summe realisierter P&L,
- Anzahl offener Positionen,
- eindeutige Broker-IDs,
- referenzielle Integrität.

---

## 23. API-Plan

## 23.1 Web-Endpunkte

```text
GET    /api/v1/signals
GET    /api/v1/signals/{id}
POST   /api/v1/signals/{id}/accept
POST   /api/v1/signals/{id}/reject

GET    /api/v1/positions
POST   /api/v1/positions/{id}/close

GET    /api/v1/orders
GET    /api/v1/orders/{id}

GET    /api/v1/risk-profile
PATCH  /api/v1/risk-profile

POST   /api/v1/kill-switch/activate
POST   /api/v1/kill-switch/deactivate

GET    /api/v1/broker/connections
POST   /api/v1/broker/alpaca/oauth/start
GET    /api/v1/broker/alpaca/oauth/callback
DELETE /api/v1/broker/connections/{id}

GET    /api/v1/performance
GET    /api/v1/audit
```

## 23.2 API-Regeln

- Idempotency Header bei mutierenden Trade-Aktionen,
- rollenbasierte Autorisierung,
- Pydantic-Schemas,
- keine Brokerdetails im Client,
- standardisierte Fehlercodes,
- Trace-ID in jeder Antwort.

---

## 24. Telegram-Umbau

## 24.1 Zu entfernen

- direkte Brokeraufrufe,
- Hebelauswahl,
- Optionsauswahl,
- komplexe Strategieeinstellungen,
- unsichere Callback-Daten.

## 24.2 Beizubehalten

- Signale anzeigen,
- Annehmen,
- Ablehnen,
- Positionen anzeigen,
- Kill-Switch,
- Link zur Web-App.

## 24.3 Callback-Sicherheit

- kurze opaque Callback-ID,
- serverseitige Intent-Auflösung,
- Ablaufzeit,
- Nutzerbindung,
- Einmalverwendung oder idempotente Wiederholung,
- Audit.

---

## 25. Web-App-Umbau

## 25.1 Signalansicht

Anzeigen:

- Strategie,
- Entry-Zone,
- Stop,
- maximales Risiko,
- Positionsgröße,
- Ablaufzeit,
- Kosten,
- Begründung,
- Marktregime,
- Datenstatus.

## 25.2 Bestätigungsdialog

Pflichtbestätigung:

- „Ich bestätige die Position.“
- „Ich akzeptiere die automatische Exit-Policy.“
- „Mir ist der maximale geplante Verlust bekannt.“

## 25.3 Risikoübersicht

- Tagesverlustlimit,
- bereits verbrauchtes Risiko,
- freie Risikokapazität,
- offene Exposure,
- Sektoren,
- aktive Kill-Switches.

## 25.4 Moduskennzeichnung

Jede Seite zeigt deutlich:

- BACKTEST,
- SHADOW,
- PAPER,
- LIVE.

LIVE erhält zusätzlich sichtbare Warnkennzeichnung.

---

## 26. Observability-Runbook

## 26.1 Kritische Alerts

- Broker nicht erreichbar,
- Marktfeed nicht aktuell,
- Order länger als Grenzwert in unbekanntem Status,
- Position ohne Stop,
- Reconciliation Mismatch,
- doppelte Client Order ID,
- Tagesverlustlimit überschritten,
- globaler Kill-Switch aktiviert,
- Queue-Rückstand kritisch.

## 26.2 Reaktionsregeln

### Datenfeed-Ausfall

- neue Positionen stoppen,
- bestehende Broker-Stops nicht entfernen,
- Alert senden,
- Statusseite aktualisieren.

### Broker-Ausfall

- keine neuen Orders,
- offene unbekannte Zustände markieren,
- Reconcile nach Wiederherstellung,
- Nutzer informieren.

### DB-Ausfall

- keine neuen Orderaktionen,
- keine Brokerorder ohne persistierte interne Order,
- Readiness Check fehlschlagen lassen.

### Reconciliation Mismatch

- Nutzerbezogenen Kill-Switch aktivieren,
- Position manuell prüfen,
- keine automatische Korrektur ohne definierte Regel.

---

## 27. Definition of Done für Version 1

Version 1 ist fertig, wenn alle folgenden Punkte erfüllt sind:

### Produkt

- nur US-Aktien und US-Aktien-ETFs,
- Long-only,
- 1×,
- keine Optionen,
- Paper als Standard,
- höchstens drei Strategiefamilien.

### Daten

- Exchange-Kalender integriert,
- Datenprovider abstrahiert,
- Produktionssignale nicht von yfinance abhängig,
- Datenherkunft gespeichert.

### Risiko

- zentraler Risk Service,
- risikobasiertes Sizing,
- Tagesverlustlimit,
- Exposure-Limits,
- Kill-Switches,
- keine Budgetüberschreitung.

### Execution

- zentrales OMS,
- Idempotency,
- Broker-Event-Verarbeitung,
- Partial-Fill-Logik,
- Reconciliation,
- vollständiges Audit.

### Research

- Backtest, Shadow, Paper und Live getrennt,
- Strategieversionierung,
- reproduzierbare Backtests,
- Labor ohne direkte Live-Änderungen.

### Betrieb

- PostgreSQL,
- dedizierte Systemnutzer,
- gepinnte Dependencies,
- sichere Secrets,
- Monitoring,
- Backups,
- Restore-Test.

### Qualität

- Unit-, Integrations-, Replay- und Failure-Tests,
- Paper-Burn-in erfolgreich,
- keine offenen P0- oder P1-Probleme,
- dokumentiertes Go/No-Go.

---

## 28. Empfohlene Reihenfolge innerhalb des bestehenden Codes

1. Live global blockieren.
2. Hebel, Optionen und Budgetüberschreitung entfernen.
3. direkte Brokeraufrufe lokalisieren.
4. Domain Models und Zustände definieren.
5. PostgreSQL und Migration einführen.
6. Exchange-Kalender integrieren.
7. Risk Service und Sizing zentralisieren.
8. Trade Intent einführen.
9. OMS einführen.
10. Broker-Event-Stream und Reconciliation einführen.
11. Telegram und Web auf Trade Intent umstellen.
12. Strategien auf drei Familien reduzieren.
13. Signalmodell und Strategieversionierung umbauen.
14. Shadow-Modus einführen.
15. Reporting trennen.
16. Backtest härten.
17. Strategie-Labor begrenzen.
18. Security und Observability abschließen.
19. Paper-Burn-in durchführen.
20. Canary Live separat entscheiden.

---

## 29. Empfohlene Tickets für den Start

### Epic 1 – Trading Safety

- TSAFE-001 Global Live Kill-Switch
- TSAFE-002 Leverage hard-block
- TSAFE-003 Options hard-block
- TSAFE-004 Remove budget oversizing
- TSAFE-005 Remove score-based exit
- TSAFE-006 Inventory broker calls
- TSAFE-007 Block direct order execution

### Epic 2 – Data and Time

- DATA-001 Exchange calendar service
- DATA-002 Replace fixed Berlin schedules
- DATA-003 Market data provider interface
- DATA-004 Quote freshness checks
- DATA-005 Data provenance metadata

### Epic 3 – Risk

- RISK-001 Risk profile model
- RISK-002 Position sizing service
- RISK-003 Pre-trade checks
- RISK-004 Daily loss limit
- RISK-005 Exposure limits
- RISK-006 Kill-switch service

### Epic 4 – OMS

- OMS-001 Trade Intent model
- OMS-002 Order state machine
- OMS-003 Idempotency
- OMS-004 Alpaca adapter
- OMS-005 Broker event worker
- OMS-006 Partial fills
- OMS-007 Reconciliation

### Epic 5 – Strategy Core

- STRAT-001 Strategy inventory
- STRAT-002 Select V1 strategies
- STRAT-003 Strategy versioning
- STRAT-004 Remove global score
- STRAT-005 Strategy-specific exits
- STRAT-006 Portfolio allocator

### Epic 6 – Research

- RES-001 Shadow mode
- RES-002 Separate mode reporting
- RES-003 Backtest reproducibility
- RES-004 Cost and slippage model
- RES-005 Holdout protection
- RES-006 Champion-candidate lab

### Epic 7 – Platform

- PLAT-001 PostgreSQL migration
- PLAT-002 Audit log
- PLAT-003 Outbox
- PLAT-004 Structured logging
- PLAT-005 Monitoring
- PLAT-006 Secret management
- PLAT-007 OAuth
- PLAT-008 Systemd hardening
- PLAT-009 Backup and restore

---

## 30. Abschlussentscheidung

Die bestehende App sollte nicht verworfen werden. Viele vorhandene Module können weiterverwendet werden.

Der Umbau sollte jedoch nicht primär als Erweiterung verstanden werden, sondern als **Reduktion und Härtung**:

- weniger Strategien,
- weniger Anlageklassen,
- keine Hebelprodukte,
- ein zentraler Risikopfad,
- ein zentraler Orderpfad,
- klare Zustände,
- saubere Datenherkunft,
- und ehrliche Performance-Trennung.

Die erste belastbare Zielversion ist eine starke Paper- und Research-Plattform mit optionalem, stark begrenztem Live-Modus. Erst danach sollte über öffentliche Multi-User-Nutzung und zusätzliche Produktfunktionen entschieden werden.
