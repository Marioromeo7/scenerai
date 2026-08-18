#!/bin/sh
# Restores a backup produced by backup_db.sh. An untested backup isn't a
# real backup -- this script exists specifically so the restore path has
# actually been exercised, not just assumed to work because pg_dump ran
# without error.
#
# Usage: ./scripts/restore_db.sh <backup_file.sql.gz>
#
# DESTRUCTIVE: drops and recreates the target database before restoring.
# Confirms before proceeding unless -y is passed.

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# shellcheck disable=SC1090
[ -f "$PROJECT_ROOT/.env" ] && . "$PROJECT_ROOT/.env"
POSTGRES_USER="${POSTGRES_USER:-scenarai}"

AUTO_YES=0
if [ "${1:-}" = "-y" ]; then AUTO_YES=1; shift; fi

BACKUP_FILE="${1:-}"
if [ -z "$BACKUP_FILE" ] || [ ! -f "$BACKUP_FILE" ]; then
    echo "Usage: $0 [-y] <backup_file.sql.gz>" >&2
    exit 1
fi

if [ "$AUTO_YES" -ne 1 ]; then
    printf 'This will DROP and recreate the "scenarai" database, discarding current data. Continue? [y/N] '
    read -r CONFIRM
    case "$CONFIRM" in
        y|Y) ;;
        *) echo "Aborted."; exit 1 ;;
    esac
fi

# Each call quotes "$PROJECT_ROOT/docker-compose.yml" inline (matching
# backup_db.sh's pattern) rather than building a reusable $COMPOSE string
# and expanding it unquoted -- found via code review: the unquoted form
# word-splits PROJECT_ROOT on any spaces in the path (e.g. a Windows user
# profile like "C:/Users/John Doe/..."), and this script is destructive
# enough (drops the database first) that a broken restore command after
# that point would leave the database dropped with nothing restored.

echo "Dropping and recreating database..."
docker compose -f "$PROJECT_ROOT/docker-compose.yml" exec -T postgres \
    psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS scenarai;"
docker compose -f "$PROJECT_ROOT/docker-compose.yml" exec -T postgres \
    psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE scenarai;"

echo "Restoring from $BACKUP_FILE ..."
# gunzip | psql hid a gunzip failure: under plain POSIX sh (no `pipefail`),
# a pipeline's exit status is its LAST command's -- psql reading empty
# stdin (because gunzip choked on a corrupt/truncated backup file) just
# exits 0 having run zero statements, so `set -e` never fires. Confirmed
# live: `gunzip -c corrupt.gz | wc -l; echo $?` prints 0 despite gunzip's
# own error. This script already drops the database before this point --
# a masked failure here would report "Restore complete" over a database
# that's been dropped and never actually repopulated. Decoupling into a
# real intermediate file means gunzip's own exit code is what `set -e`
# actually sees, before psql ever runs.
TMP_SQL="$(mktemp)"
trap 'rm -f "$TMP_SQL"' EXIT
gunzip -c "$BACKUP_FILE" > "$TMP_SQL"
docker compose -f "$PROJECT_ROOT/docker-compose.yml" exec -T postgres \
    psql -U "$POSTGRES_USER" -d scenarai < "$TMP_SQL"

echo "Restore complete."
