#!/usr/bin/env bash
# Schnelles Update-Deployment auf die Strato Ubuntu-VM:
# lokal committen/pushen, dann dieses Skript ausführen — holt den neuesten Stand
# auf den Server, aktualisiert Dependencies und startet den Dienst neu.
#
# Anpassen: SERVER_HOST (root@DEINE-SERVER-IP) und APP_DIR (Repo-Pfad auf dem Server).
set -euo pipefail

SERVER_HOST="root@DEINE-SERVER-IP"
APP_DIR="/root/trading_bot"

ssh "$SERVER_HOST" "cd $APP_DIR && git pull && ./venv/bin/pip install -q -r requirements.txt && systemctl restart stockbot && systemctl status stockbot --no-pager -l"
