# Konzept_v1: Trading Research & Execution Assistant

**Version:** 1.0  
**Stand:** 11. Juli 2026  
**Status:** Zielkonzept für den Umbau der bestehenden App

---

## 1. Zusammenfassung

Die Anwendung wird von einem breit aufgestellten „Trading-Bot“ zu einem kontrollierten, transparenten **Trading Research & Execution Assistant** weiterentwickelt.

Das System analysiert liquide US-Aktien und US-Aktien-ETFs, erzeugt nachvollziehbare, regelbasierte Trading-Setups und stellt diese über eine Web-App und Telegram bereit. Neue Positionen werden nur nach ausdrücklicher Freigabe durch den Nutzer eröffnet. Bereits vorab genehmigte Schutz- und Risiko-Exits dürfen anschließend automatisch ausgeführt werden.

Das Produkt startet standardmäßig im **Shadow- oder Paper-Modus**. Live-Handel ist eine separat freizuschaltende Funktion und bleibt in Version 1 auf ungehebelte Long-Positionen beschränkt.

Die wichtigsten Ziele sind:

- reproduzierbare und nachvollziehbare Signale,
- strikte Trennung von Research, Backtest, Paper und Live,
- risikobasiertes Position Sizing,
- robuste Broker- und Orderzustände,
- klare Produktgrenzen,
- regulatorisch und technisch kontrollierbarer Betrieb,
- und eine Architektur, die später erweitert werden kann.

---

## 2. Ausgangslage

Die bestehende App verfügt bereits über viele wertvolle Komponenten:

- Telegram-Bot und Web-App,
- gemeinsame Service-Schicht,
- Multi-User-Unterstützung,
- technische Analyse über mehrere Timeframes,
- mehrere Strategien,
- Backtesting,
- Brokeranbindung an Alpaca,
- Paper- und Live-Modus,
- Dashboard und Performance-Auswertung,
- Strategie-Labor,
- verschlüsselte Brokerzugänge,
- Deployment per systemd und Reverse Proxy,
- Sicherheitsmechanismen für Web und Sessions.

Die aktuelle Lösung ist jedoch zu breit angelegt. Sie kombiniert gleichzeitig:

1. Signal-App,
2. Broker-Frontend,
3. Multi-Strategie-Plattform,
4. Backtest-System,
5. selbstoptimierendes Strategie-Labor,
6. Hebel- und Optionskonzepte,
7. mehrere Anlageklassen.

Diese Breite erhöht Entwicklungsaufwand, Fehlerrisiko, regulatorische Unsicherheit und die Gefahr, quantitativ nicht belastbare Ergebnisse als robuste Signale darzustellen.

Version 1 konzentriert sich deshalb auf einen kleineren, klar definierten Kern.

---

## 3. Produktvision

Die Anwendung unterstützt Nutzer dabei, regelbasierte Trading-Entscheidungen strukturiert zu treffen.

Sie soll nicht als autonomer „Geldverdien-Bot“ auftreten, sondern als System, das:

- Marktinformationen verarbeitet,
- konkrete Setups nach festen Regeln erkennt,
- Risiken vor einer Order sichtbar macht,
- Nutzerentscheidungen dokumentiert,
- Orders kontrolliert ausführt,
- Positionen zuverlässig überwacht,
- und Ergebnisse ehrlich auswertet.

### Produktversprechen

> Die Anwendung liefert nachvollziehbare Trading-Setups, zeigt das maximale geplante Risiko und führt Orders nur innerhalb vorab definierter Regeln aus.

### Kein Produktversprechen

Die Anwendung verspricht ausdrücklich nicht:

- garantierte Gewinne,
- dauerhaft überlegene Rendite,
- risikoloses Trading,
- verlässliche Vorhersagen,
- oder autonome Portfolioverwaltung ohne Nutzerkontrolle.

---

## 4. Zielgruppe

### 4.1 Primäre Zielgruppe

Technisch interessierte Privatanleger und aktive Trader, die:

- Trading-Setups systematisch statt spontan auswählen möchten,
- Paper-Trading und Strategieauswertung nutzen,
- ihre Entscheidungen weiterhin selbst treffen,
- und eine transparente Alternative zu undurchsichtigen Signalgruppen suchen.

### 4.2 Interne Zielgruppe

Entwickler und Betreiber der Plattform, die:

- Strategien reproduzierbar testen,
- Signalqualität unter Live-Daten beobachten,
- Execution und Slippage messen,
- und neue Strategien kontrolliert durch Research-, Shadow- und Paper-Stufen führen.

### 4.3 Nicht-Zielgruppe in Version 1

- vollständig unerfahrene Nutzer ohne Verständnis des Verlustrisikos,
- Nutzer, die vollautomatische Vermögensverwaltung erwarten,
- Nutzer mit hochriskanten Hebel- oder Optionsstrategien,
- institutionelle Kunden,
- Copy-Trading-Netzwerke,
- öffentliche Social-Trading-Feeds.

---

## 5. Betriebsmodelle

### 5.1 Privater Research-Modus

Geeignet für:

- interne Entwicklung,
- Einzelanwender,
- geschlossene Tests,
- Paper- und Shadow-Trading.

Eigenschaften:

- keine öffentliche Verbreitung konkreter Signale,
- keine automatische Aufnahme beliebiger externer Nutzer,
- Paper-Trading als Standard,
- Live-Trading nur bei bewusst aktivierter Brokerverbindung,
- vollständige Protokollierung aller Signale und Entscheidungen.

### 5.2 Kommerzieller Multi-User-Modus

Dieser Modus wird erst freigegeben, wenn:

- das konkrete Geschäftsmodell rechtlich geprüft wurde,
- die Rolle der Plattform gegenüber Nutzern und Broker geklärt ist,
- Anforderungen an Anlageempfehlungen und Orderübermittlung bewertet wurden,
- Datenschutz- und Aufbewahrungskonzepte vorliegen,
- Interessenkonflikte und Vergütungsmodelle dokumentiert sind,
- und notwendige Erlaubnisse oder Partnerschaften bestehen.

Private und kommerzielle Deployments verwenden getrennte Konfigurationen.

---

## 6. Funktionsumfang von Version 1

### 6.1 Enthalten

- liquide US-Aktien,
- liquide US-Aktien-ETFs,
- Long-only,
- ungehebelte Positionen,
- Shadow-Trading,
- Paper-Trading,
- optionaler Live-Handel,
- Telegram-Benachrichtigungen,
- Web-App als Kontrollzentrum,
- Strategieverwaltung,
- Portfolio-Risikoprüfung,
- Order- und Positionsüberwachung,
- Backtest- und Performance-Reports,
- kontrolliertes Strategie-Labor ohne direkte Live-Änderungen.

### 6.2 Nicht enthalten

- Kryptowährungen,
- Rohstoff-Futures,
- CFD-Handel,
- Short Selling,
- Margin,
- Hebel über 1×,
- Optionen,
- automatisches Copy Trading,
- öffentlich zugängliche Signalfeeds,
- LLM-basierte Trade-Entscheidungen,
- automatische Strategie-Promotion in den Live-Betrieb.

Diese Funktionen können später als getrennte Module geplant werden.

---

## 7. Bedienkanäle

## 7.1 Web-App

Die Web-App ist das primäre Kontrollzentrum.

### Kernbereiche

- **Übersicht**
  - Kontostatus,
  - aktuelles Risikobudget,
  - offene Positionen,
  - offene Orders,
  - Tages-P&L,
  - Kill-Switch-Status.

- **Signale**
  - neue Setups,
  - abgelaufene Setups,
  - angenommene und abgelehnte Signale,
  - Begründung und Risikodarstellung.

- **Positionen**
  - aktuelle Positionen,
  - Stop und Exit-Policy,
  - geplantes und realisiertes Risiko,
  - manuelles Schließen.

- **Strategien**
  - aktive Strategien,
  - Strategieversionen,
  - historische Ergebnisse,
  - Status Research, Shadow, Paper oder Live.

- **Performance**
  - getrennte Auswertungen für Backtest, Shadow, Paper und Live,
  - Equity-Kurven,
  - Drawdown,
  - Profitfaktor,
  - Erwartungswert,
  - Kosten und Slippage.

- **Broker**
  - Brokerverbindung,
  - OAuth-Status,
  - Berechtigungen,
  - sofortiges Trennen.

- **Einstellungen**
  - Risiko pro Trade,
  - Tagesverlustlimit,
  - maximale Positionen,
  - Benachrichtigungen,
  - erlaubte Strategien.

- **Strategie-Labor**
  - nur für Admin oder Research-Rollen,
  - Kandidaten,
  - Testresultate,
  - Pending-Vorschläge,
  - Apply oder Reject.

## 7.2 Telegram

Telegram ist ein Benachrichtigungs- und Freigabekanal.

### Unterstützte Aktionen

- neue Signale anzeigen,
- Signal öffnen,
- Trade annehmen,
- Trade ablehnen,
- offene Positionen anzeigen,
- Tagesstatus anzeigen,
- Kill-Switch aktivieren,
- Link zur Web-App öffnen.

Komplexe Einstellungen, detaillierte Reports und Brokerverwaltung erfolgen ausschließlich in der Web-App.

---

## 8. Rollen- und Berechtigungsmodell

### 8.1 Nutzerrollen

#### User

- eigene Signale sehen,
- eigene Brokerverbindung verwalten,
- eigene Risikooptionen innerhalb erlaubter Grenzen verändern,
- Trades freigeben oder ablehnen,
- eigene Positionen schließen.

#### Researcher

- Backtests starten,
- Shadow-Strategien beobachten,
- Kandidaten vergleichen,
- Reports erzeugen.

#### Admin

- Strategien für Research, Shadow oder Paper freigeben,
- Pending-Vorschläge des Labors annehmen oder ablehnen,
- Systemstatus und Audits einsehen,
- globale Kill-Switches bedienen.

### 8.2 Grundsatz

Keine Rolle darf über die Web- oder Telegram-Schicht direkt Brokerorders senden. Jede Order muss den zentralen Risk- und Orderprozess durchlaufen.

---

## 9. Anlageuniversum

Version 1 verwendet ein kuratiertes Universum liquider US-Aktien und Aktien-ETFs.

### 9.1 Mindestkriterien

Ein Wertpapier muss unter anderem erfüllen:

- reguläre Börsennotierung,
- ausreichendes durchschnittliches Tagesvolumen,
- ausreichendes Dollarvolumen,
- akzeptabler typischer Spread,
- ausreichende Datenqualität,
- keine aktuelle Handelssperre,
- keine problematischen Corporate Actions,
- kein Penny-Stock-Profil.

### 9.2 Universums-Versionierung

Jede Änderung des Universums erzeugt eine neue Version.

Ein Signal speichert:

- Universums-ID,
- Zeitpunkt der Universumszusammensetzung,
- Ein- und Ausschlussgrund,
- verwendete Liquiditätskennzahlen.

Backtests müssen die jeweils historische Universumszusammensetzung verwenden oder Survivorship Bias klar als Einschränkung ausweisen.

---

## 10. Marktdaten

## 10.1 Datenquellen

- yfinance nur für explorative Research-Zwecke,
- Produktionssignale aus einem geeigneten Markt- oder Brokerfeed,
- möglichst gleiche Datenfamilie für Paper und Live,
- Streaming für Live-Quotes und Orderüberwachung.

## 10.2 Zu speichernde Rohdaten

- OHLCV-Bars,
- Bid und Ask,
- Quote-Timestamps,
- Trades,
- Handelsstatus,
- Halts,
- Splits,
- Dividenden,
- Symboländerungen,
- Datenanbieter,
- Empfangszeitpunkt,
- Exchange-Zeitpunkt.

## 10.3 Datenqualitätsprüfungen

Vor jeder Signalberechnung:

- Bar vollständig,
- Timestamp plausibel,
- keine fehlenden Kernwerte,
- kein veralteter Quote,
- Spread innerhalb Grenzwert,
- kein aktiver Halt,
- keine offensichtlichen Preissprünge durch ungepflegte Corporate Actions.

Fehlerhafte oder unvollständige Daten verhindern die Signalerzeugung.

---

## 11. Handelskalender

Alle Marktzeiten werden relativ zum Börsenkalender definiert.

Beispiele:

- `market_open + 15 Minuten`,
- `market_close - 15 Minuten`,
- `market_close - 5 Minuten`.

Berücksichtigt werden:

- US-Börsenfeiertage,
- verkürzte Handelstage,
- Sommerzeitunterschiede,
- außerplanmäßige Schließungen,
- Marktpausen.

Feste Berliner Uhrzeiten werden nur für Reports oder Nutzerbenachrichtigungen verwendet.

---

## 12. Strategiemodell

Version 1 unterstützt höchstens drei voneinander klar getrennte Strategiefamilien.

## 12.1 Intraday Momentum

### Ziel

Kurzfristige Trend- und Volumenausbrüche innerhalb einer Handelssitzung.

### Zeithorizont

Minuten bis Handelsschluss.

### Timeframes

- Regime: 1h und 1d,
- Setup: 15m,
- Entry: 5m.

### Regeln

- keine Overnight-Position,
- Liquiditäts- und Spreadfilter,
- kein Einstieg direkt vor Handelsschluss,
- strategiebezogener Zeit-Exit.

## 12.2 Swing Trend

### Ziel

Mehrere Tage anhaltende Trendbewegungen.

### Zeithorizont

Zwei bis zehn Handelstage.

### Timeframes

- Regime: 1d und 1 Woche,
- Setup: 1h und 1d,
- Entry: 15m oder 1h.

### Regeln

- Overnight-Risiko ist Bestandteil der Strategie,
- Earnings- und Eventfilter,
- strukturbasierter oder ATR-basierter Stop,
- maximale Haltedauer.

## 12.3 Mean Reversion

### Ziel

Rückkehr überdehnter liquider Werte zu einem statistischen Mittelwert.

### Zeithorizont

Intraday oder maximal wenige Handelstage, abhängig von der konkreten Strategieversion.

### Regeln

- eigenes Regime,
- eigene Entry- und Exitlogik,
- keine Vermischung mit Momentum-Scores,
- harte Liquiditätsfilter.

---

## 13. Strategiedefinition und Versionierung

Jede Strategieversion definiert unveränderlich:

- Strategie-ID,
- Versionsnummer,
- Universum,
- erlaubte Anlageklassen,
- Timeframes,
- Features,
- Entry-Regeln,
- Invalidation,
- Stop-Regeln,
- Take-Profit-Regeln,
- Exit-Regeln,
- maximale Haltedauer,
- Kostenmodell,
- Slippage-Modell,
- zugelassene Marktregime,
- Mindestliquidität,
- maximale Positionskonzentration.

Eine veröffentlichte Strategieversion wird nicht nachträglich verändert. Änderungen erzeugen eine neue Version.

---

## 14. Signalmodell

Ein Signal ist kein Kaufbefehl, sondern ein zeitlich begrenztes Trade-Setup.

### 14.1 Signalfelder

- Signal-ID,
- Nutzer-ID,
- Strategie-ID und Version,
- Ticker,
- Asset-ID,
- Erzeugungszeitpunkt,
- Ablaufzeitpunkt,
- Marktregime,
- Entry-Zone,
- Invalidation-Level,
- Stop-Level,
- optionales Target,
- erwartete Haltedauer,
- Rohscore,
- kalibrierte Erfolgswahrscheinlichkeit, falls vorhanden,
- historischer Netto-Erwartungswert,
- geschätzte Kosten,
- geplantes Risiko,
- Begründung,
- Datenversion,
- Feature-Version,
- Universums-Version.

### 14.2 Signalstatus

- generated,
- filtered,
- published,
- accepted,
- rejected,
- expired,
- blocked_by_risk,
- order_created,
- partially_filled,
- filled,
- cancelled,
- closed.

### 14.3 Transparenz

Jedes erzeugte Signal bleibt gespeichert, auch wenn es:

- abgelehnt,
- abgelaufen,
- durch Risiko blockiert,
- oder nicht ausgeführt wurde.

---

## 15. Scoring und Konfidenz

Ein globaler, universeller Score über alle Strategien entfällt.

Jede Strategie darf einen eigenen Rohscore verwenden. Dieser dient primär zur internen Rangfolge.

Ein Score darf nur als „Konfidenz“ oder „Wahrscheinlichkeit“ angezeigt werden, wenn er:

- auf Out-of-Sample-Daten kalibriert wurde,
- eine dokumentierte Kalibrierungsmethode besitzt,
- regelmäßig auf Drift geprüft wird,
- und einen Unsicherheitsbereich enthält.

Andernfalls wird er ausdrücklich als Rohscore oder Ranking bezeichnet.

---

## 16. Portfolio-Allocator

Der Portfolio-Allocator entscheidet, welche Signale einem Nutzer tatsächlich angeboten werden.

### 16.1 Aufgaben

- doppelte Ticker-Signale zusammenführen,
- konkurrierende Strategien bewerten,
- bestehende Positionen berücksichtigen,
- Sektor- und Faktorcluster prüfen,
- Korrelationen berücksichtigen,
- Risikobudget reservieren,
- maximale Anzahl gleichzeitiger Positionen einhalten,
- erwartete Kosten abziehen.

### 16.2 Auswahlkriterien

Ein Signal wird nur angeboten, wenn:

- ausreichend Risikobudget vorhanden ist,
- keine unzulässige Konzentration entsteht,
- keine offene Position oder kollidierende Order besteht,
- Liquidität und Spread akzeptabel sind,
- und die Strategie im Nutzerprofil freigegeben ist.

### 16.3 Attribution

Wenn mehrere Strategien denselben Ticker melden, speichert das System:

- alle konkurrierenden Kandidaten,
- die ausgewählte Strategie,
- die Ablehnungsgründe der übrigen Strategien,
- die spätere Performance je Kandidat im Shadow-Modus.

---

## 17. Risikomodell

## 17.1 Grundsatz

Positionsgrößen werden aus dem maximal tolerierten Verlust berechnet.

```text
Risikobetrag = Kontowert × Risiko_pro_Trade
Stückzahl = Risikobetrag / Abstand_zum_Stop
```

Danach werden weitere Caps angewandt.

## 17.2 Konservative Standardwerte

- maximales Risiko je Trade: 0,25 % des Kontowerts,
- maximales Tagesverlustlimit: 1,00 %,
- maximale gleichzeitige Positionen: 5,
- maximal 1× Exposure,
- keine Margin,
- kein Short Selling,
- keine Optionen,
- keine automatische Budgetüberschreitung.

Die finalen Werte werden vor dem Live-Betrieb fachlich und regulatorisch geprüft.

## 17.3 Zusätzliche Risikogrenzen

- maximale Einzelpositionsgröße,
- maximale Sektorexposure,
- maximale korrelierte Exposure,
- maximale tägliche neue Exposure,
- maximale offene Orders,
- Mindestliquidität,
- maximaler Spread,
- maximale Quote-Alterung,
- Earnings- und Eventfilter,
- Verlustserien-Guard,
- Drawdown-Kill-Switch.

## 17.4 Kill-Switches

### Nutzerbezogen

- Tagesverlustlimit erreicht,
- manuell aktiviert,
- Brokerverbindung fehlerhaft,
- wiederholte Order-Rejects,
- ungewöhnliche Positionsabweichung.

### Global

- Marktdatenfeed gestört,
- Broker-API gestört,
- Reconciliation schlägt fehl,
- Datenbank oder Queue inkonsistent,
- verdächtige doppelte Orders,
- globale Verlust- oder Fehlergrenze überschritten.

Ein aktiver Kill-Switch verhindert neue Positionen. Schutz-Exits bleiben erlaubt.

---

## 18. Human Gate

### 18.1 Neue Positionen

Jede neue Position benötigt eine ausdrückliche Freigabe des Nutzers.

Vor der Bestätigung werden angezeigt:

- Ticker,
- Strategie,
- Entry-Art,
- geschätzter Entry,
- Positionsgröße,
- Stop,
- maximaler geplanter Verlust,
- erwartete Kosten,
- Ablaufzeit des Signals,
- automatische Exit-Policy.

### 18.2 Schutz-Exits

Nach der Freigabe dürfen vorab akzeptierte Schutzmaßnahmen automatisch ausgeführt werden:

- Stop Loss,
- Take Profit,
- Trailing Stop,
- strategiebezogener Exit,
- Zeit-Exit,
- Emergency Exit,
- Broker-Liquidation.

Ein allgemeiner Score-Abfall ist kein Standard-Exit.

---

## 19. Order Management

Nur das zentrale Order-Management-System darf Brokerorders senden.

### 19.1 Prozess

1. Trade Intent empfangen.
2. Nutzer und Berechtigung prüfen.
3. Signalstatus und Ablauf prüfen.
4. aktuelle Marktdaten prüfen.
5. Portfolio- und Risikoprüfung ausführen.
6. Orderplan erstellen.
7. Idempotency ID vergeben.
8. Order an Broker senden.
9. Brokerstatus überwachen.
10. Partial Fills, Fills, Rejects und Cancels verarbeiten.
11. Position und Audit-Log aktualisieren.
12. Nutzer benachrichtigen.

### 19.2 Ordertypen

Version 1 bevorzugt:

- Limit Order,
- Marketable Limit Order,
- Stop Order,
- Stop-Limit Order,
- Bracket Order, wenn zuverlässig unterstützt.

### 19.3 Schutz gegen Fehlorders

- Idempotency Keys,
- keine direkte Order aus Web oder Telegram,
- Pre-Trade-Risk-Prüfung,
- maximale Slippage,
- maximale Ordergröße,
- Quote-Freshness,
- Duplicate-Order-Erkennung,
- Cancel-/Replace-Regeln,
- Timeouts,
- Reconciliation.

---

## 20. Brokerintegration

Alpaca wird bevorzugt per OAuth angebunden.

### Anforderungen

- minimal notwendige Scopes,
- verschlüsselte Tokenablage,
- Tokenwiderruf,
- keine Secrets in Logs,
- keine Anzeige gespeicherter Tokens,
- klare Trennung zwischen Paper und Live,
- sofortige Trennfunktion,
- Echtzeitverarbeitung von Orderereignissen,
- periodische Reconciliation.

Der Broker ist die maßgebliche Quelle für reale Order- und Positionszustände. Die interne Datenbank bildet diese Zustände nachvollziehbar ab.

---

## 21. Backtesting

## 21.1 Grundsätze

- kein Look-ahead,
- abgeschlossene Bars,
- zeitkorrekte Multi-Timeframe-Ausrichtung,
- dieselbe Kernlogik wie Paper und Live,
- realistische Kosten,
- realistische Slippage,
- reproduzierbare Daten- und Codeversionen.

## 21.2 Pflichtbestandteile

- point-in-time Universen,
- delistete Werte, soweit Daten verfügbar,
- Corporate Actions,
- Spreadmodell,
- Slippagemodell,
- Liquiditätsgrenzen,
- Teilfüllungsannahmen,
- Orderlatenz,
- Ausführung frühestens nach Signalentstehung,
- getrennte In-Sample- und Out-of-Sample-Phasen,
- Embargo und Purging,
- finaler unangetasteter Holdout.

## 21.3 Kennzahlen

- Anzahl Trades,
- Nettoertrag,
- CAGR,
- Max Drawdown,
- MAR,
- Profitfaktor,
- Trefferquote,
- Erwartungswert,
- Sharpe,
- downside-orientierte Kennzahlen,
- Turnover,
- durchschnittliche Haltedauer,
- durchschnittliche Kosten,
- Slippage,
- Performance nach Marktregime,
- Konfidenzintervalle.

Keine Strategie wird ausschließlich anhand einer Kennzahl freigegeben.

---

## 22. Strategie-Lebenszyklus

### 22.1 Research

- Offline-Backtest,
- Datenprüfung,
- Logikprüfung,
- Kosten- und Sensitivitätsanalyse.

### 22.2 Shadow

- Signale auf echten Live-Daten,
- keine handelbaren Nutzeraktionen,
- Messung von Datenqualität und Signalfrequenz.

### 22.3 Paper

- vollständiger Order- und Risikoprozess,
- Broker-Paperkonto,
- Messung von Slippage, Rejects und Fill-Verhalten.

### 22.4 Canary Live

- sehr kleine, fest begrenzte Exposure,
- interne oder explizit freigeschaltete Konten,
- verschärfte Monitoring-Grenzen.

### 22.5 Live freigegeben

Erst nach stabiler Shadow-, Paper- und Canary-Phase.

---

## 23. Strategie-Labor

Das Labor arbeitet mit einem Champion-Candidate-Modell.

### 23.1 Erlaubt

- Parameterkandidaten erzeugen,
- Backtests ausführen,
- Kandidaten im Shadow-Modus führen,
- Ergebnisse vergleichen,
- Pending-Vorschläge schreiben.

### 23.2 Nicht erlaubt

- Live-Parameter direkt ändern,
- Strategieversionen automatisch aktivieren,
- Human Gate umgehen,
- LLM-Ausgaben direkt als Handelssignal verwenden,
- Suchräume unbegrenzt erweitern,
- Holdout-Daten wiederholt zur Optimierung verwenden.

### 23.3 Promotion

Eine Promotion benötigt:

- ausreichende Stichprobengröße,
- mehrere OOS-Folds,
- robuste Kostenannahmen,
- stabile Ergebnisse über Marktregime,
- keine unvertretbare Drawdown-Verschlechterung,
- Shadow- oder Paper-Bestätigung,
- menschliche Freigabe.

---

## 24. Performance und Reporting

Alle Ergebnisse werden strikt getrennt:

- Backtest,
- Shadow,
- Paper,
- Live.

### 24.1 Keine Vermischung

- keine gemeinsame Equity-Kurve,
- keine gemeinsame Trefferquote,
- keine gemeinsame P&L,
- keine kombinierte Sharpe.

### 24.2 Pflichtangaben

- Brutto- und Nettoergebnis,
- Kosten,
- Slippage,
- realisierte und unrealisierte P&L,
- offene Risiken,
- Benchmark,
- Zeitraum,
- Anzahl Trades,
- Strategieversion,
- Datenmodus.

---

## 25. Technische Architektur

```text
stockbot/
  api/
    web/
    telegram/
  services/
    auth/
    users/
    signals/
    portfolio/
    risk/
    orders/
    broker/
    notifications/
    reporting/
  market/
    data/
    calendar/
    universes/
    features/
    strategies/
  execution/
    oms/
    broker_adapters/
    reconciliation/
  research/
    backtest/
    optimize/
    reports/
  infrastructure/
    db/
    queue/
    audit/
    monitoring/
  core/
    models/
    enums/
    config/
```

### 25.1 Zentrale Komponenten

- Market Data Service,
- Signal Engine,
- Portfolio Allocator,
- Risk Service,
- Order Management System,
- Broker Adapter,
- Reconciliation Service,
- Notification Service,
- Reporting Service,
- Research und Strategy Lab.

### 25.2 Datenbank

PostgreSQL wird zur zentralen transaktionalen Datenbank.

### 25.3 Event-Verarbeitung

Eine Queue oder ein Outbox-Muster verarbeitet:

- Signalereignisse,
- Trade Intents,
- Orderstatus,
- Brokerfills,
- Benachrichtigungen,
- Reconciliation-Abweichungen.

### 25.4 Audit

Sicherheits- und handelsrelevante Aktionen werden append-only protokolliert.

---

## 26. Datenmodell

Wichtige Tabellen beziehungsweise Aggregate:

- users,
- user_roles,
- broker_connections,
- risk_profiles,
- strategies,
- strategy_versions,
- universes,
- universe_memberships,
- market_data_metadata,
- signals,
- signal_candidates,
- trade_intents,
- risk_decisions,
- orders,
- order_events,
- fills,
- positions,
- position_events,
- performance_snapshots,
- backtest_runs,
- experiment_runs,
- lab_candidates,
- lab_decisions,
- audit_events,
- system_incidents,
- kill_switch_events.

Jede handelsrelevante Entität besitzt:

- eindeutige ID,
- Erstellungszeit,
- Änderungszeit,
- Nutzerbezug,
- Strategieversion,
- Modus,
- Korrelations- oder Trace-ID.

---

## 27. Sicherheit

### 27.1 Authentifizierung

- sichere Sessions,
- HttpOnly,
- Secure,
- SameSite,
- CSRF-Schutz,
- Login-Rate-Limit,
- Session-Rotation,
- optional Zwei-Faktor-Authentifizierung.

### 27.2 Secrets

- keine Produktionssecrets im Git-Repository,
- keine Produktionssecrets in normalen `.env`-Dateien,
- Secret Store oder systemd-Credentials,
- Schlüsselrotation,
- getrennte Schlüssel für verschiedene Zwecke.

### 27.3 Betrieb

- dedizierter unprivilegierter Systemnutzer,
- `NoNewPrivileges`,
- restriktive Dateisystemrechte,
- eingeschränkte Netzwerkrechte,
- gepinnte Dependencies,
- reproduzierbare Builds,
- Security Scans,
- Backup und Restore Tests,
- Monitoring und Alerting.

---

## 28. Observability

Das System erfasst mindestens:

- Datenfeed-Latenz,
- Quote-Alter,
- Signaldurchsatz,
- Risikoablehnungen,
- Orderlatenz,
- Fill Rate,
- Partial-Fill-Rate,
- Reject Rate,
- Cancel-Rate,
- Reconciliation-Abweichungen,
- Slippage,
- aktive Kill-Switches,
- Queue-Rückstand,
- Fehler je Service,
- Brokerverfügbarkeit.

Kritische Ereignisse erzeugen Alarmierungen über einen separaten Kanal.

---

## 29. Tests

### 29.1 Unit Tests

- Indikatoren und Features,
- Strategiebedingungen,
- Risikoberechnung,
- Sizing,
- Orderstatus,
- Zeitzonen und Börsenkalender,
- Berechtigungen.

### 29.2 Integrationstests

- Signal bis Trade Intent,
- Risk Decision bis Order,
- Broker-Fill bis Position,
- Reconciliation,
- Telegram und Web gegen dieselbe Service-Schicht.

### 29.3 Replay-Tests

Historische Broker- und Marktereignisse werden reproduzierbar abgespielt.

### 29.4 Failure Tests

- Marktdatenfeed fällt aus,
- Broker ist nicht erreichbar,
- doppelte Nachricht,
- verspäteter Fill,
- Partial Fill,
- DB-Neustart,
- Queue-Neustart,
- veraltete Quotes,
- inkonsistente Position.

### 29.5 Sicherheitsprüfungen

- Dependency Audit,
- Secret Scan,
- Auth- und Sessiontests,
- Rechteprüfung,
- Rate-Limit-Tests,
- OWASP-basierte Webtests.

---

## 30. Regulatorische Leitplanke

Vor einem öffentlichen Multi-User-Live-Betrieb wird der komplette Ablauf fachjuristisch geprüft.

Zu bewerten sind insbesondere:

- konkrete Anlageempfehlungen,
- Personalisierung,
- Orderannahme und Orderübermittlung,
- automatisierte Portfolioentscheidungen,
- Brokerpartnerschaft,
- Vergütungsmodell,
- Interessenkonflikte,
- Performance-Kommunikation,
- Datenschutz,
- Aufbewahrung,
- Beschwerden und Nutzerkommunikation.

Der Hinweis „keine Anlageberatung“ ersetzt keine regulatorische Prüfung.

---

## 31. Dauerhafte Leitplanken

- Paper-Modus bleibt Standard.
- Live-Trading benötigt eine separate Freigabe.
- Neue Positionen benötigen ein Human Gate.
- Schutz-Exits dürfen nach vorheriger Zustimmung automatisch laufen.
- Kein Hebel in Version 1.
- Keine Optionen in Version 1.
- Kein yfinance im Produktionssignalpfad.
- Keine Budgetüberschreitung.
- Keine direkte Order aus Telegram oder Web.
- Keine Vermischung von Backtest, Shadow, Paper und Live.
- Keine direkte Live-Änderung durch den Optimizer.
- Jede Entscheidung muss reproduzierbar sein.
- Jede Brokeraktion muss auditierbar sein.
- Bei unklarer Daten- oder Brokerlage werden keine neuen Positionen eröffnet.

---

## 32. Erfolgskriterien für Version 1

Version 1 gilt als technisch erfolgreich, wenn:

- alle neuen Positionen den Risk Service durchlaufen,
- keine doppelten Orders durch wiederholte Requests entstehen,
- Brokerfills in Echtzeit verarbeitet werden,
- Reconciliation-Abweichungen erkannt und gemeldet werden,
- keine Order Nutzer- oder Risikolimits überschreitet,
- Exchange-Kalender korrekt verwendet werden,
- Backtest, Shadow, Paper und Live getrennt ausgewertet werden,
- alle Signale und Entscheidungen gespeichert werden,
- Paper-Trading über einen ausreichend langen Zeitraum stabil läuft,
- Kill-Switches getestet und dokumentiert sind,
- und Live-Trading nur nach expliziter Aktivierung möglich ist.

---

## 33. Spätere Erweiterungen

Erst nach stabiler Version 1 werden separat bewertet:

- weitere Aktienmärkte,
- Kryptowährungen,
- Rohstoffe,
- Short Selling,
- Margin,
- Optionen,
- Portfoliooptimierung,
- zusätzliche Broker,
- mobile App,
- personalisierte Strategy Bundles,
- kommerzielle Multi-User-Veröffentlichung.

Jede Erweiterung benötigt ein eigenes Daten-, Risiko-, Execution- und Compliance-Konzept.
