#!/usr/bin/env bash
set -euo pipefail

# PG_BACKUP_MODE=docker (Standard) nutzt pg_dump im laufenden PG_CONTAINER.
# PG_BACKUP_MODE=direct nutzt lokales pg_dump gegen POSTGRES_DSN bzw. PG*-Variablen.
PG_BACKUP_MODE="${PG_BACKUP_MODE:-docker}"
PG_CONTAINER="${PG_CONTAINER:-postgres}"
PGUSER="${PGUSER:-stockbot}"
PGDATABASE="${PGDATABASE:-stockbot}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/stockbot}"

die() {
    echo "pg_backup: FEHLER: $*" >&2
    exit 1
}

command -v python >/dev/null 2>&1 || die "python wurde nicht gefunden"
mkdir -p "$BACKUP_DIR"

timestamp="$(date -u +%Y%m%d-%H%M%S)"
if command -v age >/dev/null 2>&1 && [[ -n "${AGE_RECIPIENTS:-}" ]]; then
    extension="age"
    encrypt=(age)
    for recipient in ${AGE_RECIPIENTS}; do
        encrypt+=(-r "$recipient")
    done
elif command -v gpg >/dev/null 2>&1 && [[ -n "${GPG_RECIPIENT:-}" ]]; then
    extension="gpg"
    encrypt=(gpg --batch --yes --encrypt -r "$GPG_RECIPIENT")
else
    die "kein Verschluesselungs-Recipient verfuegbar (AGE_RECIPIENTS mit age oder GPG_RECIPIENT mit gpg setzen)"
fi

target="$BACKUP_DIR/stockbot-$timestamp.dump.$extension"
partial="$target.partial"
trap 'rm -f "$partial"' EXIT

case "$PG_BACKUP_MODE" in
    docker)
        command -v docker >/dev/null 2>&1 || die "docker wurde nicht gefunden"
        docker exec "$PG_CONTAINER" pg_dump -U "$PGUSER" -d "$PGDATABASE" -Fc \
            | "${encrypt[@]}" -o "$partial"
        ;;
    direct)
        command -v pg_dump >/dev/null 2>&1 || die "pg_dump wurde nicht gefunden"
        if [[ -n "${POSTGRES_DSN:-}" ]]; then
            dsn="${POSTGRES_DSN/postgresql+psycopg2:/postgresql:}"
            pg_dump -Fc --dbname="$dsn" | "${encrypt[@]}" -o "$partial"
        else
            pg_dump -Fc | "${encrypt[@]}" -o "$partial"
        fi
        ;;
    *) die "PG_BACKUP_MODE muss 'docker' oder 'direct' sein" ;;
esac

chmod 0600 "$partial"
mv "$partial" "$target"
trap - EXIT

mapfile -d '' backups < <(find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name 'stockbot-*.dump.age' -o -name 'stockbot-*.dump.gpg' \) -print0)
python -m stockbot.ops.backup_retention --delete "${backups[@]}"
echo "pg_backup: OK: $target"
