#!/usr/bin/env bash
# Schnelles Update-Deployment: lokal committen/pushen, dann dieses Skript ausführen.
# Holt den neuesten Stand auf den Pi und startet den Service neu.
#
# Anpassen: PI_HOST (User@Hostname/IP des Pi) und APP_DIR (Pfad zum Repo auf dem Pi)
set -euo pipefail

PI_HOST="pi@auto"
APP_DIR="~/stockbot"

ssh "$PI_HOST" "cd $APP_DIR && git pull && ./venv/bin/pip install -q -r requirements.txt && sudo systemctl restart stockbot && sudo systemctl status stockbot --no-pager -l"
