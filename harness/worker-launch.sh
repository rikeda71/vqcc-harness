#!/bin/bash
# Started by bin/night-supervisor inside a herdr pane: runs one interactive worker session.
#   harness/worker-launch.sh <task> <night_id> [<session_id to resume> | fresh]
#   (fresh = new session after a context rotation; hand-off is in work/<task>/NOTES.md)
# Building the prompt here (not on the pane's command line) keeps quoting and multibyte text
# out of the interactive shell.
# The engine is chosen per session (vqcc_engine in harness/config.sh): Claude Code, or agy (Antigravity
# CLI, Claude Opus via Google) while the Claude subscription usage is high (harness/AGY.md).
set -u
ROOT="$(cd "$(dirname "$0")/.." >/dev/null 2>&1 && pwd -P)"
. "$ROOT/harness/config.sh"
w="$1"; night="$2"; resume_id="${3:-}"
export VQCC_WORKER="$w" VQCC_NIGHT_ID="$night"
# Shell fixes for every Bash tool call (unalias date, sed shim): see harness/worker-env.sh.
export VQCC_ROOT="$ROOT" CLAUDE_ENV_FILE="$ROOT/harness/worker-env.sh"
[ -n "$VQCC_SUBAGENT_MODEL" ] && export CLAUDE_CODE_SUBAGENT_MODEL="$VQCC_SUBAGENT_MODEL"
wdir="$VQCC_STATE/workers/$w"; mkdir -p "$wdir"

engine=$(vqcc_engine "$w")
# A session id saved by the other engine cannot be resumed here: start fresh from NOTES.md instead.
if [ -n "$resume_id" ] && [ "$resume_id" != fresh ] && [ "$(cat "$wdir/session_engine" 2>/dev/null || echo claude)" != "$engine" ]; then
  resume_id=fresh
fi
echo "$engine" > "$wdir/engine"
export VQCC_ENGINE="$engine"
vqcc_log "launch[$w]: engine=$engine ${resume_id:+($resume_id)} usage=$(vqcc_usage)"

if [ "$w" = strategist ]; then
  prompt="あなたは VQCC 作戦参謀 (strategist) です。AGENTS.md (特に「作戦参謀」節) を読み、skill planning-vqcc-strategy の手順に従って作戦会議を 1 回行ってください: 順位とメダル/目標までの距離の把握、上位手法の推定、公開情報に頼らない自前の抜本策の設計と机上試算、目標からの逆算、タスクごとの方針 (チューニング継続か抜本的な構造変更か) の決定、work/STRATEGY.md と各タスクの work/<task>/DIRECTIVE.md の更新。回路の作成・検証はしません。人間は見ていないので確認や質問はせず自分で判断すること。書き終えたら work/ をコミットし、短く報告して停止してください。"
  trig="$VQCC_STATE/workers/strategist/trigger"
  [ -s "$trig" ] && prompt="$prompt

(supervisor からの起動理由) $(cat "$trig")"
  resume_id=""
elif [ "$resume_id" = fresh ]; then
  prompt="あなたは VQCC 夜間ワーカー「${w}」です。コンテキスト節約 (または実行エンジンの切り替え) のため supervisor が新しいセッションに切り替えました (前セッションの会話は引き継がれません)。AGENTS.md を読み、skill optimizing-vqcc-circuits の手順に従って task=${w} の回路最適化を続けてください。前セッションからの引き継ぎは work/${w}/NOTES.md の「現在地」「進行中」「次にやること」にあります。bin/vqcc-best と bin/vqcc-remote status で走行中・完了済みの検証も確認してから再開すること。"
elif [ -n "$resume_id" ]; then
  prompt="supervisor により再起動されました (使用量ガードからの再開またはプロセス再起動)。work/${w}/NOTES.md と ledger (bin/vqcc-best) で現在地を確認し、続きを進めてください。"
else
  prompt="あなたは VQCC 夜間ワーカー「${w}」です。AGENTS.md を読み、skill optimizing-vqcc-circuits の手順に従って task=${w} の回路最適化を自律的に進めてください。まず work/${w}/NOTES.md (無ければ work/${w}/BRIEF.md と work/BRIEF_OVERVIEW.md) と bin/vqcc-best で現在地を把握してから始めること。"
fi
[ "$resume_id" = fresh ] && resume_id=""

if [ "$engine" = claude ]; then
  if [ "$w" = strategist ]; then
    exec "$VQCC_CLAUDE_BIN" --permission-mode "$VQCC_PERMISSION_MODE" ${VQCC_STRATEGIST_MODEL:+--model "$VQCC_STRATEGIST_MODEL"} -n "vqcc-strategist" "$prompt"
  fi
  exec "$VQCC_CLAUDE_BIN" --permission-mode "$VQCC_PERMISSION_MODE" -n "vqcc-${w}" ${resume_id:+--resume "$resume_id"} "$prompt"
fi

# --- agy ---------------------------------------------------------------------
# Hooks: .agents/hooks.json -> harness/hooks/agy.sh (same guard / stop / activity rules as Claude Code).
# Permissions: agy does not read .claude/settings.json, so its deny rules are mirrored here (a hook
# "allow" does not override them); everything else is auto-approved like --permission-mode auto.
export PATH="$ROOT/harness/shims:$PATH"
model="$VQCC_AGY_MODEL"; [ "$w" = strategist ] && model="$VQCC_AGY_STRATEGIST_MODEL"
K="$HOME/.kaggle"
settings=$(jq -cn --arg r "$ROOT" --arg k "$K" '{permissions: {deny: [
  "read_file(\($k))", "write_file(\($k))",
  "command(kaggle competitions submit)", "command(bin/vqcc-submit)", "command(\($r)/bin/vqcc-submit)",
  "command(bin/vqcc-schedule)", "command(bin/night-supervisor)", "command(launchctl)",
  "command(git push)", "command(gh)",
  "write_file(\($r)/data)", "write_file(\($r)/ledger)", "write_file(\($r)/models)",
  "write_file(\($r)/harness)", "write_file(\($r)/bin)", "write_file(\($r)/.claude)", "write_file(\($r)/.agents)",
  "write_file(\($r)/AGENTS.md)"
]}}')
prompt="このセッションは Claude Code ではなく agy (Antigravity CLI) で動いています。最初に harness/AGY.md を読み、AGENTS.md と skill (.claude/skills/<name>/SKILL.md) の Claude Code 向けの記述をそれに従って読み替えてください。

$prompt"
t0=$(date +%s)
"$VQCC_AGY_BIN" --model "$model" --dangerously-skip-permissions --settings "$settings" \
  ${resume_id:+--conversation "$resume_id"} -i "$prompt"
rc=$?
dt=$(( $(date +%s) - t0 ))
vqcc_log "launch[$w]: agy exited rc=$rc after ${dt}s"
# agy died right away (not signed in, quota, crash): do not use it for a while and run this session on
# Claude Code instead. (A supervisor close sends ctrl+c, which also ends this script.)
if [ "$dt" -lt "$VQCC_AGY_MIN_ALIVE_S" ] && [ "$rc" -ne 130 ]; then
  echo "$(date +%s) agy exited rc=$rc after ${dt}s" > "$VQCC_STATE/AGY_FAILED"
  vqcc_log "launch[$w]: agy failed fast; falling back to Claude Code (agy off for $VQCC_AGY_FAIL_COOLDOWN_MIN min)"
  echo claude > "$wdir/engine"; export VQCC_ENGINE=claude
  if [ "$w" = strategist ]; then
    exec "$VQCC_CLAUDE_BIN" --permission-mode "$VQCC_PERMISSION_MODE" ${VQCC_STRATEGIST_MODEL:+--model "$VQCC_STRATEGIST_MODEL"} -n "vqcc-strategist" "${prompt#*

}"
  fi
  exec "$VQCC_CLAUDE_BIN" --permission-mode "$VQCC_PERMISSION_MODE" -n "vqcc-${w}" "あなたは VQCC 夜間ワーカー「${w}」です。agy が起動直後に終了したため Claude Code で起動し直しました。AGENTS.md を読み、skill optimizing-vqcc-circuits の手順に従って task=${w} の回路最適化を続けてください。引き継ぎは work/${w}/NOTES.md の「現在地」「進行中」「次にやること」にあります。bin/vqcc-best と bin/vqcc-remote status で走行中・完了済みの検証も確認してから再開すること。"
fi
exit "$rc"
