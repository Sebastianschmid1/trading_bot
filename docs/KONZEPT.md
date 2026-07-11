# Konzept: Stock-Signal-Trading-Bot

*Stand: 2026-07-11 — Zusammenfassung des aktuellen Bot-Konzepts.*

---

## 1. Idee in einem Satz

Ein **multi-user Trading-Signal-Bot**, der auf Basis technischer Analyse (Multi-Timeframe)
täglich Aktien-/ETF-/Krypto-/Rohstoff-Signale erzeugt, sie per **Telegram** und **Web-App**
zur Annahme anbietet, die Trades trackt und auswertet — im **Demo-Modus** oder optional gegen
ein echtes **Alpaca**-Konto. Dazu kommt ein **Backtest-/Experiment-Rahmen** und ein
selbst-lernendes **Strategie-Labor**.

Kernprinzip durchgängig: **Long-only im Live-Betrieb**, **Menschen-Gate** vor jeder echten
Aktion, Secrets nur in `.env`.

---

## 2. Zwei Bedienkanäle, eine Engine

Telegram-Bot und Web-App laufen **parallel** auf denselben Account/dieselbe DB und rufen
**dieselbe Service-Schicht** (`stockbot/services/*`) auf — eine Aktion wirkt sofort in beiden Kanälen.

- **Telegram** (`stockbot/tgbot/bot.py`): tägliche Signale mit JA/NEIN-Buttons, Tagesauswertung,
  Befehle (`/signals`, `/settings`, `/strategies`, `/teststrat`, `/top5trade`, `/dashboard`, `/profil`, `/help`).
- **Web-App** (`stockbot/web/webapp.py`): `/app` (Signale, aktive Trades verkaufen, Hebel),
  `/app/settings`, `/app/watchlist`, `/app/notifications` (Live-Feed via SSE), `/app/dashboard`,
  `/app/lab` (Admin-Ansicht des Strategie-Labors). Login per „Login mit Telegram" oder privatem Token.
- **Dashboard**: Equity-Kurve, Trefferquote, P&L/Ticker, aktive Trades, Profitfaktor je Strategie
  (Strategie-Tabs). Läuft standardmäßig im Bot-Prozess mit (Port 8000).

Multi-User: jeder registriert sich per geführtem Onboarding selbst (Trade-Größe, optional Broker verbinden).

---

## 3. Signal-Engine (technische Analyse)

`stockbot/market/analyzer.py` — **Multi-Timeframe-Confluence** mit Recency-Weighting
(kürzere/aktuellere Zeiträume zählen mehr, da Trades max. ~1 Tag laufen):

| Timeframe | Periode | Gewicht |
|-----------|---------|---------|
| 5m  | 5d  | 0.40 |
| 15m | 5d  | 0.30 |
| 1h  | 1mo | 0.20 |
| 1d  | 1y  | 0.10 |

**Signalstärke (0–100)** je Zeitraum aus gewichteten Komponenten:
RSI 0.20 · MACD 0.25 · Trend (MA50/MA200) 0.25 · Volumen (RVOL) 0.30.

- **Gates:** Mindeststärke 55 für ein gültiges Signal; unter 35 wird ein aktiver Trade
  automatisch geschlossen. Long-Signale im klaren Wochen-Abwärtstrend werden unterdrückt.
- **Smart-Money** (`market/smartmoney.py`): nächtlicher Scan (23:00) von Insider-Käufen (Form 4)
  + Institutionen (13F); fließt ins Re-Ranking der Signale ein (Tech-Gewicht 1.0, Smart-Money 0.5).
- **LLM-Ranking** (optional, `ai/llm_ranker.py`): Claude Haiku rankt Signale anhand aller
  Metadaten + Fundamentaldaten. Standardmäßig **AUS** (Kosten), per `LLM_RANK_SIGNALS=true` aktivierbar.

**Datenquelle:** yfinance (kostenlos). Datenklassen mit eigenen Analyse-Profilen:
Aktien (S&P 500 / MSCI World / Emerging Markets), ETFs, Krypto (Demo/Tracking), Rohstoffe (über ETFs).
Voll-Universum (~500 Werte) standardmäßig an, alternativ kuratierter Korb.

---

## 4. Trade-Lebenszyklus & Risiko

- **Zeitplan (Berlin):** 15:35 Signale senden (nach US-Open), 30-Min-Intraday-Scan nach neuen
  Signalen, 60-Sek-Überwachung aktiver Trades, 22:15 Auswertung/Schließung.
- **SL/TP:** ATR-basiert, pro Nutzer wählbar — `aus` / `passiv` (1.0/1.5) / `normal` (1.5/2.5) /
  `aggressiv` (2.5/4.0) × ATR. Optional dynamischer ATR-Trailing-Stop (Backtest-Engine).
- **Tagesende-Schließung** pro Nutzer schaltbar: alles um 22:15 schließen **oder** über Nacht
  halten (nur SL/TP/Liquidation), max. Haltedauer 14 Tage. Backtests zeigten: EOD-Schließen
  kostet Trendfolge-Strategien die Kante.
- **Hebel** 1×–10× wählbar; Liquidation bei −1/Hebel. Bei Hebel > 1 optional über **Long-Calls**
  (Optionen mit ~passendem Omega, 30–45 DTE) statt gehebelter Aktien.
- **Sizing:** knapp über Budget → 1 ganze Aktie statt Bruchteil (bis 1.5× Budget), damit auch
  außerhalb der regulären Session als Limit handelbar (`broker/sizing.py`).

**Echt-Handel (Alpaca):** pro Nutzer schaltbar (`broker_exec`), Default **AUS** und **PAPER**.
API-Keys verschlüsselt in der DB (`ENCRYPTION_KEY`/Fernet). Täglicher Broker-Vollabgleich
(12:00) repariert Drift zwischen Bot-DB und offenen Positionen (`broker/reconcile.py`).

---

## 5. Strategien

Registry `market/strategies.py` — eine „Strategie" = Bündel aller stellbaren Parameter.
Nutzer können **mehrere gleichzeitig** aktivieren (Dedup: eine Position pro Aktie/Tag), jede
bekommt eigene Signale + eigene Dashboard-Kennzahlen.

16 Strategien + Benchmark: `standard`, `adx_trend`, `rsi_revert`, `breakout`, `ma_trend`,
`high52`, `high52_wide`, `tsmom`, `lowvol`, `faber`, `streversal`, `frog`, `bb_revert`,
`adx_mfi`, `supertrend`, `ai_adaptive` (+ `buyhold_sp500` als Vergleich).

---

## 6. Backtest- & Experiment-Rahmen

`stockbot/backtest/engine.py` — **kein Look-ahead** (tf_data nur bis Stichtag), realistische
**Transaktionskosten** (`BACKTEST_COST_PCT`, Default 0.05 %/Seite), parallelisiert über Prozesse.

- **Kennzahlen** (`core/metrics.py`): Profitfaktor (Zielgröße), Trefferquote, Drawdown, Sharpe,
  Erwartungswert, MAR (CAGR/maxDD).
- **Modi:** lang (Tages-TF, viele Jahre) und kurz (Multi-TF, ~60 Tage wegen yfinance-Intraday-Limit).
- **Vergleich/Reports:** Sweep-Reports über alle Strategien (`backtest/report.py`), Equity-Charts.
- Bekannte Fallstricke bewusst adressiert: Look-ahead, Overfitting (Out-of-Sample), Survivorship-Bias
  (als Caveat ausgewiesen), Kosten/Slippage.

---

## 7. Strategie-Labor (selbst-lernend)

`stockbot/optimize/lab.py` — tunt **nur** die Parameter der KI-Strategie `ai_adaptive`
(Saat: SuperTrend, Zielfunktion **MAR = CAGR/maxDD**) walk-forward gegen die Backtest-Engine.

- **Mehrstufiges Gate** vor jedem Vorschlag: OOS-Aggregat-MAR + Fold-Mehrheit (3 OOS-Folds) +
  Block-Bootstrap ≥ 70 % + Drawdown-Schranken + Embargo 56 Tage (Purging) + Bestätigungs-Serie
  (`LAB_STREAK=2` Gate-Siege in Folge).
- **Proposer lernen** aus `hypotheses.jsonl`: Grid-, History- (Multi-Step/Parameter-Paare) und
  LLM-Proposer (`LAB_PROPOSER=llm`, hart validiert: nur Raster-Nachbarn, genau eine Variable).
- **Rollback-Guard** (jüngste archivierte Version läuft mit) + **Reality-Check** je Zyklus
  (Live-Trades vs. OOS-Erwartung, Telegram-Alarm bei Divergenz).
- **Menschen-Gate unantastbar:** Optimizer schreibt **nie** Live-Parameter, nur `pending.json`;
  Übernahme ausschließlich per `lab.apply_pending()` (CLI `--apply`/`--reject`) bzw. `/app/lab`.
- **Cron:** täglich Mo–Fr 16:00 Berlin (`LAB_DAILY_*`), erzeugt nur Vorschläge.
- Laufzeit-Artefakte unter `data/lab/` (gitignored), Fires-Cache für ~3× schnellere Folgezyklen.
- **Verifiziert per Lern-Experiment:** auf ungesehenen Daten OOS-MAR 47.0 → 59.5 nach gelernter
  Übernahme; auf Rauschdaten promotet das neue Gate 0/6 Zyklen (altes Punkt-Gate hätte 6/6).

---

## 8. Architektur & Betrieb

```
stockbot/
  tgbot/      Telegram-Bot (bot.py, onboarding.py)
  web/        FastAPI Web-App + Dashboard + Auth
  services/   gemeinsame Service-Schicht (trades, settings, watchlist, notifications)
  market/     analyzer, strategies, smartmoney, universes, asset_classes, lookup
  core/       db (SQLite), evaluator, metrics, trade_lifecycle
  broker/     Alpaca-Client, sizing, reconcile, setup
  backtest/   engine, report
  optimize/   lab (Strategie-Labor)
  ai/         llm_ranker
  config.py   zentrale Parameter (Default-Strategie)
```

- **Speicher:** SQLite (`core/db.py`), Migrationen inline; Broker-Keys Fernet-verschlüsselt.
- **Deploy:** Ubuntu-VPS via systemd (`deploy/*.service`), TLS-Reverse-Proxy (Caddy),
  Update per `upload.ps1` / `deploy/deploy.sh` (git pull + deps + restart).
- **Sicherheit:** Session-Cookies (httponly/secure), CSP/HSTS, CSRF (Origin-Abgleich),
  Login-Rate-Limit, Session-Cleanup, Session-Hashing, Dashboard-Token-Rotation, XSS-Escaping.
- **Tests:** Offline-Suite mit pytest (kein Netz/Telegram) vom Repo-Root.

---

## 9. Leitplanken (dauerhaft)

- Live-Bot bleibt **long-only**; Shorts nur im Backtest.
- **Secrets** nur in `.env` — niemals committen/loggen.
- **Menschen-Gate** der KI-Strategie nie umgehen: Optimizer schreibt nur Pending-Vorschläge.
- Demo-Modus ist Standard; echtes Geld erfordert explizite Freigabe pro Nutzer.

---

## 10. Offene Punkte (Sicherheits-Audit, siehe `todo.md`)

- **A1:** systemd-Dienste nicht mehr als root (dedizierter Nutzer, Härtung `NoNewPrivileges` etc.).
- **A2:** Dependencies pinnen (`requirements.txt` ungepinnt); dabei yfinance-FD-Leck-Fix wählen
  (aktueller Workaround: `LimitNOFILE=65535`).
```
