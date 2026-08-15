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
docker compose -f "$PROJECT_ROOT/docker-compose.yml" exec -T postgres \
    pg_dump -U "$POSTGRES_USER" scenarai | gzip > "$OUT_FILE"

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
