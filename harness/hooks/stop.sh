#!/bin/bash
# Stop hook: keep a night worker going until the window closes or the usage guard trips.
# Only acts on harness workers (VQCC_WORKER set by harness/worker-launch.sh); interactive sessions a
# human opens in this repo are never forced to continue.
ROOT="$(cd "$(dirname "$0")/../.." >/dev/null 2>&1 && pwd -P)"
. "$ROOT/harness/config.sh"
input="$(cat)"
worker="${VQCC_WORKER:-}"
[ -z "$worker" ] && exit 0

wdir="$VQCC_STATE/workers/$worker"
mkdir -p "$wdir"
date +%s > "$wdir/last_stop"

allow() { vqcc_log "stop-hook[$worker]: allow stop ($1)"; echo "$1" > "$wdir/stop_reason"; exit 0; }

# The strategist does one planning pass per session: let it end and tell the supervisor it is done.
# If it stops before writing work/STRATEGY.md this pass (e.g. ended a turn to wait for a background
# command), send it back once; the supervisor's timeout still bounds the pass.
if [ "$worker" = strategist ]; then
  st=$(cat "$wdir/started" 2>/dev/null || echo 0)
  sm=$(/usr/bin/stat -f %m "$ROOT/work/STRATEGY.md" 2>/dev/null || echo 0)
  if [ "$sm" -lt "$st" ] && [ ! -f "$wdir/sent_back" ] && ! [ -f "$VQCC_STATE/STOP" ]; then
    date +%s > "$wdir/sent_back"
    vqcc_log "stop-hook[$worker]: STRATEGY.md not written yet, sending back once"
    jq -n --arg r "work/STRATEGY.md がまだ今回の pass で更新されていません。skill planning-vqcc-strategy の Step 5 (STRATEGY.md・各 DIRECTIVE.md・strategy/log.md の書き込みと work/ のコミット) を完了してから短く報告して停止してください。バックグラウンド処理を待つ必要はありません。" \
      '{decision: "block", reason: $r}'
    exit 0
  fi
  date +%s > "$wdir/done"; allow "strategist pass finished"
fi

[ -f "$VQCC_STATE/STOP" ] && allow "STOP file present"
[ -f "$wdir/STOP" ] && allow "worker STOP file present"
vqcc_in_window || allow "outside night window"
vqcc_over_limit && allow "usage guard: $(vqcc_usage)"
# Directive exhausted: the worker parked itself (work/<w>/IDLE) and waits, without using tokens, for
# the strategist to rewrite DIRECTIVE.md (its background wait command then wakes the session).
vqcc_parked "$worker" && allow "parked: directive exhausted, waiting for a new DIRECTIVE.md"

left=$(vqcc_min_to_end)

# Current context size = input side of the last assistant turn in the transcript.
tp=$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null)
ctx=0
if [ -n "$tp" ] && [ -f "$tp" ]; then
  ctx=$(tail -n 400 "$tp" | jq -Rn '[inputs | fromjson? | select(.type == "assistant" and .message.usage != null)
        | .message.usage | (.input_tokens // 0) + (.cache_read_input_tokens // 0) + (.cache_creation_input_tokens // 0)]
        | last // 0' 2>/dev/null || echo 0)
fi
# Background subagents of this session write <session>/subagents/agent-*.jsonl; one written in the
# last VQCC_SUBAGENT_IDLE_MIN minutes counts as still running.
sadir="${tp%.jsonl}/subagents"
subagents_active() {
  [ -n "$tp" ] && [ -d "$sadir" ] || return 1
  [ -n "$(find "$sadir" -name 'agent-*.jsonl' -mmin -"$VQCC_SUBAGENT_IDLE_MIN" 2>/dev/null | head -1)" ]
}

# Context rotation (avoid auto-compaction): the worker was asked to hand off -> let it end; the
# supervisor sees state/workers/<w>/rotate and starts a fresh session. Closing the session kills its
# background subagents, so while one runs (and the context is below the hard cap) let the session go
# idle instead: the subagent's completion wakes it, it records the result in NOTES.md and stops again.
# The supervisor rotates on its own if the wait outlives the subagent (state/workers/<w>/rotate_wait).
if [ -f "$wdir/rotate_asked" ]; then
  if subagents_active && [ "${ctx:-0}" -lt "$VQCC_ROTATE_HARD_TOKENS" ] 2>/dev/null; then
    [ -f "$wdir/rotate_wait" ] || printf '%s\n' "$sadir" > "$wdir/rotate_wait"
    allow "rotation deferred: background subagent still running (context ${ctx} tokens)"
  fi
  rm -f "$wdir/rotate_wait"
  echo "context $(cat "$wdir/rotate_asked") tokens" > "$wdir/rotate"
  allow "rotate to a fresh session (context $(cat "$wdir/rotate_asked") tokens)"
fi
if [ "${ctx:-0}" -ge "$VQCC_ROTATE_TOKENS" ] 2>/dev/null && [ "$left" -gt "$VQCC_WRAPUP_MIN" ]; then
  echo "$ctx" > "$wdir/rotate_asked"
  vqcc_log "stop-hook[$worker]: context $ctx >= $VQCC_ROTATE_TOKENS, asking for hand-off"
  msg="コンテキストが約 ${ctx} トークンに達したため、この後 supervisor が新しいセッションに切り替えます (compaction 回避)。新しいセッションは会話履歴を引き継がず work/${worker}/NOTES.md だけを頼りに再開するので、次を行ってから短く報告して停止してください: (1) NOTES.md の「現在地」「進行中」「次にやること」を最新化する。走行中の remote job id・バックグラウンド実行中のローカル検証 (ログファイルのパスと候補) ・途中の仮説や判明した知見も漏れなく書く。(2) 数分で終わるローカル検証は完了を待って ledger に記録し、長いものはそのまま走らせて NOTES に記録する。(3) git -C work add -A && git -C work commit。新しい実験は始めない。新しい subagent も起動しない。(4) バックグラウンドの subagent が走行中なら、その目的と成果物の置き場所を NOTES.md に書いてから停止する。切り替えは subagent の完了まで待つ。完了通知で起こされたら、結果を NOTES.md に記録してコミットし、再び停止すること。"
  jq -n --arg r "$msg" '{decision: "block", reason: $r}'
  exit 0
fi

n=$(( $(cat "$wdir/continues" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$wdir/continues"
if [ "$left" -le "$VQCC_WRAPUP_MIN" ]; then
  # Send the wrap-up instruction once; when the worker stops again it has wrapped up, so let it end
  # (re-sending the same message made finished workers spin empty turns until the window closed).
  [ -f "$wdir/wrapup_sent" ] && allow "wrap-up done (window ends in ${left} min)"
  date +%s > "$wdir/wrapup_sent"
  msg="夜間ウィンドウ終了まで残り約 ${left} 分。新しい長時間実験は始めず、走行中の検証結果を回収して ledger に記録し、work/${worker}/NOTES.md の「現在地」「次にやること」を更新してコミット (work/ のローカル git) してください。終わったら短く報告して停止してよい。"
else
  read -r five _ seven _ < <(vqcc_usage)
  msg="続行してください (継続 #${n}, 窓の残り約 ${left} 分, 5h 使用率 ${five}%)。work/${worker}/NOTES.md の「次にやること」から最も期待値の高い実験を選び、仮説→実装→bin/vqcc-verify で評価→ledger 記録→NOTES 更新のループを回す。検証待ちの間は次の候補を準備する。ユーザーへの確認は不要 (誰も見ていない)。行き詰まったら別のアプローチに切り替え、その理由を NOTES に残す。ただし DIRECTIVE.md の優先実験がすべて停止条件に達し、走行中の検証も無いなら、小さな再チューニングで時間を埋めず skill optimizing-vqcc-circuits の「Park」手順で待機する。"
fi
jq -n --arg r "$msg" '{decision: "block", reason: $r}'
