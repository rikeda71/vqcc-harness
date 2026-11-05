---
name: vqcc-night
description: Starts, stops or shows the nightly VQCC harness schedule. Start registers a LaunchAgent that runs bin/night-supervisor every night during the window (default 00:00-08:00) until stopped, and starts tonight's run at once if invoked inside the window. Used only when the user types "/vqcc-night", "/vqcc-night stop" or "/vqcc-night status".
disable-model-invocation: true
argument-hint: "[start|stop|status]"
allowed-tools: Bash(bin/vqcc-schedule *)
---

# vqcc-night

Control the nightly schedule with `bin/vqcc-schedule`. The supervisor runs under launchd, detached
from this Claude session: closing this session never stops it, and this session does no harness work.

## Steps

1. Map the argument to one action: empty or `start` → `start`; `stop` → `stop`; `status` → `status`.
   Anything else (e.g. `restart`): run nothing; show the usage line `/vqcc-night [start|stop|status]`.
2. Run exactly one command from the repository root (no `cd`; no other commands):
   `bin/vqcc-schedule <action>`
3. Report the output to the user in Japanese, in at most 6 lines:
   - `start`: whether tonight's run started now (inside the window) or will start at the window start,
     that it repeats every night until `/vqcc-night stop`, and `herdr --session vqcc` to watch.
     It started now only if the output has `tonight's run started now` or `night    : RUNNING`.
     If it printed `not started: ...`, say the competition is over or Kaggle is unreachable, quote the reason,
     suggest checking the `kaggle` CLI login when the reason looks like an auth error (401/403), and stop.
   - `stop`: the schedule is removed; a running night wraps up in about 5 minutes and still writes its report.
   - `status`: schedule ON/OFF (when OFF, quote the `disabled` reason and say nothing starts until
     `/vqcc-night`), whether a night is running, the last night's report summary (or that there is none yet)
     and the trend in the recent rows of reports/DAILY.md.

Do not run `bin/night-supervisor`, `launchctl`, `herdr` or any other command yourself, and do not
edit files. If the command fails, show its output and stop.
