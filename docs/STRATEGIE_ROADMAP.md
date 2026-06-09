# Strategie-Roadmap: Backtesten, Verbessern & Vergleichen

Ziel (deine Worte): **Strategien stetig verbessern, testen und mit anderen Strategien vergleichen.**
Dieses Dokument trennt die brauchbaren Ideen aus dem „Hermes/Trader-Dev"-Video vom Marketing und
beschreibt einen konkreten, stufenweisen Ausbau **deines bestehenden Python-Bots** (yfinance/SQLite).

---

## 1. Was aus dem Video wirklich brauchbar ist (und was Hype)

**Brauchbar — das fehlt deinem Bot heute:**
1. **Backtesting** — eine Strategie auf *historischen* Daten messen (Trefferquote, Profitfaktor,
   Drawdown, Sharpe), **bevor** sie live läuft. Dein Bot handelt aktuell nur *vorwärts* (Demo).
2. **Strategie-Vergleich / Experimente** — mehrere Varianten (andere Gewichte, Schwellen, SL/TP,
   Hebel) auf demselben Zeitraum gegeneinander laufen lassen und die Kennzahlen vergleichen.
3. **Parameter-Optimierung** — Schwellen/Gewichte systematisch durchprobieren (mit Overfitting-Schutz).
4. **Forward-Test-Journal** — Live-/Demo-Trades samt Ausgang protokollieren und auswerten
   (deine `trades`-Tabelle kann das schon, es fehlt nur die Auswertung).
5. **„AI-Brain" = Feature-Attribution** — datenbasiert messen, *welche* Indikatoren(-Kombis) mit
   Gewinn-Trades korrelieren (z. B. „Volumen-Bestätigung + bullishes MACD" gewinnt öfter).
6. **Geplante Selbst-Verbesserungs-Schleife** — periodisch neu backtesten und Parameter-Vorschläge
   machen (dein `job_queue` kann das schon scheduln).

**Hype / bewusst NICHT übernehmen:**
- Den ganzen „Hermes autonomous agent" + „Telegram trading floor mit Offices/Topics" — reine
  Orchestrierungs-Show. Du hast bereits einen funktionierenden Bot; ihn auf Hermes neu zu bauen bringt nichts.
- „Self-improving AI, die nie vergisst" — echte Verbesserung kommt aus **sauberem Backtest +
  Out-of-Sample-Validierung**, nicht aus dem Gedächtnis eines Agenten.
- „$10k Challenge / trade like a boss / du wirst gebannt wenn du Anthropic direkt nutzt" — Werbung/irrelevant.

---

## 2. Einordnung: `trader-dev` MCP

`claude mcp add --transport sse --scope user trader-dev https://mcp.trader.dev/sse`

- Das fügt **Claude Code** (nicht deinem Bot) einen externen MCP-Server hinzu, der **Pinescript**-
  Strategien auf **TradingView** backtestet. Es ist ein *paralleles Ökosystem* (Pinescript/TradingView),
  nicht dein Python/yfinance-Stack.
- **Nutzen:** gut als *Ideenquelle* und um Community-Strategien in Pinescript schnell zu prüfen.
- **Grenzen/Hinweis:** Es testet **nicht** deine Python-Signal-Logik (`analyzer.py`). Außerdem ist es ein
  **Drittanbieter-Dienst über SSE** — bei Nutzung gehen deine Anfragen/Strategiedaten an `mcp.trader.dev`.
  Das Hinzufügen ist reversibel (`claude mcp remove trader-dev`). Den Befehl führst **du** im Terminal aus
  (oder ich auf deine ausdrückliche Bestätigung) — ich aktiviere keine externe Datenanbindung ungefragt.

**Empfehlung:** Priorität auf den **nativen Python-Backtest-Rahmen** (unten) — er testet *deine* Strategie
direkt, ist reproduzierbar, kostenlos, offline und gibt keine Daten weg. `trader-dev` optional als Ergänzung.

---

## 3. Empfohlener Weg: nativer Backtest-/Experiment-Rahmen

Wiederverwendung deiner bestehenden Bausteine — **keine Doppel-Implementierung der Mathematik:**
- `analyzer.analyze_ticker(ticker, tf_data)`, `compute_strength`, `sl_tp_from_atr`, `calc_*`
- `evaluator.realized_pnl`, `evaluator.liquidation_price`
- `bot.evaluate_active_trade` (Schließlogik: Liquidation → SL → TP → Signal-Verfall, „aus"-Modus)
- `config.py` als Quelle der Default-Parameter; `db.get_closed_trades` für das Live-Journal.

### Kernbegriff: `StrategyConfig`
Ein „Strategie" = ein Bündel aller stellbaren Parameter (Default = aktuelle `config.py`-Werte):
`STRENGTH_WEIGHTS`, `SIGNAL_TIMEFRAMES`, `MIN_SIGNAL_STRENGTH`, `SIGNAL_CLOSE_THRESHOLD`,
`RSI_OVERSOLD/OVERBOUGHT`, `SL_TP_MODES`-Wahl, `leverage`, `top_n`, Universum.
→ als `dataclass` mit `from_config()`; `analyzer`/`evaluator` lesen die Werte aus dem übergebenen
Config-Objekt statt aus globalen Konstanten (kleine, gezielte Parametrisierung).

### Phasen

**Phase 0 — Kennzahlen (`metrics.py`)**
Aus einer Liste geschlossener Trades: Gesamt-Rendite, Trefferquote, Profitfaktor, Ø-Gewinn/Verlust,
max. Drawdown, Sharpe (vereinfacht), Anzahl Trades, Ø-Haltedauer; pro SL/TP-Modus & Hebel.
Wird von Backtest **und** Live-Journal genutzt.

**Phase 1 — Backtest-Engine (`backtest.py`)**
Eingabe: Universum, Zeitraum, `StrategyConfig`. Ablauf je „Stichtag" im Fenster:
1. tf_data **nur bis zum Stichtag** rekonstruieren (kein Look-ahead!),
2. `analyze_ticker` → Gate → Top-N Signale,
3. Trade simulieren: Einstieg (nächster Kurs), vorwärts laufen, `evaluate_active_trade`/SL/TP/Liquidation
   anwenden, mit `realized_pnl` schließen (inkl. **Kosten/Slippage-Annahme**),
4. Trade-Log + Kennzahlen (Phase 0) zurückgeben.
Start bewusst **Tages-Timeframe** über 1–2 Jahre auf dem kuratierten Korb (klein & schnell).

**Phase 2 — Experiment-Runner & Vergleich (`experiments.py`)**
Mehrere `StrategyConfig`s bzw. ein Parameter-Grid über **denselben** Zeitraum laufen lassen →
Vergleichstabelle (sortiert nach z. B. Profitfaktor/Sharpe). Ergebnisse als JSON/DB speichern.
Telegram: `/backtest <zeitraum>` und `/compare` (Tabelle der besten Varianten).

**Phase 3 — Walk-Forward / Out-of-Sample (Overfitting-Schutz)**
Daten in Train/Test splitten: auf Train optimieren, auf Test validieren; In- vs. Out-of-Sample-Lücke
ausweisen. **Regel:** Parameter werden nur übernommen, wenn sie *out-of-sample* halten.

**Phase 4 — „Insight-Brain" (Feature-Attribution)**
Pro (Backtest-)Trade den Feature-Snapshot loggen (RSI, MACD-Status, Volumen-Ratio, Wochentrend,
Smart-Money, Stärke) + Ausgang. Auswerten, welche Features/Kombis die Trefferquote heben (erst simple
Statistik/Korrelation, kein ML nötig). Das ist die seriöse Variante des Video-„AI-Brain".

**Phase 5 — Dashboard-Integration**
Im bestehenden FastAPI-Dashboard: Backtest-Equity-Kurven, Strategie-Vergleichstabelle,
Feature-Attribution. (Wiederverwendung der Chart.js-Bausteine.)

**Phase 6 (optional) — `trader-dev` MCP**
Als externe Ideenquelle / Pinescript-Gegencheck. Der native Python-Engine bleibt die „Source of Truth"
für deinen Bot.

---

## 4. Wichtige Fallstricke (unbedingt beachten)

- **yfinance-Intraday-Limit:** 5m/15m-Daten gibt es nur für die **letzten ~60 Tage**, 1h ~730 Tage,
  1d viele Jahre. → **Voller Multi-Timeframe-Backtest nur über ~60 Tage** möglich; für lange Historie
  Tages-Timeframe nutzen. (Zwei Backtest-Modi: „lang/Tages-TF" und „kurz/Multi-TF".)
- **Look-ahead-Bias:** niemals Bars nach dem Stichtag verwenden — der häufigste Backtest-Fehler.
- **Overfitting:** ohne Out-of-Sample-Test findet man immer „tolle" Parameter, die live versagen.
- **Survivorship-Bias:** das Universum ändert sich über die Zeit (heutige S&P-500-Liste ≠ damalige).
- **Kosten/Slippage:** aktuell ignoriert; im Backtest realistische Annahme ansetzen, sonst zu optimistisch.
- **Selbst-Verbesserung verantwortungsvoll:** der geplante Job **schlägt Parameter vor** (mit
  Out-of-Sample-Beleg) — er ändert nichts automatisch ohne deine Freigabe.

---

## STATUS (2026-06-09)

**Entscheidungen festgezurrt:**

- Erster Backtest: **kuratierter Korb + 2 Jahre Tages-TF**.
- Vergleichs-Zielgröße: **Profitfaktor**.
- Vorgehen: **trader-dev zuerst verbinden**, dann Framework bauen.
- Erste Version: **mehrere Strategien in /settings wählbar** — die **jetzige** + **eine aus trader-dev**
  (hoher Profitfaktor). Strategien per **Namen** über den Bot hinzufügbar (`/addstrat <name>`,
  `/strategies` listet). Neuer Befehl **`/teststrat`** = Kennzahlen (Profitfaktor-Fokus) der aktuellen Strategie.

**trader-dev MCP:** hinzugefügt (User-Scope, `~/.claude.json`), SSE „Connected".
⚠️ Tools werden erst nach **Neustart der Claude-Code-Session** geladen; danach ggf. **`/mcp` → Login**
bei trader.dev (kostenloser Account) nötig, bevor Backtest-Tools funktionieren.

### Fortschritt (2026-06-09, später)

**Foundation gebaut + getestet** (9 Tests grün, `test_backtest.py`):
- `metrics.py` — Kennzahlen (Profitfaktor-Fokus, Drawdown, Erwartungswert).
- `strategies.py` — Registry + `Strategy`-Abstraktion; „standard" (analyzer.analyze_ticker) +
  „adx_trend" (Port aus trader-dev „F40d C104 — ADX 14"; Krypto-Sizing entfernt; fix SL 2% / TP 5%).
- `backtest.py` — Tages-Backtest ohne Look-ahead (`run_backtest`, `compare_strategies`).

**Erstes echtes Ergebnis** (5 Ticker AAPL/MSFT/NVDA/JPM/XOM, 2 J. daily):
- Standard (daily-only): PF **1,16**, 98 Trades, Winrate 41,8%, DD 3,6%.
- ADX-Port: PF **0,57** (verliert!), 27 Trades, Winrate 18,5%. → trader-dev-PF (2,1 auf ETH-1h)
  überträgt sich NICHT; fixer 2%-Stop zu eng für Tagesaktien. **Lektion bestätigt.**

**ADX auf ATR umgestellt (Entscheidung des Nutzers):** SL 2,5×ATR / TP 4,0×ATR statt fix 2%/5%.
Re-Test (5 Ticker, 2 J.): PF **1,21** (Winrate 44%, DD 1,5%) — schlägt jetzt den Standard (PF 1,16).
Die Trendfolge-Logik trägt; der fixe Stop war das Problem.

**Live-Wiring ERLEDIGT (76 Tests grün):**
- `db.py`: Spalte `strategy` (Migration + `set_strategy` + im User-Dict).
- `analyzer.analyze_universe(tickers, generate=…)` — strategie-parametrisiert.
- `bot.py`: `_user_strategy`, Cache-/Analyse-Key um Strategie erweitert; `send_daily_signals` &
  `cmd_signals` nutzen die gewählte Strategie; `_personalize_signal` respektiert strategie-eigene
  SL/TP (ADX behält ATR-Exits, SL/TP-Modus gilt nur für „standard").
- `/settings`: Strategie-Auswahl-Reihe (`set_strat`). `/strategies` (Liste), `/addstrat <name>`
  (per Namen wählen), `/teststrat` (Backtest der aktiven Strategie als Hintergrund-Job → Profitfaktor).
- Anzeige in `/profil` + `/help`.

**Mehrere Strategien gleichzeitig + Strategie-Dashboards ERLEDIGT (77 Tests grün):**
- `db.py`: `strategy`-Spalte speichert kommagetrennte Liste; `_user_to_dict["strategies"]`;
  `toggle_strategy` (mind. 1 bleibt). Keine Migration.
- `bot.py`: `_user_strategies`; `/signals` & 15:35-Job senden **pro Strategie** top_n Signale mit
  Kopfzeile (Dedup: eine Position pro Aktie via `has_trade_today`); `refill_pending` füllt pro
  Strategie auf; `/settings` Strategie-**Mehrfach-Toggle**; `/addstrat` togglet; `/teststrat [name]`
  testet je gewählter Strategie; `/strategies`, `/profil` zeigen die Liste.
- `dashboard.py` + `dashboard.html`: `build_dashboard_data(user, strategy=None)` filtert nach
  `signal.strategy`, liefert **Profitfaktor** (via `metrics`) + Strategie-Tabs („Alle" + je Strategie);
  HTML-Umschalter lädt `/api/<token>/data?strategy=…` neu.

**Nächste mögliche Schritte (offen):** Walk-Forward/Out-of-Sample (Phase 3), Feature-Attribution
(Phase 4), Backtests im Dashboard visualisieren; Backtest-Engine für volles Universum beschleunigen
(Signal-Serien vorberechnen statt per-Tag neu).

---

## 6. Nächste Schritte / Entscheidung

Vorschlag zur Reihenfolge: **Phase 0 + 1** zuerst (Kennzahlen + Tages-Backtest auf dem kuratierten Korb) —
das liefert sofort messbaren Mehrwert und ist die Basis für alles Weitere. Danach Phase 2 (Vergleich).

Offene Fragen für die Umsetzung:
1. **Erste Assets/Zeitraum:** kuratierter Korb + 2 Jahre Tages-TF? Oder gezielt ein paar Ticker?
2. **Vergleichs-Zielgröße:** wonach „besser" gemessen wird (Profitfaktor, Sharpe, Drawdown-adjustiert)?
3. **`trader-dev` MCP:** jetzt einbinden (externe Daten) — oder erst den nativen Rahmen bauen?
