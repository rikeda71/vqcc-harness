# Sourced by Claude Code before every Bash command of a night worker / strategist (CLAUDE_ENV_FILE,
# exported with VQCC_ROOT by harness/worker-launch.sh). Removes shell quirks of this machine that made
# workers waste tool calls: `date` is aliased to a missing gdate, and sed is GNU (BSD `sed -i ''` failed).
unalias date 2>/dev/null
if [ -n "${VQCC_ROOT:-}" ]; then
  case ":$PATH:" in *":$VQCC_ROOT/harness/shims:"*) ;; *) export PATH="$VQCC_ROOT/harness/shims:$PATH" ;; esac
fi
true
