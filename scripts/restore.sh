#!/usr/bin/env bash
#
# Mnema restore script. Pairs with scripts/backup.sh.
#
# Usage:
#   ./scripts/restore.sh mnema-db-YYYYMMDD-HHMMSS.sql.gz mnema-uploads-YYYYMMDD-HHMMSS.tar.gz
#
# What this does:
#   1. Restores the Postgres dump into the running mnema_db container.
#      The dump was taken with --clean --if-exists, so it drops the
#      existing schema before recreating it.
#   2. WIPES the contents of the uploads volume and extracts the tarball
#      into it. (Uploads are content-addressed by UUID filename, so
#      mixing old and new files would corrupt the DB <-> disk mapping.)
#
# Run this with the app container STOPPED to avoid concurrent writes:
#   docker compose stop app
#   ./scripts/restore.sh path/to/db.sql.gz path/to/uploads.tar.gz
#   docker compose start app

set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <db-backup.sql.gz> <uploads-backup.tar.gz>" >&2
  exit 2
fi

DB_BACKUP="$1"
UPLOADS_BACKUP="$2"

DB_CONTAINER="${DB_CONTAINER:-mnema_db}"
UPLOADS_VOLUME="${UPLOADS_VOLUME:-mnema_uploads_data}"
DB_USER="${DB_USER:-app}"
DB_NAME="${DB_NAME:-app}"

[[ -f "$DB_BACKUP"      ]] || { echo "missing: $DB_BACKUP" >&2; exit 1; }
[[ -f "$UPLOADS_BACKUP" ]] || { echo "missing: $UPLOADS_BACKUP" >&2; exit 1; }

echo "About to restore:"
echo "  database: $DB_BACKUP  -> $DB_CONTAINER:$DB_NAME"
echo "  uploads:  $UPLOADS_BACKUP -> volume $UPLOADS_VOLUME (will be wiped first)"
read -r -p "Continue? [yes/N] " ans
[[ "$ans" == "yes" ]] || { echo "aborted"; exit 1; }

echo "Restoring database ..."
gunzip -c "$DB_BACKUP" | docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME"

echo "Clearing uploads volume ..."
docker run --rm -v "$UPLOADS_VOLUME":/data alpine \
  sh -c "find /data -mindepth 1 -delete"

echo "Restoring uploads ..."
docker run --rm \
  -v "$UPLOADS_VOLUME":/data \
  -v "$(realpath "$UPLOADS_BACKUP")":/backup.tar.gz:ro \
  alpine tar xzf /backup.tar.gz -C /data

echo "Restore complete. Start the app container when ready."
