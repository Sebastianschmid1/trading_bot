# Manuelle VPS-Migration auf den gehärteten systemd-Dienst

> **Manueller Deploy-Schritt (Tor T1):** Diese Anleitung verändert den produktiven VPS.
> Sie muss von einem Menschen kontrolliert ausgeführt werden; die Migration erfolgt nicht
> automatisch durch Repository-Updates.

Die Anwendung läuft danach als unprivilegierter System-User `stockbot` aus `/opt/stockbot`.
Die PostgreSQL-DSN bleibt unverändert; der Zugriff auf PostgreSQL über `localhost` bleibt
erlaubt, weil die Units absichtlich kein `PrivateNetwork` setzen.

## Migration

1. Aktuellen Stand sichern und die laufenden Dienste stoppen:

   ```bash
   sudo systemctl stop stockbot dashboard 2>/dev/null || true
   sudo cp -a /root/stockbot /root/stockbot.pre-plat008
   ```

2. Den System-User idempotent anlegen und das Zielverzeichnis vorbereiten:

   ```bash
   id -u stockbot >/dev/null 2>&1 || sudo useradd -r -s /usr/sbin/nologin -d /opt/stockbot stockbot
   sudo install -d -o stockbot -g stockbot /opt/stockbot
   ```

3. Repo einschließlich `data/`, `logs/`, `venv/` und `.env` verschieben. `rsync` bewahrt
   dabei den alten Stand bis zur erfolgreichen Verifikation:

   ```bash
   sudo rsync -a /root/stockbot/ /opt/stockbot/
   sudo install -d -o stockbot -g stockbot /opt/stockbot/data /opt/stockbot/logs
   sudo chown -R stockbot:stockbot /opt/stockbot
   sudo chown stockbot:stockbot /opt/stockbot/.env
   sudo chmod 600 /opt/stockbot/.env
   sudo install -d -o stockbot -g stockbot -m 0750 /var/cache/stockbot
   ```

4. Das mitgesicherte venv unter dem neuen Pfad neu erstellen. Seine Konsolen-Skripte
   enthalten absolute Shebang-Pfade und dürfen deshalb nicht aus `/root/stockbot` übernommen
   werden:

   ```bash
   sudo rm -rf /opt/stockbot/venv
   sudo -u stockbot python3 -m venv /opt/stockbot/venv
   sudo -u stockbot /opt/stockbot/venv/bin/pip install -r /opt/stockbot/requirements.txt
   ```

5. Prüfen, dass die vorhandene PostgreSQL-DSN in `/opt/stockbot/.env` weiterhin auf den
   erreichbaren lokalen Dienst zeigt. Keine Zugangsdaten ausgeben.

6. Die alten Dienste deaktivieren, die neuen Units installieren und systemd neu laden:

   ```bash
   sudo systemctl disable --now stockbot dashboard 2>/dev/null || true
   sudo cp /opt/stockbot/deploy/stockbot.service /etc/systemd/system/stockbot.service
   sudo cp /opt/stockbot/deploy/dashboard.service /etc/systemd/system/dashboard.service
   sudo systemctl daemon-reload
   sudo systemctl enable stockbot
   sudo systemctl restart stockbot
   ```

   Das optionale Dashboard nur separat aktivieren, wenn `RUN_DASHBOARD_IN_BOT=false` gilt:

   ```bash
   sudo systemctl enable --now dashboard
   ```

7. Verifizieren:

   ```bash
   sudo systemctl status stockbot --no-pager
   sudo systemctl show stockbot -p User -p Group -p WorkingDirectory -p LimitNOFILE
   sudo systemd-analyze security stockbot.service
   sudo journalctl -u stockbot -n 100 --no-pager
   sudo test "$(stat -c '%U:%G %a' /opt/stockbot/.env)" = "stockbot:stockbot 600"
   sudo -u stockbot test -w /opt/stockbot/data -a -w /opt/stockbot/logs -a -w /var/cache/stockbot
   ```

8. Erst nach erfolgreicher Funktions-, PostgreSQL- und Telegram-Verifikation den alten
   Root-Pfad außer Betrieb nehmen. Die gleichnamige Unit wurde bereits ersetzt; es darf
   keine zusätzliche alte Unit mehr aktiviert sein:

   ```bash
   sudo systemctl list-unit-files | grep -E 'stockbot|dashboard'
   sudo mv /root/stockbot /root/stockbot.retired
   ```

Die Sicherungen erst nach einer angemessenen Beobachtungszeit löschen. Bei einem Rollback
die gehärteten Dienste stoppen, die alte Unit/Anwendung wiederherstellen, `daemon-reload`
ausführen und den alten Dienst gezielt starten.
