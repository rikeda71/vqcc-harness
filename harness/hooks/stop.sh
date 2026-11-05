#!/bin/bash
# Stop hook: keep a night worker going until the window closes or the usage guard trips.
# Only acts on harness workers (VQCC_WORKER set by bin/night-start); interactive sessions a
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

[ -f "$VQCC_STATE/STOP" ] && allow "STOP file present"
[ -f "$wdir/STOP" ] && allow "worker STOP file present"
vqcc_in_window || allow "outside night window"
vqcc_over_limit && allow "usage guard: $(vqcc_usage)"

n=$(( $(cat "$wdir/continues" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$wdir/continues"

left=$(vqcc_min_to_end)
if [ "$left" -le "$VQCC_WRAPUP_MIN" ]; then
  msg="夜間ウィンドウ終了まで残り約 ${left} 分。新しい長時間実験は始めず、走行中の検証結果を回収して ledger に記録し、work/${worker}/NOTES.md の「現在地」「次にやること」を更新してコミット (work/ のローカル git) してください。終わったら短く報告して停止してよい。"
else
  read -r five _ seven _ < <(vqcc_usage)
  msg="続行してください (継続 #${n}, 窓の残り約 ${left} 分, 5h 使用率 ${five}%)。work/${worker}/NOTES.md の「次にやること」から最も期待値の高い実験を選び、仮説→実装→bin/vqcc-verify で評価→ledger 記録→NOTES 更新のループを回す。検証待ちの間は次の候補を準備する。ユーザーへの確認は不要 (誰も見ていない)。行き詰まったら別のアプローチに切り替え、その理由を NOTES に残す。"
fi
jq -n --arg r "$msg" '{decision: "block", reason: $r}'
