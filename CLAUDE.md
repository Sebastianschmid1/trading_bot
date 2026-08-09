# Project: stockbot (trading_bot)

Gehärteter Rebuild eines Multi-User-Signal-Bots (Telegram + Web-App, Broker Alpaca)
zum sicherheitsorientierten **"Trading Research & Execution Assistant"**. Python 3.11+,
Code unter `stockbot/` (`core/`, `market/`, `broker/`, `execution/`, `tgbot/`, `web/`,
`backtest/`, `ai/`), Tests unter `tests/`. **Paper-Trading ist Standard, Live ist hart
gesperrt** (Kill-Switch TSAFE-001). Läuft in Produktion auf `DB_BACKEND=postgres`
(Docker-Postgres 16 auf dem VPS).

> Rollen, Routing, Review- und Simplicity-Regeln stehen **zentral** in
> `main_projekt/agent-control/` (aktiv über `~/.claude/CLAUDE.md`). Hier stehen nur
> die Abweichungen und Fakten dieses Projekts.

## Worker-Besetzung (Override)

Implementierer ist hier **kein Codex**, sondern ein **Claude-Subagent** über das
Agent-Tool: `subagent_type: "general-purpose"` mit `isolation: "worktree"` — der
Worktree erbt das installierte Environment des Repos (keine `codex`-Auth, keine
Wegwerf-`.env`). Ein Subagent pro abgegrenztem Task; unabhängige Tasks als mehrere
Agent-Calls in **einer** Nachricht.

Der Subagent arbeitet nur in seinem Worktree, committet auf `agent/<ticket>`, pusht
und merged nie selbst und fasst `docs/PLAN_CHECKLIST.md` / `docs/UMSETZUNGSPLAN.md`
nie an — die gehören dem Lead. Nächste Runde am selben Task per `SendMessage` an die
Agent-ID (Kontext bleibt), nicht als neuer Agent-Call.

**Verschärfung gegenüber der zentralen Policy:** In diesem Repo wird
Implementierungsarbeit grundsätzlich delegiert, auch wenn sie klein wirkt. Selbst
erledigen darfst du nur Glue/Config/Docs und Ein-Zeilen-Fixes im Reviewfluss.
Grund: sicherheitskritisches Live-Trading — jeder Diff soll durch das Review-Gate.

## Kommandos

<!-- Ein falsches Kommando hier ist schlimmer als keins. Halte es aktuell. -->
- Install: `pip install -e . && pip install -r requirements-dev.txt`
- Tests: `python -m pytest` (pyproject setzt `testpaths=tests`, ignoriert `tests/test_bot.py`)
- Einzelne Suite: `python -m pytest tests/test_<x>.py -q`
- **Kein** konfigurierter Linter/Typechecker — verlasse dich auf Tests + Review.
- Deploy (Produktion, VPS `217.160.103.25`): git push auf einen frischen Branch →
  ssh ff-merge in `main` → `systemctl restart stockbot`. **Nur auf ausdrückliche
  Anweisung** (siehe Sicherheits-Leitplanken).

## Plan-Dokumente & Merge-Gate

- Maßgeblich: `docs/PLAN_CHECKLIST.md` (Phasen 0–12 + Gates, Tasks getaggt
  TSAFE-/PLAT-/RES-/STRAT-/OMS-…) und `docs/UMSETZUNGSPLAN.md` (Wellen W0–W8, der
  aktuelle Fahrplan). Beide gehören dem Lead — Subagenten fassen sie nie an.
- **Kein Merge ohne Plan-Eintrag.** Erledigte Tasks werden direkt beim Merge in
  `docs/UMSETZUNGSPLAN.md` eingetragen (Commit-Hash, geschlossenes Gate, Status) —
  und zwar konsistent: Wellen-Übersicht, Detail-Tabelle und „Was jetzt"/Kritischer
  Pfad mitziehen, damit keine erledigte Welle noch als To-do lesbar ist. Denselben
  Stand ins Memory spiegeln (`project_trading-bot-konzept-v1.md` + `MEMORY.md`).
- **Merge & Deploy** macht der Lead: Subagenten-Branch reviewen, ff-merge in `main`,
  Suite auf gemergtem `main` erneut laufen lassen, committen, nach GitHub pushen.
  Produktions-Deploy nur auf ausdrückliche Anweisung.
- Beim Review zusätzlich zur zentralen Checkliste: Postgres-Contract-Tests, die ohne
  echtes Postgres sauber skippen, am VPS gegenverifizieren — ein Skip ist kein Grün.

## Sicherheits-Leitplanken (dieses Projekt, nicht verhandelbar)

- **Paper bleibt Standard, Live bleibt hart gesperrt.** TSAFE-Pfade (Live-Kill-Switch,
  Leverage-/Options-Blockade, direkte Broker-Calls) nie schwächen. Änderungen am
  Live-Trade-**Verhalten** (Exit-Policies, Sizing, Order-Routing) explizit ankündigen und
  **vor** dem Deploy freigeben lassen — nie stillschweigend annehmen.
- **DB-Zeitvertrag:** immer naive UTC-Strings `'YYYY-MM-DD HH:MM:SS'` binden
  (`_utc_timestamp()`), **nie** auf Server-Default `CURRENT_TIMESTAMP` verlassen (Postgres
  liefert tz-aware `'+00'` → naiv/aware-Bugs).
- **DB-Zugriff über den Seam** (`_database().transaction()`), **nicht** rohes `_connect()`
  (das öffnet immer SQLite, ignoriert `DB_BACKEND`).
- **Produktions-Deploy ist ein Gate:** Push/ff-merge/restart auf dem Live-VPS nur, wenn der
  User das Ziel ausdrücklich benennt ("deploy to the VPS"). Die Safety-Klassifizierung
  blockt sonst zurecht — nicht umgehen.

## Projekt-Konventionen

- **DB-Zugriff über den Seam:** `with _database().transaction() as transaction:` mit
  **benannten** Parametern (`:name`, nicht `?`), dann `transaction.one/all/execute`.
- **SQLite bitgleich halten:** wer den Seam-Pfad anfasst, muss auf beiden Backends
  identische Ergebnisse liefern.
- Deutsche Kommentare/Docstrings, Namens- und Strukturmuster des umgebenden Codes
  spiegeln. Neue Tests neben die bestehenden in `tests/`.
- Commit-Messages deutsch im Repo-Stil: `<typ>(<scope>): <was & warum>`
  (z. B. `fix(dbport): ts explizit binden`).

## Memory

Persistentes Memory unter `/home/jms/.claude/projects/-home-jms-trading-bot/memory/`
(`MEMORY.md` = Index). Vor neuer Arbeit dort **und** in `docs/UMSETZUNGSPLAN.md` den
Stand prüfen, statt Snapshots zu vertrauen. Tiefes/selten gebrauchtes Wissen lebt in
`docs/` — nur referenzieren, wenn ein Task es wirklich braucht.
