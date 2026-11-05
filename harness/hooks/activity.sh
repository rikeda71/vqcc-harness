#!/bin/bash
# PostToolUse / UserPromptSubmit hook: heartbeat for night workers (clears the waiting flag)
# and records the worker's Claude session id so the supervisor can --resume it after a restart.
ROOT="$(cd "$(dirname "$0")/../.." >/dev/null 2>&1 && pwd -P)"
worker="${VQCC_WORKER:-}"
[ -z "$worker" ] && exit 0
input="$(cat)"
wdir="$ROOT/state/workers/$worker"; mkdir -p "$wdir"
date +%s > "$wdir/heartbeat"
rm -f "$wdir/waiting"
sid="$(printf '%s' "$input" | jq -r '.session_id // empty' 2>/dev/null)"
[ -n "$sid" ] && printf '%s\n' "$sid" > "$wdir/session_id"
exit 0
