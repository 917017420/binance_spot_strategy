#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_RUNTIME_ENV_FILE="/etc/binance-spot-strategy/binance-spot-strategy.env"
DEFAULT_VENV_PYTHON="${DEFAULT_REPO_DIR}/.venv/bin/python"
DEFAULT_SYSTEM_PYTHON="$(command -v python3 || true)"

usage() {
  cat <<'EOF'
Usage: scripts/systemd_runtime.sh <start|stop|status|clear-stop>

Environment variables:
  BINANCE_STRATEGY_RUNTIME_ENV_FILE         Optional shell env file to source first
  BINANCE_STRATEGY_REPO_DIR                 Repo working directory
  BINANCE_STRATEGY_PYTHON                   Python executable
  BINANCE_STRATEGY_CONFIG                   Strategy config path
  BINANCE_STRATEGY_ENV_FILE                 Optional .env path passed to src.main
  BINANCE_STRATEGY_ACTION_MODE              runtime-start action mode
  BINANCE_STRATEGY_SLEEP_SECONDS            runtime-start sleep seconds
  BINANCE_STRATEGY_SLEEP_HEARTBEAT_SECONDS  runtime-start sleep heartbeat seconds
  BINANCE_STRATEGY_LOG_LEVEL                Global CLI log level
  BINANCE_STRATEGY_CLEAR_STOP_SIGNAL        true/false for runtime-start --clear-stop-signal
  BINANCE_STRATEGY_STOP_REASON              runtime-stop reason
  BINANCE_STRATEGY_STOP_TIMEOUT_SECONDS     runtime-stop --timeout-seconds
  BINANCE_STRATEGY_STOP_POLL_SECONDS        runtime-stop --poll-seconds
EOF
}

is_truthy() {
  local value
  value="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  case "$value" in
    1|true|yes|on)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

if [[ $# -lt 1 ]]; then
  usage >&2
  exit 2
fi

COMMAND="$1"

if [[ -n "${BINANCE_STRATEGY_RUNTIME_ENV_FILE:-}" && -f "${BINANCE_STRATEGY_RUNTIME_ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${BINANCE_STRATEGY_RUNTIME_ENV_FILE}"
elif [[ -f "${DEFAULT_RUNTIME_ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${DEFAULT_RUNTIME_ENV_FILE}"
fi

REPO_DIR="${BINANCE_STRATEGY_REPO_DIR:-${DEFAULT_REPO_DIR}}"
PYTHON_BIN="${BINANCE_STRATEGY_PYTHON:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "${DEFAULT_VENV_PYTHON}" ]]; then
    PYTHON_BIN="${DEFAULT_VENV_PYTHON}"
  elif [[ -n "${DEFAULT_SYSTEM_PYTHON}" ]]; then
    PYTHON_BIN="${DEFAULT_SYSTEM_PYTHON}"
  else
    echo "Unable to resolve a Python executable; set BINANCE_STRATEGY_PYTHON explicitly." >&2
    exit 2
  fi
fi
CONFIG_PATH="${BINANCE_STRATEGY_CONFIG:-${REPO_DIR}/config/strategy.example.yaml}"
ENV_FILE_PATH="${BINANCE_STRATEGY_ENV_FILE:-${REPO_DIR}/.env}"
ACTION_MODE="${BINANCE_STRATEGY_ACTION_MODE:-dry_run}"
SLEEP_SECONDS="${BINANCE_STRATEGY_SLEEP_SECONDS:-60}"
SLEEP_HEARTBEAT_SECONDS="${BINANCE_STRATEGY_SLEEP_HEARTBEAT_SECONDS:-5}"
LOG_LEVEL="${BINANCE_STRATEGY_LOG_LEVEL:-INFO}"
CLEAR_STOP_SIGNAL="${BINANCE_STRATEGY_CLEAR_STOP_SIGNAL:-false}"
STOP_REASON="${BINANCE_STRATEGY_STOP_REASON:-systemd_stop}"
STOP_TIMEOUT_SECONDS="${BINANCE_STRATEGY_STOP_TIMEOUT_SECONDS:-110}"
STOP_POLL_SECONDS="${BINANCE_STRATEGY_STOP_POLL_SECONDS:-2}"

if [[ ! -d "${REPO_DIR}" ]]; then
  echo "Repository directory does not exist: ${REPO_DIR}" >&2
  exit 2
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python executable is not runnable: ${PYTHON_BIN}" >&2
  exit 2
fi

cd "${REPO_DIR}"

BASE_CMD=(
  "${PYTHON_BIN}"
  -m src.main
  --config "${CONFIG_PATH}"
  --env-file "${ENV_FILE_PATH}"
  --log-level "${LOG_LEVEL}"
)

case "${COMMAND}" in
  start)
    CMD=(
      "${BASE_CMD[@]}"
      runtime-start
      --action-mode "${ACTION_MODE}"
      --sleep-seconds "${SLEEP_SECONDS}"
      --sleep-heartbeat-seconds "${SLEEP_HEARTBEAT_SECONDS}"
    )
    if is_truthy "${CLEAR_STOP_SIGNAL}"; then
      CMD+=(--clear-stop-signal)
    fi
    exec "${CMD[@]}"
    ;;
  stop)
    exec "${BASE_CMD[@]}" runtime-stop --reason "${STOP_REASON}" --wait --timeout-seconds "${STOP_TIMEOUT_SECONDS}" --poll-seconds "${STOP_POLL_SECONDS}"
    ;;
  status)
    exec "${BASE_CMD[@]}" runtime-status
    ;;
  clear-stop)
    exec "${BASE_CMD[@]}" clear-runner-stop
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
