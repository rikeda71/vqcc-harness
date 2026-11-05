#!/bin/bash
# Project status line: records Claude subscription usage to state/usage.json (read by the
# supervisor and the Stop hook for the usage guard), then renders the user's own status line
# when present. rate_limits only exists for subscribers and only after the first response.
ROOT="$(cd "$(dirname "$0")/.." >/dev/null 2>&1 && pwd -P)"
STATE="$ROOT/state"
input="$(cat)"
mkdir -p "$STATE"
rec="$(printf '%s' "$input" | jq -c --argjson now "$(date +%s)" '
  select(.rate_limits != null) | {
    ts: $now,
    session_id: (.session_id // null),
    five_hour_pct: (.rate_limits.five_hour.used_percentage // null),
    five_hour_resets_at: (.rate_limits.five_hour.resets_at // null),
    seven_day_pct: (.rate_limits.seven_day.used_percentage // null),
    seven_day_resets_at: (.rate_limits.seven_day.resets_at // null)
  }' 2>/dev/null)"
printf '%s' "$input" | jq -c '.rate_limits' > "$STATE/rate_limits_raw.json" 2>/dev/null
if [ -n "$rec" ]; then
  tmp="$(mktemp "$STATE/.usage.XXXXXX")"
  printf '%s\n' "$rec" > "$tmp" && mv -f "$tmp" "$STATE/usage.json"
  printf '%s\n' "$rec" >> "$STATE/usage_history.jsonl"
fi
user_sl="$HOME/.dotfiles/.claude/statusline.sh"
if [ -x "$user_sl" ]; then
  printf '%s' "$input" | "$user_sl"
else
  printf 'vqcc %s' "${VQCC_WORKER:-lead}"
fi
