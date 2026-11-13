# Shared configuration for the night harness (sourced by bash scripts).
# Override any value in state/config.local.sh (gitignored).

VQCC_ROOT="${VQCC_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)}"
VQCC_STATE="$VQCC_ROOT/state"

# Night window, local time (HHMM). Workers run only inside [start, end).
VQCC_WINDOW_START="${VQCC_WINDOW_START:-0000}"
VQCC_WINDOW_END="${VQCC_WINDOW_END:-0800}"
# Minutes before window end when workers are told to wrap up (write notes, finish runs).
VQCC_WRAPUP_MIN="${VQCC_WRAPUP_MIN:-20}"

# Claude subscription guard: stop all workers when the 5h window usage reaches this (%).
VQCC_FIVE_HOUR_LIMIT="${VQCC_FIVE_HOUR_LIMIT:-90}"
# Also stop when the 7-day usage reaches this (%), to leave the user's weekly budget alone.
VQCC_SEVEN_DAY_LIMIT="${VQCC_SEVEN_DAY_LIMIT:-90}"
# After a 5h stop, resume workers once the window has reset (if still inside the night window).
VQCC_RESUME_AFTER_RESET="${VQCC_RESUME_AFTER_RESET:-1}"

# Session rotation (keeps workers away from auto-compaction): when a worker's context reaches this
# many tokens, the Stop hook asks it to hand off via NOTES.md and the supervisor restarts it in a
# fresh session.
VQCC_ROTATE_TOKENS="${VQCC_ROTATE_TOKENS:-120000}"
# A rotation waits while a background subagent of the worker is still running (its transcript under
# <session>/subagents/ was written in the last VQCC_SUBAGENT_IDLE_MIN minutes), because closing the
# session kills it. The wait ends at VQCC_ROTATE_HARD_TOKENS of context (sessions compact near 165k)
# or after VQCC_ROTATE_WAIT_MAX_MIN minutes.
VQCC_ROTATE_HARD_TOKENS="${VQCC_ROTATE_HARD_TOKENS:-155000}"
VQCC_SUBAGENT_IDLE_MIN="${VQCC_SUBAGENT_IDLE_MIN:-15}"
VQCC_ROTATE_WAIT_MAX_MIN="${VQCC_ROTATE_WAIT_MAX_MIN:-90}"

# Nightly schedule (bin/vqcc-schedule start|stop|status): a LaunchAgent starts the supervisor at
# VQCC_WINDOW_START every night until stopped, or until the supervisor disables it because the
# competition is over or Kaggle stays unreachable.
VQCC_LAUNCHD_LABEL="${VQCC_LAUNCHD_LABEL:-com.rikeda71.vqcc-harness}"
# Kaggle health check (harness/kaggle_health.py): at night start, retry this many times 5 minutes
# apart before giving up; during the night it runs every 10 minutes and this many consecutive
# failures end the night. Either way the schedule is disabled.
VQCC_KAGGLE_START_RETRIES="${VQCC_KAGGLE_START_RETRIES:-6}"
VQCC_KAGGLE_FAIL_MAX="${VQCC_KAGGLE_FAIL_MAX:-3}"

# Workers to run (one interactive Claude Code session per task).
VQCC_WORKERS="${VQCC_WORKERS:-invsqrt dqi dfthcsos}"
VQCC_HERDR_SESSION="${VQCC_HERDR_SESSION:-vqcc}"  # herdr named session (attach: herdr --session vqcc)
VQCC_CLAUDE_BIN="${VQCC_CLAUDE_BIN:-$(command -v claude || echo "$HOME/.local/bin/claude")}"
VQCC_PERMISSION_MODE="${VQCC_PERMISSION_MODE:-auto}"
# Models: the strategist session runs on VQCC_STRATEGIST_MODEL (alias "fable" = latest Fable; empty =
# the CLI default, as for the task workers). Subagents started by any night session (Agent tool without
# an explicit model) default to VQCC_SUBAGENT_MODEL via CLAUDE_CODE_SUBAGENT_MODEL; fork subagents always
# inherit the parent model.
VQCC_STRATEGIST_MODEL="${VQCC_STRATEGIST_MODEL:-fable}"
VQCC_SUBAGENT_MODEL="${VQCC_SUBAGENT_MODEL:-sonnet}"

# agy (Antigravity CLI, Claude Opus via Google) as a second engine that saves the Claude subscription.
# A session is launched on agy instead of Claude Code (harness/worker-launch.sh, see harness/AGY.md) when
#   - the role is in VQCC_AGY_ROLES and the Claude 7-day usage is at/over VQCC_AGY_SEVEN_DAY_PCT (until the
#     7-day window resets), or
#   - the Claude usage guard trips (VQCC_FIVE_HOUR_LIMIT / VQCC_SEVEN_DAY_LIMIT): then every role, the
#     strategist included, runs on agy instead of pausing.
# A running session whose engine no longer matches hands off via NOTES.md at its next stop and restarts
# on the other engine (harness/hooks/stop.sh). VQCC_AGY_SEVEN_DAY_PCT=0 turns agy off.
# Manual switches: state/AGY_OFF (never use agy), state/AGY_FORCE (always use agy for VQCC_AGY_ROLES).
# An agy session that dies within VQCC_AGY_MIN_ALIVE_S seconds, or reports a quota error, writes
# state/AGY_FAILED; agy is then not used for VQCC_AGY_FAIL_COOLDOWN_MIN minutes.
VQCC_AGY_BIN="${VQCC_AGY_BIN:-$(command -v agy || echo "$HOME/.local/bin/agy")}"
VQCC_AGY_SEVEN_DAY_PCT="${VQCC_AGY_SEVEN_DAY_PCT:-70}"
VQCC_AGY_ROLES="${VQCC_AGY_ROLES:-invsqrt dqi dfthcsos}"
VQCC_AGY_MODEL="${VQCC_AGY_MODEL:-claude-opus-5-5-high}"
VQCC_AGY_STRATEGIST_MODEL="${VQCC_AGY_STRATEGIST_MODEL:-claude-opus-5-5-high}"
VQCC_AGY_MIN_ALIVE_S="${VQCC_AGY_MIN_ALIVE_S:-120}"
VQCC_AGY_FAIL_COOLDOWN_MIN="${VQCC_AGY_FAIL_COOLDOWN_MIN:-60}"

# Submissions: Kaggle allows 5/day (UTC); the harness uses at most this many.
VQCC_MAX_SUBMITS_PER_DAY="${VQCC_MAX_SUBMITS_PER_DAY:-5}"
VQCC_COMPETITION="google-verifiable-quantum-circuit-compilation"
VQCC_TEAM_NAME="${VQCC_TEAM_NAME:-ikechan}"  # our team name on the leaderboard (bin/vqcc-standing)
# Target for the strategist: gold | silver | bronze | <score>; empty = the next medal line above us.
VQCC_TARGET="${VQCC_TARGET:-}"

# Strategist ("作戦会議", skill planning-vqcc-strategy): one extra Claude session at night start and
# then every this many minutes (0 = off). It writes work/STRATEGY.md and work/<task>/DIRECTIVE.md and
# is closed after its single pass or after VQCC_STRATEGY_TIMEOUT_MIN.
VQCC_STRATEGY_INTERVAL_MIN="${VQCC_STRATEGY_INTERVAL_MIN:-240}"
VQCC_STRATEGY_TIMEOUT_MIN="${VQCC_STRATEGY_TIMEOUT_MIN:-60}"
# A worker whose directive is exhausted parks (work/<task>/IDLE, no token use) until DIRECTIVE.md changes.
# A newly parked worker brings the next strategist pass forward, but not sooner than this many
# minutes after the previous pass.
VQCC_STRATEGY_IDLE_GAP_MIN="${VQCC_STRATEGY_IDLE_GAP_MIN:-30}"
# When every worker stays parked after a pass (none woken), the strategist is re-run after the same gap,
# at most this many times a night (0 = off), so the night does not stall until the next interval.
VQCC_STRATEGY_ALL_PARKED_MAX="${VQCC_STRATEGY_ALL_PARKED_MAX:-4}"

# 0 (true) when worker $1 is parked: work/$1/IDLE exists and is not older than work/$1/DIRECTIVE.md.
vqcc_parked() {
  local i="$VQCC_ROOT/work/$1/IDLE" d="$VQCC_ROOT/work/$1/DIRECTIVE.md"
  [ -f "$i" ] && ! [ "$d" -nt "$i" ]
}

# Obsidian daily-note logging of the morning report: off by default. To enable, set it to the
# obsidian-daily append_log.sh path in state/config.local.sh.
VQCC_OBSIDIAN_APPEND="${VQCC_OBSIDIAN_APPEND:-}"

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

# --- engine helpers (Claude Code / agy) --------------------------------------
# 0 (true) when agy can be used: enabled, installed, not switched off, no recent failure.
vqcc_agy_ok() {
  [ "${VQCC_AGY_SEVEN_DAY_PCT:-0}" -gt 0 ] 2>/dev/null || return 1
  [ -x "$VQCC_AGY_BIN" ] || return 1
  [ -f "$VQCC_STATE/AGY_OFF" ] && return 1
  local f="$VQCC_STATE/AGY_FAILED" m
  if [ -f "$f" ]; then
    # "<ts> until=<epoch> ...": agy reported when its quota resets; otherwise the default cooldown.
    m=$(grep -Eo 'until=[0-9]+' "$f" 2>/dev/null | head -n 1 | cut -d= -f2)
    if [ -n "$m" ]; then
      [ "$(date +%s)" -lt "$m" ] && return 1
    else
      m=$(/usr/bin/stat -f %m "$f" 2>/dev/null || echo 0)
      [ $(( $(date +%s) - m )) -lt $(( VQCC_AGY_FAIL_COOLDOWN_MIN * 60 )) ] && return 1
    fi
  fi
  return 0
}
# 0 (true) when the Claude 7-day usage is at/over VQCC_AGY_SEVEN_DAY_PCT and that 7-day window has not
# reset yet (state/usage.json is only refreshed by Claude sessions, so it goes stale while all run on agy;
# the recorded reset time ends the agy period).
vqcc_seven_day_high() {
  local f="$VQCC_STATE/usage.json" seven resets
  [ -f "$f" ] || return 1
  read -r seven resets < <(jq -r '[(.seven_day_pct // "-"), (.seven_day_resets_at // "-")] | map(tostring) | join(" ")' "$f" 2>/dev/null)
  [ -n "$seven" ] && [ "$seven" != "-" ] || return 1
  if [ -n "$resets" ] && [ "$resets" != "-" ] && [ "$resets" -le "$(date +%s)" ] 2>/dev/null; then return 1; fi
  awk -v a="$seven" -v b="$VQCC_AGY_SEVEN_DAY_PCT" 'BEGIN{exit !(a>=b)}'
}
# Engine for a new session of role $1 (task worker or "strategist"): prints "agy" or "claude".
vqcc_engine() {
  if vqcc_agy_ok; then
    vqcc_over_limit && { echo agy; return; }
    case " $VQCC_AGY_ROLES " in
      *" $1 "*) { [ -f "$VQCC_STATE/AGY_FORCE" ] || vqcc_seven_day_high; } && { echo agy; return; } ;;
    esac
  fi
  echo claude
}

# --- window helpers ----------------------------------------------------------
_hhmm_to_min() { local t=$((10#$1)); echo $(( (t / 100) * 60 + t % 100 )); }
_now_min() { echo $(( 10#$(date +%H) * 60 + 10#$(date +%M) )); }

# 0 (true) when now is inside [start, end) (handles windows that cross midnight).
vqcc_in_window() {
  local s e n; s=$(_hhmm_to_min "$VQCC_WINDOW_START"); e=$(_hhmm_to_min "$VQCC_WINDOW_END"); n=$(_now_min)
  if [ "$s" -le "$e" ]; then [ "$n" -ge "$s" ] && [ "$n" -lt "$e" ]; else [ "$n" -ge "$s" ] || [ "$n" -lt "$e" ]; fi
}

# Minutes until the window starts next (0 inside the window).
vqcc_min_to_start() {
  vqcc_in_window && { echo 0; return; }
  local s n; s=$(_hhmm_to_min "$VQCC_WINDOW_START"); n=$(_now_min)
  echo $(( (s - n + 1440) % 1440 ))
}

# Minutes until the window ends (only meaningful inside the window).
vqcc_min_to_end() {
  local e n; e=$(_hhmm_to_min "$VQCC_WINDOW_END"); n=$(_now_min)
  echo $(( (e - n + 1440) % 1440 ))
}

# --- schedule helpers --------------------------------------------------------
vqcc_plist() { echo "$HOME/Library/LaunchAgents/$VQCC_LAUNCHD_LABEL.plist"; }
vqcc_launchd_loaded() { launchctl print "gui/$(id -u)/$VQCC_LAUNCHD_LABEL" >/dev/null 2>&1; }

# Turn the nightly schedule off: write state/DISABLED and remove the LaunchAgent plist (so it is
# gone after a reboot). The caller unloads the job (launchctl bootout) itself: when the caller is
# the supervisor started by that job, unloading kills it, so it must be the caller's last step.
vqcc_disable() {  # $1 = reason
  printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$1" > "$VQCC_STATE/DISABLED"
  rm -f "$(vqcc_plist)"
  vqcc_log "schedule disabled: $1"
  osascript -e "display notification \"$(printf '%s' "$1" | head -c 150 | tr -d '"\\')\" with title \"vqcc-harness: 夜間スケジュール停止\"" 2>/dev/null
}
