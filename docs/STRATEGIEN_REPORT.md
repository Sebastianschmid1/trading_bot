# 📊 Strategie-Report (alle Strategien) — Tiefenanalyse

*Erstellt: 2026-06-25 · Korb: S&P 500 (38 Aktien) · Zeitraum: 2024-06-24 → 2026-06-24 · 2 Jahre · Tages-Timeframe · long + short · Portfolio: 10 Signale/Tag, Hebel 1×, Start 10000€*

> **Demo-Backtest** — **long + short**, ATR-SL/TP (Short gespiegelt), ohne Gebühren/Slippage. Shorts liefert **nur die Standard-Strategie** (Spiegel der Long-Logik); die übrigen Strategien bleiben auch im Backtest long-only. **Der Live-Bot handelt unverändert ausschließlich long** — Shorts sind reine Backtest-Analyse. Alle Kennzahlen basieren auf einer täglichen Mark-to-Market-Equity-Kurve der Portfolio-Simulation.

## 📐 Methodik — wie Strategien verglichen werden

Verglichen wird wie bei professionellen Backtests **risiko-adjustiert**, nicht nur nach roher Rendite. Grundlage ist eine **tägliche Mark-to-Market-Equity-Kurve** (offene Positionen werden täglich zum Schlusskurs neu bewertet), aus der die Standard-Kennzahlen abgeleitet werden:

- **CAGR** — annualisierte Wachstumsrate (vergleichbar über verschieden lange Zeiträume).
- **Volatilität** — annualisierte Schwankung der Tagesrenditen (Risikomaß).
- **Max-Drawdown (+ Dauer)** — größter Rückgang vom Hoch und wie lange er anhielt.
- **Sharpe** = Überrendite / Volatilität (Rendite je Risikoeinheit; > 1 gut, > 2 sehr gut).
- **Sortino** = wie Sharpe, aber nur **Downside**-Volatilität (bestraft nur Verluste).
- **Calmar** = CAGR / Max-Drawdown (Rendite je Drawdown-Risiko).
- **Recovery-Factor** = Gesamtrendite / Max-Drawdown.
- **Beta / Alpha / Korrelation** gegenüber dem **S&P 500** (Markt­abhängigkeit & Mehrwert).
- **Trade-Qualität**: Profitfaktor, Trefferquote, **Payoff** (Ø Gewinn/Ø Verlust), Erwartungswert, **Kelly**, längste Gewinn-/Verlustserie, **Exposure** (Zeit im Markt).
- **t-Statistik** der mittleren Trade-Rendite: ist die Edge statistisch belastbar (Faustregel |t| > 2) oder Zufall?

## 🏆 Rangliste (nach Sharpe-Ratio)

| # | Strategie | CAGR % | Sharpe | Sortino | Calmar | Max DD % | Vol % | PF | Win % | Trades |
|---|-----------|------:|------:|------:|------:|--------:|-----:|----|------:|------:|
| 1 | Donchian-Ausbruch (20T) (`breakout`) | +19.8 | 1.55 | 2.45 | 2.67 | 7.4 | 12.1 | 1.51 | 43.6 | 298 |
| 2 | ADX-Trendfolge (trader-dev Port) (`adx_trend`) | +13.2 | 1.22 | 1.86 | 1.23 | 10.7 | 10.6 | 1.61 | 53.8 | 169 |
| 3 | Trend-Ausrichtung (MA20>50>200) (`ma_trend`) | +15.1 | 1.17 | 1.73 | 2.13 | 7.1 | 12.7 | 1.37 | 40.7 | 403 |
| 4 | Momentum 52W-Hoch (streng) (`high52`) | +11.8 | 0.97 | 1.46 | 0.98 | 12.1 | 12.3 | 1.41 | 45.2 | 199 |
| 5 | Momentum 52W-Hoch (aktiv) (`high52_wide`) | +11.5 | 0.84 | 1.23 | 0.85 | 13.6 | 14.3 | 1.34 | 44.1 | 227 |
| 6 | Mean-Reversion (RSI-Dip) (`rsi_revert`) | +0.2 | 0.08 | 0.11 | 0.05 | 4.2 | 3.1 | 1.04 | 39.5 | 38 |
| 7 | Standard (Multi-Timeframe) (`standard`) | -8.4 | -0.45 | -0.65 | -0.34 | 24.7 | 16.5 | 0.90 | 36.2 | 566 |
| **S&P 500 (Buy & Hold)** | +16.3 | 0.99 | 1.45 | 0.86 | 18.9 | 16.6 | — | — | — |

## 📈 Grafiken

![Equity-Vergleich vs S&P 500](backtest_strategien_vergleich.png)

![Tagesende vs Halten](backtest_tagesende_vs_halten.png)

## 🔍 Strategien im Detail

### Donchian-Ausbruch (20T)  (`breakout`)

Kauft den Ausbruch über das 20-Tage-Hoch (Trendfilter >MA50, Volumen). Weite ATR-SL/TP.

**Rendite & Risiko (Equity-Kurve)**

| Kennzahl | Wert |
|----------|-----:|
| Gesamt-Rendite | +43.1 % |
| CAGR (annualisiert) | +19.8 % |
| Volatilität (annualisiert) | 12.1 % |
| Max. Drawdown | 7.4 % |
| Max. Drawdown-Dauer | 115 Handelstage |

**Risiko-adjustiert & vs. S&P 500**

| Kennzahl | Wert |
|----------|-----:|
| Sharpe-Ratio | 1.55 |
| Sortino-Ratio | 2.45 |
| Calmar-Ratio | 2.67 |
| Recovery-Factor | 5.83 |
| Beta (vs. S&P 500) | 0.40 |
| Alpha annualisiert | +12.1 % |
| Korrelation (vs. S&P 500) | 0.55 |

**Trade-Qualität**

| Kennzahl | Wert |
|----------|-----:|
| Profitfaktor | 1.51 |
| Trefferquote (Win) | 43.6 % (130/298) |
| Verlustquote (Lose) | 56.4 % (168/298) |
| Payoff-Ratio (Ø Gewinn/Ø Verlust) | 1.95 |
| Ø Gewinn / Ø Verlust | +98.42€ / -50.50€ |
| Erwartungswert/Trade | +14.47€ (+1.45 %) |
| Bester / schlechtester Trade | +22.9 % / -12.6 % |
| Längste Serie Gewinne / Verluste | 9 / 9 |
| Kelly-Anteil | 14.7 % |
| t-Statistik der Edge | 3.06  ✅ signifikant |

**Aktivität**

| Kennzahl | Wert |
|----------|-----:|
| Trades gesamt / pro Jahr | 298 / 149.1 |
| Ø Haltedauer | 14.2 Tage |
| Exposure (Zeit im Markt) | 97.8 % |
| Ø gleichzeitige Positionen | 8.45 |
| Ø Signale/Tag | 3.7 (Median 3, Max 25) |

### ADX-Trendfolge (trader-dev Port)  (`adx_trend`)

Trendfolge: Kurs>EMA200 + ADX(14)-Trend + Volatilitäts-Expansion & Velocity. ATR-SL/TP.

**Rendite & Risiko (Equity-Kurve)**

| Kennzahl | Wert |
|----------|-----:|
| Gesamt-Rendite | +27.9 % |
| CAGR (annualisiert) | +13.2 % |
| Volatilität (annualisiert) | 10.6 % |
| Max. Drawdown | 10.7 % |
| Max. Drawdown-Dauer | 160 Handelstage |

**Risiko-adjustiert & vs. S&P 500**

| Kennzahl | Wert |
|----------|-----:|
| Sharpe-Ratio | 1.22 |
| Sortino-Ratio | 1.86 |
| Calmar-Ratio | 1.23 |
| Recovery-Factor | 2.61 |
| Beta (vs. S&P 500) | 0.33 |
| Alpha annualisiert | +7.5 % |
| Korrelation (vs. S&P 500) | 0.51 |

**Trade-Qualität**

| Kennzahl | Wert |
|----------|-----:|
| Profitfaktor | 1.61 |
| Trefferquote (Win) | 53.8 % (91/169) |
| Verlustquote (Lose) | 46.2 % (78/169) |
| Payoff-Ratio (Ø Gewinn/Ø Verlust) | 1.38 |
| Ø Gewinn / Ø Verlust | +81.14€ / -58.96€ |
| Erwartungswert/Trade | +16.48€ (+1.65 %) |
| Bester / schlechtester Trade | +20.9 % / -17.5 % |
| Längste Serie Gewinne / Verluste | 9 / 7 |
| Kelly-Anteil | 20.3 % |
| t-Statistik der Edge | 2.75  ✅ signifikant |

**Aktivität**

| Kennzahl | Wert |
|----------|-----:|
| Trades gesamt / pro Jahr | 169 / 84.6 |
| Ø Haltedauer | 18.8 Tage |
| Exposure (Zeit im Markt) | 94.4 % |
| Ø gleichzeitige Positionen | 6.33 |
| Ø Signale/Tag | 1.4 (Median 1, Max 4) |

### Trend-Ausrichtung (MA20>50>200)  (`ma_trend`)

Kauft nur bei voll gestapeltem Aufwärtstrend (Kurs>MA20>MA50>MA200) + bullishem MACD.

**Rendite & Risiko (Equity-Kurve)**

| Kennzahl | Wert |
|----------|-----:|
| Gesamt-Rendite | +32.3 % |
| CAGR (annualisiert) | +15.1 % |
| Volatilität (annualisiert) | 12.7 % |
| Max. Drawdown | 7.1 % |
| Max. Drawdown-Dauer | 114 Handelstage |

**Risiko-adjustiert & vs. S&P 500**

| Kennzahl | Wert |
|----------|-----:|
| Sharpe-Ratio | 1.17 |
| Sortino-Ratio | 1.73 |
| Calmar-Ratio | 2.13 |
| Recovery-Factor | 4.54 |
| Beta (vs. S&P 500) | 0.41 |
| Alpha annualisiert | +8.2 % |
| Korrelation (vs. S&P 500) | 0.53 |

**Trade-Qualität**

| Kennzahl | Wert |
|----------|-----:|
| Profitfaktor | 1.37 |
| Trefferquote (Win) | 40.7 % (164/403) |
| Verlustquote (Lose) | 59.3 % (239/403) |
| Payoff-Ratio (Ø Gewinn/Ø Verlust) | 1.99 |
| Ø Gewinn / Ø Verlust | +73.64€ / -37.01€ |
| Erwartungswert/Trade | +8.02€ (+0.80 %) |
| Bester / schlechtester Trade | +16.6 % / -10.5 % |
| Längste Serie Gewinne / Verluste | 8 / 12 |
| Kelly-Anteil | 10.9 % |
| t-Statistik der Edge | 2.73  ✅ signifikant |

**Aktivität**

| Kennzahl | Wert |
|----------|-----:|
| Trades gesamt / pro Jahr | 403 / 201.6 |
| Ø Haltedauer | 9.8 Tage |
| Exposure (Zeit im Markt) | 96.6 % |
| Ø gleichzeitige Positionen | 7.85 |
| Ø Signale/Tag | 7.2 (Median 7, Max 20) |

### Momentum 52W-Hoch (streng)  (`high52`)

Kauft Stärke nahe dem 52-Wochen-Hoch (≥98 %) im Aufwärtstrend (>MA50). Weite ATR-SL/TP. Im Backtest robust besser als die übrigen.

**Rendite & Risiko (Equity-Kurve)**

| Kennzahl | Wert |
|----------|-----:|
| Gesamt-Rendite | +24.7 % |
| CAGR (annualisiert) | +11.8 % |
| Volatilität (annualisiert) | 12.3 % |
| Max. Drawdown | 12.1 % |
| Max. Drawdown-Dauer | 189 Handelstage |

**Risiko-adjustiert & vs. S&P 500**

| Kennzahl | Wert |
|----------|-----:|
| Sharpe-Ratio | 0.97 |
| Sortino-Ratio | 1.46 |
| Calmar-Ratio | 0.98 |
| Recovery-Factor | 2.05 |
| Beta (vs. S&P 500) | 0.38 |
| Alpha annualisiert | +5.6 % |
| Korrelation (vs. S&P 500) | 0.52 |

**Trade-Qualität**

| Kennzahl | Wert |
|----------|-----:|
| Profitfaktor | 1.41 |
| Trefferquote (Win) | 45.2 % (90/199) |
| Verlustquote (Lose) | 54.8 % (109/199) |
| Payoff-Ratio (Ø Gewinn/Ø Verlust) | 1.71 |
| Ø Gewinn / Ø Verlust | +94.49€ / -55.33€ |
| Erwartungswert/Trade | +12.43€ (+1.24 %) |
| Bester / schlechtester Trade | +26.1 % / -12.8 % |
| Längste Serie Gewinne / Verluste | 8 / 17 |
| Kelly-Anteil | 13.2 % |
| t-Statistik der Edge | 2.08  ✅ signifikant |

**Aktivität**

| Kennzahl | Wert |
|----------|-----:|
| Trades gesamt / pro Jahr | 199 / 99.6 |
| Ø Haltedauer | 19.6 Tage |
| Exposure (Zeit im Markt) | 93.2 % |
| Ø gleichzeitige Positionen | 7.79 |
| Ø Signale/Tag | 5.3 (Median 5, Max 14) |

### Momentum 52W-Hoch (aktiv)  (`high52_wide`)

Wie streng, aber schon ab ≥95 % des 52-Wochen-Hochs → mehr Signale, höhere Gesamtrendite.

**Rendite & Risiko (Equity-Kurve)**

| Kennzahl | Wert |
|----------|-----:|
| Gesamt-Rendite | +24.3 % |
| CAGR (annualisiert) | +11.5 % |
| Volatilität (annualisiert) | 14.3 % |
| Max. Drawdown | 13.6 % |
| Max. Drawdown-Dauer | 118 Handelstage |

**Risiko-adjustiert & vs. S&P 500**

| Kennzahl | Wert |
|----------|-----:|
| Sharpe-Ratio | 0.84 |
| Sortino-Ratio | 1.23 |
| Calmar-Ratio | 0.85 |
| Recovery-Factor | 1.78 |
| Beta (vs. S&P 500) | 0.50 |
| Alpha annualisiert | +3.8 % |
| Korrelation (vs. S&P 500) | 0.58 |

**Trade-Qualität**

| Kennzahl | Wert |
|----------|-----:|
| Profitfaktor | 1.34 |
| Trefferquote (Win) | 44.1 % (100/227) |
| Verlustquote (Lose) | 55.9 % (127/227) |
| Payoff-Ratio (Ø Gewinn/Ø Verlust) | 1.70 |
| Ø Gewinn / Ø Verlust | +96.55€ / -56.92€ |
| Erwartungswert/Trade | +10.69€ (+1.07 %) |
| Bester / schlechtester Trade | +26.7 % / -17.5 % |
| Längste Serie Gewinne / Verluste | 10 / 17 |
| Kelly-Anteil | 11.1 % |
| t-Statistik der Edge | 1.87  ⚠️ schwach |

**Aktivität**

| Kennzahl | Wert |
|----------|-----:|
| Trades gesamt / pro Jahr | 227 / 113.6 |
| Ø Haltedauer | 19.5 Tage |
| Exposure (Zeit im Markt) | 97.2 % |
| Ø gleichzeitige Positionen | 8.81 |
| Ø Signale/Tag | 10.6 (Median 11, Max 22) |

### Mean-Reversion (RSI-Dip)  (`rsi_revert`)

Kauft Rücksetzer: RSI(14)<30 im langfristigen Aufwärtstrend (Kurs>MA200). ATR-SL/TP.

**Rendite & Risiko (Equity-Kurve)**

| Kennzahl | Wert |
|----------|-----:|
| Gesamt-Rendite | +0.4 % |
| CAGR (annualisiert) | +0.2 % |
| Volatilität (annualisiert) | 3.1 % |
| Max. Drawdown | 4.2 % |
| Max. Drawdown-Dauer | 333 Handelstage |

**Risiko-adjustiert & vs. S&P 500**

| Kennzahl | Wert |
|----------|-----:|
| Sharpe-Ratio | 0.08 |
| Sortino-Ratio | 0.11 |
| Calmar-Ratio | 0.05 |
| Recovery-Factor | 0.10 |
| Beta (vs. S&P 500) | 0.09 |
| Alpha annualisiert | -1.3 % |
| Korrelation (vs. S&P 500) | 0.50 |

**Trade-Qualität**

| Kennzahl | Wert |
|----------|-----:|
| Profitfaktor | 1.04 |
| Trefferquote (Win) | 39.5 % (15/38) |
| Verlustquote (Lose) | 60.5 % (23/38) |
| Payoff-Ratio (Ø Gewinn/Ø Verlust) | 1.60 |
| Ø Gewinn / Ø Verlust | +67.98€ / -42.61€ |
| Erwartungswert/Trade | +1.05€ (+0.10 %) |
| Bester / schlechtester Trade | +10.5 % / -9.7 % |
| Längste Serie Gewinne / Verluste | 3 / 6 |
| Kelly-Anteil | 1.5 % |
| t-Statistik der Edge | 0.11  ⚠️ schwach |

**Aktivität**

| Kennzahl | Wert |
|----------|-----:|
| Trades gesamt / pro Jahr | 38 / 19.0 |
| Ø Haltedauer | 9.8 Tage |
| Exposure (Zeit im Markt) | 45.2 % |
| Ø gleichzeitige Positionen | 0.74 |
| Ø Signale/Tag | 1.3 (Median 1, Max 3) |

### Standard (Multi-Timeframe)  (`standard`)

Multi-Timeframe-Momentum: RSI/MACD/Trend/Volumen über 5m–1d, Stärke 0–100, ATR-SL/TP.

**Rendite & Risiko (Equity-Kurve)**

| Kennzahl | Wert |
|----------|-----:|
| Gesamt-Rendite | -15.9 % |
| CAGR (annualisiert) | -8.4 % |
| Volatilität (annualisiert) | 16.5 % |
| Max. Drawdown | 24.7 % |
| Max. Drawdown-Dauer | 476 Handelstage |

**Risiko-adjustiert & vs. S&P 500**

| Kennzahl | Wert |
|----------|-----:|
| Sharpe-Ratio | -0.45 |
| Sortino-Ratio | -0.65 |
| Calmar-Ratio | -0.34 |
| Recovery-Factor | -0.65 |
| Beta (vs. S&P 500) | -0.58 |
| Alpha annualisiert | +2.2 % |
| Korrelation (vs. S&P 500) | -0.58 |

**Trade-Qualität**

| Kennzahl | Wert |
|----------|-----:|
| Profitfaktor | 0.90 |
| Trefferquote (Win) | 36.2 % (205/566) |
| Verlustquote (Lose) | 63.8 % (361/566) |
| Payoff-Ratio (Ø Gewinn/Ø Verlust) | 1.58 |
| Ø Gewinn / Ø Verlust | +67.69€ / -42.86€ |
| Erwartungswert/Trade | -2.82€ (-0.28 %) |
| Bester / schlechtester Trade | +17.2 % / -15.4 % |
| Längste Serie Gewinne / Verluste | 8 / 18 |
| Kelly-Anteil | -4.2 % |
| t-Statistik der Edge | -1.17  ⚠️ schwach |

**Aktivität**

| Kennzahl | Wert |
|----------|-----:|
| Trades gesamt / pro Jahr | 566 / 283.2 |
| Ø Haltedauer | 8.6 Tage |
| Exposure (Zeit im Markt) | 97.8 % |
| Ø gleichzeitige Positionen | 9.73 |
| Ø Signale/Tag | 12.2 (Median 12, Max 26) |
| Richtungs-Split (Long / Short) | 171 / 395 Trades |
| P&L-Beitrag (Long / Short) | +731.28€ / -2326.48€ |

