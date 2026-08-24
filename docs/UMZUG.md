# Umzug des Arbeitsplatzes auf einen anderen Rechner

Stand: 2026-08-24, Repo-Stand `aba1886` (== `origin/main`).

Es zieht **nur dieser Entwicklungsrechner** um. Die Produktion bleibt, wo sie ist: der Live-Bot
laeuft auf dem VPS aus `/root/stockbot` mit eigener Postgres-Datenbank und eigenen Secrets. Am
Bot aendert der Umzug nichts — er laeuft waehrenddessen weiter.

Der Clone allein reicht trotzdem nicht. Drei Dinge leben ausserhalb von Git.

## 1. Der SSH-Key — das kritische Stueck

`~/.ssh/id_ed25519` (+ `.pub`) ist der **einzige Weg von hier in die Produktion**. Der Deploy
laeuft ueber den Git-Remote `vps` und einen anschliessenden Merge per SSH; **der VPS kann
seinerseits nicht von GitHub fetchen** (kein Deploy-Key hinterlegt, `Permission denied
(publickey)`). Ohne diesen Schluessel auf dem neuen Rechner gibt es also keinen Pfad mehr, um
Aenderungen in den Live-Betrieb zu bringen — GitHub allein hilft dabei nicht.

```bash
# vom alten Rechner, auf dem neuen dann:
chmod 600 ~/.ssh/id_ed25519
ssh root@217.160.103.25 exit      # muss ohne Passwortfrage durchlaufen
```

Geht der Schluessel verloren, ist das kein Datenverlust, aber ein Zugangsverlust: dann muss
ueber die Hoster-Konsole ein neuer oeffentlicher Schluessel in `/root/.ssh/authorized_keys`
eingetragen werden.

## 2. Der Git-Remote `vps`

Ein Clone kennt nur `origin`. Der Deploy-Remote muss von Hand zurueck:

```bash
git remote add vps root@217.160.103.25:/root/stockbot
```

**Merke zum Deploy-Weg:** Der VPS-`main` ist von GitHub-`main` bewusst divergiert (er traegt
zusaetzlich die Hochzeits-Galerie, ein getrenntes Projekt im selben Verzeichnis). Deploy heisst
deshalb: Branch nach `vps` pushen, dann per SSH mit `git merge --no-ff` in den VPS-`main` —
**kein ff-merge**.

## 3. Die `.env`

Bequemlichkeit, kein Datenzugang: die Werte hier sind **reine Entwicklungswerte**, nachgeprueft
am 2026-08-24. Der lokale `ENCRYPTION_KEY` ist ein anderer als der der Produktion, der
`TELEGRAM_TOKEN_ENV` gehoert einem eigenen Test-Bot, und `data/bot.db` enthaelt 0 Nutzer und 0
hinterlegte Broker-Keys. Der Umzug scheitert also nicht an dieser Datei — neu erzeugen geht
auch. Mitnehmen spart Tipparbeit und den `OPENROUTER_API_KEY` (der kostet echtes Geld).

285 Bytes, enthaelt: `ENCRYPTION_KEY`, `TELEGRAM_TOKEN_ENV`, `OPENROUTER_API_KEY`,
`LOCAL_PASSWORD`, `LOCAL_PW`, `SSH_ASKPASS`.

```bash
scp ~/trading_bot/.env  <neuer-rechner>:~/trading_bot/.env
chmod 600 .env          # auf dem Zielrechner
```

> **Wofuer der `ENCRYPTION_KEY` in der Produktion steht** — nicht fuer diesen Umzug relevant,
> aber gut zu wissen: `core/db.py` baut daraus `Fernet(ENCRYPTION_KEY.encode())`. Damit sind auf
> dem VPS die Broker-API-Keys, OAuth- und Callback-Tokens verschluesselt. Wer dort je neu
> aufsetzt, muss den **bestehenden** Schluessel uebernehmen — `deploy/setup_server.sh` generiert
> bei fehlender `.env` einen frischen, und danach sind diese Spalten dauerhaft unlesbar
> (`InvalidToken`). Siehe `docs/BACKUP_RESTORE.md`.

## 4. Schritte auf dem Zielrechner

```bash
git clone https://github.com/Sebastianschmid1/trading_bot.git
cd trading_bot

# Python 3.11+; das alte .venv NICHT kopieren (777 MB, plattformgebunden)
python3 -m venv .venv && source .venv/bin/activate
pip install -e . && pip install -r requirements-dev.txt

git remote add vps root@217.160.103.25:/root/stockbot
# .env und ~/.ssh/id_ed25519 uebertragen, beide auf chmod 600
```

### Gegenprobe

```bash
python -m pytest                 # erwartet: ~1461 passed, 29 skipped
ssh root@217.160.103.25 exit     # ohne Passwortfrage
git remote -v                    # origin UND vps
```

Zum Entwickeln bleibt SQLite der Default (`DB_BACKEND=sqlite`); die Laufzeit verlangt seit W3.1
Postgres, ausser `ALLOW_SQLITE_RUNTIME=true` ist gesetzt. Fuer einen Arbeitsplatz ist das der
richtige Schalter, fuer die Produktion niemals.

## 5. Optional: die Agent-Umgebung

Nur noetig, wenn der neue Rechner dieselbe Arbeitsweise mit Claude/Codex erben soll:

- `~/main_projekt/agent-control/` (104 KB) — Rollen, Routing, Policies. `~/.claude/CLAUDE.md`
  bindet sie per `@`-Referenz ein; fehlen sie, laeuft die Datei ins Leere.
- `~/.claude/projects/-home-jms-trading-bot/memory/` (132 KB) — das Projektgedaechtnis
  (Befundhistorie, Deploy-Stand, offene Tore). Ohne diesen Ordner faengt jede Session bei null an.

## 6. Was bewusst zurueckbleibt

- `.pytmp/` (772 MB) und `.venv/` (777 MB) — werden neu erzeugt. `.pytmp` ist nur der
  Ausweichpfad fuer Test-Temporaerdateien, weil `/tmp` hier ein RAM-Dateisystem mit Quota ist.
- `.impeccable/` (9,7 MB) — Screenshots vergangener Design-Durchsichten, reines Archiv.
- `data/bot.db`, `data/lab/`, `logs/` — lokale Wegwerf-Spielstaende.
- Die lokalen `worktree-agent-*`-Branches — Reste abgeschlossener Subagenten-Laeufe, alle
  laengst in `main`. Ein Clone legt sie gar nicht erst an.
