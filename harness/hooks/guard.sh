#!/bin/bash
# PreToolUse hook: enforce AGENTS.md 禁止事項 for night workers and the strategist (VQCC_WORKER set).
# Interactive sessions a human opens in this repo are not restricted here (settings.json deny still applies),
# so daytime maintenance of harness/, bin/, .claude/ keeps working.
#   - Edit/Write/NotebookEdit: inside the repo only under work/; never ~/.kaggle.
#   - Bash: no submission, schedule/launchd, supervisor, push/gh, or ~/.kaggle access.
ROOT="$(cd "$(dirname "$0")/../.." >/dev/null 2>&1 && pwd -P)"
[ -z "${VQCC_WORKER:-}" ] && exit 0
input="$(cat)"
tool="$(printf '%s' "$input" | jq -r '.tool_name // empty' 2>/dev/null)"

deny() {
  jq -n --arg r "(harness guard) $1 — AGENTS.md「禁止事項」により夜間ワーカー・作戦参謀には許可されていません。必要なら work/HARNESS_ISSUES.md に要望を書き、別の方法で続けてください。" \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $r}}'
  exit 0
}

case "$tool" in
  Edit|Write|MultiEdit|NotebookEdit)
    p="$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' 2>/dev/null)"
    [ -z "$p" ] && exit 0
    case "$p" in /*) ;; *) p="$ROOT/$p" ;; esac
    case "$p" in *"/../"*|*"/..") deny "'..' を含むパスへの書き込み ($p)" ;; esac
    case "$p" in *"/.kaggle/"*|*"/.kaggle") deny "~/.kaggle への書き込み" ;; esac
    case "$p" in
      "$ROOT/work/"*) ;;
      "$ROOT"/*|"$ROOT") deny "work/ 以外のリポジトリ内ファイルの編集 ($p)" ;;
    esac
    ;;
  Bash)
    c="$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)"
    case "$c" in
      *"competitions submit"*|*vqcc-submit*|*"harness/submit.py"*) deny "Kaggle への提出" ;;
      *launchctl*|*vqcc-schedule*|*night-supervisor*) deny "夜間スケジュール・supervisor の操作" ;;
      *"git push"*|"gh "*|*" gh "*|*"&&gh "*|*";gh "*) deny "git push / gh" ;;
      *".kaggle/"*|*"/.kaggle"*|*"~/.kaggle"*) deny "~/.kaggle へのアクセス" ;;
    esac
    ;;
esac
exit 0
