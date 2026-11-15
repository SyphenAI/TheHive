#!/usr/bin/env bash
# Update TheHive in place: back up data/, fetch latest code, rebuild, restart.
# Usage: ./scripts/update.sh [--branch <name|tag>] [--tls] [--no-build]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="https://github.com/SyphenAI/TheHive.git"
BRANCH="" TLS=0 NOBUILD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --branch) BRANCH="$2"; shift 2 ;;
    --tls) TLS=1; shift ;;
    --no-build) NOBUILD=1; shift ;;
    *) echo "unknown arg $1"; exit 2 ;;
  esac
done
cd "$ROOT"
echo "== TheHive update ($ROOT)"

STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p data/backups
if [ -f data/thehive.db ]; then
  tar -czf "data/backups/pre-update-$STAMP.tgz" data/thehive.db* .env 2>/dev/null || true
  echo "   backup: data/backups/pre-update-$STAMP.tgz"
  ls -1t data/backups/pre-update-*.tgz 2>/dev/null | tail -n +11 | xargs -r rm -f
fi

if [ -d .git ]; then
  [ -n "$BRANCH" ] || BRANCH=$(git rev-parse --abbrev-ref HEAD)
  git fetch --tags origin
  git checkout -q "$BRANCH"
  git pull --ff-only origin "$BRANCH"
else
  [ -n "$BRANCH" ] || BRANCH=pre-prod
  TMP=$(mktemp -d)
  git clone -q --depth 1 -b "$BRANCH" "$REPO" "$TMP"
  rsync -a --exclude .git --exclude data --exclude storage --exclude node_modules --exclude .venv --exclude .env "$TMP/" "$ROOT/"
  rm -rf "$TMP"
fi
SHA=""; [ -d .git ] && SHA=$(git rev-parse --short=12 HEAD)
echo "   code: $BRANCH $(cat VERSION 2>/dev/null) $SHA"

FILES=(-f docker-compose.yml)
[ $NOBUILD -eq 1 ] && FILES+=(-f docker-compose.ghcr.yml)
[ $TLS -eq 1 ] && FILES+=(-f docker-compose.tls.yml)
export GIT_SHA="$SHA"
[ $NOBUILD -eq 1 ] && docker compose "${FILES[@]}" pull
docker compose "${FILES[@]}" up -d --build --remove-orphans
echo "== done."
