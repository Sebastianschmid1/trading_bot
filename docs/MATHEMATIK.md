# Mathematische Herangehensweise des Trading-Bots

*Eine formale Beschreibung der verwendeten Verfahren*

> **Disclaimer.** Dieses Dokument beschreibt ein **Demo-/Lehrsystem**. Alle Trades sind
> simuliert (kein echtes Geld). Es handelt sich **nicht um Anlageberatung**. Die Datenquelle
> (yfinance) hat Verzug und liefert keine Echtzeit-Garantie. Sämtliche Formeln sind direkt aus
> dem Quellcode abgeleitet; jede Gleichung verweist auf ihre Implementierung.

---

## 1. Abstract

Der Bot erzeugt täglich kurz nach US-Eröffnung (15:35 Berliner Zeit) **Long-only**-Handelssignale
für ein wählbares Aktienuniversum und überwacht aktive (Demo-)Positionen im 60-Sekunden-Takt bis
spätestens zum Tagesende. Jedes Signal beruht auf einer **mehrdimensionalen, gewichteten
Signal-Stärke** $S \in [0,100]$, die etablierte technische Indikatoren (RSI, MACD, Trend, Volumen)
über mehrere Zeiträume ($5\text{m}, 15\text{m}, 1\text{h}, 1\text{d}$) verdichtet. Risiko wird über
**ATR-basierte** Stop-Loss-/Take-Profit-Grenzen, einen wählbaren **Hebel** mit zugehörigem
**Liquidationskurs** und eine auf die Margin begrenzte **P&L-Funktion** gesteuert. Ergänzend wird
ein **Smart-Money-Score** aus öffentlich gemeldeter Insider- und Institutionen-Aktivität gebildet
und in das Ranking eingerechnet. Dieses Papier stellt die zugrunde liegende Mathematik vollständig
und nachvollziehbar dar.

---

## 2. Notation & Daten

Für ein Wertpapier bezeichne

- $P_t$ den Schlusskurs (Close) zum Bar-Index $t$, analog $H_t$ (High), $L_t$ (Low), $V_t$ (Volumen);
- $N$ die Anzahl verfügbarer Bars eines Zeitraums;
- $\mathcal{T} = \{\,5\text{m}, 15\text{m}, 1\text{h}, 1\text{d}\,\}$ die Menge der **Zeiträume**
  (Timeframes) mit zugehörigen Aggregationsgewichten $\omega_{tf}$.

Die Daten werden je Zeitraum als **ein** Batch-Download geladen (`analyzer._download_all_timeframes`);
pro Ticker entsteht ein OHLCV-DataFrame je $tf \in \mathcal{T}$. Das System ist im Demo-Modus
**Long-only**; Short-Formeln sind der Vollständigkeit halber angegeben, werden aber nicht aktiv genutzt.

---

## 3. Technische Indikatoren

### 3.1 RSI — Relative Strength Index *(→ `analyzer.calc_rsi`)*

Aus den Kursdifferenzen $\Delta_t = P_t - P_{t-1}$ werden Gewinne und Verluste getrennt:

$$
G_t = \max(\Delta_t, 0), \qquad L_t = \max(-\Delta_t, 0). \tag{1}
$$

Die mittleren Gewinne/Verluste werden nach **Wilder** geglättet (Periode $n=14$, Seed = einfacher
Mittelwert der ersten $n$ Werte):

$$
\overline{G}_t = \frac{(n-1)\,\overline{G}_{t-1} + G_t}{n}, \qquad
\text{RSI} = 100 - \frac{100}{1 + \overline{G}/\overline{L}}. \tag{2}
$$

Konvention: ist $\overline{L}=0$, gilt $\text{RSI}=100$; bei zu wenigen Bars wird neutral $50$
zurückgegeben. Schwellen: $\text{RSI} < 35$ = überverkauft (bullish), $\text{RSI} > 65$ = überkauft.

### 3.2 MACD — Moving Average Convergence/Divergence *(→ `analyzer.calc_macd`)*

Mit dem exponentiellen gleitenden Durchschnitt (EMA) der Spanne $s$, Glättungsfaktor
$\alpha = \tfrac{2}{s+1}$,

$$
\text{EMA}_t(s) = \alpha\,P_t + (1-\alpha)\,\text{EMA}_{t-1}(s), \qquad \text{EMA}_0 = P_0, \tag{3}
$$

ergeben sich MACD-Linie, Signallinie und Histogramm:

$$
\text{MACD}_t = \text{EMA}_t(12) - \text{EMA}_t(26), \tag{4}
$$

$$
\text{Signal}_t = \text{EMA}_t^{\text{(auf MACD)}}(9), \tag{5}
$$

$$
\text{Hist}_t = \text{MACD}_t - \text{Signal}_t. \tag{6}
$$

Ein **bullishes** Setup liegt vor, wenn $\text{MACD}_t > \text{Signal}_t$ **und** $\text{Hist}_t > 0$.

### 3.3 ATR — Average True Range *(→ `analyzer.calc_atr`)*

Die *True Range* erfasst die größte der drei Spannen aus Tagesschwankung und Vortages-Lücken:

$$
\text{TR}_t = \max\!\bigl(\,H_t - L_t,\; |H_t - P_{t-1}|,\; |L_t - P_{t-1}|\,\bigr). \tag{7}
$$

Der ATR ist die Wilder-Glättung der True Range (Periode $n=14$, Seed = Mittelwert der ersten $n$):

$$
\text{ATR}_t = \frac{(n-1)\,\text{ATR}_{t-1} + \text{TR}_t}{n}. \tag{8}
$$

Der ATR misst die typische Schwankungsbreite in Kurspunkten und ist die Grundlage der
volatilitätsadaptiven SL/TP-Abstände (Abschnitt 6).

### 3.4 Wochentrend *(→ `analyzer.calc_weekly_trend`)*

Die täglichen Schlusskurse werden auf Wochenbasis resampelt: $W_k = $ letzter Schluss der Woche $k$.
Mit dem 10-Wochen-Mittel jetzt bzw. vor 4 Wochen,

$$
\overline{W}_{\text{jetzt}} = \frac{1}{10}\sum_{k=K-9}^{K} W_k, \qquad
\overline{W}_{\text{prev}} = \frac{1}{10}\sum_{k=K-13}^{K-4} W_k, \tag{9}
$$

gilt der Trend als **up**, falls $W_K > \overline{W}_{\text{jetzt}} \ge \overline{W}_{\text{prev}}$;
als **down**, falls $W_K < \overline{W}_{\text{jetzt}} \le \overline{W}_{\text{prev}}$; sonst **flat**.
Long-Signale in einem klaren Wochen-Abwärtstrend werden unterdrückt (Abschnitt 5).

### 3.5 Support & Resistance *(→ `analyzer.calc_support_resistance`)*

Ein Bar $i$ ist ein **Swing-Hoch**, wenn er das Maximum im Fenster $[\,i-w,\ i+w\,]$ ist
(analog Swing-Tief mit Minimum), mit $w=5$:

$$
i \in \text{Pivots}_{\text{high}} \iff H_i = \max_{\,i-w \le j \le i+w} H_j. \tag{10}
$$

Nahe beieinanderliegende Pivot-Preise werden zu einem Level zusammengefasst, sofern ihr relativer
Abstand die Toleranz $\tau = 0{,}02$ (2 %) nicht überschreitet; jedem Cluster wird sein
Durchschnittspreis und die Anzahl Berührungen (*touches*) zugewiesen:

$$
\text{Level} = \Bigl(\bar c = \tfrac{1}{|C|}\!\sum_{p\in C} p,\;\; \text{touches} = |C|\Bigr),
\qquad \frac{p_{j+1}-p_j}{p_j} \le \tau. \tag{11}
$$

Als **Unterstützung** dient das höchste Tief-Cluster unter dem Kurs, als **Widerstand** das
niedrigste Hoch-Cluster über dem Kurs.

---

## 4. Signal-Stärke (0–100)

Die Stärke ist eine zweistufige, gewichtete Verdichtung: zuerst je Zeitraum ein Score, dann eine
Aggregation über die Zeiträume.

### 4.1 Komponenten-Scores je Zeitraum *(→ `analyzer.compute_timeframe_score`)*

Pro Zeitraum (mindestens 60 Bars erforderlich) werden vier auf $[0,1]$ normierte Teil-Scores gebildet
(je höher, desto bullisher). Mit $\text{clip}(x)=\min(\max(x,0),1)$:

**RSI-Score** (überverkauft ⇒ stark):

$$
s_{\text{rsi}} = \text{clip}\!\left(\frac{65 - \text{RSI}}{35}\right). \tag{12}
$$

**MACD-Score** (gestuft nach Bestätigungsgrad):

$$
s_{\text{macd}} =
\begin{cases}
1{,}0 & \text{MACD} > \text{Signal}\ \wedge\ \text{Hist} > 0,\\
0{,}5 & \text{MACD} > \text{Signal}\ \vee\ \text{Hist} > 0,\\
0{,}0 & \text{sonst.}
\end{cases} \tag{13}
$$

**Trend-Score** (Lage zu den gleitenden Mitteln $\text{MA}_{20}, \text{MA}_{50}$ des Zeitraums):

$$
s_{\text{trend}} =
\begin{cases}
1{,}0 & P > \text{MA}_{20} > \text{MA}_{50},\\
0{,}66 & P > \text{MA}_{20},\\
0{,}33 & P > \text{MA}_{50},\\
0{,}0 & \text{sonst.}
\end{cases} \tag{14}
$$

**Volumen-Score** über das relative Volumen $\text{RVOL} = V_t / \overline{V}_{20}$
($\overline{V}_{20}$ = 20-Bar-Durchschnittsvolumen), voll bestätigt ab $\approx 1{,}5\times$:

$$
s_{\text{vol}} = \text{clip}\!\left(\frac{\text{RVOL} - 0{,}8}{0{,}7}\right). \tag{15}
$$

Der **Timeframe-Score** ist das mit den Komponenten-Gewichten $w_c$ (`config.STRENGTH_WEIGHTS`)
normierte Mittel, skaliert auf $[0,100]$:

$$
\sigma_{tf} = 100 \cdot \frac{\sum_{c} w_c\, s_c}{\sum_{c} w_c}, \qquad
(w_{\text{rsi}}, w_{\text{macd}}, w_{\text{trend}}, w_{\text{vol}}) = (0{,}20,\ 0{,}25,\ 0{,}25,\ 0{,}30). \tag{16}
$$

> **Begründung der Volumen-Gewichtung.** Im Intraday-Handel ist das relative Volumen (RVOL) kein
> Nebenaspekt, sondern ein zentrales **Bestätigungssignal**: Es zeigt an, ob hinter einer
> Kursbewegung tatsächlich Liquidität und institutionelles Interesse stehen. Ein Kursimpuls ohne
> Volumenanstieg ist häufig ein **Fehlausbruch** (geringe Marktbeteiligung, leicht zurückzudrehen).
> Daher erhält die Volumenkomponente hier mit $0{,}30$ das höchste Einzelgewicht — bewusst höher als
> in einem reinen Trendfolge-Ansatz auf Tagesbasis. Die Gewichte sind in `config.STRENGTH_WEIGHTS`
> zentral und frei anpassbar; sie müssen nicht auf $1$ summieren, da in Gl. (16) normiert wird.

### 4.2 Aggregation über die Zeiträume *(→ `analyzer.compute_strength`)*

Die Gesamt-Stärke ist das mit den Zeitraum-Gewichten $\omega_{tf}$ (`config.SIGNAL_TIMEFRAMES`)
gewichtete Mittel der verfügbaren $\sigma_{tf}$ (fehlende Zeiträume werden übersprungen und die
Gewichte über die vorhandenen normiert):

$$
S = \frac{\sum_{tf \in \mathcal{T}} \omega_{tf}\,\sigma_{tf}}{\sum_{tf \in \mathcal{T}} \omega_{tf}},
\qquad (\omega_{5m}, \omega_{15m}, \omega_{1h}, \omega_{1d}) = (0{,}40,\ 0{,}30,\ 0{,}20,\ 0{,}10). \tag{17}
$$

**Designprinzipien.** (i) *Recency-Weighting* — kürzere/aktuellere Zeiträume tragen mehr, da sie für
einen Intraday-Horizont (Trade $\le 1$ Tag) am prädiktivsten sind. (ii) *Multi-Timeframe-Confluence*
(Elder, „Triple Screen") — längere Zeiträume liefern Kontext/Trend, kürzere den Auslöser; ein Signal
ist umso belastbarer, je mehr Zeiträume übereinstimmen. Die Gewichte sind heuristische, konfigurierbare
Defaults, keine aus einem einzelnen Paper übernommenen Konstanten.

---

## 5. Eintritts-Logik (Gate) *(→ `analyzer.analyze_ticker`)*

Ein Long-Signal wird nur erzeugt, wenn **alle** folgenden Bedingungen erfüllt sind (Indikatoren auf
dem $1\text{d}$-Zeitraum für das Gate, Stärke aus der Multi-TF-Aggregation):

$$
\underbrace{\bigl(\text{MACD-bullish} \ \vee\ \text{RSI} < 35\bigr)}_{\text{Auslöser}}
\ \wedge\ \underbrace{\text{RSI} < 65}_{\text{nicht überkauft}}
\ \wedge\ \underbrace{\text{Wochentrend} \neq \text{down}}_{\text{Trendfilter}}
\ \wedge\ \underbrace{S \ge S_{\min}}_{\text{Mindeststärke}}, \tag{18}
$$

mit $S_{\min} = \texttt{MIN\_SIGNAL\_STRENGTH} = 55$. Andernfalls wird kein Signal ausgegeben.
Gefundene Signale werden absteigend nach $S$ sortiert (Tie-Break: $|\text{RSI}-50|$).

---

## 6. Risikomanagement: Stop-Loss & Take-Profit *(→ `analyzer.sl_tp_from_atr`)*

SL/TP werden volatilitätsadaptiv als ATR-Vielfache vom Einstiegskurs $P$ gesetzt. Mit den
modusabhängigen Faktoren $(m_{sl}, m_{tp})$ aus `config.SL_TP_MODES`:

$$
\text{SL} = P - m_{sl}\cdot \text{ATR}, \qquad
\text{TP} = P + m_{tp}\cdot \text{ATR}, \tag{19}
$$

$$
\text{CRV} = \frac{m_{tp}}{m_{sl}} \quad (\text{Chance-Risiko-Verhältnis}). \tag{20}
$$

| Modus | $m_{sl}$ | $m_{tp}$ | CRV | Charakter |
|---|---|---|---|---|
| `aus` | – | – | – | keine festen Grenzen |
| `passiv` | 1,0 | 1,5 | 1,5 | enge Stops, kleine Ziele |
| `normal` | 1,5 | 2,5 | ≈1,67 | ausgewogen (Default) |
| `aggressiv` | 2,5 | 4,0 | 1,6 | weite Stops, große Ziele |

Im Modus **`aus`** (sowie bei fehlendem/ungültigem ATR) gilt $\text{SL}=\text{TP}=\varnothing$; die
Position wird dann **nur** durch Liquidation (Abschnitt 7) oder Signal-Verfall (Abschnitt 9)
geschlossen. Die prozentualen Abstände betragen $\text{sl\%} = (\text{SL}-P)/P\cdot 100 < 0$ und
$\text{tp\%} = (\text{TP}-P)/P\cdot 100 > 0$.

---

## 7. Hebel & Liquidation *(→ `evaluator.liquidation_price`)*

Bei Hebel $L > 1$ wird eine Long-Position liquidiert, sobald der Kursverlust die eingesetzte Margin
aufzehrt, d. h. bei einem relativen Verlust von $1/L$:

$$
\text{Liq}_{\text{long}} = P_{\text{entry}}\left(1 - \frac{1}{L}\right), \qquad
\text{Liq}_{\text{short}} = P_{\text{entry}}\left(1 + \frac{1}{L}\right). \tag{21}
$$

Für $L \le 1$ existiert keine Liquidation ($\varnothing$). **Konsequenz:** Je höher der Hebel, desto
näher liegt der Liquidationskurs am Einstieg — bei $L=10$ genügt bereits ein Rückgang von $10\,\%$,
bei $L=2$ erst $50\,\%$. Höherer Hebel vergrößert also Gewinn **und** Verlust und führt zu deutlich
**früherer** Liquidation.

---

## 8. P&L — Gewinn und Verlust *(→ `evaluator.realized_pnl`)*

Die reine Kursrendite (ungehebelt) beim Ausstieg zu $P_{\text{exit}}$:

$$
\text{pnl\%} =
\begin{cases}
\dfrac{P_{\text{exit}} - P_{\text{entry}}}{P_{\text{entry}}}\cdot 100 & \text{long},\\[2ex]
\dfrac{P_{\text{entry}} - P_{\text{exit}}}{P_{\text{entry}}}\cdot 100 & \text{short}.
\end{cases} \tag{22}
$$

Das in Euro realisierte Ergebnis skaliert mit dem Hebel und ist auf den **Totalverlust der Margin**
begrenzt (man kann nicht mehr als den Einsatz verlieren):

$$
\text{pnl}_{\text{€}} = \max\!\left(\; M \cdot \frac{\text{pnl\%}}{100}\cdot L,\ \ -M \;\right),
\qquad M = \text{Trade-Größe (€)}. \tag{23}
$$

Der Margin-Cap $-M$ modelliert vereinfachend die Liquidation auf P&L-Ebene; konsistent dazu wird beim
tatsächlichen Liquidations-Exit der Ausstiegskurs auf $\text{Liq}$ gesetzt (Abschnitt 9).

---

## 9. Schließlogik *(→ `bot.evaluate_active_trade`, `evaluator.evaluate_trades`)*

Ein aktiver Trade wird in **fester Prioritätsreihenfolge** geprüft (schlimmster Fall zuerst):

$$
\textbf{1. } P \le \text{Liq} \ \Rightarrow\ \text{Liquidation 💥}
\;\to\;
\textbf{2. } P \le \text{SL} \ \Rightarrow\ \text{Stop-Loss 🛑}
\;\to\;
\textbf{3. } P \ge \text{TP} \ \Rightarrow\ \text{Take-Profit 🎯}
\;\to\;
\textbf{4. } S < S_{\text{close}} \ \Rightarrow\ \text{Signal-Verfall 📉}, \tag{24}
$$

mit $S_{\text{close}} = \texttt{SIGNAL\_CLOSE\_THRESHOLD} = 35$. Greift keine Bedingung, bleibt die
Position offen. Es existieren zwei Auswertungspfade mit identischer Mathematik:

- **60-Sekunden-Monitor** (`bot.monitor_trades`): nutzt den Live-Kurs und die live neu berechnete
  Stärke $S$; schließt intraday bei Liquidation/SL/TP/Signal-Verfall.
- **Tages-Sweep** (`evaluator.evaluate_trades`, 22:15): nutzt Tages-Hoch/-Tief zur SL/TP-Prüfung und
  schließt alles noch Offene, sodass kein Trade länger als einen Tag läuft.

In beiden Fällen wird das Ergebnis über Gl. (22)–(23) mit dem **trade-individuellen** Hebel berechnet;
beim Liquidations-Exit ist $P_{\text{exit}} = \text{Liq}$.

---

## 10. Smart-Money-Score *(→ `smartmoney.compute_score`, `smartmoney.rank`)*

Aus öffentlich gemeldeter Aktivität großer/informierter Marktteilnehmer wird ein Score
$\text{SM} \in [0,100]$ als Summe dreier Komponenten gebildet.

**Insider-Komponente** $[0, 50]$, neutral $= 25$ (SEC Form 4, ~2 Tage Verzug). Aus gekauften/verkauften
Stückzahlen $B_s, S_s$ und der Zahl der Kauf-/Verkaufs-Transaktionen $B_n, S_n$ wird ein
Netto-Signal $\text{sig}_{\text{ins}} \in [-1, 1]$ gemittelt:

$$
\text{sig}_{\text{ins}} = \text{mean}\!\left(\frac{B_s - S_s}{B_s + S_s},\ \frac{B_n - S_n}{B_n + S_n}\right),
\qquad
C_{\text{ins}} = 25\,\bigl(1 + \text{sig}_{\text{ins}}\bigr). \tag{25}
$$

**Institutionen-Komponente** $[0, 40]$, neutral $= 20$ (SEC 13F, quartalsweise). Aus der mittleren
Bestandsänderung $\overline{\Delta}$ (auf $[-1,1]$ geklippt) und dem Verhältnis aufstockender ($a$) zu
reduzierenden ($r$) Haltern bei $m$ Haltern insgesamt:

$$
\text{sig}_{\text{inst}} = \text{mean}\!\left(\text{clip}_{[-1,1]}(\overline{\Delta}),\ \frac{a - r}{m}\right),
\qquad
C_{\text{inst}} = 20\,\bigl(1 + \text{sig}_{\text{inst}}\bigr). \tag{26}
$$

**Niveau-Komponente** $[0, 10]$ aus dem institutionellen Besitzanteil $\pi \in [0,1]$:
$C_{\text{lvl}} = 10\cdot\text{clip}_{[0,1]}(\pi)$. Der Gesamtscore und die Sterne-Darstellung:

$$
\text{SM} = \text{round}\!\Bigl(\text{clip}_{[0,100]}\bigl(C_{\text{ins}} + C_{\text{inst}} + C_{\text{lvl}}\bigr)\Bigr),
\qquad
\text{Sterne} = \text{clip}_{[1,5]}\!\left(\text{round}\!\tfrac{\text{SM}}{20}\right). \tag{27}
$$

**Re-Ranking** *(→ `smartmoney.rank`)*: Technische Stärke und Smart-Money-Score (beide auf der
$0$–$100$-Skala) werden linear kombiniert; danach wird absteigend sortiert:

$$
\text{combined} = w_{\text{tech}}\cdot S + w_{\text{smart}}\cdot \text{SM},
\qquad (w_{\text{tech}}, w_{\text{smart}}) = (1{,}0,\ 0{,}5). \tag{28}
$$

---

## 11. Parameter-Tabelle (Defaults aus `config.py`)

| Symbol / Konstante | Wert | Bedeutung |
|---|---|---|
| RSI-Periode | 14 | Glättung RSI |
| `RSI_OVERSOLD` / `RSI_OVERBOUGHT` | 35 / 65 | bullish- / überkauft-Schwelle |
| MACD-Spannen | 12 / 26 / 9 | schnell / langsam / Signal |
| `ATR_PERIOD` | 14 | ATR-Glättung |
| `SIGNAL_TIMEFRAMES` $\omega_{tf}$ | 0,40 / 0,30 / 0,20 / 0,10 | 5m / 15m / 1h / 1d |
| `STRENGTH_WEIGHTS` $w_c$ | 0,20 / 0,25 / 0,25 / 0,30 | rsi / macd / trend / **volume** |
| `MIN_SIGNAL_STRENGTH` $S_{\min}$ | 55 | Mindeststärke für ein Signal |
| `SIGNAL_CLOSE_THRESHOLD` $S_{\text{close}}$ | 35 | Auto-Close bei Signal-Verfall |
| `SL_TP_MODES` $(m_{sl}, m_{tp})$ | aus / (1,0;1,5) / (1,5;2,5) / (2,5;4,0) | aus / passiv / normal / aggressiv |
| `LEVERAGE_CHOICES` | 1; 1,5; 2; 3; 5; 10 | wählbarer Hebel $L$ |
| Smart-Money-Maxima | 50 / 40 / 10 | Insider / Institutionen / Niveau |
| `SMARTMONEY_W_TECH` / `_W_SMART` | 1,0 / 0,5 | Re-Ranking-Gewichte |
| `MONITOR_INTERVAL_SEC` | 60 | Überwachungstakt |

---

## 12. Limitationen & Annahmen

- **Demo-Modell:** keine echte Orderausführung; P&L vereinfacht (kein Spread, keine Slippage, keine
  Gebühren, keine Finanzierungskosten für den Hebel).
- **Datenverzug & Granularität:** yfinance liefert Intraday-Daten mit Minuten-Granularität und Verzug;
  das 60-Sekunden-Raster ist daher eine Näherung, kein Tick-Monitoring.
- **Liquidationsmodell:** vereinfacht über $1/L$ ohne Wartungsmarge, gestaffelte Margin oder
  Nachschuss; der Margin-Cap $-M$ ist eine konservative P&L-Begrenzung.
- **Smart-Money:** Positionierungs-/Sentiment-Indikator mit strukturellem Verzug (Form 4 ~2 Tage,
  13F quartalsweise) — kein Echtzeitsignal, keine Garantie.
- **Long-only & heuristische Gewichte:** Short wird nicht gehandelt; alle Gewichte sind konfigurierbare
  Defaults, nicht aus einem Optimierungs-/Backtest-Verfahren abgeleitet.

---

## 13. Referenzen

1. J. Welles Wilder Jr., *New Concepts in Technical Trading Systems*, 1978 — RSI & ATR (Wilder-Glättung).
2. Gerald Appel, *Technical Analysis: Power Tools for Active Investors*, 2005 — MACD.
3. Alexander Elder, *Trading for a Living*, 1993 — „Triple Screen" / Multi-Timeframe-Confluence.
4. N. Jegadeesh, S. Titman, *Returns to Buying Winners and Selling Losers*, Journal of Finance, 1993 —
   Momentum-Persistenz (Motivation des Recency-Weightings).
5. U.S. Securities and Exchange Commission — Form 4 (Insider) & Form 13F (institutionelle Halter).
