# vqcc-harness

Kaggle「Google Quantum AI - Verifiable Quantum Circuit Compilation」向けの、Claude Code (サブスクリプション) による
夜間自律最適化ハーネス。人間は介入しない前提で、夜間ウィンドウに tmux 上でタスクごとの Claude Code ワーカーを走らせ、
評価 (公式 verifier) → 記録 (ledger) → 提出 (決定的スクリプト) までを回す。

> 競技データ・生成した回路・ledger・ワーカーの作業領域 (`work/`) はこのリポジトリに含まれない (`.gitignore`)。

## 構成

| パス | 役割 |
|---|---|
| `AGENTS.md` | ワーカー (Claude Code) 向けの規約。Claude Code はこれをネイティブに読み込む |
| `.claude/skills/` | ワーカー用 skill: `optimizing-vqcc-circuits` (最適化ループ), `evaluating-vqcc-candidates` (評価と ledger), `gathering-vqcc-intel` (公開情報の安全な取り込み) |
| `.claude/settings.json` | statusLine (使用量の取得), hooks (Stop で継続 / Notification / 心拍), 権限 (提出・push・トークン読取を deny) |
| `bin/night-supervisor` | 夜間ウィンドウ中の監督プロセス。tmux セッション `vqcc` にワーカーを起動・再起動・停止し、使用量ガード、リモート検証の回収、自動提出、朝のレポートを行う |
| `bin/vqcc-verify` / `vqcc-best` / `vqcc-calibrate` | ローカル検証 (quick / full)、verified best と予測 LB、サンプル提出による ref 校正 |
| `bin/vqcc-remote` | Kaggle の CPU notebook で公式 verifier を走らせる (submit / poll / status) |
| `bin/vqcc-submit` | ledger の verified best をタスクごとに組み合わせて zip 化し、方針 (`--auto`) に従って提出 |
| `bin/vqcc-report` | `reports/<night>.md` の朝レポート + macOS 通知 + Obsidian daily への追記 |
| `bin/install-launchd` | 毎晩ウィンドウ開始時刻に supervisor を起動する LaunchAgent を登録 (`--uninstall` で解除) |
| `harness/` | 上記の実装 (`config.sh` が設定の中心) |

## セットアップ

```bash
uv sync                                   # .venv に kickmix / numpy / scipy
kaggle competitions download -c google-verifiable-quantum-circuit-compilation -p data/raw  # 展開して data/raw/ に置く
bin/vqcc-calibrate                        # サンプル提出のコストを測って harness/refs.json を作る
bin/install-launchd                       # 毎晩自動起動
```

前提: Kaggle CLI 認証済み・競技参加済み、`claude` CLI ログイン済み、tmux。初回は一度このリポジトリで
`claude` を対話起動して workspace trust を承認しておく (`.claude/settings.json` があるため)。

## 夜間運用

- 既定ウィンドウ 00:30–07:30 (ローカル時刻)。終了 20 分前から Stop hook がワーカーに「まとめて終われ」と指示する。
- 観察: `tmux attach -t vqcc` (ウィンドウ `supervisor` がログ tail、`w-<task>` が各ワーカー)。デタッチは `C-b d`。
- 止める: `touch state/STOP` (全体, 5 分の猶予後に停止) / `touch state/workers/<task>/STOP` (個別)。
- 手動起動 (ウィンドウ外でも): `bin/night-supervisor --force`。
- 朝: `reports/<night_id>.md` と `state/last_report_summary.txt`。

### 使用量ガード

- `harness/statusline.sh` が Claude Code の statusLine 入力 (`rate_limits`) を `state/usage.json` に書き出す。
- 5 時間枠の使用率が `VQCC_FIVE_HOUR_LIMIT` (既定 90%) 以上で全ワーカーを停止。`VQCC_RESUME_AFTER_RESET=1` なら
  リセット時刻 + 2 分後に (ウィンドウ内であれば) 再開する。
- 7 日枠にも `VQCC_SEVEN_DAY_LIMIT` (既定 90%) のガードを掛けている (日中の利用分を残すため)。不要なら 100 にする。

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

提出はワーカーではなく supervisor の決定的スクリプト (`bin/vqcc-submit --auto`) だけが行う。
full/remote 検証が全て PASS した候補のみを使い、予測 LB が過去提出の予測を上回るときだけ、1 日 4 回まで提出する
(Kaggle の上限 5 回のうち 1 回は手動用に残す)。

## 設計メモ

- **評価がすべて**: 改善は ledger (`ledger/results.jsonl`) の full/remote PASS 記録でのみ主張できる。
  候補は内容ハッシュで `artifacts/` に固定され、提出 zip はそこから組み立てる。
- **ワーカーの権限**: auto permission mode。提出・`git push`・`gh`・`~/.kaggle` の読取・ハーネス/データ/ledger の編集は
  `.claude/settings.json` で deny、`AGENTS.md` でも禁止。
- **herdr ではなく tmux**: herdr は TUI 中心でヘッドレスな監督 (キー送信・生死判定・再起動) をスクリプトから扱いにくいため、
  バックボーンは tmux にしている。人間は `tmux attach` でそのまま覗ける。
- 公開ノートブック・discussion の内容はデータとして扱い、そのコードは実行しない (`gathering-vqcc-intel`)。
