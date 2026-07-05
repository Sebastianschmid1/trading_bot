# TODO — Handoff an Hermes-Bot

Stand: 2026-07-06. Repo-Root: `c:\Users\sebas\OneDrive\trading_bot` (Windows, PowerShell primär).
Alle Aufgaben unten sind **offen/aufgeschoben**. **Reihenfolge = Priorität (oben zuerst): T3 → T2 → T1 → T4.**

## Verbindliche Leitplanken (für JEDE Aufgabe)
- **Live-Bot bleibt long-only.** Shorts nur im Backtest. Keine Ausnahme.
- **Secrets nur in `.env`** — niemals committen/loggen. Keys für neue Features aus `.env` lesen.
- **Nach Code-Änderungen immer:** committen → `.\upload.ps1 "msg"` (push + VPS-Pull + Restart).
  Commit-Trailer exakt: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- **Tests müssen grün bleiben:** `python -m pytest -q` (aktuell 352 passed).
- Menschen-Gate der KI-Strategie nicht umgehen: Optimizer darf NIE direkt `strategy_configs`
  (Live-Parameter) schreiben — nur über `lab.apply_pending()` nach Freigabe.

---

## T3 — LLM-Proposer für das Labor (hinter Flag; Default bleibt Raster) — **PRIORITÄT 1**
**Warum:** Statt „±1 Rasterschritt" soll eine LLM die Kandidaten-Hypothese vorschlagen
(welche EINE Variable, welche Richtung, mit Begründung aus den letzten Trades/Regime).

**Integrationspunkt:** `stockbot/optimize/lab.py`
- Aktuell: `candidates(champion)` erzeugt die Ein-Variablen-Nachbarn.
- Neu: `_llm_candidates(champion, context)` hinter Flag (z. B. `LAB_PROPOSER=llm` aus `.env`),
  sonst weiter `candidates()`. Rückgabeformat identisch: Liste `{param, old, new, params}`.

**Modell:** **GPT-5.5 (OpenAI)** — API-Key aus `.env` (`OPENAI_API_KEY`). Modell-ID beim Bau gegen die
verfügbare OpenAI-Modellliste verifizieren und in einer Konstante (`LAB_LLM_MODEL`) halten.

**Regeln (nicht verletzen):**
- LLM schlägt NUR die eine Variable + Richtung vor. **Backtest-Engine bleibt Richter**
  (IS-Auswahl + OOS-Gate in `run_cycle` unverändert). LLM entscheidet nichts, löst keine Orders aus.
- Nur erlaubte Parameter aus `SEARCH_SPACE`, nur gültige Rasterwerte — LLM-Output hart validieren.
- Menschen-Gate (`apply_pending`) bleibt. Key/Prompt niemals loggen.

**Akzeptanz:**
- Ohne Flag: Verhalten exakt wie heute (Tests grün).
- Mit Flag + gemocktem LLM-Client: `run_cycle` erzeugt Kandidaten aus LLM-Output, ungültige werden
  verworfen. Neuer Test mit Mock (kein echter API-Call in der CI).

---

## T2 — Echter dynamischer Trailing-Stop in der Backtest-Engine — **PRIORITÄT 2**
**Warum:** Aktuell fester SL/TP für alle Strategien. `supertrend`/`ai_adaptive` nutzen als Ersatz nur
ein weites Fix-TP (`tp_mult=10`). Ein echter Trailing-Stop würde Gewinner besser laufen lassen.

**Dateien:**
- `stockbot/backtest/engine.py` → `_walk_exit()` (≈Z.277, Portfolio-Pfad) und die Ausstiegsschleife
  in `backtest_ticker()` (≈Z.160–176, Einzel-Pfad). Beide prüfen aktuell nur festes `sl`/`tp`.
- Signal liefert `stop_loss`/`take_profit` aus `strategies._make_signal()` (≈Z.252).

**Tun:** optionalen Trailing-Modus einführen (rückwärtskompatibel, Default = Fix wie bisher):
- Parameter `trail_mode`/`trail_mult` durch `simulate_portfolio` → `_walk_exit` reichen.
- Im Ausstiegsloop je Bar den Stop nachziehen: z. B. ATR-Trailing (`stop = max(stop, close - trail_mult*ATR)`
  für long) oder SuperTrend-basiert. TP optional entfernen, wenn Trailing aktiv.
- Kein Look-ahead: Stop-Anpassung nutzt nur Daten bis Bar j (die schon geschlossene Bar).
- Die KI-Strategie `ai_adaptive` als ersten Nutzer verdrahten; `SEARCH_SPACE` in
  `stockbot/optimize/lab.py` um `trail_mult` erweitern (falls Trailing für sie aktiv).

**Akzeptanz:**
- Neuer Test in `tests/test_backtest*` : Trailing-Ausstieg zieht bei steigendem Kurs den Stop nach
  und schließt beim Rücklauf über dem Einstieg (Gewinn gesichert), Fix-Modus unverändert.
- `python -m pytest -q` grün. Vergleich supertrend fix vs. trailing dokumentieren.

---

## T1 — Offizielle Sweep-Reports regenerieren — **PRIORITÄT 3**
**Problem:** `data/reports/*_{1,3,5,8,15}y.json` stammen vom 2026-06-28 und enthalten nur 7 alte
Strategien. Es fehlen: `tsmom, lowvol, faber, streversal, frog, bb_revert, adx_mfi, supertrend`
und **`ai_adaptive`**. Folge: `/app/reports` zeigt sie nicht, und der Reiter „Labor" blendet die
„KI-vs-fix"-Einordnung aus (liest `strategies_5y.json`, sucht Zeile `ai_adaptive`).

**Tun:** je Zeitfenster den vollen Sweep neu erzeugen (schreibt nach `data/reports/`):
```
python -m tools.sweep_report --years 1
python -m tools.sweep_report --years 3
python -m tools.sweep_report --years 5
python -m tools.sweep_report --years 8
python -m tools.sweep_report --years 15
```
(Jeder Lauf ~20–25 Min über die volle S&P 500, nutzt alle Kerne. `--jobs 0` = alle Kerne.)

**Akzeptanz:**
- `strategies_5y.json` `rows` enthält `ai_adaptive` UND `supertrend` (+ die 8 fehlenden).
- `/app/reports` listet alle 16 Strategien; `/app/lab` zeigt die „KI-vs-fix"-Einordnungszeile.
- Danach committen + `upload.ps1` (Reports sind versioniert, `.gitignore`-Ausnahme `data/reports/`).

---

## T4 — Wochen-Cron für automatische Optimierungsläufe (optional) — **PRIORITÄT 4**
**Warum:** Heute nur manueller Trigger (`/app/lab` Button oder CLI). Ein Zeitplan-Job würde
regelmäßig einen Zyklus fahren; Freigabe bleibt manuell.

**Datei:** `stockbot/tgbot/bot.py` (Scheduler-Setup, wo `daily_signals`/`monitor_trades` registriert
werden). Neuen APScheduler-Job hinzufügen, z. B. wöchentlich So 03:00, der
`stockbot.optimize.lab.start_background_cycle(limit=None)` (voller Lauf) aufruft.

**Regeln:** nur EIN Lauf gleichzeitig (`lab.is_running()` prüfen; `_run_lock` existiert schon).
CPU-Last auf der VPS bedenken → außerhalb der Handelszeiten. Schreibt weiterhin nur `pending.json`
(kein Auto-Live).

**Akzeptanz:** Job erscheint im Scheduler-Log beim Start; ein manuell ausgelöster Lauf setzt
`data/lab/pending.json`. Kein Live-Eingriff ohne Freigabe.

---

## Referenz — bereits erledigt (nur Kontext, nichts zu tun)
- KI-Strategie `ai_adaptive` + Reiter `/app/lab` + Walk-Forward-Optimizer (Ziel MAR) — Commit `8154501`.
  Kern: `stockbot/optimize/lab.py`. CLI: `python -m stockbot.optimize.lab [--limit N|--apply|--reject]`.
  Web-Lauf nutzt `WEB_LIMIT=150` Werte; Laufzeit-Zustand in `data/lab/` (gitignored).
- 3 TradingView-Strategien (`supertrend`/`adx_mfi`/`bb_revert`) — Commit `1eb0014`.
- 5 akademische Faktor-Strategien — Commit `7a35840`.
