#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v docker >/dev/null || { echo 'Install Docker Engine and Compose v2 first.' >&2; exit 1; }
docker compose version
if [ ! -f .env ]; then
  umask 077
  token="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
  sed "s/replace-with-generated-random-token/$token/" .env.example > .env
fi
if [ "${1:-}" != '--no-start' ]; then
  docker compose up --build -d --wait
  echo 'Frontier is ready. Open http://localhost:8765 (or WORKBENCH_PORT in .env).'
fi
