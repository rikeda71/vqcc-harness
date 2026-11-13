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
if [ -n "$sid" ]; then
  printf '%s\n' "$sid" > "$wdir/session_id"
  # Engine of that session (claude | agy, harness/hooks/agy.sh): a resume must use the same engine.
  printf '%s\n' "${VQCC_ENGINE:-claude}" > "$wdir/session_engine"
fi
# A new strategist directive arrived mid-session: tell the worker (as hook context) once per update.
d="$ROOT/work/$worker/DIRECTIVE.md"
if [ -f "$d" ]; then
  m=$(/usr/bin/stat -f %m "$d" 2>/dev/null || echo 0); seen=$(cat "$wdir/directive_seen" 2>/dev/null || echo 0)
  ev="$(printf '%s' "$input" | jq -r '.hook_event_name // empty' 2>/dev/null)"
  if [ "$m" -gt "$seen" ] 2>/dev/null && [ -n "$ev" ]; then
    echo "$m" > "$wdir/directive_seen"
    jq -n --arg ev "$ev" --arg c "(harness) 作戦参謀 (strategist) が work/${worker}/DIRECTIVE.md を更新しました。今の操作が一段落したら読み、skill optimizing-vqcc-circuits Step 2 に従って「次にやること」と進め方を指令に合わせてください (指令に従わない場合は根拠を NOTES.md「指令との差分」に書く)。" \
      '{hookSpecificOutput: {hookEventName: $ev, additionalContext: $c}}'
  fi
fi
exit 0
