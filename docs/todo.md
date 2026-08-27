# TODO — Handoff an Hermes-Bot

Stand: 2026-07-09. Repo-Root: `c:\Users\sebas\OneDrive\trading_bot` (Windows, PowerShell primär).

## Offen (aus Sicherheits-Audit 2026-07-08)

### A1 — systemd-Dienste nicht mehr als root betreiben
- `deploy/stockbot.service` und `deploy/dashboard.service` laufen mit `User=root` unter `/root/stockbot`.
- Ziel: dedizierter Nutzer (`useradd -r stockbot`), Repo z. B. nach `/opt/stockbot`, `.env` mit `chmod 600`.
- systemd-Härtung ergänzen: `NoNewPrivileges=true`, `ProtectSystem=strict` (+ `ReadWritePaths` für `data/` und `logs/`), `ProtectHome=true`, `PrivateTmp=true`.
- Achtung: Pfade in `deploy/upload.ps1`/`deploy/*.sh` (`/root/stockbot`) müssen mitziehen; Migration auf dem VPS nötig (Daten/venv umziehen).

### A2 — Dependencies pinnen
- `requirements.txt` ist komplett ungepinnt — jedes Deploy installiert blind die neuesten Versionen (Breaking Changes / Supply-Chain-Risiko direkt auf dem VPS).
- Ziel: Versionen pinnen (z. B. `pip freeze` in ein `requirements.lock`, das beim Deploy installiert wird, oder pip-tools) und Updates bewusst einspielen.
- Dabei prüfen: **yfinance-Handle-Leck** (2026-07-10: ~1000 offene FDs auf `~/.cache/py-yfinance/tkr-tz.db` → DB-Ausfall). `LimitNOFILE=65535` in den systemd-Units ist der Workaround; beim Pinnen eine yfinance-Version mit gefixtem tz-Cache wählen bzw. Leck upstream nachverfolgen.

## Status
Alle priorisierten Punkte aus dem Hermes-Handoff wurden umgesetzt bzw. nach Nutzerfreigabe angepasst.

## Erledigt

### T3 — LLM-Proposer für das Labor
- `LAB_PROPOSER=llm` aktiviert den LLM-Proposer; ohne Flag bleibt der Rastermodus aktiv.
- `LAB_LLM_MODEL` hält die Modell-ID, Default `gpt-5.5`.
- API-Key wird ausschließlich aus `.env` via `OPENAI_API_KEY` gelesen.
- LLM-Output wird hart validiert: nur Parameter aus `SEARCH_SPACE`, nur direkte Raster-Nachbarn, genau eine Variable.
- Backtest-/OOS-Gate bleibt Richter; kein Live-Write ohne `lab.apply_pending()`.
- Mock-Tests decken gültige/ungültige LLM-Vorschläge und Flag-Fallback ab.

### T2 — Echter dynamischer Trailing-Stop in der Backtest-Engine
- ATR-Trailing-Stop ist optional und rückwärtskompatibel (`trail_mode=None` bleibt Fix-SL/TP).
- Verdrahtet in `backtest_ticker`, `_walk_exit`, `simulate_portfolio`, `backtest_portfolio`, `run_backtest`.
- Kein Look-ahead: Stop-Anpassung nutzt nur abgeschlossene Bars vor der gerade geprüften Bar.
- `ai_adaptive` nutzt `trail_mult`; `SEARCH_SPACE` enthält `trail_mult`.
- Regressionstests sichern Gewinn-Trailing und unveränderten Fix-Modus.

### T1 — Sweep-Reports regeneriert
- Nach Nutzerentscheidung wurden die offiziellen Reports für `1y` und `3y` regeneriert.
- Beide enthalten jetzt alle 16 Strategien plus Benchmark:
  - `standard`, `adx_trend`, `rsi_revert`, `breakout`, `ma_trend`, `high52`, `high52_wide`
  - `tsmom`, `lowvol`, `faber`, `streversal`, `frog`
  - `bb_revert`, `adx_mfi`, `supertrend`, `ai_adaptive`
  - `buyhold_sp500`
- 5y/8y/15y wurden auf Nutzerwunsch nicht weiter regeneriert.

### T4 — Wochen-Cron für automatische Optimierungsläufe
- Neuer APScheduler-Job `weekly_lab_optimization` im Bot.
- Default: Sonntag 03:00 Berlin; konfigurierbar via `LAB_WEEKLY_OPTIMIZATION`, `LAB_WEEKLY_DAY`, `LAB_WEEKLY_HOUR`, `LAB_WEEKLY_MIN`.
- Ruft nur `stockbot.optimize.lab.start_background_cycle(limit=None)` auf.
- Prüft `lab.is_running()`; schreibt nur `pending.json`; kein Live-Eingriff ohne Freigabe.

## Verifikation
- Gezielte Tests: grün.
- Betroffene Testdateien: grün.
- Vollsuite vor finalem Commit: grün (`python -m pytest -q`).

## Dauerhafte Leitplanken
- Live-Bot bleibt long-only. Shorts nur im Backtest.
- Secrets nur in `.env`; niemals committen/loggen.
- Menschen-Gate der KI-Strategie nicht umgehen: Optimizer schreibt nie direkt Live-Parameter, sondern nur Pending-Vorschläge; Übernahme nur per `lab.apply_pending()`.
