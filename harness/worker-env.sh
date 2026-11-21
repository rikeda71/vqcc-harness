# Sourced by Claude Code before every Bash command of a night worker / strategist (CLAUDE_ENV_FILE,
# exported with VQCC_ROOT by harness/worker-launch.sh). Removes shell quirks of this machine that made
# workers waste tool calls: `date` is aliased to a missing gdate, and sed is GNU (BSD `sed -i ''` failed).
unalias date 2>/dev/null
# zsh: `echo ====` (EQUALS expands =word to a command path -> "=== not found") and an unmatched glob
# (NOMATCH aborts the whole command line) were the most common failed Bash calls of the night workers.
if [ -n "${ZSH_VERSION:-}" ]; then setopt NO_EQUALS NO_NOMATCH 2>/dev/null; fi
if [ -n "${VQCC_ROOT:-}" ]; then
  case ":$PATH:" in *":$VQCC_ROOT/harness/shims:"*) ;; *) export PATH="$VQCC_ROOT/harness/shims:$PATH" ;; esac
fi
true
