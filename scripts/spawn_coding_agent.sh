#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <prompt-file>" >&2
  echo "Example: $0 $REPO_DIR/CODING_AGENT.md" >&2
  exit 1
fi

PROMPT_FILE="$1"
if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "Prompt file not found: $PROMPT_FILE" >&2
  exit 1
fi

CODEX_MODEL="${CODEX_MODEL:-gpt-5.3-codex}"

codex --model "$CODEX_MODEL" --ask-for-approval never --sandbox danger-full-access exec --cd "$REPO_DIR" "$(cat "$PROMPT_FILE")"
