# Runbook — Betrieb, Störung, Eskalation

Antwort auf Go/No-Go-Kriterium 3.3 (`docs/GO_NO_GO.md`): *wer schaltet den Kill-Switch, wie
wird eskaliert*. Bewusst knapp — es soll im Störungsfall lesbar sein, nicht vollständig.

**Betreiber ist eine Einzelperson.** Es gibt keine Rufbereitschaft und keine zweite Instanz;
„Eskalation" heißt hier: der Betreiber wird erreicht und entscheidet. Genau deshalb muss der
sichere Zustand ohne Entscheidung eintreten können — dafür ist der Kill-Switch da.

## Der sichere Zustand zuerst

Im Zweifel **erst stoppen, dann verstehen**. Der Kill-Switch blockiert neue Positionen und
lässt Schutz-Exits (Stop-Loss, Take-Profit) weiter zu — er sperrt also den Einstieg, nicht
den Ausstieg. Das ist der richtige Reflex bei jedem unklaren Befund.

| Weg | Kommando / Ort | Wirkung |
|---|---|---|
| Telegram | `/killswitch on <Grund>` | global, sofort, überlebt den Prozessneustart |
| Telegram | `/killswitch status` | aktueller Zustand inkl. Grund und Auslöser |
| Telegram | `/killswitch off` | Aufhebung (bewusst nur manuell) |
| Web | Kill-Switch-Bedienung im Dashboard, sichtbar für den Admin (`ADMIN_CHAT_ID`) | dasselbe, mit Rückfrage-Dialog |

Der Zustand liegt in der Datenbank, nicht im Prozessspeicher (W1.3) — ein Neustart hebt ihn
**nicht** auf. Der Statuschip in der Appbar zeigt ihn auf jeder Seite an.

**Live-Handel ist unabhängig davon hart gesperrt** (TSAFE-001). Der Kill-Switch ist die
Bremse für den Paper-/Broker-Pfad, nicht der Live-Schutz.

## Wenn ein einzelner Nutzer betroffen ist

Der Nutzer kann seinen Brokerzugang jederzeit selbst widerrufen — Web: Einstellungen →
*Verbindung entfernen*; Telegram: `/disconnectalpaca`. Danach führt der Bot für ihn keine
Orders mehr aus. Das ist der schnellste Weg, wenn nur ein Konto auffällig ist.

## Dienst und Diagnose

Produktion läuft auf `217.160.103.25` in `/root/stockbot`, Dienst `stockbot`:

```console
systemctl status stockbot
journalctl -u stockbot -f
journalctl -u stockbot --since "1 hour ago" | grep -iE "error|kritisch|traceback"
systemctl restart stockbot
```

Python der Produktion ist `/root/stockbot/venv/bin/python` (nicht das System-Python — dort
fehlen die Abhängigkeiten). Für einen Read-only-Blick in die Daten:

```console
cd /root/stockbot && set -a && . .env && set +a
PYTHONPATH=/root/stockbot venv/bin/python -c "…"
```

Die Postgres-Instanz läuft im Container `stockbot-postgres-postgres-1`.

**Nach jedem Neustart prüfen:** Dienst aktiv, alle Scheduler-Jobs registriert, keine
Warnungen im Journal, `/api/v1/health` liefert 200 mit `x-trace-id`.

## Wenn Daten betroffen sind

Backups laufen automatisch (`pg-backup.timer`), sind mit `age` verschlüsselt und
restore-verifiziert. Ablauf und die genauen Kommandos stehen in
[BACKUP_RESTORE.md](BACKUP_RESTORE.md) — hier bewusst nicht dupliziert, damit es nur eine
Quelle gibt. **Vor jedem Restore in die Produktionsdatenbank: Kill-Switch an.**

## Eskalationsstufen

| Stufe | Auslöser | Handlung |
|---|---|---|
| 1 | Auffälliger Einzeltrade, unklare Ablehnung | Journal lesen, Reconciliation prüfen. Kein Eingriff nötig, wenn erklärbar. |
| 2 | Wiederholte Fehler, Abweichung zwischen Bot- und Broker-Sicht | **Kill-Switch an**, Ursache klären, danach bewusst aufheben. |
| 3 | Unerwartete Order, fremde Position, Verdacht auf Kompromittierung | **Kill-Switch an**, betroffene Broker-Verbindung trennen, Dashboard-Token rotieren, Telegram-Bot-Token beim BotFather neu setzen. |
| 4 | Datenverlust oder -korruption | **Kill-Switch an**, Dienst stoppen, Restore nach `BACKUP_RESTORE.md`, erst danach neu starten. |

## Was ausdrücklich **nicht** im Störungsfall passiert

- Kein Deploy „schnell zwischendurch". Ein Deploy ist ein eigenes Gate mit Backup, Merge und
  Smoke-Test (siehe `CLAUDE.md`) — im Störungsfall ist der Kill-Switch das Mittel, nicht ein
  neuer Stand.
- Kein Abschalten von Guards, Rate-Limits oder Risk-Checks, „um zu sehen ob es dann geht".
- Kein `ff-merge` auf dem VPS: dessen `main` trägt bewusst zusätzliche, projektfremde Commits
  (Hochzeits-Galerie). Deploy läuft immer über `git merge --no-ff` eines gepushten Branches.
