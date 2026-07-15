#!/usr/bin/env bash
# Auf dem SERVER ausführen (per SSH eingeloggt): holt den neuesten Stand und startet neu.
#   git pull -> Dependencies aktualisieren -> Caddy/TLS synchronisieren -> Dienst(e) neu starten
#
# Im Gegensatz zu upload.sh/deploy.sh (laufen lokal und pushen erst) macht dieses Skript NUR
# das Server-seitige Update — praktisch, wenn die Änderungen schon auf GitHub liegen.
#
# Lauf (auf dem Server, im Repo-Verzeichnis):
#   sudo bash deploy/update.sh
# git pull läuft als root, weil der bestehende GitHub-Deploy-Key unter /root liegt. Danach
# wird die Repo-Ownership wiederhergestellt; pip läuft im Kontext des Service-Users.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

if [ "$(id -u)" -ne 0 ]; then
    echo "FEHLER: deploy/update.sh muss als root laufen." >&2
    exit 1
fi

echo "→ git pull …"
git -c safe.directory="$APP_DIR" pull
chown -R stockbot:stockbot "$APP_DIR"

echo "→ Dependencies als stockbot aktualisieren …"
runuser -u stockbot -- "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.lock" || echo "WARN: pip install fehlgeschlagen"

# TLS/Caddy nur synchronisieren, wenn das Skript vorhanden ist (no-op ohne DOMAIN in .env).
[ -f deploy/sync_caddy.sh ] && { bash deploy/sync_caddy.sh || echo "WARN: caddy-sync fehlgeschlagen"; }

echo "→ Dienst(e) neu starten …"
systemctl restart stockbot
# Separater Dashboard-Dienst nur neu starten, wenn er überhaupt läuft (sonst übernimmt der Bot).
systemctl is-active --quiet dashboard && systemctl restart dashboard || true

systemctl status stockbot --no-pager -l | head -n 15
echo "✅ fertig: aktualisiert und neu gestartet."
