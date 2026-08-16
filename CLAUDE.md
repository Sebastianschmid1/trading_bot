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

Kommandos, DB-Konventionen und die Implementierer-Regeln stehen in `AGENTS.md`
(dieselbe Datei liest Codex) und werden hier eingebunden:

@AGENTS.md

## Worker-Besetzung

**Codex** ist hier der Implementierer — der zentrale Default gilt unverändert.
Aufruf pro Task mit dem Handoff-Block aus `planner.md`:

```bash
codex exec "<Goal · Context · Scope · Acceptance Criteria · Validation>"
```

Ein Task = ein Branch `agent/<ticket>`. Codex pusht und merged nie selbst; Merge,
Push und Deploy macht der Lead nach dem Diff-Review.

**Claude-Fallback** nach der zentralen Regel (`routing.md` → „Worker-Besetzung und
Claude-Fallback"): ist Codex nicht verfügbar oder scheitern zwei Fix-Runden, springt
ein Claude-Subagent ein — `subagent_type: "general-purpose"` mit
`isolation: "worktree"`, weil der Worktree das installierte Environment des Repos
erbt (kein Neuaufsetzen, keine Wegwerf-`.env`). Der Grund wird im Ergebnis genannt.

**Verschärfung gegenüber der zentralen Policy:** In diesem Repo wird
Implementierungsarbeit grundsätzlich delegiert, auch wenn sie klein wirkt. Selbst
erledigen darfst du nur Glue/Config/Docs und Ein-Zeilen-Fixes im Reviewfluss.
Grund: sicherheitskritisches Live-Trading — jeder Diff soll durch das Review-Gate.

## Design der Web-App: `design-lead`

Für alles unter `stockbot/web/` gibt es einen eigenen Agenten
(`.claude/agents/design-lead.md`, Opus, read-only bis auf `DESIGN.md`-Ergänzungen).
Der Lead ruft ihn bei UI-Arbeit **zweimal**: einmal **vor** dem Handoff für die
Design-Vorgabe (Komponente, Tokens, Zustände) und einmal **nach** dem Worker-Branch
zur Abnahme, bevor gemergt wird.

Seine **Blocker sind bindend** (Kontrast < 4.5:1, Tastaturbedienung, Fork des
vendorierten `liquid-glass.css`, fehlender Bestätigungsdialog bei Geldbewegung,
verharmloster Verlust, gebrochener Zustand); alles Gestalterische ist Empfehlung.

Rangordnung der Design-Quellen: visuelle Fragen entscheidet `DESIGN.md` +
`liquid-glass.css`, Prinzipien und Fachstruktur `docs/Stylekonzept.md` (dessen
**visuelle** Kapitel sind seit der Liquid-Glass-Migration überholt).

Die Sichtprüfung braucht **Seed-Daten** — die lokale DB ist leer. Ohne sie liefert
der Agent nur den Code-Teil und meldet den Rest als ungeprüft.

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

## Sicherheits-Leitplanken für den Lead (nicht verhandelbar)

Die Implementierer-Verbote (TSAFE-Pfade, Plan-Dateien, Deploy, Secrets) stehen in
`AGENTS.md`. Zusätzlich gilt für dich als Lead:

- **Paper bleibt Standard, Live bleibt hart gesperrt.** Änderungen am
  Live-Trade-**Verhalten** (Exit-Policies, Sizing, Order-Routing) explizit ankündigen
  und **vor** dem Deploy freigeben lassen — nie stillschweigend annehmen.
- **Produktions-Deploy ist ein Gate:** Push/ff-merge/restart auf dem Live-VPS
  (`217.160.103.25`, `systemctl restart stockbot`) nur, wenn der User das Ziel
  ausdrücklich benennt („deploy to the VPS"). Die Safety-Klassifizierung blockt sonst
  zurecht — nicht umgehen.
- Jeder Codex-Diff, der einen TSAFE-Pfad oder den DB-Seam berührt, wird vollständig
  gelesen — keine Stichprobe.

## Memory

Persistentes Memory unter `/home/jms/.claude/projects/-home-jms-trading-bot/memory/`
(`MEMORY.md` = Index). Vor neuer Arbeit dort **und** in `docs/UMSETZUNGSPLAN.md` den
Stand prüfen, statt Snapshots zu vertrauen. Tiefes/selten gebrauchtes Wissen lebt in
`docs/` — nur referenzieren, wenn ein Task es wirklich braucht.
