#!/usr/bin/env bash
# Einmaliges Setup auf dem Raspberry Pi.
# Ausführen mit: bash deploy/setup_pi.sh   (nachdem das Repo geklont wurde)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

echo "→ Lege Python-venv an und installiere Dependencies..."
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

if [ ! -f .env ]; then
    cp deploy/.env.example .env
    echo "→ .env angelegt. Bitte jetzt befüllen: nano $APP_DIR/.env"
fi

echo "→ Installiere systemd-Service..."
sudo cp deploy/stockbot.service /etc/systemd/system/stockbot.service
sudo systemctl daemon-reload
sudo systemctl enable stockbot
sudo systemctl restart stockbot

echo "✅ Fertig. Status prüfen mit: sudo systemctl status stockbot"
echo "   Logs ansehen mit:        journalctl -u stockbot -f"
