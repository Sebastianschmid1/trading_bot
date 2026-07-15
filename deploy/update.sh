#!/usr/bin/env bash
# Auf dem SERVER ausführen (per SSH eingeloggt): holt den neuesten Stand und startet neu.
#   git pull -> Dependencies aktualisieren -> Caddy/TLS synchronisieren -> Dienst(e) neu starten
#
# Im Gegensatz zu upload.sh/deploy.sh (laufen lokal und pushen erst) macht dieses Skript NUR
# das Server-seitige Update — praktisch, wenn die Änderungen schon auf GitHub liegen.
#
# Lauf (auf dem Server, im Repo-Verzeichnis):
#   bash deploy/update.sh
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # Repo-Wurzel

echo "→ git pull …"
git pull

echo "→ Dependencies aktualisieren …"
./venv/bin/pip install -q -r requirements.lock || echo "WARN: pip install fehlgeschlagen"

# TLS/Caddy nur synchronisieren, wenn das Skript vorhanden ist (no-op ohne DOMAIN in .env).
[ -f deploy/sync_caddy.sh ] && { bash deploy/sync_caddy.sh || echo "WARN: caddy-sync fehlgeschlagen"; }

echo "→ Dienst(e) neu starten …"
systemctl restart stockbot
# Separater Dashboard-Dienst nur neu starten, wenn er überhaupt läuft (sonst übernimmt der Bot).
systemctl is-active --quiet dashboard && systemctl restart dashboard || true

systemctl status stockbot --no-pager -l | head -n 15
echo "✅ fertig: aktualisiert und neu gestartet."
