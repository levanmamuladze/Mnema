#!/usr/bin/env bash
#
# Mnema backup script.
#
# Produces two artefacts in the directory given by BACKUP_DIR (or `./backups`):
#   1. mnema-db-YYYYMMDD-HHMMSS.sql.gz   — pg_dump of the Postgres database
#   2. mnema-uploads-YYYYMMDD-HHMMSS.tar.gz — full contents of the uploads volume
#
# The Hugging Face model cache is NOT backed up — it's ~1 GB of files
# Hugging Face will re-download on demand. Backing it up wastes space.
#
# Usage:
#   ./scripts/backup.sh                  # writes to ./backups
#   BACKUP_DIR=/mnt/backups ./scripts/backup.sh
#
# Schedule via cron (daily at 03:00 in this example):
#   0 3 * * * cd /path/to/mnema && ./scripts/backup.sh >> backups/backup.log 2>&1
#
# Restore: see scripts/restore.sh.

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
TS="$(date +%Y%m%d-%H%M%S)"

DB_CONTAINER="${DB_CONTAINER:-mnema_db}"
UPLOADS_VOLUME="${UPLOADS_VOLUME:-mnema_uploads_data}"
DB_USER="${DB_USER:-app}"
DB_NAME="${DB_NAME:-app}"

mkdir -p "$BACKUP_DIR"

echo "[$(date -Iseconds)] Backing up Postgres ($DB_CONTAINER -> $DB_NAME) ..."
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" --clean --if-exists \
  | gzip > "$BACKUP_DIR/mnema-db-$TS.sql.gz"
echo "  -> $BACKUP_DIR/mnema-db-$TS.sql.gz"

echo "[$(date -Iseconds)] Backing up uploads volume ($UPLOADS_VOLUME) ..."
docker run --rm \
  -v "$UPLOADS_VOLUME":/data:ro \
  -v "$(pwd)/$BACKUP_DIR":/backup \
  alpine tar czf "/backup/mnema-uploads-$TS.tar.gz" -C /data .
echo "  -> $BACKUP_DIR/mnema-uploads-$TS.tar.gz"

echo "[$(date -Iseconds)] Backup complete."
