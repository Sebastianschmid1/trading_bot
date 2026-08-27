# Plan: das Repository vorzeigbar machen (Bewerbungsmappe)

> **Status: A1, A2, B1, B2 und B3 freigegeben und umgesetzt (2026-08-27). C2 nicht freigegeben.**
> Die Entscheidung zur Urheberschaft ist gefallen: **offen lassen** (siehe A3/C1).
> Ursprünglicher Hinweis: Jeder Punkt trägt eine eigene Freigabe-Zeile.
> Erst nach deinem „ja" zu einem Punkt wird daran gearbeitet.
> Erstellt 2026-08-27. Betrifft **nur das Repository** — Betrieb, VPS und Deploy bleiben außen vor.

## Wofür das hier optimiert wird

Ein fremder Mensch — Recruiter, Team-Lead, künftiger Kollege — öffnet den GitHub-Link und hat
**etwa zwei Minuten**, bevor er entscheidet, ob er weiterliest. In dieser Zeit sieht er drei
Dinge: die Dateiliste im Wurzelverzeichnis, das README darunter, und wenn er neugierig ist,
eine einzige Quelldatei seiner Wahl. Alles Übrige — 1522 Tests, das Risk-Gate, die
Idempotenz-Logik — sieht er nur, wenn diese drei Dinge ihn nicht vorher vertreiben.

Der Plan ist danach sortiert: **Wirkung auf den ersten Eindruck ÷ Aufwand**, nicht nach
technischer Wichtigkeit.

## Der Ausgangsbefund in Zahlen

Gemessen am Stand `8134f48`:

| | |
|---|---|
| Produktionscode | 25.390 Zeilen in 241 Python-Dateien |
| Funktionen | 1.086 |
| **mit Typannotation** | **1.016 (93 %)** |
| mit Docstring | 700 (64 %) |
| Tests | 1.522 grün, 29 übersprungen |
| Commits | 509 |
| Dateien über 500 Zeilen | 10, davon zwei über 3.000 |

**Die Substanz ist gut.** 93 % Typannotationen und eine grüne Suite dieser Größe sind
überdurchschnittlich — das ist nichts, was man in einem Wochenendprojekt findet. Das Problem
ist nicht der Code. Das Problem ist, dass man ihn nicht sieht.

---

## A — Erster Eindruck

### A1. Das README beschreibt ein anderes, viel kleineres Projekt

**Der wichtigste Punkt der ganzen Liste.**

Das README beginnt mit „📈 Stock Signal Telegram Bot — Tägliche Aktienempfehlungen per
Telegram mit Demo-Trade-Tracking" und nennt als Feature: „**Demo-Modus**: Kein echtes Geld, nur
Tracking".

Tatsächlich enthält das Repository: ein zentrales Order-Management-System mit
Idempotenzschlüsseln und Zustandsautomat, einen Risk-Service mit vorgelagerter Prüfkette,
einen persistenten Kill-Switch, Broker-Anbindung mit Reconciliation gegen die Gegenseite,
eine Backtest-Engine mit Walk-Forward-Validierung, ein Strategie-Labor mit
Promotion-Gate, Alembic-Migrationen über zehn Versionen, Postgres/SQLite hinter einem
gemeinsamen Seam, eine Web-App und 1.522 Tests.

Ein Leser, der beim Wort „Demo-Trade-Tracking" abbricht, sieht davon nichts. Das README
verschenkt das Projekt.

**Vorschlag:** README neu schreiben, mit dieser Reihenfolge:

1. **Ein Satz, der stimmt.** Was ist das, für wen, in welchem Zustand (Paper-Handel, Live
   gesperrt).
2. **Warum es anspruchsvoll ist** — drei bis vier Punkte, die ein Fachleser sofort einordnet:
   keine Order kann das Risk-Gate umgehen (mit Verweis auf den Test, der das strukturell
   beweist), Idempotenz gegen Doppelausführung, Abgleich gegen die Broker-Sicht,
   Backtest ohne Look-ahead.
3. **Architektur in einem Bild** — ein Mermaid-Diagramm, das GitHub direkt rendert:
   Signal → Freigabe → Risk → OMS → Broker, daneben die Nebenpfade (Backtest, Labor, Web).
   Zehn Zeilen Markdown, und der Leser hat das System verstanden.
4. **Einstiegspunkte für Leser** — „wenn dich X interessiert, sieh dir Datei Y an". Das ist
   der billigste Weg, jemanden zum guten Code zu führen, statt ihn raten zu lassen.
5. Setup, dann der Rest.

Was heute im README steht (Indikatoren-Tabelle, Setup-Schritte) bleibt erhalten, rutscht aber
nach unten.

**Aufwand:** ein halber Tag. **Wirkung:** die höchste in dieser Liste.

> **Freigabe A1:** ✅ **erledigt** (`4c9ab09`) — README neu aufgebaut, alle Verweise geprüft, Zahlen gegen die Suite belegt.

### A2. Das Wurzelverzeichnis sieht nach Werkstatt aus

Was ein Leser als Erstes sieht, sind 27 Einträge, darunter:

- **vier Deploy-Skripte nebeneinander** — `deploy.sh`, `upload.sh`, `upload.ps1`, `update.ps1`
  (zwei davon PowerShell). Das liest sich wie „hier hat jemand mehrfach angefangen".
- `todo.md` — eine private Notizliste im Schaufenster.
- `_imported_transcripts/` und `logs/` — leere, ignorierte Verzeichnisse, die trotzdem in der
  Dateiliste stehen.
- `AGENTS.md`, `CLAUDE.md`, `DESIGN.md`, `PRODUCT.md` — vier Großbuchstaben-Dateien, deren
  Zweck sich einem Fremden nicht erschließt.

**Vorschlag:** Deploy-Skripte nach `deploy/` (es existiert bereits), `todo.md` nach `docs/`,
die ignorierten Verzeichnisse lokal löschen. Wurzel danach: Quellcode, Tests, Doku, Konfiguration,
README — mehr nicht.

**Aufwand:** eine Stunde, reines Verschieben, kein Codeeingriff. **Wirkung:** hoch, weil es
das Erste ist, was im Blick liegt.

> **Freigabe A2:** ✅ **erledigt** (`770b095`) — Skripte nach `deploy/`, `todo.md` nach `docs/`. **Wichtig dabei:** alle vier Skripte leiteten ihr Arbeitsverzeichnis aus dem eigenen Ort ab und hätten nach dem Umzug die `.env` im falschen Verzeichnis gesucht; die Pfade wurden mitgezogen. Die ignorierten Verzeichnisse `logs/` und `_imported_transcripts/` waren nie im Repo — sie stehen nur lokal in der Dateiliste, für einen fremden Leser sind sie unsichtbar (Korrektur an meiner ursprünglichen Einschätzung).

### A3. `CLAUDE.md` und `AGENTS.md` liegen offen im Wurzelverzeichnis

Beide Dateien sind Arbeitsanweisungen an KI-Agenten. Sie sind gut geschrieben und verraten viel
über die Arbeitsweise — aber sie beantworten für jeden Leser sofort die Frage, wie dieser Code
entstanden ist. Dasselbe gilt für die Commit-Historie (siehe **C1**), die 143 Commits unter
den Autorennamen „Claude" und „Sol Worker" führt.

**Das ist deine Entscheidung, nicht meine.** Ich lege die Optionen sachlich daneben:

- **Offen lassen und erklären.** Ein Projekt dieser Größe mit einem Agenten-Setup zu
  orchestrieren — mit Rollentrennung, Review-Gate und Handoff-Format — ist selbst eine
  Fähigkeit, und zwar eine gefragte. Wer die Dateien liest, sieht jemanden, der Delegation
  strukturiert statt Code zu tippen. Das *kann* stärker wirken als ein Repository, das
  behauptet, alles handgeschrieben zu sein.
- **Nicht zeigen.** Dann gehören die Dateien in eine separate, private Ablage — und die Frage
  aus **C1** stellt sich trotzdem, weil die Autorennamen in der Historie stehen.

**Was ich nicht empfehle:** die Autorenangaben nachträglich umzuschreiben. Das ginge technisch,
wäre aber eine Falschangabe über die Urheberschaft — und sie fliegt auf, sobald jemand im
Gespräch nach einer Stelle im Code fragt, die du nicht selbst geschrieben hast.

> **Freigabe A3:** ✅ **entschieden: offen lassen.** `CLAUDE.md` und `AGENTS.md` bleiben im Wurzelverzeichnis, die Historie bleibt unangetastet. Der Betreiber erklärt die Arbeitsweise im Gespräch.

---

## B — Struktur: die zwei Dateien, die jeden Leser abschrecken

### B1. `stockbot/core/db.py` — 3.198 Zeilen

Wer wissen will, wie die Persistenz gebaut ist, öffnet eine Datei mit 3.200 Zeilen. Sie enthält
das komplette Schema, alle Migrationen des Alt-Pfads, sämtliche Abfragen für Nutzer, Trades,
Orders, Signale, Audit-Log, Risikoprofile, Outbox und Burn-in-Statistik.

Der Inhalt ist **nicht schlecht** — er ist konsistent, kommentiert und folgt einem klaren
Zugriffsmuster. Er ist nur in einer einzigen Datei.

**Vorschlag:** aufteilen entlang der Fachbereiche, die es ohnehin schon gibt — Schema, Nutzer,
Handel, Ausführung, Beobachtung. Der bestehende Seam (`_database().transaction()`) bleibt
unangetastet, es werden nur Funktionen verschoben und ein Paket daraus gemacht.

**Ehrlich zum Risiko:** Das ist der DB-Zugriffspfad. Verschieben ist mechanisch, aber die
Datei hat sehr viele Aufrufer, und ein übersehener Import fällt erst zur Laufzeit auf. Die
Testsuite deckt das gut ab (die Postgres-Contract-Tests laufen allerdings nur mit echter
Datenbank). Ich würde das **nur mit anschließendem Lauf gegen echtes Postgres** machen.

**Aufwand:** ein Tag mit Absicherung. **Wirkung:** mittel — es fällt nur auf, wenn jemand
wirklich in den Code geht. Aber wenn er es tut, ist es der Unterschied zwischen „ah, sauber
geschnitten" und „oh".

> **Freigabe B1:** ✅ **erledigt** (`f3969f6`) — elf Fachmodule zwischen 67 und 705 Zeilen,
> geschnitten entlang der Abschnittsüberschriften, die die Datei schon selbst trug. Kein Aufrufer
> angefasst. **Der Befund, der das Design bestimmt hat:** `db` ist selbst eine Test-Naht — rund 50
> Teststellen ersetzen Namen *auf dem Modul* (`db.DB_FILE` 51×, `db.yf` 26×, `db._today` 7×). Ein
> naiver Schnitt mit einem `base.py` hätte diese Naht lautlos zerschnitten: die Fachmodule hätten
> eigene Bindungen gehabt, das Patchen wäre wirkungslos geblieben — grüne Tests, die nichts mehr
> prüfen, also genau das Muster, das dieses Repo schon zehnmal getroffen hat. Deshalb liegt das
> Fundament in `__init__.py`. Der Umzug ist per AST erfolgt und rückwärts verifiziert (alle 167
> Top-Level-Namen zeichengleich). **Belegt: 1529 Tests lokal grün + 47 Postgres-Contract-Tests
> gegen die echte Instanz am VPS** — die lokalen Skips wären für einen Eingriff am DB-Seam kein
> ausreichender Nachweis gewesen.

### B2. `stockbot/tgbot/bot.py` — 3.136 Zeilen

Dieselbe Lage, andere Ursache: die Datei enthält Telegram-Handler, Nachrichtenformatierung,
**alle** Scheduler-Jobs und die Verdrahtung des OMS. Sie ist der Ort, an dem in dieser Sitzung
die meisten Verdrahtungen gelandet sind — was zeigt, dass sie als Sammelbecken dient.

**Vorschlag:** die Scheduler-Jobs in ein eigenes Modul, die Nachrichtenformatierung in ein
zweites. Beides sind natürliche Schnitte: die Jobs haben eine gemeinsame Signatur, die
Formatierung hat keine Seiteneffekte. Die Handler bleiben, wo sie sind.

**Aufwand:** ein halber Tag. **Risiko:** geringer als B1, weil die Job-Registrierung an einer
Stelle sitzt und die Tests sie zählen.

> **Freigabe B2:** ✅ **erledigt** (`273ded6`), zweiter Zuschnitt. Der erste Anlauf ist gescheitert und war
> lehrreich: von dreizehn Scheduler-Jobs ließen sich nur **drei** entkoppeln (die übrigen rufen
> Handler auf, die in `bot.py` bleiben), macht 243 Zeilen — die Datei wäre bei 2.893 geblieben, das
> Leseproblem also ungelöst. Zusätzlich brach das Auslagern einen Test, der per `inspect.getsource`
> den Quelltext prüft: einzeln grün, im Gesamtlauf rot. Neuer Zuschnitt: **nur die reine
> Nachrichtenformatierung heraus, plus eine echte Navigationshilfe** (Modul-Docstring mit
> Inhaltsverzeichnis, einheitliche Abschnittsmarker im vorhandenen Stil). Die Datei bleibt groß,
> wird aber navigierbar — bei einem Bruchteil des Risikos. Ergebnis: Modul-Docstring mit den fünf
> Einstiegspunkten, 16 Abschnittsmarker, `stockbot/tgbot/messages.py` für die reine Formatierung,
> und ein ausdrücklicher Warnhinweis auf die `inspect.getsource`-Falle, damit der nächste Anlauf
> nicht dieselbe Runde dreht. Suite unverändert bei 1529.
>
> **Ehrlich zum Ergebnis:** die Datei ist dabei nicht kleiner geworden. Wer sie wirklich
> aufteilen will, muss die Handler mitziehen — das ist ein echtes Refactoring im Hochrisiko-Pfad
> und war für diesen Zweck nicht verhältnismäßig.

### B3. `stockbot/web/webapp.py` — 1.368 Zeilen

Alle Routen der Web-App in einer Datei. Weniger dringend als B1/B2, aber derselbe Effekt.
FastAPI-Router lassen sich sauber nach Bereichen trennen (Auth, Signale, Einstellungen,
Auswertung) — das Muster ist im Projekt bereits vorhanden (`web/api_v1.py` ist ein eigener
Router).

**Aufwand:** ein halber Tag. **Wirkung:** gering bis mittel.

> **Freigabe B3:** ✅ freigegeben und umgesetzt (2026-08-27, Merge `d451609`).
> 1.368 → 391 Zeilen; fünf Fach-Router `webapp_auth.py` (64), `webapp_signals.py` (351),
> `webapp_settings.py` (150), `webapp_reports.py` (406), `webapp_watchlist_lab.py` (133).
> Geschnitten entlang der Abschnittsmarken, die schon in der Datei standen.
>
> Wie bei B1 war der Knackpunkt, dass `webapp.py` **selbst eine Test-Naht** ist: Tests
> ersetzen Namen wie `_alpaca_ready` oder `_broker_will_execute` **auf dem Modul**, und
> Python löst einen unqualifizierten Namen über die Globals des *definierenden* Moduls auf.
> Ein naiver Schnitt hätte diese Nähte lautlos gekappt — grüner Test, wirkungsloser Patch.
> Deshalb bleiben die ersetzten Helfer im Fundament, und die Fach-Router rufen sie bewusst
> als `webapp.<name>` auf. Empirisch gegengeprüft: der Fach-Router löst `webapp` auf dasselbe
> Modulobjekt auf und sieht einen Patch, und es existiert kein Direktimport, der das bräche.
>
> Abgesichert von `tests/test_webapp_router_split.py`: die Routentabelle ist eingefroren
> (Pfad + Methode), **und** jede Route wird durch die echte `dashboard.py`-App gerufen — ein
> Router mit Routen, der nirgends eingehängt ist, fällt damit auf. 1.533 grün.

---

## C — Die Historie

### C1. 143 von 509 Commits tragen einen Agenten als Autor

`git log --pretty=%an | sort | uniq -c`:

```
366  Sebastianschmid1
123  Claude
 20  Sol Worker
```

Das ist auf GitHub sichtbar, in jedem `git blame` und in der Contributor-Liste. Siehe **A3** —
es ist dieselbe Entscheidung, und sie gehört dir.

> **Freigabe C1:** ✅ **entschieden: offen lassen** (zusammen mit A3).

### C2. 86 Commits ohne Nachrichtenkonvention

423 von 509 folgen dem Schema `typ(scope): was & warum` — das sind 83 % und für ein
gewachsenes Projekt ein guter Wert. Die restlichen 86 stammen überwiegend aus der Frühphase.

**Vorschlag: nichts tun.** Die Historie nachträglich zu glätten hieße, 500 Commits neu zu
schreiben, alle Hashes zu ändern und die Verbindung zum VPS-Repository zu brechen — für einen
Schönheitsgewinn, den kein Leser bemerkt. Wer in die Historie schaut, sieht die letzten
zwanzig Commits, und die sind sauber.

Ich führe den Punkt nur auf, damit er geprüft und bewusst abgelehnt ist, statt übersehen.

> **Freigabe C2:** ⬜ offen — nicht freigegeben, Empfehlung bleibt **ablehnen**.

---

## D — Was schon gut ist

Damit du es im Gespräch nennen kannst, ohne danach suchen zu müssen:

- **93 % der Funktionen sind typannotiert.** Das ist der Wert, den ich einem Fachleser zuerst
  zeigen würde.
- **1.522 Tests**, darunter Suiten, die es selten gibt: Replay gegen dieselbe Ereignisfolge,
  gezielte Fehlerinjektion, ein Test, der **strukturell** beweist, dass kein Codepfad am
  Risk-Gate vorbeikommt.
- **Zehn Alembic-Migrationen** mit vollzogenem Backend-Wechsel von SQLite auf Postgres — im
  laufenden Betrieb, mit Rollback-Netz.
- **Die `.gitignore` erklärt ihre Ausnahmen.** Kleinigkeit, aber genau die Sorte Detail, die
  jemand bemerkt, der selbst schon aufgeräumt hat.
- **Die Kommentare erklären das Warum, nicht das Was** — und mehrere von ihnen dokumentieren
  einen echten Vorfall samt Ursache. Das ist selten und liest sich sehr professionell.

Eine Anmerkung zur Sprache: Kommentare und Commit-Nachrichten sind durchgehend deutsch. Für
eine Bewerbung im deutschsprachigen Raum ist das unproblematisch bis sympathisch. Bei
internationalen Zielen wäre es ein Thema — aber eine Übersetzung von 25.000 Zeilen ist
unverhältnismäßig, und halb übersetzt wäre schlechter als konsequent deutsch.

---

## Was ich empfehlen würde, wenn du nur wenig Zeit hast

**A1 + A2.** Zusammen etwa ein Tag, und sie machen den Unterschied zwischen „sieht aus wie ein
Bastelprojekt" und „sieht aus wie Arbeit". Alles unter B ist Feinschliff, der erst zählt, wenn
jemand tatsächlich in den Code steigt — und dorthin kommt er nur über A.

**A3/C1 brauchen keine Arbeit, sondern eine Entscheidung.** Die solltest du treffen, bevor du
den Link weitergibst, nicht danach.
