#!/usr/bin/env bash
# Idempotenter TLS-Reverse-Proxy-Sync (Caddy + Let's Encrypt).
#
# Wird sowohl von deploy/setup_server.sh (einmaliges Setup) als auch von jedem
# upload.ps1-Deploy aufgerufen. Steuert sich vollständig über DOMAIN in der .env:
#   - DOMAIN leer/fehlt  → es passiert NICHTS (kein TLS); der Token-Login über http
#                          funktioniert weiterhin. So bricht der Deploy nie an Caddy.
#   - DOMAIN gesetzt     → Caddy installieren (falls nötig), /etc/caddy/Caddyfile aus
#                          deploy/Caddyfile mit der Domain erzeugen, .env auf HTTPS/secure
#                          stellen, Caddy (neu) laden und Port 8000 nach außen schließen.
#
# Als root ausführen. Schlägt nie hart fehl (Deploy soll weiterlaufen).
set -u

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 0   # Projekt-Root
ENV_FILE=".env"

DOMAIN="$(sed -nE 's/^[[:space:]]*DOMAIN[[:space:]]*=[[:space:]]*"?([^"#]*)"?[[:space:]]*$/\1/p' "$ENV_FILE" 2>/dev/null | tail -n1 | xargs 2>/dev/null || true)"

if [ -z "${DOMAIN}" ]; then
    echo "sync_caddy: keine DOMAIN in .env → TLS/Caddy übersprungen (http-Token-Login bleibt aktiv)."
    exit 0
fi
echo "sync_caddy: DOMAIN=${DOMAIN}"

# 1) Caddy installieren, falls nicht vorhanden (offizielles Caddy-Repo).
if ! command -v caddy >/dev/null 2>&1; then
    echo "sync_caddy: installiere Caddy ..."
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl gnupg >/dev/null 2>&1 || true
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    apt-get install -y -qq caddy
fi

# 2) /etc/caddy/Caddyfile aus dem Repo-Template mit der echten Domain erzeugen.
mkdir -p /etc/caddy
sed "s/DEINE-DOMAIN/${DOMAIN}/g" deploy/Caddyfile > /etc/caddy/Caddyfile
echo "sync_caddy: /etc/caddy/Caddyfile geschrieben."

# 3) .env auf HTTPS/secure stellen (idempotent: vorhandene Keys ersetzen, sonst anhängen).
set_env() {  # $1=key  $2=value
    if grep -qE "^[[:space:]]*$1[[:space:]]*=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^[[:space:]]*$1[[:space:]]*=.*|$1=$2|" "$ENV_FILE"
    else
        echo "$1=$2" >> "$ENV_FILE"
    fi
}
set_env DASHBOARD_BASE_URL "https://${DOMAIN}"
set_env COOKIE_SECURE "true"
set_env HSTS_ENABLED "true"
echo "sync_caddy: .env → DASHBOARD_BASE_URL=https://${DOMAIN}, COOKIE_SECURE=true, HSTS_ENABLED=true."

# 4) Caddy aktivieren & (neu) laden — nur bei gültiger Konfiguration.
systemctl enable caddy >/dev/null 2>&1 || true
if caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
    systemctl reload caddy 2>/dev/null || systemctl restart caddy
    echo "sync_caddy: Caddy neu geladen."
else
    echo "sync_caddy: WARN — Caddyfile-Validierung fehlgeschlagen, KEIN Reload (alte Config bleibt aktiv)."
fi

# 5) Port 8000 nach außen schließen (Bot ist nur noch über Caddy/HTTPS erreichbar).
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw delete allow 8000/tcp  >/dev/null 2>&1 || true
    ufw delete allow 8000      >/dev/null 2>&1 || true
    ufw allow 80/tcp           >/dev/null 2>&1 || true
    ufw allow 443/tcp          >/dev/null 2>&1 || true
    echo "sync_caddy: Firewall → 80/443 offen, 8000 geschlossen."
fi
