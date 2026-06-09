# Strategie-Paper: ADX-Trendfolge mit Volatilitäts-Expansion

*Portierung & mathematische Beschreibung der zweiten Bot-Strategie (`strategies.adx_trend_signal`)*

> **Disclaimer.** Demo-/Lehrsystem, **keine Anlageberatung**. Long-only, simuliert. Alle Formeln
> sind aus dem Quellcode (`strategies.py`) abgeleitet. Vgl. Gesamt-Mathematik in
> [MATHEMATIK.md](MATHEMATIK.md), Vorhaben/Status in [STRATEGIE_ROADMAP.md](STRATEGIE_ROADMAP.md).

---

## 1. Herkunft & Idee

Die Strategie ist eine nach Python **portierte** Variante der trader-dev-Strategie
„F40d C104 — ADX 14" (Pine, ursprünglich auf ETHUSDT 1h, Profitfaktor ≈ 2,1). Übernommen wurde die
**Signal-Logik**; die krypto-/hebel-spezifischen Teile (Streak-Growth/Decay-Sizing, Leverage-Cash)
wurden **bewusst entfernt** — Positionsgröße und Hebel regelt der Bot selbst.

Die Idee: in einen **bestätigten Aufwärtstrend** einsteigen, **wenn die Volatilität gerade
aufflammt** (Beginn einer Bewegung), nicht in der Ruhephase. Drei Bausteine:

1. **Regime-Filter** — nur long, wenn der Kurs über dem EMA200 liegt.
2. **Trendstärke** — ADX(14) muss über einer Schwelle liegen (kein Seitwärtsmarkt).
3. **Volatilitäts-Expansion** — eine Hüllkurven-Amplitude steigt sprunghaft (Z-Score-Crossover),
   bestätigt durch positive Kurs-Velocity.

---

## 2. Indikatoren

Notation: Pₜ = Schlusskurs, Hₜ = Hoch, Lₜ = Tief zur Bar t. Wilder-Glättung (RMA) eines Reihenwerts
xₜ mit Periode n:

$$
\tilde{x}_t = \frac{(n-1)\,\tilde{x}_{t-1} + x_t}{n}
\qquad\bigl(\text{entspricht EWMA mit } \alpha = 1/n\bigr). \qquad (1)
$$

### 2.1 Regime (EMA200)

$$
\mathrm{EMA}_t(s) = \alpha\,P_t + (1-\alpha)\,\mathrm{EMA}_{t-1}(s),\quad \alpha=\tfrac{2}{s+1},\quad s=200;
\qquad \text{bullish} \iff P_t > \mathrm{EMA}_t(200). \qquad (2)
$$

### 2.2 ADX(14) — Trendstärke

True Range und gerichtete Bewegung:

$$
\mathrm{TR}_t = \max\bigl(H_t-L_t,\; |H_t-P_{t-1}|,\; |L_t-P_{t-1}|\bigr), \qquad (3)
$$

$$
\mathrm{+DM}_t = \begin{cases} H_t-H_{t-1} & \text{falls } H_t-H_{t-1} > L_{t-1}-L_t \text{ und } > 0\\ 0 & \text{sonst}\end{cases},
\quad
\mathrm{-DM}_t = \begin{cases} L_{t-1}-L_t & \text{falls } L_{t-1}-L_t > H_t-H_{t-1} \text{ und } > 0\\ 0 & \text{sonst}\end{cases}. \qquad (4)
$$

Mit Wilder-Glättung (1), Periode 14, ergeben sich gerichtete Indizes und der ADX:

$$
\mathrm{+DI} = 100\,\frac{\widetilde{\mathrm{+DM}}}{\widetilde{\mathrm{TR}}}, \quad
\mathrm{-DI} = 100\,\frac{\widetilde{\mathrm{-DM}}}{\widetilde{\mathrm{TR}}}, \quad
\mathrm{DX} = 100\,\frac{|\mathrm{+DI}-\mathrm{-DI}|}{\mathrm{+DI}+\mathrm{-DI}}, \quad
\mathrm{ADX} = \widetilde{\mathrm{DX}}. \qquad (5)
$$

**Nicht seitwärts** ⇔ ADX ≥ `adx_thresh` (Default 10).

### 2.3 Volatilitäts-Hüllkurve (Quadratur-Filter)

Detrending mit gleitendem Mittel (Länge 50) und ein Quadratur-Filter (90°-Phasenschätzung) liefern
eine momentane **Amplitude** envₜ:

$$
\mathrm{osc}_t = P_t - \mathrm{SMA}_{50}(P)_t, \qquad (6)
$$

$$
\mathrm{quad}_t = 0{,}0962\,\mathrm{osc}_t + 0{,}5769\,\mathrm{osc}_{t-2} - 0{,}5769\,\mathrm{osc}_{t-4} - 0{,}0962\,\mathrm{osc}_{t-6}, \qquad (7)
$$

$$
\mathrm{env}_t = \mathrm{EMA}_{10}\!\left(\sqrt{\mathrm{osc}_t^{2} + \mathrm{quad}_t^{2}}\right). \qquad (8)
$$

Die **Steigung** der Hüllkurve über 10 Bars wird über ein Fenster von `env_base` = 150 Bars
z-standardisiert:

$$
\Delta\mathrm{env}_t = \mathrm{env}_t - \mathrm{env}_{t-10}, \qquad
Z^{\mathrm{exp}}_t = \frac{\Delta\mathrm{env}_t - \mu_{150}(\Delta\mathrm{env})}{\sigma_{150}(\Delta\mathrm{env})}. \qquad (9)
$$

### 2.4 Kurs-Velocity

$$
v_t = P_t - P_{t-5}, \qquad
Z^{\mathrm{vel}}_t = \frac{v_t - \mu_{150}(v)}{\sigma_{150}(v)}. \qquad (10)
$$

---

## 3. Einstiegsregel (long)

Eingestiegen wird, wenn die Volatilität **gerade** über die Schwelle expandiert (Crossover),
die Bewegung nach oben zeigt und der Trendkontext stimmt:

$$
\text{long} \iff
\underbrace{\bigl(Z^{\mathrm{exp}}_{t-1} \le \theta < Z^{\mathrm{exp}}_t\bigr)}_{\text{Expansions-Crossover},\ \theta=1{,}0}
\;\wedge\; v_t > 0
\;\wedge\; |Z^{\mathrm{vel}}_t| > 0{,}5
\;\wedge\; P_t > \mathrm{EMA}_t(200)
\;\wedge\; \mathrm{ADX}_t \ge 10. \qquad (11)
$$

**Stärke 0–100** (für Ranking/Top-N) aus Trendstärke und Velocity, mit
clip(x) = min(max(x,0),1):

$$
S = 100\cdot\bigl(0{,}6\cdot\mathrm{clip}(\tfrac{\mathrm{ADX}-10}{30}) + 0{,}4\cdot\mathrm{clip}(\tfrac{|Z^{\mathrm{vel}}|}{2})\bigr). \qquad (12)
$$

---

## 4. Ausstieg: ATR-adaptive SL/TP (angepasst beim Port)

Das Original nutzte **fixe** 2 % Stop / 5 % Ziel. Auf US-**Tages**aktien ist ein 2%-Stop zu eng
(Backtest: Profitfaktor 0,57 — verlustreich). Daher in der Portierung **volatilitätsadaptiv** über
den ATR(14) (Wilder, siehe MATHEMATIK.md Gl. 7–8) gesetzt:

$$
\mathrm{SL} = P - 2{,}5\,\mathrm{ATR}, \qquad
\mathrm{TP} = P + 4{,}0\,\mathrm{ATR}, \qquad
\mathrm{CRV} = \frac{4{,}0}{2{,}5} = 1{,}6. \qquad (13)
$$

Ohne gültigen ATR entsteht **kein** Signal (kein blinder Fix-Stop). Anders als bei der
Standard-Strategie wird der nutzerseitige SL/TP-Modus hier **nicht** angewandt — die SL/TP gehören
zur Strategie (`bot._personalize_signal`).

---

## 5. Backtest-Ergebnis (Re-Validierung auf eigenen Daten)

5 Ticker (AAPL, MSFT, NVDA, JPM, XOM), 2 Jahre, Tages-TF, long-only, ohne Gebühren/Slippage:

| Variante | Trades | Winrate | **Profitfaktor** | Max DD |
|---|---|---|---|---|
| Original (fix 2%/5%) | 27 | 18,5 % | 0,57 (verliert) | 3,0 % |
| **Port mit ATR-Exits** | 25 | 44,0 % | **1,21** | 1,5 % |
| Vergleich: Standard (daily-only) | 98 | 41,8 % | 1,16 | 3,6 % |

**Kernaussage:** Der Wechsel von fixem Prozent-Stop auf ATR-Exits dreht die Strategie von Verlust
auf einen **Profitfaktor von 1,21** (über dem Standard) — bei nur 1,5 % Drawdown. Die Trendfolge-
Logik trägt; der zu enge Stop war das Problem. **trader-dev-Kennzahlen (PF 2,1 auf ETH-1h)
übertragen sich nicht** auf Tagesaktien — Re-Validierung ist Pflicht.

---

## 6. Parameter (`strategies.ADX_PARAMS`, frei anpassbar)

| Parameter | Wert | Bedeutung |
| --- | --- | --- |
| `adx_len` | 14 | ADX-Periode (Wilder) |
| `adx_thresh` | 10 | darunter = seitwärts → kein Einstieg |
| `atr_sl_mult` / `atr_tp_mult` | 2,5 / 4,0 | SL/TP als ATR-Vielfache (CRV 1,6) |
| `detrend` | 50 | SMA-Länge fürs Detrending der Hüllkurve |
| `env_base` | 150 | Fenster der Z-Scores (Expansion & Velocity) |
| `exp_z` (θ) | 1,0 | Expansions-Z-Schwelle (Crossover) |
| `min_vel_z` | 0,5 | Mindest-Betrag der Velocity-Z |
| `ema_regime` | 200 | Regime-Filter (Kurs > EMA200 = bullish) |

---

## 7. Limitationen

- **Tages-TF & kleine Stichprobe:** Ergebnis aus 5 Tickern/2 Jahren — keine Garantie; vor breitem
  Einsatz auf mehr Titeln und out-of-sample prüfen (Roadmap Phase 3).
- **Kein Walk-Forward:** Parameter sind Defaults aus der Vorlage; nicht auf Tagesaktien optimiert
  (bewusst, gegen Overfitting). Optimierung später mit Out-of-Sample-Beleg.
- **Demo:** keine Gebühren/Slippage/Finanzierungskosten; long-only.
- **Monitor-Hinweis:** der 60s-Live-Monitor bewertet „Signal-Verfall" weiterhin über die generische
  Multi-Timeframe-Stärke (Momentum-Guard), nicht über die ADX-Logik (v1-Vereinfachung).

---

## 8. Referenzen

1. J. Welles Wilder Jr., *New Concepts in Technical Trading Systems*, 1978 — ADX/DMI & ATR.
2. trader-dev, öffentliche Strategie „F40d C104 — ADX 14" (Pine, Vorlage der Portierung).
3. Hilbert-Transform / Quadratur-Filter zur Amplituden-/Zyklusschätzung (Ehlers, *Cybernetic Analysis
   for Stocks and Futures*, 2004) — Motivation für Gl. (7)–(8).
