#!/usr/bin/env bash
# Copy local .env and Kalshi PEM to /opt/nullone with safe permissions.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OPT_DIR="${OPT_DIR:-/opt/nullone}"
NULLONE_USER="${NULLONE_USER:-nullone}"
SOURCE_ENV="${1:-$REPO_ROOT/.env}"

if [[ ! -f "$SOURCE_ENV" ]]; then
  echo "Usage: $0 [path/to/.env]" >&2
  echo "Source env not found: $SOURCE_ENV" >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

mkdir -p "$OPT_DIR/logs/releases"
cp "$SOURCE_ENV" "$OPT_DIR/.env"

# Normalize log dir and private key path for server layout.
sed -i "s|^NULLONE_LOG_DIR=.*|NULLONE_LOG_DIR=$OPT_DIR/logs/releases|" "$OPT_DIR/.env"

if grep -q '^KALSHI_PRIVATE_KEY_PATH=' "$OPT_DIR/.env"; then
  sed -i "s|^KALSHI_PRIVATE_KEY_PATH=.*|KALSHI_PRIVATE_KEY_PATH=$OPT_DIR/kalshi-private-key.pem|" "$OPT_DIR/.env"
fi

PEM_SRC=""
if [[ -f "$REPO_ROOT/kalshi-private-key.pem" ]]; then
  PEM_SRC="$REPO_ROOT/kalshi-private-key.pem"
elif [[ -f "$(dirname "$SOURCE_ENV")/kalshi-private-key.pem" ]]; then
  PEM_SRC="$(dirname "$SOURCE_ENV")/kalshi-private-key.pem"
fi

if [[ -n "$PEM_SRC" ]]; then
  install -m 600 -o "$NULLONE_USER" -g "$NULLONE_USER" "$PEM_SRC" "$OPT_DIR/kalshi-private-key.pem"
  echo "Installed $OPT_DIR/kalshi-private-key.pem"
else
  echo "Warning: kalshi-private-key.pem not found next to repo or .env; set KALSHI_PRIVATE_KEY_PEM manually."
fi

chown "$NULLONE_USER:$NULLONE_USER" "$OPT_DIR/.env"
chmod 600 "$OPT_DIR/.env"
chown -R "$NULLONE_USER:$NULLONE_USER" "$OPT_DIR/logs"

echo "Installed $OPT_DIR/.env"
