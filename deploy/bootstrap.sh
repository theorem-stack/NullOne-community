#!/usr/bin/env bash
# First-time server setup for NullOne on Ubuntu 22.04/24.04.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
NULLONE_USER="${NULLONE_USER:-nullone}"
OPT_DIR="${OPT_DIR:-/opt/nullone}"

echo "==> NullOne bootstrap"
echo "    repo:  $REPO_ROOT"
echo "    user:  $NULLONE_USER"
echo "    env:   $OPT_DIR/.env"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git chrony python3.12 python3.12-venv python3-pip

systemctl enable --now chrony
chronyc waitsync 10 0.1 0.0 1 || true

if ! id "$NULLONE_USER" &>/dev/null; then
  useradd --system --create-home --home-dir "$OPT_DIR" --shell /bin/bash "$NULLONE_USER"
fi

mkdir -p "$OPT_DIR/logs/releases"
chown -R "$NULLONE_USER:$NULLONE_USER" "$OPT_DIR"

if [[ ! -f "$OPT_DIR/.env" ]]; then
  cp "$REPO_ROOT/.env.example" "$OPT_DIR/.env"
  sed -i "s|^NULLONE_LOG_DIR=.*|NULLONE_LOG_DIR=$OPT_DIR/logs/releases|" "$OPT_DIR/.env"
  chown "$NULLONE_USER:$NULLONE_USER" "$OPT_DIR/.env"
  chmod 600 "$OPT_DIR/.env"
  echo "Created $OPT_DIR/.env from .env.example — edit it before running."
fi

sudo -u "$NULLONE_USER" bash <<EOF
set -euo pipefail
cd "$REPO_ROOT"
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
EOF

chown -R "$NULLONE_USER:$NULLONE_USER" "$REPO_ROOT"
chmod 755 "$REPO_ROOT"

echo
echo "Bootstrap complete."
echo "Next steps:"
echo "  1. Edit $OPT_DIR/.env and install your Kalshi PEM at $OPT_DIR/kalshi-private-key.pem"
echo "  2. sudo $SCRIPT_DIR/setup-env.sh   (if copying env from your laptop)"
echo "  3. sudo -u $NULLONE_USER $REPO_ROOT/.venv/bin/nullone next-release"
echo "  4. sudo $SCRIPT_DIR/install-timer.sh"
