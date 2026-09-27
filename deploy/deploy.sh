#!/usr/bin/env bash
# Pull both repos, rebuild, migrate, restart. Run on the VM from anywhere:
#   ./deploy/deploy.sh
# Safe to re-run. See docs/DEPLOY.md.
set -euo pipefail

cd "$(dirname "$0")"
set -a; . ./.env; set +a

branch="${DEPLOY_BRANCH:-staging}"
for repo in .. "${CLIENT_DIR:-../../IndoLegalBench-client}"; do
  git -C "$repo" fetch --quiet origin
  git -C "$repo" checkout --quiet "$branch"
  git -C "$repo" pull --quiet --ff-only origin "$branch"
  echo "$(basename "$(git -C "$repo" rev-parse --show-toplevel)"): $(git -C "$repo" log -1 --oneline)"
done

docker compose build
docker compose up -d db
docker compose run --rm api alembic upgrade head
docker compose up -d
docker image prune -f > /dev/null

docker compose ps
