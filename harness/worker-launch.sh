#!/bin/bash
# Started by bin/night-supervisor inside a herdr pane: runs one interactive Claude Code worker.
#   harness/worker-launch.sh <task> <night_id> [<session_id to resume> | fresh]
#   (fresh = new session after a context rotation; hand-off is in work/<task>/NOTES.md)
# Building the prompt here (not on the pane's command line) keeps quoting and multibyte text
# out of the interactive shell.
set -u
ROOT="$(cd "$(dirname "$0")/.." >/dev/null 2>&1 && pwd -P)"
. "$ROOT/harness/config.sh"
w="$1"; night="$2"; resume_id="${3:-}"
export VQCC_WORKER="$w" VQCC_NIGHT_ID="$night"

if [ "$w" = strategist ]; then
  prompt="あなたは VQCC 作戦参謀 (strategist) です。AGENTS.md (特に「作戦参謀」節) を読み、skill planning-vqcc-strategy の手順に従って作戦会議を 1 回行ってください: 順位とメダル/目標までの距離の把握、上位手法の推定、目標からの逆算、タスクごとの方針 (チューニング継続か抜本的な構造変更か) の決定、work/STRATEGY.md と各タスクの work/<task>/DIRECTIVE.md の更新。回路の作成・検証はしません。人間は見ていないので確認や質問はせず自分で判断すること。書き終えたら work/ をコミットし、短く報告して停止してください。"
  exec "$VQCC_CLAUDE_BIN" --permission-mode "$VQCC_PERMISSION_MODE" -n "vqcc-strategist" "$prompt"
fi
if [ "$resume_id" = fresh ]; then
  prompt="あなたは VQCC 夜間ワーカー「${w}」です。コンテキスト節約のため supervisor が新しいセッションに切り替えました (前セッションの会話は引き継がれません)。AGENTS.md を読み、skill optimizing-vqcc-circuits の手順に従って task=${w} の回路最適化を続けてください。前セッションからの引き継ぎは work/${w}/NOTES.md の「現在地」「進行中」「次にやること」にあります。bin/vqcc-best と bin/vqcc-remote status で走行中・完了済みの検証も確認してから再開すること。"
  exec "$VQCC_CLAUDE_BIN" --permission-mode "$VQCC_PERMISSION_MODE" -n "vqcc-${w}" "$prompt"
fi
if [ -n "$resume_id" ]; then
  prompt="supervisor により再起動されました (使用量ガードからの再開またはプロセス再起動)。work/${w}/NOTES.md と ledger (bin/vqcc-best) で現在地を確認し、続きを進めてください。"
  exec "$VQCC_CLAUDE_BIN" --permission-mode "$VQCC_PERMISSION_MODE" -n "vqcc-${w}" --resume "$resume_id" "$prompt"
fi
prompt="あなたは VQCC 夜間ワーカー「${w}」です。AGENTS.md を読み、skill optimizing-vqcc-circuits の手順に従って task=${w} の回路最適化を自律的に進めてください。まず work/${w}/NOTES.md (無ければ work/${w}/BRIEF.md と work/BRIEF_OVERVIEW.md) と bin/vqcc-best で現在地を把握してから始めること。"
exec "$VQCC_CLAUDE_BIN" --permission-mode "$VQCC_PERMISSION_MODE" -n "vqcc-${w}" "$prompt"
