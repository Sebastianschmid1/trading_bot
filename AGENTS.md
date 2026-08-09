# AGENTS.md — stockbot (trading_bot), Projektfakten

> Rollen, Routing, Review-, Testing- und Simplicity-Regeln stehen **zentral** in
> `~/.codex/AGENTS.md` (Quelle: `main_projekt/agent-control/`). Hier steht nur, was
> für dieses Repo gilt. Architektur und Lead-Themen: `CLAUDE.md`.

Gehärteter Trading Research & Execution Assistant (Telegram + Web-App, Broker
Alpaca), Python 3.11+, Code unter `stockbot/`, Tests unter `tests/`. **Paper-Trading
ist Standard, Live ist hart gesperrt.** Produktion läuft auf PostgreSQL
(`DB_BACKEND=postgres`). Sicherheit vor Features.

## Kommandos

<!-- Ein falsches Kommando hier ist schlimmer als keins. Halte es aktuell. -->
- Install: `pip install -e . && pip install -r requirements-dev.txt`
- Tests: `python -m pytest` (pyproject setzt `testpaths=tests`, ignoriert `tests/test_bot.py`)
- Einzelne Suite: `python -m pytest tests/test_<x>.py -q`
- **Kein** konfigurierter Linter/Typechecker — Korrektheit über Tests belegen.

Postgres-Contract-Tests dürfen sauber skippen, wenn in der Sandbox kein Postgres
erreichbar ist (der Lead verifiziert am VPS). Sag das ehrlich — ein Skip ist kein Grün.

## Nie ohne ausdrücklichen Auftrag anfassen

- **TSAFE-Pfade:** Live-Kill-Switch, Leverage-/Options-Blockade (TSAFE-001/002/003),
  das zentrale OMS-Order-Routing (TSAFE-007), der Risk Service. Nie schwächen.
- **Plan-Dateien** `docs/PLAN_CHECKLIST.md` und `docs/UMSETZUNGSPLAN.md` — die
  gehören dem Lead.
- **Deploy:** kein Push nach `main`, kein ff-merge, kein `systemctl restart` auf dem
  Live-VPS. Merge und Deploy macht der Lead.
- Braucht ein Testlauf lokal einen `ENCRYPTION_KEY` oder eine `.env`, gilt der Wert
  nur für den Lauf — nie committen, nie ausgeben.

Scheint ein Task eines dieser Dinge zu erfordern: stoppen und fragen.

## DB-Konventionen (kritisch)

- **Zugriff über den Seam:** `with _database().transaction() as transaction:` mit
  **benannten** Parametern (`:name`, nicht `?`), dann `transaction.one/all/execute`.
  **Nie** rohes `_connect()` in Laufzeitfunktionen — das öffnet immer SQLite und
  ignoriert `DB_BACKEND` (auf Postgres: stille Schreibfehler in die stale
  SQLite-Datei).
- **Zeitvertrag:** Zeitstempel-Spalten immer explizit mit dem naiven UTC-String
  `'YYYY-MM-DD HH:MM:SS'` binden (`_utc_timestamp()`) — **nie** die Spalte weglassen
  und auf `server_default=CURRENT_TIMESTAMP` verlassen (Postgres liefert dann
  tz-aware `'+00'`, SQLite naiv → naiv/aware-Subtraktions-Bugs). Beim Lesen
  aware→naiv koerzen.
- **SQLite bitgleich halten:** wer den Seam-Pfad anfasst, muss auf beiden Backends
  identische Ergebnisse liefern.

## Code-Konventionen

- Deutsche Kommentare/Docstrings; Namens- und Strukturmuster des umgebenden Codes
  spiegeln. Neue Tests neben die bestehenden in `tests/`.
- Fehler nicht still schlucken; wo der Code loggt, mit Kontext loggen.
- Ein logischer Change pro Commit, Messages deutsch im Repo-Stil:
  `<typ>(<scope>): <was & warum>` (z. B. `fix(dbport): ts explizit binden`).
- Ein Task = ein Branch `agent/<ticket>`. Arbeit dort committen, damit der Lead sie
  reviewen und mergen kann.
