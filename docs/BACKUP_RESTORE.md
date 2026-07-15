# PostgreSQL-Backup und Restore

`scripts/pg_backup.sh` erzeugt mit `pg_dump -Fc` einen konsistenten Custom-Dump und
schreibt ihn ausschliesslich verschluesselt nach `/var/backups/stockbot`. Standardmaessig
laeuft `pg_dump` im Container `postgres`; `PG_BACKUP_MODE=direct` nutzt den lokalen Client
mit `POSTGRES_DSN` (SQLAlchemy-Suffix `+psycopg2` wird entfernt) oder den ueblichen
`PGHOST`/`PGPORT`/`PGUSER`/`PGDATABASE`-Variablen.

## Voraussetzungen und Betrieb

- Bevorzugt: `age` installieren und `AGE_RECIPIENTS` mit einem oder mehreren, durch
  Leerzeichen getrennten Public-Key-Recipients setzen. Alternativ: `gpg` installieren und
  `GPG_RECIPIENT` setzen. Ohne nutzbaren Recipient bricht das Skript ab.
- `BACKUP_DIR`, `PG_CONTAINER`, `PGUSER` und `PGDATABASE` sind bei Bedarf anpassbar.
- Der Backup-User braucht Schreibrechte auf `BACKUP_DIR` und im Docker-Modus Zugriff auf
  den Docker-Daemon. Backup-Dateien erhalten Modus `0600`.
- Retention: neuestes Backup je der letzten 7 belegten Tage plus neuestes Backup je der
  letzten 4 belegten ISO-Wochen. Unbekannte Dateinamen werden nicht angefasst.

Beim Deploy muss ein Mensch den age-Recipient (oder GPG-Recipient) in der geschuetzten
Environment-Datei hinterlegen, `/opt/stockbot` und die Rechte fuer den kuenftigen User
`stockbot` einrichten, die Units nach `/etc/systemd/system/` kopieren und aktivieren:

```console
sudo systemctl daemon-reload
sudo systemctl enable --now pg-backup.timer
systemctl list-timers pg-backup.timer
```

## Restore regelmaessig pruefen

Der Test restauriert nur in einen neuen, nicht publizierten Wegwerf-Container, vergleicht
alle Tabellen-Zeilenzahlen mit `PG_CONTAINER` und entfernt den Container auch bei Fehlern:

```console
AGE_IDENTITY=/sicher/id_ed25519 \
BACKUP_FILE=/var/backups/stockbot/stockbot-20260715-033000.dump.age \
PG_CONTAINER=postgres ./scripts/pg_restore_test.sh
```

Fuer GPG-Dateien wird der lokale Keyring genutzt; optional kann `GPG_HOMEDIR` gesetzt
werden. `RESTORE_IMAGE`, `RESTORE_CONTAINER`, `RESTORE_DATABASE`, `PGUSER` und
`PGDATABASE` sind ebenfalls konfigurierbar. Ein erfolgreicher Lauf endet mit `OK`, jede
Abweichung mit `FAIL`. Der Vergleich ist eine Momentaufnahme; waehrend des Tests sollten
keine Schreibvorgaenge auf der Quelle stattfinden.

## Restore im Ernstfall

Zuerst eine neue, leere Zieldatenbank bereitstellen und niemals ungeprueft die laufende
Produktionsdatenbank ueberschreiben. Dann entschluesseln und direkt an `pg_restore` leiten:

```console
age --decrypt -i /sicher/id_ed25519 backup.dump.age \
  | pg_restore --no-owner --no-privileges --dbname="$RESTORE_DSN"
```

Bei GPG ersetzt `gpg --decrypt backup.dump.gpg` den age-Befehl. Anschliessend Schema,
Zeilenzahlen und Anwendung gegen das neue Ziel pruefen; erst danach kontrolliert umschalten.
