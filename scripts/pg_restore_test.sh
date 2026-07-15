#!/usr/bin/env bash
set -euo pipefail

# Aufruf: BACKUP_FILE=/pfad/backup.dump.age ./scripts/pg_restore_test.sh
# Quelle: PG_CONTAINER, PGUSER, PGDATABASE. Ziel: RESTORE_IMAGE/RESTORE_CONTAINER.
# age: AGE_IDENTITY setzen. gpg nutzt den konfigurierten Keyring (optional GPG_HOMEDIR).
BACKUP_FILE="${BACKUP_FILE:-${1:-}}"
PG_CONTAINER="${PG_CONTAINER:-postgres}"
PGUSER="${PGUSER:-stockbot}"
PGDATABASE="${PGDATABASE:-stockbot}"
RESTORE_IMAGE="${RESTORE_IMAGE:-postgres:16-alpine}"
RESTORE_CONTAINER="${RESTORE_CONTAINER:-stockbot-restore-test-$$}"
RESTORE_DATABASE="${RESTORE_DATABASE:-stockbot_restore_test}"

die() {
    echo "pg_restore_test: FAIL: $*" >&2
    exit 1
}

cleanup() {
    docker rm -f "$RESTORE_CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

[[ -n "$BACKUP_FILE" ]] || die "BACKUP_FILE oder erstes Argument fehlt"
[[ -f "$BACKUP_FILE" ]] || die "Backup nicht gefunden: $BACKUP_FILE"
command -v docker >/dev/null 2>&1 || die "docker wurde nicht gefunden"

case "$BACKUP_FILE" in
    *.age)
        command -v age >/dev/null 2>&1 || die "age wurde nicht gefunden"
        [[ -n "${AGE_IDENTITY:-}" ]] || die "AGE_IDENTITY fuer age-Entschluesselung fehlt"
        decrypt=(age --decrypt -i "$AGE_IDENTITY")
        ;;
    *.gpg)
        command -v gpg >/dev/null 2>&1 || die "gpg wurde nicht gefunden"
        decrypt=(gpg --batch --decrypt)
        [[ -z "${GPG_HOMEDIR:-}" ]] || decrypt+=(--homedir "$GPG_HOMEDIR")
        ;;
    *) die "Backup muss auf .age oder .gpg enden" ;;
esac

docker inspect "$PG_CONTAINER" >/dev/null 2>&1 || die "Quellcontainer '$PG_CONTAINER' laeuft nicht"
docker run -d --name "$RESTORE_CONTAINER" \
    -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_USER=postgres \
    -e POSTGRES_DB="$RESTORE_DATABASE" "$RESTORE_IMAGE" >/dev/null

for _ in $(seq 1 30); do
    if docker exec "$RESTORE_CONTAINER" pg_isready -U postgres -d "$RESTORE_DATABASE" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
docker exec "$RESTORE_CONTAINER" pg_isready -U postgres -d "$RESTORE_DATABASE" >/dev/null 2>&1 \
    || die "Wegwerf-Postgres wurde nicht bereit"

"${decrypt[@]}" "$BACKUP_FILE" \
    | docker exec -i "$RESTORE_CONTAINER" pg_restore \
        -U postgres -d "$RESTORE_DATABASE" --no-owner --no-privileges

mapfile -t tables < <(docker exec "$PG_CONTAINER" psql -U "$PGUSER" -d "$PGDATABASE" \
    -Atqc "SELECT quote_ident(schemaname)||'.'||quote_ident(tablename) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1")

for table in "${tables[@]}"; do
    source_count="$(docker exec "$PG_CONTAINER" psql -U "$PGUSER" -d "$PGDATABASE" -Atqc "SELECT count(*) FROM $table")"
    restore_count="$(docker exec "$RESTORE_CONTAINER" psql -U postgres -d "$RESTORE_DATABASE" -Atqc "SELECT count(*) FROM $table")"
    [[ "$source_count" == "$restore_count" ]] \
        || die "$table: Quelle=$source_count, Restore=$restore_count"
done

echo "pg_restore_test: OK: ${#tables[@]} Tabellen stimmen ueberein"
