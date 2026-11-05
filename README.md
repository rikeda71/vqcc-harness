# vqcc-harness

Kaggle「Google Quantum AI - Verifiable Quantum Circuit Compilation」向けの、Claude Code (サブスクリプション) による
夜間自律最適化ハーネス。人間は介入しない前提で、夜間ウィンドウに herdr 上でタスクごとの Claude Code ワーカーを走らせ、
評価 (公式 verifier) → 記録 (ledger) → 提出 (決定的スクリプト) までを回す。

> 競技データ・生成した回路・ledger・ワーカーの作業領域 (`work/`)・回路の版管理 (`models/`) はこのリポジトリに含まれない
> (`.gitignore`。競技規約で private なコード共有が禁止されているため)。

## 構成

| パス | 役割 |
|---|---|
| `AGENTS.md` | ワーカー (Claude Code) 向けの規約。Claude Code はこれをネイティブに読み込む |
| `.claude/skills/` | ワーカー用 skill: `optimizing-vqcc-circuits` (最適化ループ), `evaluating-vqcc-candidates` (評価と ledger), `gathering-vqcc-intel` (公開情報の安全な取り込み) |
| `.claude/settings.json` | statusLine (使用量の取得), hooks (Stop で継続 / Notification / 心拍), 権限 (提出・push・トークン読取を deny) |
| `bin/night-supervisor` | 夜間ウィンドウ中の監督プロセス。herdr セッション `vqcc` にワーカーを起動・再起動・停止し、使用量ガード、リモート検証の回収、自動提出、朝のレポートを行う |
| `bin/vqcc-verify` / `vqcc-best` / `vqcc-calibrate` | ローカル検証 (quick / full)、verified best と予測 LB、サンプル提出による ref 校正 |
| `bin/vqcc-remote` | Kaggle の CPU notebook で公式 verifier を走らせる (submit / poll / status) |
| `bin/vqcc-submit` | ledger の verified best をタスクごとに組み合わせて zip 化し、方針 (`--auto`) に従って提出。`--sync` で Kaggle の提出結果 (public LB) を取り込んで履歴表示 |
| `bin/vqcc-model` | 採用回路と生成コードの版管理 (`models/<task>/v1, v2, …`, `current` = 次に提出される版)。`save` / `list` / `sync` |
| `bin/vqcc-report` | `reports/<night>.md` の朝レポート + macOS 通知 (Obsidian 追記は `VQCC_OBSIDIAN_APPEND=1` のときだけ) |
| `bin/install-launchd` | 毎晩ウィンドウ開始時刻に supervisor を起動する LaunchAgent を登録 (`--uninstall` で解除) |
| `harness/` | 上記の実装 (`config.sh` が設定の中心) |

## セットアップ

```bash
uv sync                                   # .venv に kickmix / numpy / scipy
kaggle competitions download -c google-verifiable-quantum-circuit-compilation -p data/raw  # 展開して data/raw/ に置く
bin/vqcc-calibrate                        # サンプル提出のコストを測って harness/refs.json を作る
bin/install-launchd                       # 毎晩自動起動
```

前提: Kaggle CLI 認証済み・競技参加済み、`claude` CLI ログイン済み、herdr (0.9 以降)。初回は一度このリポジトリで
`claude` を対話起動して workspace trust を承認しておく (`.claude/settings.json` があるため)。

## 夜間運用

- 既定ウィンドウ 00:00–08:00 (ローカル時刻)。終了 20 分前から Stop hook がワーカーに「まとめて終われ」と指示する。
- 観察: `herdr --session vqcc` (タブ `supervisor` がログ tail、`w-<task>` が各ワーカー)。ワーカーの画面に直接入力しない。
- 止める: `touch state/STOP` (全体, 5 分の猶予後に停止) / `touch state/workers/<task>/STOP` (個別)。
- 手動起動 (ウィンドウ外でも): `bin/night-supervisor --force`。
- 朝: `reports/<night_id>.md` と `state/last_report_summary.txt`。

### 使用量ガード

- `harness/statusline.sh` が Claude Code の statusLine 入力 (`rate_limits`) を `state/usage.json` に書き出す。
- 5 時間枠の使用率が `VQCC_FIVE_HOUR_LIMIT` (既定 90%) 以上で全ワーカーを停止。`VQCC_RESUME_AFTER_RESET=1` なら
  リセット時刻 + 2 分後に (ウィンドウ内であれば) 再開する。
- 7 日枠にも `VQCC_SEVEN_DAY_LIMIT` (既定 80%) のガードを掛けている (日中の利用分を残すため)。不要なら 100 にする。

### 設定の上書き

`state/config.local.sh` (gitignore) に書くと `harness/config.sh` の後に読み込まれる。例:

```bash
VQCC_WINDOW_START=0100
VQCC_WINDOW_END=0700
VQCC_WORKERS="invsqrt dfthcsos"
VQCC_FIVE_HOUR_LIMIT=85
```

ウィンドウ開始時刻を変えたら `bin/install-launchd` を再実行する。

### 提出方針

提出はワーカーではなく supervisor の決定的スクリプト (`bin/vqcc-submit --auto`) だけが行う
(ウィンドウ開始 1.5 時間後から 2 時間ごと + 終了 15 分前)。毎回まず Kaggle の提出一覧を
`state/kaggle_submissions.json` に取り込み (public LB・含まれていた cand_id と版)、次の条件を全て満たすときだけ提出する:

1. full/remote 検証が全て PASS した候補 (タスクごとの verified best) で構成されている
2. 同じ組み合わせを過去に提出していない
3. 予測 LB が「過去提出の予測の最大」と「Kaggle で実際に出た public LB の最大」の両方を上回る
4. 前回の提出がまだ採点中でない (6 時間以上 pending なら詰まりとみなして無視)
5. `state/SUBMIT_BLOCKED` が無い。Kaggle で ERROR になった、または public LB が予測より 5% 以上低かった提出があると
   自動で作られる (ローカル検証と LB がずれている)。原因を確認し、必要なら候補を BLOCKLIST に入れてから削除すると再開する
6. 1 日 (UTC) 4 回まで (Kaggle の上限 5 回のうち 1 回は手動用に残す)

確認: `bin/vqcc-submit --sync` (Kaggle 提出履歴と public LB)、`bin/vqcc-model list` (版ごとの verified ratio と LB)、
`state/submissions.jsonl` (提出判断の全記録と見送り理由)。

## 設計メモ

- **評価がすべて**: 改善は ledger (`ledger/results.jsonl`) の full/remote PASS 記録でのみ主張できる。
  候補は内容ハッシュで `artifacts/` に固定され、提出 zip はそこから組み立てる。
- **ワーカーの権限**: auto permission mode。提出・`git push`・`gh`・`~/.kaggle` の読取・ハーネス/データ/ledger の編集は
  `.claude/settings.json` で deny、`AGENTS.md` でも禁止。
- **herdr**: supervisor は `herdr --session vqcc server` をヘッドレスで起動し、`tab create` / `pane run` /
  `pane process-info` (生死判定) / `pane send-keys` / `pane read` (画面取得) を CLI から呼ぶ。人間は
  `herdr --session vqcc` でそのまま覗ける。
- **版管理 (`models/`)**: 提出に使う候補は `artifacts/` (内容ハッシュ) から取るが、人が追える単位として
  `models/<task>/v<N>/` に候補・生成コード・再生成コマンド・メモを保存する。ワーカーが 2 salt PASS で保存し、
  supervisor も提出前に verified best を (コード無しで) 自動保存する。
- 公開ノートブック・discussion の内容はデータとして扱い、そのコードは実行しない (`gathering-vqcc-intel`)。
