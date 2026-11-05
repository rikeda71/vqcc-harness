# Shared configuration for the night harness (sourced by bash scripts).
# Override any value in state/config.local.sh (gitignored).

VQCC_ROOT="${VQCC_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)}"
VQCC_STATE="$VQCC_ROOT/state"

# Night window, local time (HHMM). Workers run only inside [start, end).
VQCC_WINDOW_START="${VQCC_WINDOW_START:-0030}"
VQCC_WINDOW_END="${VQCC_WINDOW_END:-0730}"
# Minutes before window end when workers are told to wrap up (write notes, finish runs).
VQCC_WRAPUP_MIN="${VQCC_WRAPUP_MIN:-20}"

# Claude subscription guard: stop all workers when the 5h window usage reaches this (%).
VQCC_FIVE_HOUR_LIMIT="${VQCC_FIVE_HOUR_LIMIT:-90}"
# Also stop when the 7-day usage reaches this (%), to leave the user's weekly budget alone.
VQCC_SEVEN_DAY_LIMIT="${VQCC_SEVEN_DAY_LIMIT:-90}"
# After a 5h stop, resume workers once the window has reset (if still inside the night window).
VQCC_RESUME_AFTER_RESET="${VQCC_RESUME_AFTER_RESET:-1}"

# Workers to run (one interactive Claude Code session per task).
VQCC_WORKERS="${VQCC_WORKERS:-invsqrt dqi dfthcsos}"
VQCC_TMUX_SESSION="${VQCC_TMUX_SESSION:-vqcc}"
VQCC_CLAUDE_BIN="${VQCC_CLAUDE_BIN:-$(command -v claude || echo "$HOME/.local/bin/claude")}"
VQCC_PERMISSION_MODE="${VQCC_PERMISSION_MODE:-auto}"

# Submissions: Kaggle allows 5/day (UTC); the harness uses at most this many.
VQCC_MAX_SUBMITS_PER_DAY="${VQCC_MAX_SUBMITS_PER_DAY:-4}"
VQCC_COMPETITION="google-verifiable-quantum-circuit-compilation"

# Obsidian daily-note logging of the morning report (empty = disabled).
VQCC_OBSIDIAN_APPEND="${VQCC_OBSIDIAN_APPEND:-$HOME/.claude/skills/obsidian-daily/scripts/append_log.sh}"

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/run/current-system/sw/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

[ -f "$VQCC_STATE/config.local.sh" ] && . "$VQCC_STATE/config.local.sh"

mkdir -p "$VQCC_STATE" "$VQCC_STATE/workers" "$VQCC_STATE/logs"

vqcc_log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >> "$VQCC_STATE/logs/supervisor.log"; }

# --- usage helpers -----------------------------------------------------------
# state/usage.json is written by harness/statusline.sh on every status-line refresh of any
# Claude session started in this repo. Prints "<five_hour_pct> <five_hour_resets_at> <seven_day_pct> <age_s>".
vqcc_usage() {
  local f="$VQCC_STATE/usage.json"
  [ -f "$f" ] || { echo "- - - -"; return; }
  jq -r --argjson now "$(date +%s)" '
    [ (.five_hour_pct // "-"), (.five_hour_resets_at // "-"), (.seven_day_pct // "-"),
      ($now - (.ts // 0)) ] | map(tostring) | join(" ")' "$f" 2>/dev/null || echo "- - - -"
}

# 0 (true) when usage is at/over a limit. Unknown usage counts as under the limit.
vqcc_over_limit() {
  local five resets seven age
  read -r five resets seven age < <(vqcc_usage)
  local now; now=$(date +%s)
  # Fallback when the status line carries no five_hour data: a worker pane showed a limit message
  # (written by the supervisor). Treat as over the limit for an hour, then retry.
  local hit; hit=$(cat "$VQCC_STATE/limit_hit" 2>/dev/null || echo 0)
  [ $(( now - hit )) -lt 3600 ] 2>/dev/null && return 0
  if [ "$five" != "-" ] && [ "$resets" != "-" ] && [ "$resets" -gt "$now" ] 2>/dev/null; then
    awk -v a="$five" -v b="$VQCC_FIVE_HOUR_LIMIT" 'BEGIN{exit !(a>=b)}' && return 0
  fi
  if [ "$seven" != "-" ]; then
    awk -v a="$seven" -v b="$VQCC_SEVEN_DAY_LIMIT" 'BEGIN{exit !(a>=b)}' && return 0
  fi
  return 1
}

# --- window helpers ----------------------------------------------------------
_hhmm_to_min() { local t=$((10#$1)); echo $(( (t / 100) * 60 + t % 100 )); }
_now_min() { echo $(( 10#$(date +%H) * 60 + 10#$(date +%M) )); }

# 0 (true) when now is inside [start, end) (handles windows that cross midnight).
vqcc_in_window() {
  local s e n; s=$(_hhmm_to_min "$VQCC_WINDOW_START"); e=$(_hhmm_to_min "$VQCC_WINDOW_END"); n=$(_now_min)
  if [ "$s" -le "$e" ]; then [ "$n" -ge "$s" ] && [ "$n" -lt "$e" ]; else [ "$n" -ge "$s" ] || [ "$n" -lt "$e" ]; fi
}

# Minutes until the window ends (only meaningful inside the window).
vqcc_min_to_end() {
  local e n; e=$(_hhmm_to_min "$VQCC_WINDOW_END"); n=$(_now_min)
  echo $(( (e - n + 1440) % 1440 ))
}
