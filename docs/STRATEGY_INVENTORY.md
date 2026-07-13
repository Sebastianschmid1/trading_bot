# Strategie-Inventur (STRAT-001)

*Stand: 2026-07-13. Codebasis: `stockbot/market/strategies.py`,
`stockbot/market/analyzer.py`, `stockbot/backtest/engine.py` und
`stockbot/optimize/lab.py`. Dieses Inventar beschreibt den Ist-Zustand; die verbindliche
Klassifizierung und V1-Auswahl folgen separat in STRAT-002.*

## 1. Lesart und gemeinsame Rahmenbedingungen

- Das Registry enthält 16 Einträge einschließlich `standard`. Alle produktiven Generatoren sind
  long-only; nur `standard` kann in einem ausdrücklich so gestarteten Backtest zusätzlich gespiegelte
  Short-Signale erzeugen.
- Das handelbare Ticker-Universum kommt von außen aus Scanner/Region/Watchlist. Keine Strategie
  erzwingt selbst „S&P 500“. Die Spalte **Universum/Filter** nennt deshalb die implizite
  Code-Eignung und die tatsächlich geprüften Kurs-/Regimefilter, nicht eine feste Indexmitgliedschaft.
- Mit Ausnahme von `standard` lesen alle Generatoren ausschließlich `tf_data["1d"]`. `standard`
  nutzt live `5m`, `15m`, `1h` und `1d`; Tagesdaten liefern dort zusätzlich Entry-Gate,
  Wochentrend und ATR-SL/TP. Die aktuelle Backtest-Engine reicht auch an `standard` nur `1d` weiter.
- Alle Signale setzen ATR-basierte initiale Stop-Loss- und Take-Profit-Marken. Seit TSAFE-005 ist
  der fortlaufende Health-Score **kein Exit** mehr. Live schließen nur Liquidation, SL, TP,
  manueller Verkauf sowie der Tagesjob: optional EOD oder, bei ausgeschaltetem EOD, spätestens nach
  14 Kalendertagen. Strategiebezogene Struktur-/Momentum-Exits sind noch nicht implementiert.
- Der Demo-Backtest prüft Exits ab der nächsten Tagesbar intrabar und konservativ in der Reihenfolge
  Liquidation → SL → TP; andernfalls gilt ein Zeitlimit von 40 Handelstagen. Das ist nicht identisch
  mit der Live-Haltepolitik. Bei `ai_adaptive` verwendet der Backtest abweichend einen
  3-ATR-Trailing-Stop und lässt dann das feste TP unberücksichtigt.

## 2. Backtest- und OOS-Datenbasis

Die Spalten **Trades** und **Ø Haltedauer** stammen aus dem bereits im Repository liegenden Report
`data/reports/strategies_3y.json` (erzeugt 2026-07-06) und wurden nicht in dieser Sandbox neu
berechnet. Reportfenster: 2023-07-03 bis 2026-07-02; vollständige heutige S&P-500-Liste mit 501
Werten; Top 10 Signale, höchstens 10 gleichzeitige Positionen à 1.000 USD, Startkapital 10.000 USD,
Hebel 1, native SL/TP, maximal 40 Handelstage und 0,05 % pauschale Kosten je Seite. Die Tradezahl
ist somit die Zahl **ausgeführter Portfolio-Trades**, nicht die Zahl aller Rohsignale. Wegen der
heutigen Indexliste besteht Survivorship-Bias.

Für keine fixe Strategie liegt ein separater Walk-Forward-/OOS-Report vor. `ai_adaptive` besitzt
zwar einen implementierten 70/30-IS/OOS-Lab-Loop mit Embargo, drei OOS-Folds, Bootstrap-Gate,
Drawdown-Grenzen und menschlicher Freigabe; unter `data/lab/` liegen in diesem Checkout jedoch keine
persistierten Runs. Deshalb werden auch für diese Strategie keine OOS-Kennzahlen behauptet.

## 3. Strategie-Inventar: Hypothese, Entry und Universum

| Key / Label | Zielidee / Hypothese | Entry-Bedingung aus dem Code | Implizites Universum / Filter | Abhängigkeiten zu anderen Strategien |
|---|---|---|---|---|
| `standard` — Standard (Multi-Timeframe) | Bullische Momentum-Konfluenz über mehrere Zeitebenen soll kurzfristige Long-Chancen identifizieren. | Tages-Gate: bullischer MACD **oder** RSI(14) < 35, zugleich RSI < 65; kein klarer Wochen-Abwärtstrend; gewichtete Stärke aus 5m/15m/1h/1d mindestens 55. | Liquide OHLCV-Werte mit mindestens ca. 210 Tagesbars und verfügbaren Intraday-Bars; Longs bei Wochen-Abwärtstrend blockiert. Kein fester Indexfilter. | Eigenständige Analyzer-Baseline; alle anderen Registry-Strategien umgehen deren Entry-Logik, nutzen aber teilweise dieselben Indikatorhelfer. |
| `adx_trend` — ADX-Trendfolge (trader-dev Port) | Eine neu einsetzende Volatilitätsexpansion in einem etablierten Aufwärtstrend soll einen Trendimpuls ankündigen. | Expansion-Z-Score kreuzt 1,0 aufwärts; positive 5-Tage-Velocity mit \|Z\| > 0,5; ADX(14) ≥ 10; Schlusskurs > EMA200. | Tages-OHLCV, mindestens 260 Bars; nur bullisches EMA200-Regime und nicht seitwärts laut ADX. | Eigenständiger Pine-Port; teilt nur ATR-/RSI-/MACD-Helfer und Signalformat mit den übrigen Strategien. |
| `rsi_revert` — Mean-Reversion (RSI-Dip) | Ein überverkaufter Rücksetzer innerhalb eines langfristigen Aufwärtstrends soll zum Mittel zurückkehren. | RSI(14) < 30 und Schlusskurs > MA200. | Tages-OHLCV, mindestens 210 Bars; ausschließlich Rücksetzer oberhalb MA200. | Eigenständig; konzeptionell verwandt mit `bb_revert` und `streversal`, aber keine Signalabhängigkeit. |
| `breakout` — Donchian-Ausbruch (20T) | Ein neues 20-Tage-Hoch in positivem Trend soll Anschlusskäufe auslösen. | Schlusskurs erreicht/übersteigt das höchste Hoch der vorherigen 20 Bars und liegt über MA50; Volumen erhöht nur das Ranking, es ist kein hartes Gate. | Tages-OHLCV, mindestens 210 Bars; nur Kurs > MA50. | Eigenständig; Momentum-Verwandtschaft zu `high52`/`high52_wide`, keine Code-Abhängigkeit. |
| `ma_trend` — Trend-Ausrichtung (MA20>50>200) | Vollständig gestapelte gleitende Durchschnitte plus MACD sollen einen intakten Aufwärtstrend abbilden. | Kurs > MA20 > MA50 > MA200 und MACD-Linie > Signallinie bei positivem Histogramm. | Tages-OHLCV, mindestens 210 Bars; streng bullischer MA-Stack. | Eigenständig; teilt MACD-Helfer mit `standard`. |
| `high52` — Momentum 52W-Hoch (streng) | Relative Stärke nahe dem Jahreshoch soll sich fortsetzen. | Kurs ≥ 98 % des höchsten Hochs der letzten 252 Bars und Kurs > MA50. | Tages-OHLCV, mindestens 260 Bars; Jahreshoch-Nähe und MA50-Regime. | Gemeinsame Implementierung und gemeinsamer Health-Score mit `high52_wide`; unterscheidet sich primär durch `tol=0.98`. |
| `high52_wide` — Momentum 52W-Hoch (aktiv) | Dieselbe Jahreshoch-Hypothese mit breiterem Gate soll mehr handelbare Signale liefern. | Wie `high52`, aber Kurs bereits ab 95 % des 252-Bar-Hochs; zusätzlich Kurs > MA50. | Tages-OHLCV, mindestens 260 Bars; breiterer Jahreshoch-Filter als `high52`. | Direkte Variante von `high52`; gleiche `_high52_signal`-Implementierung und gleicher Health-Score, `tol=0.95`. |
| `tsmom` — Time-Series-Momentum (12-1) | Positives längerfristiges Eigenmomentum soll nach Auslassen des jüngsten Monats fortbestehen. | 252-Bar-Rendite, gemessen bis 21 Bars vor heute, > 0; aktueller Kurs > MA200. | Tages-OHLCV, mindestens 278 Bars; positives 12-1-Momentum und bullisches MA200-Regime. Stärke dient der Top-N-Rangfolge. | Eigenständig; Momentum-Verwandtschaft zu `frog`, aber keine Signalabhängigkeit. |
| `lowvol` — Low-Volatility-Anomalie (BAB) | Ruhige Aktien im Aufwärtstrend sollen risikoadjustiert besser abschneiden; die ruhigsten Kandidaten werden bevorzugt. | Kurs > MA100 und annualisierte realisierte Volatilität der letzten 126 Bars zwischen 0 und 45 %. | Tages-OHLCV, mindestens 131 Bars; Aufwärtstrend plus Volatilitätsdeckel. Stärke ist invers zur Volatilität und wirkt cross-sektional im Top-N. | Eigenständig; verwendet das gemeinsame Signal-/Top-N-Schema. |
| `faber` — Faber-Tactical (10-Monats-SMA) | Der frische Wechsel über den langfristigen Mittelwert soll den Beginn eines positiven Regimes markieren. | Ausschließlich frischer Aufwärts-Cross: vorheriger Schluss ≤ vorheriger SMA200 und aktueller Schluss > aktueller SMA200. | Tages-OHLCV, mindestens 205 Bars; kein fortlaufendes „oberhalb SMA“-Signal, sondern nur der Cross-Tag. | Eigenständig; keine Abhängigkeit zu einer anderen Strategie. |
| `streversal` — Short-Term-Reversal (1M) | Ein starker Monatsverlust in einem weiterhin positiven Langfristregime soll kurzfristig zurücklaufen. | 21-Bar-Rendite < -8 % und aktueller Kurs > MA200. | Tages-OHLCV, mindestens 226 Bars; nur starke Verlierer, deren MA200-Regime noch intakt ist. | Eigenständig; Mean-Reversion-Verwandtschaft zu `rsi_revert`/`bb_revert`, keine Signalabhängigkeit. |
| `frog` — Frog-in-the-Pan-Momentum | Kontinuierlich aufgebautes 6-Monats-Momentum soll nachhaltiger sein als Momentum aus wenigen Sprüngen. | Positives Momentum über ein 126-Bar-Segment unter Auslassung der jüngsten 21 Bars; Kurs > MA200; mehr als 52 % positive Tage im Segment. | Tages-OHLCV, mindestens 152 Bars laut Mindestprüfung; MA200 wird bei kürzerer Historie technisch über die verfügbaren Bars gemittelt. Stärke rankt Momentum und Kontinuität. | Eigenständig; konzeptionell verwandt mit `tsmom`, keine Signalabhängigkeit. |
| `bb_revert` — Bollinger %B Mean-Reversion | Ein Rücksetzer an das untere Bollinger-Band im positiven Langfristregime soll zum Bandmittel zurückkehren. | 20-Bar-%B ≤ 0,10 bei Bandbreite 2 Standardabweichungen und Kurs > MA200. | Tages-OHLCV, mindestens 205 Bars; nur Band-Rücksetzer oberhalb MA200. | Eigenständig; Mean-Reversion-Verwandtschaft zu `rsi_revert`/`streversal`, keine Signalabhängigkeit. |
| `adx_mfi` — ADX+MFI Trend-Confirmed | Trendstärke, positive Richtung und Geldfluss sollen gemeinsam robustere Trend-Entries liefern. | ADX(14) ≥ 20, +DI > -DI, Kurs > MA50 und MFI(14) ≥ 50. | Tages-OHLCV mit Volumen, mindestens 90 Bars; bullisches DMI-/MA50-Regime und positiver Money Flow. | Eigenständig; teilt das ADX-Konzept mit `adx_trend`, aber weder dessen Expansionstrigger noch Codepfad. |
| `supertrend` — SuperTrend Trend-Follow | Eine bullische SuperTrend-Richtung oberhalb des Langfristregimes soll Trends mit weitem Gewinnziel ausnutzen. | SuperTrend(ATR 10, Faktor 3) ist bullisch und Kurs > MA200; ein frischer Richtungswechsel erhöht nur das Ranking. | Tages-OHLCV, mindestens 210 Bars; bullisches SuperTrend- und MA200-Regime. Der Zustand wird aus höchstens 250 Bars rekonstruiert. | Direkte Saat-/Referenzstrategie von `ai_adaptive`; beide teilen `_supertrend_dir` und aktuell dieselbe Entry-Struktur. |
| `ai_adaptive` — KI-Strategie (selbst-lernend) | Die SuperTrend-Hypothese soll durch kontrollierte, walk-forward geprüfte Parameteränderungen adaptieren. | Aktuell identisch zu `supertrend`: bullische SuperTrend-Richtung und Kurs > MA200; Parameter kommen aus einem eigenen, durch das Lab veränderbaren Satz. | Tages-OHLCV, mindestens 210 Bars; derzeit dasselbe SuperTrend-/MA200-Regime wie `supertrend`. | Startet als Kopie von `supertrend`; eigener Parametersatz und Lab-Loop. Kandidaten ändern pro Zyklus gezielt Parameter, nicht die Entry-Struktur. |

## 4. Strategie-Inventar: Timeframe, Exit, Haltedauer, Kosten und Evidenz

**Gemeinsame Live-Exit-Ergänzung für jede Tabellenzeile:** kein Score-Exit; zusätzlich zu den
genannten SL/TP-Marken gelten Liquidation, manueller Exit und der gemeinsame EOD-/14-Tage-Exit.
Die Ø-Haltedauer ist der beobachtete Wert des oben beschriebenen 3-Jahres-Backtests; jeder dortige
Trade endet spätestens nach 40 Handelstagen.

| Key | Timeframe(s) | Strategieparameter für Exit | Typische Haltedauer | Kostenempfindlichkeit | # Backtest-Trades | OOS-Status |
|---|---|---|---|---|---:|---|
| `standard` | Live: 5m/15m/1h/1d; Backtest: nur 1d | SL 1,5 ATR / TP 2,5 ATR | Ø 8,3 Handelstage im 3J-BT; die Intraday-Zielidee ist mit dem Tages-BT nur eingeschränkt geprüft. | **hoch**: enge Marken, häufige Signale und Intraday-Ausführung. | 891 | Kein separater Walk-Forward-/OOS-Report vorhanden. |
| `adx_trend` | 1d | SL 2,5 ATR / TP 4 ATR | Ø 19,5 Handelstage im 3J-BT. | **mittel**: selektiver Crossover-Trigger, aber moderater Stop. | 371 | Kein separater Walk-Forward-/OOS-Report vorhanden. |
| `rsi_revert` | 1d | SL 1,5 ATR / TP 2,5 ATR | Ø 9,7 Handelstage im 3J-BT. | **hoch**: enger Stop und kurze Rücklaufthese. | 395 | Kein separater Walk-Forward-/OOS-Report vorhanden. |
| `breakout` | 1d | SL 2 ATR / TP 4 ATR | Ø 16,9 Handelstage im 3J-BT. | **mittel**: Ausbrüche sind slippage-anfällig, Marken sind aber nicht sehr eng. | 434 | Kein separater Walk-Forward-/OOS-Report vorhanden. |
| `ma_trend` | 1d | SL 1,5 ATR / TP 3 ATR | Ø 9,2 Handelstage im 3J-BT. | **mittel bis hoch**: enger Stop und wiederholt erfüllbares Trend-Gate. | 806 | Kein separater Walk-Forward-/OOS-Report vorhanden. |
| `high52` | 1d | SL 2,5 ATR / TP 5 ATR | Ø 21,9 Handelstage im 3J-BT. | **niedrig bis mittel**: breitere Marken und strenger Filter; Gap-Risiko nahe Hochs bleibt. | 337 | Kein separater Walk-Forward-/OOS-Report vorhanden. |
| `high52_wide` | 1d | SL 2,5 ATR / TP 5 ATR | Ø 21,2 Handelstage im 3J-BT. | **mittel**: gleiche breite Marken wie `high52`, aber das 95-%-Gate liefert mehr Kandidaten. | 349 | Kein separater Walk-Forward-/OOS-Report vorhanden. |
| `tsmom` | 1d | SL 3 ATR / TP 6 ATR | Ø 25,3 Handelstage im 3J-BT. | **niedrig bis mittel**: breite Marken und langfristige These; tägliches persistentes Gate kann Re-Entries erzeugen. | 287 | Kein separater Walk-Forward-/OOS-Report vorhanden. |
| `lowvol` | 1d | SL 2,5 ATR / TP 4 ATR | Ø 19,7 Handelstage im 3J-BT. | **niedrig bis mittel**: ruhige Titel und breiter Stop, aber Kosten wiegen bei niedriger erwarteter Bewegung relativ stärker. | 377 | Kein separater Walk-Forward-/OOS-Report vorhanden. |
| `faber` | 1d | SL 3 ATR / TP 6 ATR | Ø 26,0 Handelstage im 3J-BT. | **niedrig**: seltener frischer Cross und breite Marken. | 284 | Kein separater Walk-Forward-/OOS-Report vorhanden. |
| `streversal` | 1d | SL 1,5 ATR / TP 2,5 ATR | Ø 10,2 Handelstage im 3J-BT. | **hoch**: enger Stop in gerade stark gefallenen, potenziell gap-/spread-anfälligen Werten. | 676 | Kein separater Walk-Forward-/OOS-Report vorhanden. |
| `frog` | 1d | SL 2,5 ATR / TP 5 ATR | Ø 20,2 Handelstage im 3J-BT. | **niedrig bis mittel**: breite Marken und langsames Signal, aber persistentes Gate. | 368 | Kein separater Walk-Forward-/OOS-Report vorhanden. |
| `bb_revert` | 1d | SL 1,5 ATR / TP 2,5 ATR | Ø 9,9 Handelstage im 3J-BT. | **hoch**: enge Marken und sehr häufige Rücksetzer-Entries im Report. | 741 | Kein separater Walk-Forward-/OOS-Report vorhanden. |
| `adx_mfi` | 1d | SL 2 ATR / TP 4 ATR | Ø 13,7 Handelstage im 3J-BT. | **mittel**: mehrere Bestätigungsfilter reduzieren Frequenz; Breakout-/Trend-Ausführung bleibt slippage-sensitiv. | 542 | Kein separater Walk-Forward-/OOS-Report vorhanden. |
| `supertrend` | 1d | SL 3 ATR / TP 10 ATR | Ø 28,0 Handelstage im 3J-BT. | **niedrig bis mittel**: sehr breite Marken und lange Trades; persistentes bullisches Gate ermöglicht spätere Re-Entries. | 265 | Kein separater Walk-Forward-/OOS-Report vorhanden. |
| `ai_adaptive` | 1d | Live-Signal: SL 3 ATR / TP 10 ATR; Backtest/Lab: 3-ATR-Trailing-Stop statt festem TP | Ø 21,0 Handelstage im 3J-BT. | **mittel**: breite Entry-Marken, aber Trailing-Stop und Optimierung reagieren auf das Kostenmodell. | 353 | OOS-Gate implementiert; kein persistierter Lab-Run und keine zitierbare OOS-Kennzahl in diesem Checkout. |

Die Einschätzung der Kostenempfindlichkeit ist qualitativ. Sie berücksichtigt Signalhäufigkeit,
ATR-Abstände, erwartete Haltedauer sowie Gap-/Slippage-Risiko; sie ersetzt keinen expliziten
Kosten-Sensitivitätssweep.

## 5. Beobachtung zu möglichen Familien (keine STRAT-002-Entscheidung)

Auf Codeebene zeichnen sich drei größere Verwandtschaftsgruppen ab:

- **Momentum/Trend:** `standard`, `adx_trend`, `breakout`, `ma_trend`, `high52`,
  `high52_wide`, `tsmom`, `faber`, `frog`, `adx_mfi`, `supertrend` und `ai_adaptive`.
  Darin sind Jahreshoch-Momentum, MA-/Regimefolge, ADX-Bestätigung und SuperTrend jedoch
  eigenständige Unterideen; `ai_adaptive` ist derzeit keine eigenständige Entry-Familie, sondern
  eine parametrisierte SuperTrend-Variante.
- **Mean-Reversion:** `rsi_revert`, `streversal` und `bb_revert` kaufen unterschiedliche Arten
  von Rücksetzern, jeweils mit positivem Langfristregime als Schutzfilter.
- **Defensiver Faktor:** `lowvol` fällt zwischen klassische Trendselektion und eigenständige
  Low-Volatility-Faktoridee; sein Top-N-Ranking ist für die Hypothese besonders wichtig.

Diese Gruppierung ist nur eine technische Beobachtung aus Entry-Code und Parameterverwandtschaft.
Produktionsstatus, „Research only“/„Deprecated“ und die verbindliche Auswahl bleiben ausdrücklich
der Owner-Entscheidung in STRAT-002 vorbehalten.
