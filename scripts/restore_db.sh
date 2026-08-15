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

COMPOSE="docker compose -f $PROJECT_ROOT/docker-compose.yml"

echo "Dropping and recreating database..."
$COMPOSE exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS scenarai;"
$COMPOSE exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE scenarai;"

echo "Restoring from $BACKUP_FILE ..."
gunzip -c "$BACKUP_FILE" | $COMPOSE exec -T postgres psql -U "$POSTGRES_USER" -d scenarai

echo "Restore complete."
