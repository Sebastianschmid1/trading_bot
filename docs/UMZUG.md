# Umzug auf einen anderen Rechner

Stand: 2026-08-24, Repo-Stand `d4169a0` (== `origin/main`).

**Der Clone allein reicht nicht.** Vier Dinge leben ausserhalb von Git und muessen von Hand
mit — ohne sie startet der Bot entweder gar nicht oder er startet und kann die in der
Datenbank verschluesselten Broker-Zugaenge nicht mehr lesen.

Die **Produktion ist vom Umzug nicht betroffen**: der Live-Bot laeuft auf dem VPS aus dessen
eigenem Repo-Verzeichnis und hat keine Verbindung zu diesem Arbeitsplatz.

## 1. Was der Clone mitbringt

`git clone https://github.com/Sebastianschmid1/trading_bot.git` holt den kompletten Code, die
Tests, alle Plan- und Doku-Dateien und die Historie (~20 MB). Auch die offenen
`origin/agent/*`-Branches kommen als Remote-Tracking-Branches mit — es geht nichts verloren.

## 2. Was NICHT mitkommt

| Was | Wo | Warum es gebraucht wird |
|---|---|---|
| **`.env`** | Repo-Wurzel, `.gitignore` | **Kritisch**, siehe unten |
| **SSH-Key** | `~/.ssh/id_ed25519` (+ `.pub`) | oeffnet den Deploy-Zugang zum VPS |
| **Git-Remote `vps`** | `.git/config` | ein Clone kennt nur `origin` |
| Laufzeitdaten | `data/bot.db`, `data/lab/`, `logs/` | nur lokale Dev-Spielstaende, entbehrlich |

### Warum `.env` das eine kritische Stueck ist

`core/db.py` baut daraus das Fernet-Schluesselmaterial:

```python
_fernet = Fernet(ENCRYPTION_KEY.encode())
```

Damit sind die **Broker-API-Keys**, die **OAuth-Tokens** und die **Callback-Tokens** in der
Datenbank verschluesselt. Ein *neuer* Schluessel macht diese Spalten dauerhaft unlesbar
(`InvalidToken`) — die Daten sind dann nicht kaputt, aber ohne den alten Schluessel auch nicht
mehr zu oeffnen.

**Die Falle:** `deploy/setup_server.sh` legt eine `.env` **mit frisch generiertem
`ENCRYPTION_KEY`** an, wenn noch keine da ist (`if [ ! -f .env ]` — eine vorhandene wird nicht
angetastet). Auf einem frischen Rechner ist genau das der Fall. Also erst die alte `.env`
uebertragen und dann das Skript — oder es hier gar nicht verwenden, es ist fuer die
Servereinrichtung gedacht, nicht fuer einen Arbeitsplatz.

Die Datei ist 285 Bytes und enthaelt: `ENCRYPTION_KEY`, `TELEGRAM_TOKEN_ENV`,
`OPENROUTER_API_KEY`, `LOCAL_PASSWORD`, `LOCAL_PW`, `SSH_ASKPASS`.

Uebertragen ueber einen Kanal, der keine Kopie behaelt — z. B. direkt von Rechner zu Rechner:

```bash
scp ~/trading_bot/.env  <neuer-rechner>:~/trading_bot/.env
chmod 600 ~/trading_bot/.env      # auf dem Zielrechner
```

Nicht per Chat, Cloud-Ordner oder E-Mail: dort bleibt der Schluessel liegen, mit dem alle
Broker-Zugaenge aufgehen.

## 3. Schritte auf dem Zielrechner

```bash
# 1 — Code
git clone https://github.com/Sebastianschmid1/trading_bot.git
cd trading_bot

# 2 — Python 3.11+ und Umgebung (das alte .venv NICHT kopieren, 777 MB und plattformgebunden)
python3 -m venv .venv && source .venv/bin/activate
pip install -e . && pip install -r requirements-dev.txt

# 3 — .env uebertragen (siehe oben), dann Rechte setzen
chmod 600 .env

# 4 — Deploy-Remote wieder anlegen (kommt beim Clone nicht mit)
git remote add vps root@217.160.103.25:/root/stockbot

# 5 — SSH-Key uebernehmen, damit der Deploy-Zugang funktioniert
#     ~/.ssh/id_ed25519 + .pub vom alten Rechner, dann:
chmod 600 ~/.ssh/id_ed25519
```

### Gegenprobe

```bash
python -m pytest                      # erwartet: ~1461 passed, 29 skipped
ssh root@217.160.103.25 exit          # muss ohne Passwortfrage durchlaufen
git remote -v                         # origin UND vps muessen stehen
```

Laeuft die Suite gruen und oeffnet sich der VPS ohne Passwort, ist der Umzug vollstaendig.

**Zum Testen bleibt SQLite der Default** (`DB_BACKEND=sqlite`). Die Laufzeit verlangt seit W3.1
allerdings Postgres, ausser `ALLOW_SQLITE_RUNTIME=true` ist gesetzt — fuer einen reinen
Entwicklungsrechner ist das der richtige Schalter, fuer die Produktion niemals.

## 4. Was zur Agent-Umgebung gehoert (optional)

Diese Dateien steuern, wie Claude/Codex in diesem Projekt arbeiten. Sie liegen bewusst
ausserhalb des Repos und werden nur gebraucht, wenn der neue Rechner dieselbe Arbeitsweise
erben soll:

- `~/main_projekt/agent-control/` (104 KB) — Rollen, Routing, Policies. `~/.claude/CLAUDE.md`
  bindet sie per `@`-Referenz ein; fehlen sie, laeuft die Datei ins Leere.
- `~/.claude/projects/-home-jms-trading-bot/memory/` (132 KB) — das Projektgedaechtnis
  (Befundhistorie, Deploy-Stand, offene Tore). Ohne diesen Ordner faengt jede Session bei null an.

## 5. Was bewusst zurueckbleibt

- `.pytmp/` (772 MB) und `.venv/` (777 MB) — beide werden neu erzeugt. `.pytmp` ist nur der
  Ausweichpfad fuer Test-Temporaerdateien, weil `/tmp` hier ein RAM-Dateisystem mit Quota ist.
- `.impeccable/` (9,7 MB) — Screenshots vergangener Design-Durchsichten, reines Archiv.
- Die lokalen `worktree-agent-*`-Branches — Reste abgeschlossener Subagenten-Laeufe, alle
  laengst in `main`. Ein Clone legt sie gar nicht erst an.
