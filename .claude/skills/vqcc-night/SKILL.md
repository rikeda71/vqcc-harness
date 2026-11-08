---
name: vqcc-night
description: Starts, stops, restarts or shows the nightly VQCC harness schedule. Start registers a LaunchAgent that runs bin/night-supervisor every night during the window (default 00:00-08:00) until stopped, starts tonight's run at once if invoked inside the window, and schedules a daily morning report job (a self-contained prompt) in this session. Used only when the user types "/vqcc-night", "/vqcc-night stop", "/vqcc-night restart" or "/vqcc-night status".
disable-model-invocation: true
argument-hint: "[start|stop|restart|status]"
allowed-tools: Bash(bin/vqcc-schedule *), CronCreate, CronList, CronDelete
---

# vqcc-night

Control the nightly schedule with `bin/vqcc-schedule`. The supervisor runs under launchd, detached
from this Claude session: closing this session never stops it, and this session does no harness work.
The only thing tied to this session is the morning report (a session-only cron job).

A cron prompt reaches the session as plain text, and this skill cannot be invoked by the model, so the
morning job must not be `/vqcc-night status` (it would do nothing). It is the self-contained prompt below,
which the session carries out on its own. Its first line is the marker `[vqcc-morning]`.

Morning prompt (pass it verbatim as the CronCreate `prompt`):

```
[vqcc-morning] VQCC 朝の報告 (/vqcc-night start が登録した定期ジョブ)。リポジトリのルートで、cd を使わず次を行い、日本語で報告する。
1. `bin/vqcc-schedule status` を実行する。night が RUNNING なら、終了処理中であることと現状だけを報告して終える。
2. schedule が ON で night が not running のときだけ、`bin/vqcc-schedule start` を 1 回実行し、launchd の起動時刻を設定済みの夜間ウィンドウに合わせ直す (一時的なウィンドウ延長の後始末。ウィンドウ外なので夜間ランは始まらない)。出力に `not started:` があればその理由を報告に載せて続ける。schedule が OFF なら何もしない。これ以外のスケジュール操作・launchctl・提出はしない。
3. status の `last night's report` 行が示すレポートを最後まで読み、`bin/vqcc-standing` で現在の順位・メダル線・必要倍率を測る。
4. 報告: 夜の成果 (task ごとの verified ratio の変化、save した版、提出の有無と public LB)、現在の順位とメダル線との差、各 worker の work/<task>/NOTES.md の現在地と IDLE の要点、work/STRATEGY.md の「作戦参謀の裁定」と work/HARNESS_ISSUES.md の「ハーネス要望」のうち前夜の開始以降に追記されたもの (開始時刻はレポート名の night id `YYYYMMDD-HHMM`。項目先頭の日時で選び、日時の無い項目は `git -C work log -p --since=<開始時刻> -- STRATEGY.md HARNESS_ISSUES.md` の追加行で判断する)、夜間ランが途中で止まっていればその原因 (status の supervisor log の tail)。最後に今夜への推奨を 3 行以内で。
```

## Steps

1. Map the argument to one action: empty or `start` → `start`; `stop` → `stop`; `restart` → `restart`;
   `status` → `status`. Anything else: run nothing; show the usage line `/vqcc-night [start|stop|restart|status]`.
2. Run exactly one command from the repository root (no `cd`; no other commands):
   `bin/vqcc-schedule <action>`. `restart` waits for the running night to end (up to 20 min): run it with
   `run_in_background: true` and wait for its completion notice.
3. Only after `start` or `restart` succeeded (no `not started:` in the output) — set up the morning check:
   - Run CronList. Jobs whose prompt starts with `[vqcc-morning]` are the morning report. Jobs whose
     prompt is exactly `/vqcc-night status` are an old form that does nothing: CronDelete them.
   - If a `[vqcc-morning]` job exists, keep it and create nothing. Otherwise run CronCreate with
     `cron` = the expression in the output's `morning  : HH:MM (cron "...")` line,
     `prompt` = the morning prompt above, `recurring` = true, `durable` = false.
     Never use `durable: true`: durable jobs fire in every Claude session started in this repo,
     including the night workers.
4. After `stop` — run CronList and CronDelete every job whose prompt starts with `[vqcc-morning]` or is
   exactly `/vqcc-night status`.
5. Report in Japanese (formats below). If a command fails, show its output and stop.

## Report formats

`start` (at most 12 lines):
- Whether tonight's run started now (`tonight's run started now`), was already running
  (`a night is already running`; also `night    : RUNNING`), or will start at the window start; that it
  repeats every night until stopped.
- Morning report: the time from CronCreate/CronList (the actual next run, not the cron minute); that it
  lives only in this session (closing it cancels the report, not the nightly runs) and expires after
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

`restart` (at most 8 lines): that the previous night ended (its report was written) and a new run started
 now and lasts until the window end, using the code and settings as of now; that unfinished work inside the
 worker sessions was cut off (they resume from NOTES.md, remote jobs on Kaggle continue); the first
 auto-submit check comes 90 minutes after the new start. If it printed `outside the window` or `still running`,
 quote it and stop.

`stop` (at most 6 lines): the schedule is removed; a running night wraps up in about 5 minutes and still
writes its report; the morning report was cancelled (or there was none).

`status` (at most 12 lines): schedule ON/OFF (when OFF, quote the `disabled` reason and say nothing
starts until `/vqcc-night`), whether a night is running, the last night's report summary (or that there
is none yet), the trend in the recent rows of reports/DAILY.md, and the auto-submit block if shown.
Then one line per task from the `== workers` section: WORKING or PARKED (since when), and why, taken from
its `|` lines (what it finished, what it is waiting for, e.g. a remote job), plus its active remote jobs.

Do not run `bin/night-supervisor`, `launchctl`, `herdr` or any other command yourself, and do not
edit files.
