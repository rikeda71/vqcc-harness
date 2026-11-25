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
# Hook context for the worker (one JSON object per hook call, so the notices are joined):
#  - a new strategist directive arrived mid-session (once per update);
#  - remote results of this task landed in the ledger (the supervisor polls them asynchronously; once per
#    ledger change). The first call only records the ledger time, so a fresh state announces nothing old.
ev="$(printf '%s' "$input" | jq -r '.hook_event_name // empty' 2>/dev/null)"
[ -n "$ev" ] || exit 0
ctx=""
d="$ROOT/work/$worker/DIRECTIVE.md"
if [ -f "$d" ]; then
  m=$(/usr/bin/stat -f %m "$d" 2>/dev/null || echo 0); seen=$(cat "$wdir/directive_seen" 2>/dev/null || echo 0)
  if [ "$m" -gt "$seen" ] 2>/dev/null; then
    echo "$m" > "$wdir/directive_seen"
    ctx="(harness) 作戦参謀 (strategist) が work/${worker}/DIRECTIVE.md を更新しました。今の操作が一段落したら読み、skill optimizing-vqcc-circuits Step 2 に従って「次にやること」と進め方を指令に合わせてください (指令に従わない場合は根拠を NOTES.md「指令との差分」に書く)。"
  fi
fi
lg="$ROOT/ledger/results.jsonl"
if [ "$worker" != strategist ] && [ -f "$lg" ]; then
  m=$(/usr/bin/stat -f %m "$lg" 2>/dev/null || echo 0); seen=$(cat "$wdir/ledger_seen" 2>/dev/null || echo "")
  if [ -z "$seen" ]; then
    echo "$m" > "$wdir/ledger_seen"
  elif [ "$m" -gt "$seen" ] 2>/dev/null; then
    echo "$m" > "$wdir/ledger_seen"
    if res=$("$ROOT/harness/pyrun" "$ROOT/harness/workers.py" new-results "$worker" "$seen" 2>/dev/null); then
      ctx="${ctx:+$ctx
}(harness) 新しい remote 検証結果が ledger に入りました: ${res}。NOTES.md「進行中」の該当 job を「試したこと」に移し、2 salt PASS で best を更新したなら skill optimizing-vqcc-circuits Step 5 の save を行う。DECISIONS.md「試した手と前後の値」にも 1 行足す。"
    fi
  fi
fi
[ -n "$ctx" ] && jq -n --arg ev "$ev" --arg c "$ctx" '{hookSpecificOutput: {hookEventName: $ev, additionalContext: $c}}'
exit 0
