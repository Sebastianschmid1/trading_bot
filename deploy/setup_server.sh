#!/usr/bin/env bash
# Einmaliges Setup auf der Strato Ubuntu-VM (als root ausführen, nach dem git clone).
# Ausführen mit:  bash deploy/setup_server.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

echo "→ System-Pakete installieren (git, python venv/pip)..."
apt-get update -qq
apt-get install -y -qq git python3-venv python3-pip

echo "→ Python-venv anlegen und Dependencies installieren..."
python3 -m venv venv
./venv/bin/pip install --upgrade pip -q
./venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
    cp deploy/.env.example .env
    # Verschlüsselungs-Key automatisch erzeugen, falls noch Platzhalter drin steht
    KEY="$(./venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    sed -i "s|generierten_schluessel_hier_einfuegen|$KEY|" .env
    echo "→ .env angelegt + ENCRYPTION_KEY generiert."
    echo "  WICHTIG: Jetzt noch TELEGRAM_TOKEN_ENV eintragen:  nano $APP_DIR/.env"
fi

echo "→ systemd-Service installieren (Dashboard läuft im Bot-Prozess mit)..."
cp deploy/stockbot.service /etc/systemd/system/stockbot.service
systemctl daemon-reload
systemctl enable stockbot
systemctl restart stockbot

# Firewall (falls ufw aktiv): Dashboard-Port 8000 öffnen
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
    ufw allow 8000/tcp || true
    echo "→ Firewall: Port 8000 (Dashboard) geöffnet."
fi

echo
echo "✅ Fertig."
echo "   Status:  systemctl status stockbot"
echo "   Logs:    journalctl -u stockbot -f"
echo "   Denk dran: TELEGRAM_TOKEN_ENV in .env eintragen und danach 'systemctl restart stockbot'."
