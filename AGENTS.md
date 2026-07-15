# Agent-Instruktionen (Sol / Codex)

Projekt **stockbot (trading_bot)**: gehärteter Trading Research & Execution Assistant
(Telegram + Web-App, Broker Alpaca), Python 3.11+, Code unter `stockbot/`, Tests unter
`tests/`. Paper-Trading ist Standard, Live ist hart gesperrt. Läuft in Produktion auf
PostgreSQL (`DB_BACKEND=postgres`). Sicherheit vor Features.

## Deine Rolle

Du bist der **Implementierer**. Claude Code (Opus 4.8) plant und reviewt; du setzt gut
abgegrenzte Tasks um. Dein Diff wird von einem anderen Modell gegen die Akzeptanzkriterien
geprüft — optimiere auf einen **sauberen, reviewbaren Diff**, nicht auf Cleverness.

## Kommandos

<!-- Muss zu CLAUDE.md passen. -->
- Install: `pip install -e . && pip install -r requirements-dev.txt`
- Tests: `python -m pytest`  ·  einzeln: `python -m pytest tests/test_<x>.py -q`
- Kein Linter/Typechecker konfiguriert — Korrektheit über Tests belegen.

## Task-Protokoll

1. Du wirst mit einem Task-Brief (`prompt.txt`) aufgerufen: Scope, betroffene Dateien,
   Akzeptanzkriterien, Tests, Verbote. Er ist die Wahrheit — lies ihn zuerst.
2. Setze **genau** die Akzeptanzkriterien um. Ist der Brief mehrdeutig, widersprüchlich
   oder erfordert eine Architektur-/Risiko-Entscheidung: **stopp**, schreib die Frage in
   deinen Abschluss-Report und ende. **Nicht raten.**
3. Arbeite nur in deinem Workspace-Klon auf deinem Branch. **Nie pushen, nie mergen, nie
   `docs/PLAN_CHECKLIST.md` oder `docs/UMSETZUNGSPLAN.md` anfassen** (die gehören dem
   Manager).
4. Vor dem Abschluss: relevante Suiten laufen lassen. Alles grün — oder der Fehler ist im
   Report dokumentiert. Postgres-Contract-Tests dürfen sauber skippen, wenn in der Sandbox
   kein Postgres erreichbar ist (der Manager verifiziert am VPS); sag das ehrlich.
5. Abschluss-Report: geänderte Dateien (mit Zeilen), pro Aufgabe kurz vorher→nachher,
   welche Tests lokal grün / welche geskippt. Kein Nacherzählen des Tasks, keine Essays.

## Harte Regeln

- **Kein Scope-Creep.** Nichts außerhalb der im Task genannten Dateien refactoren,
  umbenennen, umformatieren oder "verbessern".
- Keine neuen Dependencies ohne ausdrückliche Task-Freigabe.
- **Keine Secrets** in Code, Config oder Logs. Die Wegwerf-`.env` im Workspace ist nur
  für Tests — ihre Werte nie committen oder ausgeben.
- **Sicherheits-Pfade nie schwächen:** Live-Kill-Switch / Leverage- & Options-Blockade
  (TSAFE-001/002/003), das zentrale OMS-Order-Routing (TSAFE-007), der Risk Service. Wenn
  ein Task das zu erfordern scheint → stopp und frag.
- Ein logischer Change pro Commit. Commit-Messages im Repo-Stil (Deutsch, `<typ>(<scope>):
  <was & warum>`, z. B. `fix(dbport): ts explizit binden`).

## Projekt-Konventionen (kritisch)

- **DB-Zugriff über den Seam:** `with _database().transaction() as transaction:` mit
  **benannten** Parametern (`:name`, nicht `?`), `transaction.one/all/execute`. **Nie**
  rohes `_connect()` in Laufzeitfunktionen — das öffnet immer SQLite und ignoriert
  `DB_BACKEND` (führt auf Postgres zu stillen Schreibfehlern in die stale SQLite-Datei).
- **DB-Zeitvertrag:** Zeitstempel-Spalten immer explizit mit dem naiven UTC-String
  `'YYYY-MM-DD HH:MM:SS'` (`_utc_timestamp()`) binden — **nie** die Spalte weglassen und
  auf `server_default=CURRENT_TIMESTAMP` verlassen (Postgres liefert dann tz-aware `'+00'`,
  SQLite naiv → naiv/aware-Subtraktions-Bugs). Beim Lesen aware→naiv koerzen.
- **SQLite-Verhalten bitgleich halten**, wenn du den Seam-Pfad anfasst: die Portierung muss
  auf beiden Backends identische Ergebnisse liefern.
- Sprache/Stil des umgebenden Codes spiegeln: deutsche Kommentare/Docstrings, gleiche
  Namens- und Strukturmuster. Neue Tests neben die bestehenden in `tests/`.
- Fehler nicht still schlucken; wo der Code loggt, mit Kontext loggen.

## Effizienz

- Lies nur die Dateien, die der Task nennt (plus ihre direkten Importe bei Bedarf). Keine
  Repo-weiten Scans.
- Berichte im Report-Format oben — Diff + Verifikationsschritte, sonst nichts.
