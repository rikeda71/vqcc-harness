# vqcc-harness

Kaggle「Google Quantum AI - Verifiable Quantum Circuit Compilation」用の夜間自律最適化ハーネス。
`/vqcc-night` で登録すると、停止するまで毎晩 00:00–08:00 に Claude Code ワーカー (タスクごとに 1 つ) を herdr 上で動かし、
回路生成 → 公式 verifier で評価 → ledger に記録 → 条件を満たせば自動提出 → 朝レポート、までを人手なしで回す。

競技データ・回路・ledger・`work/`・`models/` はリポジトリに含めない (競技規約により private なコード共有は禁止)。

## セットアップ (初回のみ)

前提: Kaggle CLI 認証済みで競技に参加済み、`claude` CLI ログイン済み、herdr 0.9 以降、`uv`。

```bash
uv sync --frozen             # .venv に kickmix / numpy / scipy (uv.lock の版。公式 verifier と同じ版にピン留め)
kaggle competitions download -c google-verifiable-quantum-circuit-compilation -p data/raw   # 展開して data/raw/ へ
bin/vqcc-calibrate           # サンプル提出のコストを測り harness/refs.json を作る
claude                       # 一度対話起動して workspace trust を承認し、終了する
```

bin/ のコマンドは `harness/pyrun` 経由で `uv run --frozen --offline --project . python -I <script>` を実行する (uv.lock どおりの .venv、`-I` で cwd/PYTHONPATH を遮断。uv が PATH に無ければ `.venv/bin/python -I` に退避)。
新しい Python スクリプトは `harness/` に置き、`bin/` のラッパーから `exec "$ROOT/harness/pyrun" "$ROOT/harness/<x>.py" "$@"` で呼ぶ。依存を足すときは `uv add <pkg>` で pyproject.toml / uv.lock を更新してから `uv sync --frozen`。

## 開始と停止

このリポジトリで `claude` を開き、skill を実行する。

| コマンド | 動作 |
|---|---|
| `/vqcc-night` | 毎晩の実行を登録する。ウィンドウ内 (00:00–08:00) なら今夜の分を即開始、それ以外は次の 00:00 から。止め方・status の見方を案内し、そのセッションに毎朝 08:20 頃の `/vqcc-night status` を予約する (セッション限定・7 日で失効、再実行で更新) |
| `/vqcc-night status` | 登録状態・実行中か・前夜の成果 |
| `/vqcc-night stop` | 登録を解除する。実行中の夜は約 5 分で片付けて終了 (レポートは書く)。朝の status 予約も消す |

supervisor は launchd (`~/Library/LaunchAgents/com.rikeda71.vqcc-harness.plist`) から起動されるので、
skill を実行した claude セッションを閉じても、Mac を再起動しても止まらない。中身は `bin/vqcc-schedule start|stop|status`。
コンペが終了したとき、または Kaggle に接続できない状態が続いたとき (開始時に 5 分おき 6 回、夜間は 10 分おき 3 回連続で失敗)
は自動で登録を解除し `state/DISABLED` に理由を残す。再開は `/vqcc-night`。
00:00 に Mac がスリープしていると起動は復帰時になる。蓋を閉じない・自動スリープしない設定にしておく。

## 毎日の使い方

| やりたいこと | コマンド |
|---|---|
| 夜の様子を見る | `herdr --session vqcc` (タブ `supervisor` = ログ、`w-<task>` = 各ワーカー。ワーカー画面には入力しない) |
| 今夜だけ止める (登録は残す) | `touch state/STOP` (5 分の猶予後に停止) |
| 1 タスクだけ止める | `touch state/workers/<task>/STOP` |
| ウィンドウ外で手動起動 | `bin/night-supervisor --force` (自動提出はしない) |
| 毎日の成果 | 朝に macOS 通知。`reports/DAILY.md` (1 夜 1 行の推移) / `reports/<night_id>.md` (詳細) / `/vqcc-night status` |
| タスクごとのベストと予測 LB | `bin/vqcc-best` |
| 採用回路の版一覧 (ratio・Kaggle LB) | `bin/vqcc-model list` |
| Kaggle 提出履歴と public LB | `bin/vqcc-submit --sync` |
| 次に提出される内容の確認 | `bin/vqcc-submit` (`--auto --dry-run` で提出条件も判定) |
| 自動提出の停止を解除 | 原因確認後 `rm state/SUBMIT_BLOCKED` |

昼間にこのリポジトリで `claude` を開いて調査・保守してよい (hooks はワーカーにだけ作用し、提出・push は deny 済み)。

## 設定

`state/config.local.sh` (gitignore) に書くと `harness/config.sh` の既定値を上書きできる。

```bash
VQCC_WINDOW_START=0000          # 変えたら /vqcc-night を再実行
VQCC_WINDOW_END=0800
VQCC_WORKERS="invsqrt dqi dfthcsos"
VQCC_FIVE_HOUR_LIMIT=90         # Claude 5 時間枠の使用率 (%) で全ワーカー停止
VQCC_SEVEN_DAY_LIMIT=80         # 7 日枠 (日中の利用分を残す)
VQCC_MAX_SUBMITS_PER_DAY=4
VQCC_ROTATE_TOKENS=120000       # ワーカーのコンテキストがこれを超えたら新セッションに切り替え
VQCC_OBSIDIAN_APPEND=1          # 朝レポートを Obsidian daily に追記 (既定オフ)
```

## 仕組み

- **supervisor** (`bin/night-supervisor`): ワーカーの起動・死活監視・再起動、使用量ガード、
  Kaggle 上のリモート検証の回収と Kaggle 疎通・締切の確認 (10 分ごと)、自動提出、朝レポート。
- **コンテキスト管理**: ワーカーのコンテキストが `VQCC_ROTATE_TOKENS` を超えると、Stop hook が NOTES.md への引き継ぎと
  コミットを指示し、supervisor が新しいセッション (`--resume` なし) で起動し直す。auto-compaction を起こさないため。
- **ワーカー**: `AGENTS.md` と `.claude/skills/` (`optimizing-vqcc-circuits` / `evaluating-vqcc-candidates` /
  `gathering-vqcc-intel`) に従い、`work/<task>/` で回路を作る。背景知識は `docs/intel/kaggle-vqcc-primer.md`
  (束野「Google Quantum AI の Kaggle コンペ基礎知識」, CC BY 4.0, https://zenodo.org/records/23875845 の要約)。
- **評価**: `bin/vqcc-verify` (ローカル quick / full) と `bin/vqcc-remote` (Kaggle CPU notebook) が公式 verifier を走らせ、
  結果を `ledger/results.jsonl` に追記。候補は内容ハッシュで `artifacts/` に固定される。
  改善と認めるのは full/remote が全 salt で PASS したものだけ。
- **版管理**: ベスト更新時に回路と生成コードを `models/<task>/v<N>/` に保存。`current` が次に提出される版。
- **自動提出** (`bin/vqcc-submit --auto`, 開始 1.5 時間後から 2 時間ごと + 終了 15 分前):
  タスクごとの verified best を組み合わせ、次を全て満たすときだけ提出する。
  1. 同じ組み合わせを提出していない
  2. 予測 LB が過去提出の予測と実際の public LB の最大値を上回る
  3. 前回提出が採点中でない
  4. `state/SUBMIT_BLOCKED` が無い (Kaggle で ERROR、または LB が予測より 5% 以上低いと自動作成される)
  5. 1 日 4 回まで (残り 1 回は手動用)

  提出ファイルは `submissions/<UTC 時刻>/submission.zip` (この競技はファイル名が `submission.zip` 以外だと 400 で拒否される)。
  毎回の試行は `state/submissions.jsonl` に残り、`kaggle competitions submit` 自体が失敗したときは exit 1。
- **安全**: ワーカーは提出・`git push`・`gh`・`~/.kaggle` 読取・ハーネス/データ/ledger の編集が deny。
  公開ノートブックのコードは実行せず、データとして読むだけ。
