#!/usr/bin/env bash
# Pull both repos, rebuild, migrate, restart one stack. Run on the VM:
#   ./deploy/deploy.sh
# Which stack and branch come from deploy/.env next to this script. Safe to
# re-run. Also what the staging CD job runs over SSH. See docs/DEPLOY.md.
set -euo pipefail

# Everything is inside main, and main is called on the last line: bash then
# reads the whole file before running it, so the `git pull` below can change
# this script without breaking the run in progress.
main() {
  cd "$(dirname "$0")"
  set -a; . ./.env; set +a
  : "${CREDENTIAL_ENCRYPTION_KEY:?is empty in deploy/.env, see docs/DEPLOY.md section 3}"
  : "${STACK:?is empty in deploy/.env, see docs/DEPLOY.md section 3}"
  # Both checkouts name this folder `deploy`, which is also compose's default
  # project name. Without an explicit one, dev would take over prod's stack.
  : "${COMPOSE_PROJECT_NAME:?is empty in deploy/.env, see docs/DEPLOY.md section 3}"

  # One deploy at a time on the whole VM: two builds at once starve the
  # 2 vCPU, and both repos' CD jobs can fire for the same merge.
  exec 9> /tmp/indolegalbench-deploy.lock
  flock -w 1800 9 || { echo "another deploy is still running" >&2; exit 1; }

  if ! docker network inspect ilb-edge > /dev/null 2>&1; then
    echo "network ilb-edge is missing: start deploy/proxy first, see docs/DEPLOY.md" >&2
    exit 1
  fi

  branch="${DEPLOY_BRANCH:-staging}"
  echo "stack $STACK ($COMPOSE_PROJECT_NAME), branch $branch"
  for repo in .. "${CLIENT_DIR:-../../IndoLegalBench-client}"; do
    git -C "$repo" fetch --quiet origin
    git -C "$repo" checkout --quiet "$branch"
    git -C "$repo" pull --quiet --ff-only origin "$branch"
    echo "$(basename "$(git -C "$repo" rev-parse --show-toplevel)"): $(git -C "$repo" log -1 --oneline)"
  done

  docker compose build
  docker compose up -d db
  docker compose run --rm api alembic upgrade head
  # --remove-orphans also drops the old per-stack Caddy of the single-stack
  # layout, if it is still there.
  docker compose up -d --remove-orphans
  docker image prune -f > /dev/null

  docker compose ps
}

main "$@"; exit
