#!/usr/bin/env bash
# Manage NullOne on a deployed server (or locally).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OPT_DIR="${OPT_DIR:-/opt/nullone}"
ENV_FILE="${NULLONE_ENV:-$OPT_DIR/.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

NULLONE="${REPO_ROOT}/.venv/bin/nullone"
if [[ ! -x "$NULLONE" ]]; then
  NULLONE="$(command -v nullone || true)"
fi
if [[ -z "$NULLONE" ]]; then
  echo "nullone not found. Run deploy/bootstrap.sh first." >&2
  exit 1
fi

usage() {
  cat <<EOF
Usage: $0 <command> [args]

Commands:
  status       Show timer, clock sync, and next release
  logs         Print the latest release log JSON
  tail-logs    Follow systemd journal for the release service
  next-release Show upcoming WNGSR window
  dry-run      Run Kalshi book connectivity check
  discover     Discover Kalshi tickers for a week (--week YYYY-MM-DD optional)
  run-now      Run release now (passes extra args to nullone run)
  simulate     Paper run against fixture (no wait)
  update       git pull and reinstall package
  timer-on     Enable weekly systemd timer
  timer-off    Disable weekly systemd timer

Environment:
  NULLONE_ENV   Path to env file (default: $OPT_DIR/.env)
  NULLONE_MODE  paper|live for timer install (default: paper)
EOF
}

cmd="${1:-}"
shift || true

case "$cmd" in
  status)
    echo "==> chrony"
    chronyc tracking 2>/dev/null || echo "chrony not available"
    echo
    echo "==> next release"
    "$NULLONE" next-release
    echo
    if systemctl is-enabled nullone-release.timer &>/dev/null; then
      echo "==> timer"
      systemctl status nullone-release.timer --no-pager || true
      systemctl list-timers nullone-release.timer --no-pager || true
    else
      echo "Timer not installed. Run: sudo deploy/install-timer.sh"
    fi
    ;;
  logs)
    LOG_DIR="${NULLONE_LOG_DIR:-$REPO_ROOT/logs/releases}"
    latest="$(ls -t "$LOG_DIR"/*.json 2>/dev/null | head -1 || true)"
    if [[ -z "$latest" ]]; then
      echo "No logs in $LOG_DIR" >&2
      exit 1
    fi
    echo "$latest"
    cat "$latest"
    ;;
  tail-logs)
    journalctl -u nullone-release.service -f
    ;;
  next-release)
    "$NULLONE" next-release
    ;;
  dry-run)
    "$NULLONE" dry-run "$@"
    ;;
  discover)
    "$NULLONE" discover "$@"
    ;;
  run-now)
    "$NULLONE" run "$@"
    ;;
  simulate)
    "$NULLONE" run --mode paper --simulate "$@"
    ;;
  update)
    cd "$REPO_ROOT"
    git pull
    source "$REPO_ROOT/.venv/bin/activate"
    pip install -e .
    echo "Updated $(git rev-parse --short HEAD)"
    ;;
  timer-on)
    exec sudo "$SCRIPT_DIR/install-timer.sh"
    ;;
  timer-off)
    sudo systemctl disable --now nullone-release.timer
    echo "Disabled nullone-release.timer"
    ;;
  ""|-h|--help|help)
    usage
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    usage >&2
    exit 1
    ;;
esac
