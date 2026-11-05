#!/bin/bash
# Notification hook: record when a night worker is waiting (permission prompt / idle) so the
# supervisor can nudge it. Writes state/workers/<worker>/waiting = "<epoch> <type>".
ROOT="$(cd "$(dirname "$0")/../.." >/dev/null 2>&1 && pwd -P)"
. "$ROOT/harness/config.sh"
worker="${VQCC_WORKER:-}"
[ -z "$worker" ] && exit 0
input="$(cat)"
type="$(printf '%s' "$input" | jq -r '.notification_type // "unknown"' 2>/dev/null)"
msg="$(printf '%s' "$input" | jq -r '.message // ""' 2>/dev/null | head -c 300)"
wdir="$VQCC_STATE/workers/$worker"; mkdir -p "$wdir"
echo "$(date +%s) $type" > "$wdir/waiting"
vqcc_log "notify[$worker]: $type: $msg"
exit 0
