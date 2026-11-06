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

left=$(vqcc_min_to_end)

# Context rotation (avoid auto-compaction): the worker was asked to hand off -> let it end; the
# supervisor sees state/workers/<w>/rotate and starts a fresh session.
if [ -f "$wdir/rotate_asked" ]; then
  echo "context $(cat "$wdir/rotate_asked") tokens" > "$wdir/rotate"
  allow "rotate to a fresh session (context $(cat "$wdir/rotate_asked") tokens)"
fi
# Current context size = input side of the last assistant turn in the transcript.
tp=$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null)
ctx=0
if [ -n "$tp" ] && [ -f "$tp" ]; then
  ctx=$(tail -n 400 "$tp" | jq -Rn '[inputs | fromjson? | select(.type == "assistant" and .message.usage != null)
        | .message.usage | (.input_tokens // 0) + (.cache_read_input_tokens // 0) + (.cache_creation_input_tokens // 0)]
        | last // 0' 2>/dev/null || echo 0)
fi
if [ "${ctx:-0}" -ge "$VQCC_ROTATE_TOKENS" ] 2>/dev/null && [ "$left" -gt "$VQCC_WRAPUP_MIN" ]; then
  echo "$ctx" > "$wdir/rotate_asked"
  vqcc_log "stop-hook[$worker]: context $ctx >= $VQCC_ROTATE_TOKENS, asking for hand-off"
  msg="コンテキストが約 ${ctx} トークンに達したため、この後 supervisor が新しいセッションに切り替えます (compaction 回避)。新しいセッションは会話履歴を引き継がず work/${worker}/NOTES.md だけを頼りに再開するので、次を行ってから短く報告して停止してください: (1) NOTES.md の「現在地」「進行中」「次にやること」を最新化する。走行中の remote job id・バックグラウンド実行中のローカル検証 (ログファイルのパスと候補) ・途中の仮説や判明した知見も漏れなく書く。(2) 数分で終わるローカル検証は完了を待って ledger に記録し、長いものはそのまま走らせて NOTES に記録する。(3) git -C work add -A && git -C work commit。新しい実験は始めない。"
  jq -n --arg r "$msg" '{decision: "block", reason: $r}'
  exit 0
fi

n=$(( $(cat "$wdir/continues" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$wdir/continues"
if [ "$left" -le "$VQCC_WRAPUP_MIN" ]; then
  msg="夜間ウィンドウ終了まで残り約 ${left} 分。新しい長時間実験は始めず、走行中の検証結果を回収して ledger に記録し、work/${worker}/NOTES.md の「現在地」「次にやること」を更新してコミット (work/ のローカル git) してください。終わったら短く報告して停止してよい。"
else
  read -r five _ seven _ < <(vqcc_usage)
  msg="続行してください (継続 #${n}, 窓の残り約 ${left} 分, 5h 使用率 ${five}%)。work/${worker}/NOTES.md の「次にやること」から最も期待値の高い実験を選び、仮説→実装→bin/vqcc-verify で評価→ledger 記録→NOTES 更新のループを回す。検証待ちの間は次の候補を準備する。ユーザーへの確認は不要 (誰も見ていない)。行き詰まったら別のアプローチに切り替え、その理由を NOTES に残す。"
fi
jq -n --arg r "$msg" '{decision: "block", reason: $r}'
