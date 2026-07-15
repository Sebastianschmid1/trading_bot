#!/usr/bin/env bash
# Idempotentes Setup auf der Strato Ubuntu-VM (als root ausführen, nach dem git clone).
# Empfohlener Klon-Ort: /opt/stockbot
# Ausführen mit:  bash deploy/setup_server.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

if ! id -u stockbot >/dev/null 2>&1; then
    echo "→ System-User stockbot anlegen..."
    useradd -r -s /usr/sbin/nologin -d /opt/stockbot stockbot
fi

echo "→ System-Pakete installieren (git, python venv/pip)..."
apt-get update -qq
apt-get install -y -qq git python3-venv python3-pip

echo "→ Python-venv anlegen und Dependencies installieren..."
install -d -o stockbot -g stockbot "$APP_DIR/data" "$APP_DIR/logs"
chown -R stockbot:stockbot "$APP_DIR"
runuser -u stockbot -- python3 -m venv "$APP_DIR/venv"
runuser -u stockbot -- "$APP_DIR/venv/bin/pip" install --upgrade pip -q
runuser -u stockbot -- "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.lock"

if [ ! -f .env ]; then
    cp deploy/.env.example .env
    # Verschlüsselungs-Key automatisch erzeugen, falls noch Platzhalter drin steht
    KEY="$(./venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    sed -i "s|generierten_schluessel_hier_einfuegen|$KEY|" .env
    echo "→ .env angelegt + ENCRYPTION_KEY generiert."
    echo "  WICHTIG: Jetzt noch TELEGRAM_TOKEN_ENV eintragen:  nano $APP_DIR/.env"
fi

chown stockbot:stockbot .env
chmod 600 .env
# CacheDirectory=stockbot legt diesen Pfad beim Dienststart ebenfalls idempotent an.
install -d -o stockbot -g stockbot -m 0750 /var/cache/stockbot
chown -R stockbot:stockbot "$APP_DIR/venv" "$APP_DIR/data" "$APP_DIR/logs"

echo "→ systemd-Service installieren (Pfad = aktueller Repo-Ordner: $APP_DIR)..."
# Pfade in der Unit auf den tatsächlichen Klon-Ort setzen — egal wie der Ordner heißt.
sed "s#/opt/stockbot#$APP_DIR#g" deploy/stockbot.service > /etc/systemd/system/stockbot.service
systemctl daemon-reload
systemctl enable stockbot
systemctl restart stockbot

# Firewall (falls ufw aktiv): Dashboard-Port 8000 öffnen
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
    ufw allow 8000/tcp || true
    echo "→ Firewall: Port 8000 (Dashboard) geöffnet."
fi

# TLS-Reverse-Proxy (Caddy) einrichten — passiert nur, wenn DOMAIN in der .env gesetzt ist.
echo "→ TLS/Caddy synchronisieren (nur aktiv mit DOMAIN in .env)..."
bash deploy/sync_caddy.sh || echo "  WARN: Caddy-Sync fehlgeschlagen (Deploy läuft weiter)."

echo
echo "✅ Fertig."
echo "   Status:  systemctl status stockbot"
echo "   Logs:    journalctl -u stockbot -f"
echo "   Denk dran: TELEGRAM_TOKEN_ENV in .env eintragen und danach 'systemctl restart stockbot'."
