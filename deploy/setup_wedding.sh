#!/usr/bin/env bash
# Idempotentes Setup der Hochzeits-Foto-Galerie „Amelie & Tobi" (als root ausführen).
# Der Dienst läuft KOMPLETT getrennt von stockbot: eigener Systemuser `wedding`,
# eigene Daten unter /var/lib/wedding, eigene Config unter /etc/wedding.
#
# Liegt das Repo an einem Ort, den der unprivilegierte User `wedding` nicht lesen kann
# (z. B. /root, 0700, oder ein Home-Verzeichnis), spiegelt das Skript den App-Code
# automatisch nach /opt/wedding und legt dort ein eigenes Mini-venv an.
#
# Ausführen mit:  bash deploy/setup_wedding.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

DATA_DIR="/var/lib/wedding"
CONF_DIR="/etc/wedding"
OPT_DIR="/opt/wedding"
PORT="8100"

# --- 1) Systemuser --------------------------------------------------------- #
if ! id -u wedding >/dev/null 2>&1; then
    echo "→ System-User wedding anlegen..."
    useradd -r -s /usr/sbin/nologin -d "$DATA_DIR" wedding
fi

# --- 2) Code-Standort bestimmen (Direktbetrieb oder /opt-Fallback) --------- #
# Der Dienst läuft als `wedding`. Kommt der User an das Repo und an dessen venv heran,
# läuft er direkt daraus. Sonst → Kopie nach $OPT_DIR mit eigenem venv.
FALLBACK=0
if runuser -u wedding -- test -r "$APP_DIR/wedding/run.py" \
   && runuser -u wedding -- test -x "$APP_DIR/venv/bin/python"; then
    CODE_DIR="$APP_DIR"
    WEDDING_PY="$APP_DIR/venv/bin/python"
    echo "→ Direktbetrieb aus dem Repo: $CODE_DIR"
else
    FALLBACK=1
    echo "→ $APP_DIR ist für den User wedding nicht nutzbar (Rechte/venv fehlen)."
    echo "  → FALLBACK: App-Code wird nach $OPT_DIR/app gespiegelt."
    install -d "$OPT_DIR" "$OPT_DIR/app"
    # Idempotent und ohne rsync-Abhängigkeit; das Paket-Layout bleibt erhalten, damit
    # der Import-String "wedding.app:app" mit WorkingDirectory=$OPT_DIR/app aufgeht.
    rm -rf "$OPT_DIR/app/wedding"
    cp -a "$APP_DIR/wedding" "$OPT_DIR/app/"
    # Der Dienst braucht nur Lesen/Betreten — unabhängig von der umask beim Klonen.
    chmod -R a+rX "$OPT_DIR/app"

    if [ ! -x "$OPT_DIR/venv/bin/python" ]; then
        echo "  → eigenes venv anlegen ($OPT_DIR/venv)..."
        python3 -m venv "$OPT_DIR/venv"
    fi
    # Beim Re-Run ein schneller No-op; ein Fehlschlag darf das Setup nicht abbrechen.
    "$OPT_DIR/venv/bin/pip" install -q --disable-pip-version-check \
        fastapi "uvicorn[standard]" jinja2 python-multipart pillow \
        || echo "  WARN: pip install fehlgeschlagen (weiter mit vorhandenem venv)"

    CODE_DIR="$OPT_DIR/app"
    WEDDING_PY="$OPT_DIR/venv/bin/python"
    echo "  → Code: $CODE_DIR · Python: $WEDDING_PY"
    echo "  → Nach jedem 'git pull' erneut 'bash deploy/setup_wedding.sh' ausführen,"
    echo "    damit die Kopie unter $CODE_DIR aktualisiert wird."
fi

# --- 3) Verzeichnisse ------------------------------------------------------ #
echo "→ Verzeichnisse anlegen ($DATA_DIR, $CONF_DIR)..."
install -d -o wedding -g wedding -m 0750 "$DATA_DIR"
install -d -o wedding -g wedding -m 0750 "$DATA_DIR/photos"
install -d -o root -g wedding -m 0750 "$CONF_DIR"

# --- 4) users.json (bestehende Einträge NIE überschreiben) ----------------- #
if [ ! -f "$CONF_DIR/users.json" ]; then
    echo "→ users.json aus dem Repo übernehmen..."
    install -o root -g wedding -m 0640 deploy/wedding-users.json "$CONF_DIR/users.json"
else
    # Datei existiert (typisch nach dem ersten Deploy): nur *fehlende* Benutzer ergänzen.
    # Bestehende Einträge inkl. der auf dem Server gesetzten Passwörter bleiben unberührt.
    echo "→ $CONF_DIR/users.json existiert — fehlende Benutzer ergänzen..."
    ( cd "$CODE_DIR" && "$WEDDING_PY" "$CODE_DIR/wedding/manage.py" \
        --file "$CONF_DIR/users.json" seed "$APP_DIR/deploy/wedding-users.json" ) \
        || echo "  WARN: seed fehlgeschlagen — users.json bleibt unverändert."
fi
chown root:wedding "$CONF_DIR/users.json"
chmod 0640 "$CONF_DIR/users.json"

# --- 5) Session-Secret ----------------------------------------------------- #
if [ ! -f "$CONF_DIR/secret" ]; then
    echo "→ Session-Secret erzeugen..."
    openssl rand -hex 32 > "$CONF_DIR/secret"
fi
chown root:wedding "$CONF_DIR/secret"
chmod 0640 "$CONF_DIR/secret"

# --- 6) Konfiguration ------------------------------------------------------ #
# Zwei mögliche Reverse-Proxy-Wege (beide über die .env des Bots gesteuert):
#   WEDDING_DOMAIN → eigene TLS-Domain NUR für die Galerie (stockbot bleibt unberührt).
#   DOMAIN         → gemeinsamer Caddy mit stockbot, Galerie unter /hochzeit.
# WEDDING_DOMAIN hat Vorrang. Ohne beides: Direktbetrieb auf Port 8100 (kein TLS).
WEDDING_DOMAIN="$(sed -nE 's/^[[:space:]]*WEDDING_DOMAIN[[:space:]]*=[[:space:]]*"?([^"#]*)"?[[:space:]]*$/\1/p' "$APP_DIR/.env" 2>/dev/null | tail -n1 | xargs 2>/dev/null || true)"
DOMAIN="$(sed -nE 's/^[[:space:]]*DOMAIN[[:space:]]*=[[:space:]]*"?([^"#]*)"?[[:space:]]*$/\1/p' "$APP_DIR/.env" 2>/dev/null | tail -n1 | xargs 2>/dev/null || true)"

if [ -n "${WEDDING_DOMAIN}" ]; then
    echo "→ WEDDING_DOMAIN=${WEDDING_DOMAIN} → eigene TLS-Domain nur für die Galerie."
    cat > "$CONF_DIR/wedding.env" <<EOF
# Von deploy/setup_wedding.sh erzeugt — eigene TLS-Domain (Caddy) nur für die Galerie.
WEDDING_BIND=127.0.0.1
WEDDING_PORT=${PORT}
WEDDING_ROOT_PATH=
WEDDING_COOKIE_SECURE=true
WEDDING_DATA_DIR=${DATA_DIR}
WEDDING_USERS_FILE=${CONF_DIR}/users.json
WEDDING_SECRET_FILE=${CONF_DIR}/secret
WEDDING_MAX_BYTES=31457280
EOF
elif [ -n "${DOMAIN}" ]; then
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
    echo "→ Keine (WEDDING_)DOMAIN in .env → Galerie direkt auf Port ${PORT} (ohne TLS)."
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

# --- 7) systemd-Unit ------------------------------------------------------- #
echo "→ systemd-Service installieren (Code: $CODE_DIR, Python: $WEDDING_PY)..."
# Das Template bleibt /opt-basiert; hier werden die drei Pfade gezielt ersetzt.
sed -e "s#WorkingDirectory=/opt/stockbot#WorkingDirectory=$CODE_DIR#" \
    -e "s#/opt/stockbot/venv/bin/python#$WEDDING_PY#" \
    -e "s#/opt/stockbot/wedding/run.py#$CODE_DIR/wedding/run.py#" \
    deploy/wedding.service > /etc/systemd/system/wedding.service
systemctl daemon-reload
systemctl enable wedding >/dev/null 2>&1 || true
systemctl restart wedding

# --- 8) Netzwerk / Reverse-Proxy ------------------------------------------- #
if [ -n "${WEDDING_DOMAIN}" ]; then
    echo "→ Caddy (eigene TLS-Domain für die Galerie) einrichten..."
    # Caddy installieren, falls nicht vorhanden (gleiche Quelle wie deploy/sync_caddy.sh).
    if ! command -v caddy >/dev/null 2>&1; then
        echo "  → installiere Caddy ..."
        apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl gnupg >/dev/null 2>&1 || true
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
            | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
            > /etc/apt/sources.list.d/caddy-stable.list
        apt-get update -qq
        apt-get install -y -qq caddy
    fi
    # WICHTIG: Dieser Weg besitzt /etc/caddy/Caddyfile allein (nur der Galerie-Block).
    # Er ist NICHT mit dem stockbot-DOMAIN/sync_caddy-Weg kombinierbar — der würde
    # dieselbe Datei überschreiben. Immer nur EINEN der beiden Wege verwenden.
    mkdir -p /etc/caddy
    cat > /etc/caddy/Caddyfile <<EOF
# Von deploy/setup_wedding.sh erzeugt — eigene TLS-Domain NUR für die Hochzeits-Galerie.
# Zeigt ausschließlich auf die Galerie (Port ${PORT}); der stockbot-Dienst (Port 8000)
# wird NICHT über Caddy bedient und bleibt unberührt.
${WEDDING_DOMAIN} {
    encode gzip
    header -Server
    request_body {
        max_size 200MB
    }
    reverse_proxy 127.0.0.1:${PORT}
}
EOF
    systemctl enable caddy >/dev/null 2>&1 || true
    if caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
        systemctl reload caddy 2>/dev/null || systemctl restart caddy
        echo "  → Caddy neu geladen."
    else
        echo "  WARN: Caddyfile-Validierung fehlgeschlagen — KEIN Reload (alte Config bleibt aktiv)."
    fi
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
        ufw allow 80/tcp  >/dev/null 2>&1 || true
        ufw allow 443/tcp >/dev/null 2>&1 || true
        echo "→ Firewall: Ports 80/443 geöffnet."
    fi
    URL="https://${WEDDING_DOMAIN}/"
    echo "  Hinweis: Für das Let's-Encrypt-Zertifikat müssen Ports 80/443 aus dem Internet"
    echo "  erreichbar sein und der DNS-Eintrag von ${WEDDING_DOMAIN} auf diesen Server zeigen."
elif [ -n "${DOMAIN}" ]; then
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
echo "   Gäste-Link:  ${URL}gast"
echo "   Status:   systemctl status wedding"
echo "   Logs:     journalctl -u wedding -f"
echo "   Fotos:    ${DATA_DIR}/photos"
echo "   Passwort ändern:  ${WEDDING_PY} ${CODE_DIR}/wedding/manage.py --file ${CONF_DIR}/users.json set-password amelie"

if [ "$FALLBACK" = "1" ]; then
    echo
    echo "ℹ️  FALLBACK aktiv: der Dienst läuft aus ${CODE_DIR} (Kopie), NICHT aus ${APP_DIR}."
    echo "   Grund: der User wedding kommt an ${APP_DIR} nicht heran (z. B. Repo unter /root)."
    echo "   Nach JEDEM 'git pull' erneut 'bash deploy/setup_wedding.sh' ausführen —"
    echo "   sonst läuft die Galerie weiter mit dem alten Code aus ${CODE_DIR}."
fi
