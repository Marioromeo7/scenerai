#!/bin/sh
# Logical (pg_dump) backup of the Scenarai Postgres database. No backup
# mechanism existed before this -- data lived only in the postgres_data
# Docker volume, which doesn't survive `docker compose down -v` or volume
# corruption. Run manually or wire into a scheduler (cron/systemd timer);
# not scheduled automatically by anything yet.
#
# Usage: ./scripts/backup_db.sh [keep_count]
#   keep_count: how many most-recent backups to retain (default 14)

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="$PROJECT_ROOT/backups"
KEEP_COUNT="${1:-14}"

# shellcheck disable=SC1090
[ -f "$PROJECT_ROOT/.env" ] && . "$PROJECT_ROOT/.env"
POSTGRES_USER="${POSTGRES_USER:-scenarai}"

mkdir -p "$BACKUP_DIR"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_FILE="$BACKUP_DIR/scenarai_${TIMESTAMP}.sql.gz"

echo "Backing up to $OUT_FILE ..."
# pg_dump piped straight into gzip would hide a pg_dump failure: under
# plain POSIX sh (no `pipefail`, which is a bash-only option), a pipeline's
# exit status is its LAST command's -- gzip happily "succeeds" compressing
# empty/partial input even if pg_dump itself failed (wrong password,
# container down, disk full), so `set -e` never fires and the script
# reports "Done" over a silently empty or corrupt backup. Confirmed live:
# `false | gzip > f; echo $?` prints 0. Decoupling into two real commands
# means pg_dump's own exit code is what `set -e` actually sees.
TMP_FILE="$BACKUP_DIR/.tmp_${TIMESTAMP}.sql"
# The shell creates/truncates TMP_FILE via this redirect before pg_dump
# even runs, so a pg_dump failure still leaves a 0-byte file behind --
# confirmed live. Trap removes it on any non-zero exit; cleared right
# before the final mv so a successful run doesn't delete its own output.
trap 'rm -f "$TMP_FILE" "$TMP_FILE.gz"' EXIT
docker compose -f "$PROJECT_ROOT/docker-compose.yml" exec -T postgres \
    pg_dump -U "$POSTGRES_USER" scenarai > "$TMP_FILE"
gzip "$TMP_FILE"
mv "$TMP_FILE.gz" "$OUT_FILE"
trap - EXIT

SIZE=$(du -h "$OUT_FILE" | cut -f1)
echo "Done: $OUT_FILE ($SIZE)"

# Prune old backups beyond keep_count, oldest first.
COUNT=$(find "$BACKUP_DIR" -maxdepth 1 -name 'scenarai_*.sql.gz' | wc -l)
if [ "$COUNT" -gt "$KEEP_COUNT" ]; then
    TO_DELETE=$((COUNT - KEEP_COUNT))
    find "$BACKUP_DIR" -maxdepth 1 -name 'scenarai_*.sql.gz' | sort | head -n "$TO_DELETE" | while read -r f; do
        echo "Pruning old backup: $f"
        rm -f "$f"
    done
fi
