# agy で動く夜間セッションの読み替え

Claude の使用量が高いとき (7 日枠が `VQCC_AGY_SEVEN_DAY_PCT` (既定 70%) 以上。または使用量ガードに達したとき)、
supervisor はワーカー (と、ガード到達時は作戦参謀も) を Claude Code ではなく agy (Antigravity CLI、Claude Opus) で起動する。
仕組みは `harness/config.sh` の `vqcc_engine`、`harness/worker-launch.sh`、`.agents/hooks.json` → `harness/hooks/agy.sh` を参照。

このファイルを読んでいるのが agy のセッションなら、AGENTS.md と skill の内容はそのまま有効で、
**役割・目的・禁止事項・評価手順・NOTES.md による引き継ぎはすべて同じ**。違うのは道具の名前だけなので、次のように読み替える。

## 読み替え表

| AGENTS.md / skill の記述 (Claude Code) | agy での実行方法 |
|---|---|
| skill `<name>` に従う | `.claude/skills/<name>/SKILL.md` を読んで従う (`.agents/skills/` からも同じものが見える)。参照ファイルも同じディレクトリにある |
| Bash tool | `run_command` |
| Bash の `run_in_background: true` で起動して完了通知を待つ | `run_command` を `WaitMsBeforeAsync` 小さめ (例 5000) で実行し、非同期化させる。終わると通知が来る。途中経過は `command_status` で確かめる。出力はログファイルにリダイレクトしておく |
| `until grep -q DONE <log>; do sleep 15; done` を background で待つ | 同じコマンドを `run_command` で非同期に実行する |
| Monitor tool | 使わない。上の非同期 `run_command` で代える |
| Read / Edit / Write tool | `read_file` / `edit_file` / `write_file` |
| Agent tool (サブエージェント) | agy のサブエージェント機能。同時 2 つまでの規則は同じ。重い調査・実装に限る |
| ToolSearch / Skill tool | 不要 (skill は上のとおりファイルを読む) |

## 注意

- 禁止事項は agy でも hook (`harness/hooks/guard.sh`) と起動時の deny 設定で強制される。拒否されたら別の許可された手段で続ける
  (拒否を回避しようとしない)。書き込めるのは `work/` だけ。
- シェルの `date` は壊れていることがあるので `/bin/date` を使う。`cd` は使わない (絶対パスか `git -C`)。
- 停止すると Stop hook (`harness/hooks/stop.sh`) が続行の指示を返す。それに従う。Park (IDLE を書いて停止) も同じ手順で、
  supervisor が入力欄に指示を送って起こす。
- コンテキストが大きくなったとき、または実行エンジンの切り替え (Claude Code ⇄ agy) のときは、Stop hook が引き継ぎを指示する。
  NOTES.md を最新化してコミットし、停止する。次のセッションは NOTES.md だけを頼りに再開する。
- agy のセッションは Claude の使用量 (`state/usage.json`) を更新しない。使用量の確認や節約は気にしなくてよい。

## 人間向け: 切り替えの操作

- `state/AGY_OFF` を作る: agy を使わない (全員 Claude Code)。
- `state/AGY_FORCE` を作る: 使用量に関係なく `VQCC_AGY_ROLES` (既定: 3 タスクのワーカー) を agy にする。
- `state/AGY_FAILED`: agy が起動直後に落ちた、またはクォータ系のエラーで止まったときに自動で書かれる。
  `VQCC_AGY_FAIL_COOLDOWN_MIN` 分 (既定 60) は agy を使わない。消すとすぐ再び使う。
- 切り替えは各セッションの次の停止時に、NOTES.md 経由の引き継ぎ (コンテキストのローテーションと同じ) で行われる。
  `bin/vqcc-schedule status` の `engine` 行に、各役割の現在のエンジンと次のセッションのエンジンが出る。
