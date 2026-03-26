#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR_DEFAULT="$(cd "${SCRIPT_DIR}/.." && pwd)"
UNIT_TEMPLATE_DEFAULT="${REPO_DIR_DEFAULT}/deploy/systemd/binance-spot-strategy.service.template"
ENV_TEMPLATE_DEFAULT="${REPO_DIR_DEFAULT}/deploy/systemd/binance-spot-strategy.env.example"
DEFAULT_VENV_PYTHON="${REPO_DIR_DEFAULT}/.venv/bin/python"
DEFAULT_SYSTEM_PYTHON="$(command -v python3 || true)"

escape_sed() {
  printf '%s' "$1" | sed -e 's/[\/&]/\\&/g'
}

usage() {
  cat <<EOF
Usage: scripts/install_systemd_service.sh [options]

Options:
  --service-name NAME               systemd service name (default: binance-spot-strategy)
  --service-user USER               systemd user (default: current user)
  --service-group GROUP             systemd group (default: current group)
  --repo-dir PATH                   repo root used as WorkingDirectory
  --python PATH                     Python executable used by systemd_runtime.sh
  --config PATH                     Config path passed to src.main
  --env-file PATH                   .env path passed to src.main
  --action-mode MODE                runtime action mode (default: dry_run)
  --sleep-seconds N                 resident runtime sleep seconds (default: 60)
  --sleep-heartbeat-seconds N       resident sleep heartbeat seconds (default: 5)
  --log-level LEVEL                 CLI log level (default: INFO)
  --clear-stop-signal true|false    include runtime-start --clear-stop-signal in managed starts
  --stop-reason TEXT                runtime-stop reason (default: systemd_stop)
  --stop-timeout-seconds N          runtime-stop wait timeout (default: 110)
  --stop-poll-seconds N             runtime-stop poll interval (default: 2)
  --systemd-dir PATH                output directory for rendered unit (default: /etc/systemd/system)
  --env-dir PATH                    output directory for rendered runtime env file (default: /etc/binance-spot-strategy)
  --force                           overwrite an existing rendered runtime env file
  --help                            show this help
EOF
}

SERVICE_NAME="binance-spot-strategy"
SERVICE_USER="$(id -un)"
SERVICE_GROUP="$(id -gn)"
REPO_DIR="${REPO_DIR_DEFAULT}"
if [[ -x "${DEFAULT_VENV_PYTHON}" ]]; then
  PYTHON_BIN="${DEFAULT_VENV_PYTHON}"
elif [[ -n "${DEFAULT_SYSTEM_PYTHON}" ]]; then
  PYTHON_BIN="${DEFAULT_SYSTEM_PYTHON}"
else
  PYTHON_BIN="${REPO_DIR_DEFAULT}/.venv/bin/python"
fi
CONFIG_PATH="${REPO_DIR_DEFAULT}/config/strategy.example.yaml"
ENV_FILE_PATH="${REPO_DIR_DEFAULT}/.env"
ACTION_MODE="dry_run"
SLEEP_SECONDS="60"
SLEEP_HEARTBEAT_SECONDS="5"
LOG_LEVEL="INFO"
CLEAR_STOP_SIGNAL="true"
STOP_REASON="systemd_stop"
STOP_TIMEOUT_SECONDS="110"
STOP_POLL_SECONDS="2"
SYSTEMD_DIR="/etc/systemd/system"
ENV_DIR="/etc/binance-spot-strategy"
FORCE="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service-name)
      SERVICE_NAME="$2"
      shift 2
      ;;
    --service-user)
      SERVICE_USER="$2"
      shift 2
      ;;
    --service-group)
      SERVICE_GROUP="$2"
      shift 2
      ;;
    --repo-dir)
      REPO_DIR="$2"
      shift 2
      ;;
    --python)
      PYTHON_BIN="$2"
      shift 2
      ;;
    --config)
      CONFIG_PATH="$2"
      shift 2
      ;;
    --env-file)
      ENV_FILE_PATH="$2"
      shift 2
      ;;
    --action-mode)
      ACTION_MODE="$2"
      shift 2
      ;;
    --sleep-seconds)
      SLEEP_SECONDS="$2"
      shift 2
      ;;
    --sleep-heartbeat-seconds)
      SLEEP_HEARTBEAT_SECONDS="$2"
      shift 2
      ;;
    --log-level)
      LOG_LEVEL="$2"
      shift 2
      ;;
    --clear-stop-signal)
      CLEAR_STOP_SIGNAL="$2"
      shift 2
      ;;
    --stop-reason)
      STOP_REASON="$2"
      shift 2
      ;;
    --stop-timeout-seconds)
      STOP_TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    --stop-poll-seconds)
      STOP_POLL_SECONDS="$2"
      shift 2
      ;;
    --systemd-dir)
      SYSTEMD_DIR="$2"
      shift 2
      ;;
    --env-dir)
      ENV_DIR="$2"
      shift 2
      ;;
    --force)
      FORCE="true"
      shift
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "${UNIT_TEMPLATE_DEFAULT}" ]]; then
  echo "Missing unit template: ${UNIT_TEMPLATE_DEFAULT}" >&2
  exit 2
fi

if [[ ! -f "${ENV_TEMPLATE_DEFAULT}" ]]; then
  echo "Missing env template: ${ENV_TEMPLATE_DEFAULT}" >&2
  exit 2
fi

if [[ ! -d "${REPO_DIR}" ]]; then
  echo "Repository directory does not exist: ${REPO_DIR}" >&2
  exit 2
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python executable is not runnable: ${PYTHON_BIN}" >&2
  exit 2
fi

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Config file does not exist: ${CONFIG_PATH}" >&2
  exit 2
fi

mkdir -p "${SYSTEMD_DIR}" "${ENV_DIR}"

UNIT_PATH="${SYSTEMD_DIR}/${SERVICE_NAME}.service"
RUNTIME_ENV_FILE="${ENV_DIR}/${SERVICE_NAME}.env"

sed \
  -e "s/__SERVICE_NAME__/$(escape_sed "${SERVICE_NAME}")/g" \
  -e "s/__SERVICE_USER__/$(escape_sed "${SERVICE_USER}")/g" \
  -e "s/__SERVICE_GROUP__/$(escape_sed "${SERVICE_GROUP}")/g" \
  -e "s#__REPO_DIR__#$(escape_sed "${REPO_DIR}")#g" \
  -e "s#__RUNTIME_ENV_FILE__#$(escape_sed "${RUNTIME_ENV_FILE}")#g" \
  "${UNIT_TEMPLATE_DEFAULT}" > "${UNIT_PATH}"

chmod 0644 "${UNIT_PATH}"

if [[ -f "${RUNTIME_ENV_FILE}" && "${FORCE}" != "true" ]]; then
  ENV_RESULT="kept_existing"
else
  sed \
    -e "s#__REPO_DIR__#$(escape_sed "${REPO_DIR}")#g" \
    -e "s#__PYTHON_BIN__#$(escape_sed "${PYTHON_BIN}")#g" \
    -e "s#__CONFIG_PATH__#$(escape_sed "${CONFIG_PATH}")#g" \
    -e "s#__ENV_FILE_PATH__#$(escape_sed "${ENV_FILE_PATH}")#g" \
    -e "s/__ACTION_MODE__/$(escape_sed "${ACTION_MODE}")/g" \
    -e "s/__SLEEP_SECONDS__/$(escape_sed "${SLEEP_SECONDS}")/g" \
    -e "s/__SLEEP_HEARTBEAT_SECONDS__/$(escape_sed "${SLEEP_HEARTBEAT_SECONDS}")/g" \
    -e "s/__LOG_LEVEL__/$(escape_sed "${LOG_LEVEL}")/g" \
    -e "s/__CLEAR_STOP_SIGNAL__/$(escape_sed "${CLEAR_STOP_SIGNAL}")/g" \
    -e "s/__STOP_REASON__/$(escape_sed "${STOP_REASON}")/g" \
    -e "s/__STOP_TIMEOUT_SECONDS__/$(escape_sed "${STOP_TIMEOUT_SECONDS}")/g" \
    -e "s/__STOP_POLL_SECONDS__/$(escape_sed "${STOP_POLL_SECONDS}")/g" \
    "${ENV_TEMPLATE_DEFAULT}" > "${RUNTIME_ENV_FILE}"
  chmod 0644 "${RUNTIME_ENV_FILE}"
  ENV_RESULT="written"
fi

cat <<EOF
Installed systemd unit: ${UNIT_PATH}
Runtime env file: ${RUNTIME_ENV_FILE} (${ENV_RESULT})

Next steps:
  sudo systemctl daemon-reload
  sudo systemctl enable --now ${SERVICE_NAME}.service
  systemctl status ${SERVICE_NAME}.service
  journalctl -u ${SERVICE_NAME}.service -f
  sudo systemctl stop ${SERVICE_NAME}.service

Operator runtime commands remain available:
  ${REPO_DIR}/scripts/systemd_runtime.sh status
  ${REPO_DIR}/scripts/systemd_runtime.sh clear-stop
EOF
