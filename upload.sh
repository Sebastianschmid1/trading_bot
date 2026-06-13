#!/usr/bin/env bash
# Upload/Deploy in einem Schritt:
#   1) lokal committen (wenn es Änderungen gibt) + pushen
#   2) per SSH auf den Server, dort: git pull && systemctl restart stockbot
#
# Die SSH-Key-Passphrase kommt aus .env (LOCAL_PASSWORD) — wird NIE ausgegeben/committet.
#
# Voraussetzung: `sshpass` installiert
#   Linux/WSL:  sudo apt install sshpass
#   macOS:      brew install hudochenkov/sshpass/sshpass
#
# Lauf:   ./upload.sh "deine commit message"
#         (ohne Argument wird eine Standard-Message mit Zeitstempel genutzt)

set -euo pipefail

SERVER="root@217.160.103.25"
APP_DIR="stockbot"
MSG="${1:-deploy $(date '+%Y-%m-%d %H:%M')}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── .env → LOCAL_PASSWORD (Key-Passphrase); Wert nie ausgeben ────────────────
[ -f .env ] || { echo "❌ .env nicht gefunden in $SCRIPT_DIR"; exit 1; }
LOCAL_PASSWORD="$(sed -n 's/^LOCAL_PASSWORD=//p' .env | head -1)"
# evtl. umschließende Anführungszeichen entfernen
LOCAL_PASSWORD="${LOCAL_PASSWORD%\"}"; LOCAL_PASSWORD="${LOCAL_PASSWORD#\"}"
LOCAL_PASSWORD="${LOCAL_PASSWORD%\'}"; LOCAL_PASSWORD="${LOCAL_PASSWORD#\'}"
[ -n "$LOCAL_PASSWORD" ] || { echo "❌ LOCAL_PASSWORD fehlt in .env"; exit 1; }

command -v sshpass >/dev/null 2>&1 || { echo "❌ sshpass nicht installiert (siehe Kopf des Scripts)"; exit 1; }

# sshpass für Key-Passphrase: -P 'passphrase' matcht den Prompt „Enter passphrase for key …"
export SSHPASS="$LOCAL_PASSWORD"
SSH_OPTS="-o StrictHostKeyChecking=accept-new"
# Gilt auch für `git push`, falls das Remote über SSH mit verschlüsseltem Key läuft:
export GIT_SSH_COMMAND="sshpass -P passphrase -e ssh $SSH_OPTS"

# ── 1) commit (nur bei Änderungen) + push ───────────────────────────────────
if [ -n "$(git status --porcelain)" ]; then
    echo "→ committe lokale Änderungen …"
    git add -A
    git commit -m "$MSG"
else
    echo "ℹ️ keine lokalen Änderungen — überspringe commit."
fi
echo "→ push …"
git push

# ── 2) Server: pullen + Dienst neu starten ──────────────────────────────────
echo "→ deploye auf $SERVER …"
sshpass -P passphrase -e ssh $SSH_OPTS "$SERVER" \
    "cd $APP_DIR && git pull && systemctl restart stockbot && systemctl status stockbot --no-pager -l | head -n 15"

echo "✅ fertig: gepusht und auf dem Server neu gestartet."
