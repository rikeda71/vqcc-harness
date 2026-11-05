---
name: vqcc-night
description: Starts, stops or shows the nightly VQCC harness schedule. Start registers a LaunchAgent that runs bin/night-supervisor every night during the window (default 00:00-08:00) until stopped, starts tonight's run at once if invoked inside the window, and schedules a daily morning `/vqcc-night status` in this session. Used only when the user types "/vqcc-night", "/vqcc-night stop" or "/vqcc-night status".
disable-model-invocation: true
argument-hint: "[start|stop|status]"
allowed-tools: Bash(bin/vqcc-schedule *), CronCreate, CronList, CronDelete
---

# vqcc-night

Control the nightly schedule with `bin/vqcc-schedule`. The supervisor runs under launchd, detached
from this Claude session: closing this session never stops it, and this session does no harness work.
The only thing tied to this session is the morning status check (a session-only cron job).

## Steps

1. Map the argument to one action: empty or `start` → `start`; `stop` → `stop`; `status` → `status`.
   Anything else (e.g. `restart`): run nothing; show the usage line `/vqcc-night [start|stop|status]`.
2. Run exactly one command from the repository root (no `cd`; no other commands):
   `bin/vqcc-schedule <action>`
3. Only after `start` succeeded (no `not started:` in the output) — set up the morning check:
   - Run CronList. Existing jobs whose prompt is exactly `/vqcc-night status` are the morning check.
   - If there is one, keep it and create nothing. Otherwise run CronCreate with
     `cron` = the expression in the output's `morning  : HH:MM (cron "...")` line,
     `prompt` = `/vqcc-night status`, `recurring` = true, `durable` = false.
     Never use `durable: true`: durable jobs fire in every Claude session started in this repo,
     including the night workers.
4. After `stop` — run CronList and CronDelete every job whose prompt is exactly `/vqcc-night status`.
5. Report in Japanese (formats below). If a command fails, show its output and stop.

## Report formats

`start` (at most 12 lines):
- Whether tonight's run started now (`tonight's run started now`), was already running
  (`a night is already running`; also `night    : RUNNING`), or will start at the window start; that it
  repeats every night until stopped.
- Morning check: the time from CronCreate/CronList (the actual next run, not the cron minute); that it
  lives only in this session (closing it cancels the check, not the nightly runs) and expires after
  7 days (run `/vqcc-night` again in this session to renew; it is safe while a night is running).
- Then this guide, verbatim:
  ```
  止め方: /vqcc-night stop（今夜の実行は約 5 分で片付けてレポートを書き終了。以後は起動しない）
  様子見: /vqcc-night status（随時）/ herdr --session vqcc（ワーカーの画面）/ reports/DAILY.md（夜ごとの成績）
  status の見方: schedule=毎晩起動の ON/OFF・night=今夜の実行状況・usage=Claude 使用量 (5h% / 7d%)
                 ・last night's report=前夜の結果（提出の有無・public LB）・recent nights=直近の推移
                 ・「!! auto-submit blocked」が出たら自動提出停止中（state/SUBMIT_BLOCKED を確認して削除）
  ```
- If it printed `not started: ...`: say the competition is over or Kaggle is unreachable, quote the
  reason, suggest checking the `kaggle` CLI login when it looks like an auth error (401/403), and stop
  (no cron job).

`stop` (at most 6 lines): the schedule is removed; a running night wraps up in about 5 minutes and still
writes its report; the morning check was cancelled (or there was none).

`status` (at most 8 lines): schedule ON/OFF (when OFF, quote the `disabled` reason and say nothing
starts until `/vqcc-night`), whether a night is running, the last night's report summary (or that there
is none yet), the trend in the recent rows of reports/DAILY.md, and the auto-submit block if shown.

Do not run `bin/night-supervisor`, `launchctl`, `herdr` or any other command yourself, and do not
edit files.
