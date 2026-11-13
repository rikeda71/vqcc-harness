#!/bin/bash
# agy (Antigravity CLI) hook adapter: .agents/hooks.json runs `agy.sh <event>` with agy's hook JSON on
# stdin. Converts it to the Claude Code hook format, runs the same harness hooks (guard / activity /
# stop / notify) and converts their answer back, so a night worker on agy follows the same rules.
#   pre     PreToolUse     -> guard.sh             ({"decision": allow|deny|ask})
#   post    PostToolUse    -> activity.sh          ({})
#   preinv  PreInvocation  -> activity.sh          (injectSteps: DIRECTIVE.md update notice)
#   stop    Stop           -> stop.sh, notify.sh   ({"decision":"continue"} keeps the worker going)
# Sessions a human opens with agy in this repo (VQCC_WORKER unset) get "ask" (agy's normal permission
# flow) and are otherwise left alone. agy treats an empty PreToolUse answer as deny, so always answer.
ROOT="$(cd "$(dirname "$0")/../.." >/dev/null 2>&1 && pwd -P)"
H="$ROOT/harness/hooks"
ev="${1:-}"
input="$(cat)"
worker="${VQCC_WORKER:-}"
export VQCC_ENGINE=agy

pre_answer() {  # $1 decision, $2 reason
  jq -cn --arg d "$1" --arg r "${2:-}" 'if $r == "" then {decision: $d} else {decision: $d, reason: $r} end'
  exit 0
}

case "$ev" in
  pre)
    [ -z "$worker" ] && pre_answer ask
    wdir="$ROOT/state/workers/$worker"; mkdir -p "$wdir"
    date +%s > "$wdir/heartbeat"; rm -f "$wdir/waiting"
    name="$(printf '%s' "$input" | jq -r '.toolCall.name // empty' 2>/dev/null)"
    args="$(printf '%s' "$input" | jq -c '.toolCall.args // {}' 2>/dev/null)"
    case "$args" in *".kaggle"*) pre_answer deny "(harness guard) ~/.kaggle へのアクセス — AGENTS.md「禁止事項」により許可されていません。" ;; esac
    case "$name" in
      run_command|send_command_input)
        cin="$(printf '%s' "$args" | jq -c '{tool_name: "Bash", tool_input: {command: (.CommandLine // .Input // "")}}')" ;;
      write_file|write_to_file|create_file|edit_file|replace_file_content|multi_replace_file_content|delete_file|move_file|rename_file)
        cin="$(printf '%s' "$args" | jq -c '{tool_name: "Edit", tool_input: {file_path: (.Path // .TargetFile // .AbsolutePath // .FilePath // .File // "")}}')" ;;
      *) pre_answer allow ;;
    esac
    out="$(printf '%s' "$cin" | "$H/guard.sh" 2>/dev/null)"
    if [ "$(printf '%s' "$out" | jq -r '.hookSpecificOutput.permissionDecision // empty' 2>/dev/null)" = deny ]; then
      pre_answer deny "$(printf '%s' "$out" | jq -r '.hookSpecificOutput.permissionDecisionReason // "denied"')"
    fi
    pre_answer allow
    ;;
  post)
    [ -n "$worker" ] && printf '%s' "$input" | jq -c '{session_id: .conversationId}' | "$H/activity.sh" >/dev/null 2>&1
    echo '{}'
    ;;
  preinv)
    [ -z "$worker" ] && { echo '{}'; exit 0; }
    out="$(printf '%s' "$input" | jq -c '{session_id: .conversationId, hook_event_name: "PreInvocation"}' | "$H/activity.sh" 2>/dev/null)"
    ctx="$(printf '%s' "$out" | jq -r '.hookSpecificOutput.additionalContext // empty' 2>/dev/null)"
    if [ -n "$ctx" ]; then
      jq -cn --arg c "$ctx" '{injectSteps: [{systemMessage: {systemMessage: $c}}], terminationBehavior: ""}'
    else
      echo '{}'
    fi
    ;;
  stop)
    [ -z "$worker" ] && { echo '{}'; exit 0; }
    . "$ROOT/harness/config.sh"
    wdir="$VQCC_STATE/workers/$worker"; mkdir -p "$wdir"
    reason="$(printf '%s' "$input" | jq -r '.terminationReason // empty' 2>/dev/null)"
    err="$(printf '%s' "$input" | jq -r '.error // empty' 2>/dev/null)"
    # agy quota / account trouble: hand the role back to Claude Code (vqcc_engine skips agy during the
    # cooldown) through a fresh session that resumes from NOTES.md.
    if [ -n "$err" ] && printf '%s' "$err" | grep -Eqi 'quota|rate.?limit|resource.?exhausted|429|credits|unauthenticated|sign.?in|log.?in'; then
      # "Resets in 4h24m11s": keep agy off until then (otherwise the default cooldown applies).
      now=$(date +%s); rs=$(printf '%s' "$err" | grep -Eo 'Resets in ([0-9]+h)?([0-9]+m)?([0-9]+s)?' | head -n 1)
      h=$(printf '%s' "$rs" | grep -Eo '[0-9]+h' | tr -d h); m=$(printf '%s' "$rs" | grep -Eo '[0-9]+m' | tr -d m)
      until=""; [ -n "$h$m" ] && until="until=$(( now + ${h:-0} * 3600 + ${m:-0} * 60 + 60 )) "
      printf '%s %s%s\n' "$now" "$until" "$(printf '%s' "$err" | head -c 300)" > "$VQCC_STATE/AGY_FAILED"
      vqcc_log "agy-hook[$worker]: agy error ($reason): $(printf '%s' "$err" | head -c 200); back to Claude Code"
      [ "$worker" != strategist ] && echo "agy error: back to Claude Code" > "$wdir/rotate"
      echo '{}'; exit 0
    fi
    out="$(printf '%s' "$input" | jq -c '{session_id: .conversationId, transcript_path: .transcriptPath, hook_event_name: "Stop"}' | "$H/stop.sh" 2>/dev/null)"
    r="$(printf '%s' "$out" | jq -r 'select(.decision == "block") | .reason // empty' 2>/dev/null)"
    # agy has no Notification hook: flag the worker as waiting like notify.sh does for Claude. The next
    # model invocation or tool call clears it (activity.sh / pre); if agy stays idle the supervisor nudges.
    if ! vqcc_parked "$worker"; then echo "$(date +%s) idle_prompt" > "$wdir/waiting"; fi
    if [ -n "$r" ]; then
      jq -cn --arg r "$r" '{decision: "continue", reason: $r}'
    else
      echo '{}'
    fi
    ;;
  *) echo '{}' ;;
esac
exit 0
