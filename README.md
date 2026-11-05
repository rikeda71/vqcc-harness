# vqcc-harness

Kaggle「Google Quantum AI - Verifiable Quantum Circuit Compilation」用の夜間自律最適化ハーネス。
毎晩 00:00–08:00 に Claude Code ワーカー (タスクごとに 1 つ) を herdr 上で動かし、
回路生成 → 公式 verifier で評価 → ledger に記録 → 条件を満たせば自動提出、までを人手なしで回す。

競技データ・回路・ledger・`work/`・`models/` はリポジトリに含めない (競技規約により private なコード共有は禁止)。

## セットアップ (初回のみ)

前提: Kaggle CLI 認証済みで競技に参加済み、`claude` CLI ログイン済み、herdr 0.9 以降、`uv`。

```bash
uv sync                      # .venv に kickmix / numpy / scipy
kaggle competitions download -c google-verifiable-quantum-circuit-compilation -p data/raw   # 展開して data/raw/ へ
bin/vqcc-calibrate           # サンプル提出のコストを測り harness/refs.json を作る
claude                       # 一度対話起動して workspace trust を承認し、終了する
bin/install-launchd          # 毎晩ウィンドウ開始時刻に supervisor を起動 (--uninstall で解除)
```

## 毎日の使い方

| やりたいこと | コマンド |
|---|---|
| 夜の様子を見る | `herdr --session vqcc` (タブ `supervisor` = ログ、`w-<task>` = 各ワーカー。ワーカー画面には入力しない) |
| 全体を止める | `touch state/STOP` (5 分の猶予後に停止) |
| 1 タスクだけ止める | `touch state/workers/<task>/STOP` |
| ウィンドウ外で手動起動 | `bin/night-supervisor --force` (自動提出はしない) |
| 朝の結果 | `reports/<night_id>.md` / `state/last_report_summary.txt` |
| タスクごとのベストと予測 LB | `bin/vqcc-best` |
| 採用回路の版一覧 (ratio・Kaggle LB) | `bin/vqcc-model list` |
| Kaggle 提出履歴と public LB | `bin/vqcc-submit --sync` |
| 次に提出される内容の確認 | `bin/vqcc-submit` (`--auto --dry-run` で提出条件も判定) |
| 自動提出の停止を解除 | 原因確認後 `rm state/SUBMIT_BLOCKED` |

昼間にこのリポジトリで `claude` を開いて調査・保守してよい (hooks はワーカーにだけ作用し、提出・push は deny 済み)。

## 設定

`state/config.local.sh` (gitignore) に書くと `harness/config.sh` の既定値を上書きできる。

```bash
VQCC_WINDOW_START=0000          # 変えたら bin/install-launchd を再実行
VQCC_WINDOW_END=0800
VQCC_WORKERS="invsqrt dqi dfthcsos"
VQCC_FIVE_HOUR_LIMIT=90         # Claude 5 時間枠の使用率 (%) で全ワーカー停止
VQCC_SEVEN_DAY_LIMIT=80         # 7 日枠 (日中の利用分を残す)
VQCC_MAX_SUBMITS_PER_DAY=4
VQCC_OBSIDIAN_APPEND=1          # 朝レポートを Obsidian daily に追記 (既定オフ)
```

## 仕組み

- **supervisor** (`bin/night-supervisor`): ワーカーの起動・死活監視・再起動、使用量ガード、
  Kaggle 上のリモート検証の回収 (10 分ごと)、自動提出、朝レポート。
- **ワーカー**: `AGENTS.md` と `.claude/skills/` (`optimizing-vqcc-circuits` / `evaluating-vqcc-candidates` /
  `gathering-vqcc-intel`) に従い、`work/<task>/` で回路を作る。
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
- **安全**: ワーカーは提出・`git push`・`gh`・`~/.kaggle` 読取・ハーネス/データ/ledger の編集が deny。
  公開ノートブックのコードは実行せず、データとして読むだけ。
