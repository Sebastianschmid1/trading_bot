#!/usr/bin/env bash
# Idempotentes Setup der Hochzeits-Foto-Galerie „Amelie & Tobi" (als root ausführen).
# Der Dienst läuft KOMPLETT getrennt von stockbot: eigener Systemuser `wedding`,
# eigene Daten unter /var/lib/wedding, eigene Config unter /etc/wedding.
#
# Ausführen mit:  bash deploy/setup_wedding.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

DATA_DIR="/var/lib/wedding"
CONF_DIR="/etc/wedding"
PORT="8100"

# --- 1) Systemuser --------------------------------------------------------- #
if ! id -u wedding >/dev/null 2>&1; then
    echo "→ System-User wedding anlegen..."
    useradd -r -s /usr/sbin/nologin -d "$DATA_DIR" wedding
fi

# --- 2) Verzeichnisse ------------------------------------------------------ #
echo "→ Verzeichnisse anlegen ($DATA_DIR, $CONF_DIR)..."
install -d -o wedding -g wedding -m 0750 "$DATA_DIR"
install -d -o wedding -g wedding -m 0750 "$DATA_DIR/photos"
install -d -o root -g wedding -m 0750 "$CONF_DIR"

# --- 3) users.json (niemals überschreiben!) -------------------------------- #
if [ ! -f "$CONF_DIR/users.json" ]; then
    echo "→ users.json aus dem Repo übernehmen..."
    install -o root -g wedding -m 0640 deploy/wedding-users.json "$CONF_DIR/users.json"
else
    echo "→ $CONF_DIR/users.json existiert bereits — bleibt unverändert."
    chown root:wedding "$CONF_DIR/users.json"
    chmod 0640 "$CONF_DIR/users.json"
fi

# --- 4) Session-Secret ----------------------------------------------------- #
if [ ! -f "$CONF_DIR/secret" ]; then
    echo "→ Session-Secret erzeugen..."
    openssl rand -hex 32 > "$CONF_DIR/secret"
fi
chown root:wedding "$CONF_DIR/secret"
chmod 0640 "$CONF_DIR/secret"

# --- 5) Konfiguration ------------------------------------------------------ #
# DOMAIN aus der .env des Bots lesen (gleiche Logik wie deploy/sync_caddy.sh).
DOMAIN="$(sed -nE 's/^[[:space:]]*DOMAIN[[:space:]]*=[[:space:]]*"?([^"#]*)"?[[:space:]]*$/\1/p' "$APP_DIR/.env" 2>/dev/null | tail -n1 | xargs 2>/dev/null || true)"

if [ -n "${DOMAIN}" ]; then
    echo "→ DOMAIN=${DOMAIN} → Galerie läuft hinter Caddy unter /hochzeit."
    cat > "$CONF_DIR/wedding.env" <<EOF
# Von deploy/setup_wedding.sh erzeugt — hinter Caddy (TLS) unter /hochzeit.
WEDDING_BIND=127.0.0.1
WEDDING_PORT=${PORT}
WEDDING_ROOT_PATH=/hochzeit
WEDDING_COOKIE_SECURE=true
WEDDING_DATA_DIR=${DATA_DIR}
WEDDING_USERS_FILE=${CONF_DIR}/users.json
WEDDING_SECRET_FILE=${CONF_DIR}/secret
WEDDING_MAX_BYTES=31457280
EOF
else
    echo "→ Keine DOMAIN in .env → Galerie direkt auf Port ${PORT} (ohne TLS)."
    cat > "$CONF_DIR/wedding.env" <<EOF
# Von deploy/setup_wedding.sh erzeugt — Direktbetrieb ohne Reverse-Proxy.
WEDDING_BIND=0.0.0.0
WEDDING_PORT=${PORT}
WEDDING_ROOT_PATH=
WEDDING_COOKIE_SECURE=false
WEDDING_DATA_DIR=${DATA_DIR}
WEDDING_USERS_FILE=${CONF_DIR}/users.json
WEDDING_SECRET_FILE=${CONF_DIR}/secret
WEDDING_MAX_BYTES=31457280
EOF
fi
chown root:wedding "$CONF_DIR/wedding.env"
chmod 0640 "$CONF_DIR/wedding.env"

# --- 6) systemd-Unit ------------------------------------------------------- #
echo "→ systemd-Service installieren (Pfad = aktueller Repo-Ordner: $APP_DIR)..."
sed "s#/opt/stockbot#$APP_DIR#g" deploy/wedding.service > /etc/systemd/system/wedding.service
systemctl daemon-reload
systemctl enable wedding >/dev/null 2>&1 || true
systemctl restart wedding

# --- 7) Netzwerk / Reverse-Proxy ------------------------------------------- #
if [ -n "${DOMAIN}" ]; then
    echo "→ Caddy synchronisieren (Hochzeits-Block ist im Template enthalten)..."
    bash deploy/sync_caddy.sh || echo "  WARN: Caddy-Sync fehlgeschlagen (Setup läuft weiter)."
    URL="https://${DOMAIN}/hochzeit/"
else
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
        ufw allow ${PORT}/tcp >/dev/null 2>&1 || true
        echo "→ Firewall: Port ${PORT} geöffnet."
    fi
    SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    URL="http://${SERVER_IP:-<server-ip>}:${PORT}/"
fi

echo
systemctl status wedding --no-pager --lines=5 || true
echo
echo "✅ Fertig — die Hochzeits-Galerie läuft."
echo "   URL:      ${URL}"
echo "   Status:   systemctl status wedding"
echo "   Logs:     journalctl -u wedding -f"
echo "   Fotos:    ${DATA_DIR}/photos"
echo "   Passwort ändern:  ${APP_DIR}/venv/bin/python ${APP_DIR}/wedding/manage.py --file ${CONF_DIR}/users.json set-password amelie"
