#!/usr/bin/env bash
# Daily database dump of one stack, keeps the last 7 days. Run from cron, see
# docs/DEPLOY.md. Which stack and where to write come from deploy/.env.
# The dumps stay on this VM, so they do not survive losing the VM itself.
set -euo pipefail

cd "$(dirname "$0")"
set -a; . ./.env; set +a
: "${COMPOSE_PROJECT_NAME:?is empty in deploy/.env, see docs/DEPLOY.md section 3}"
dir="${BACKUP_DIR:-/var/backups/indolegalbench}"
mkdir -p "$dir"

file="$dir/indolegalbench-$(date +%F).sql.gz"
docker compose exec -T db pg_dump -U indolegalbench --clean --if-exists indolegalbench \
  | gzip > "$file.tmp"
mv "$file.tmp" "$file"

find "$dir" -name 'indolegalbench-*.sql.gz' -mtime +7 -delete
