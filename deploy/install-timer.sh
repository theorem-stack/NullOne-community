#!/usr/bin/env bash
# Install systemd service + weekly timer for NullOne.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
NULLONE_USER="${NULLONE_USER:-nullone}"
NULLONE_MODE="${NULLONE_MODE:-paper}"
OPT_DIR="${OPT_DIR:-/opt/nullone}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo NULLONE_MODE=paper $0" >&2
  exit 1
fi

if [[ ! -x "$REPO_ROOT/.venv/bin/nullone" ]]; then
  echo "Missing venv/nullone. Run sudo $SCRIPT_DIR/bootstrap.sh first." >&2
  exit 1
fi

if [[ ! -f "$OPT_DIR/.env" ]]; then
  echo "Missing $OPT_DIR/.env. Run sudo $SCRIPT_DIR/setup-env.sh first." >&2
  exit 1
fi

SERVICE=$(sed \
  -e "s|%REPO_ROOT%|$REPO_ROOT|g" \
  -e "s|%NULLONE_MODE%|$NULLONE_MODE|g" \
  "$SCRIPT_DIR/systemd/nullone-release.service")

echo "$SERVICE" > /etc/systemd/system/nullone-release.service
cp "$SCRIPT_DIR/systemd/nullone-release.timer" /etc/systemd/system/nullone-release.timer

systemctl daemon-reload
systemctl enable nullone-release.timer
systemctl start nullone-release.timer

echo "Installed nullone-release.timer (Thu 10:00 AM ET, mode=$NULLONE_MODE)"
systemctl status nullone-release.timer --no-pager || true
