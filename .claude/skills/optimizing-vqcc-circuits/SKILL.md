---
name: optimizing-vqcc-circuits
description: This skill should be used by a VQCC night worker (environment variable VQCC_WORKER set to invsqrt, dqi or dfthcsos) to run the autonomous circuit-optimization loop for its task — resuming from work/TASK/NOTES.md, choosing the next experiment, building a candidate circuit, evaluating it with the harness tools, and recording the outcome. Triggers include the supervisor's start prompt, a Stop-hook "続行してください" continuation, a restart after the usage guard, and phrases such as "回路最適化を進めて", "次の実験", "work/TASK/NOTES.md から再開".
---

# Optimizing VQCC Circuits

## Overview

Run one task's optimization loop for the Kaggle "Verifiable Quantum Circuit Compilation" competition
without human input. The loop is: resume → pick the highest-expected-value experiment → implement →
evaluate with `bin/vqcc-verify` / `bin/vqcc-remote` → record → update notes → repeat. Progress counts
only when the ledger shows a PASS; everything else is a hypothesis.

Use this skill only inside the vqcc-harness repository as a night worker. Do not use it for harness
maintenance (editing `bin/`, `harness/`, `.claude/`) — workers never edit those.
For how to read evaluation results and the tier rules, use skill `evaluating-vqcc-candidates`.
For public notebooks and discussions, use skill `gathering-vqcc-intel`.

## Step 1: Resume (every session start or restart)

1. Read `$VQCC_WORKER` to know the task. Work only in `work/<task>/`.
2. Read, in this order, whatever exists: `work/<task>/NOTES.md`, `work/<task>/BRIEF.md`,
   `work/BRIEF_OVERVIEW.md`, `work/HARNESS_ISSUES.md`. On your first session (or when NOTES.md does not
   mention it yet), also read `docs/intel/kaggle-vqcc-primer.md` (cost tables, score trade-offs, verifier
   behaviour, invsqrt approximation from an external guide) and note in NOTES.md which ideas apply.
3. Run `bin/vqcc-best` to see the verified best per task and the predicted LB score.
4. Run `bin/vqcc-remote status` to see remote jobs still in flight for this task.
5. If `work/<task>/NOTES.md` does not exist, create it from the template below before any experiment.

## Step 2: Choose the next experiment

Pick from "次にやること" in NOTES.md, ranked by (expected ratio gain) × (probability it passes) ÷ (hours).
Prefer, in order:

0. Bootstrap: while the task's verified best is still the official sample (ratio ≈ 1), first import the
   best public circuit (skill `gathering-vqcc-intel`, Procedure step 4: `kaggle kernels output`), audit it
   (below) and verify it (an output already under `data/intel/outputs/` may be reused). Try the notebook with the highest claimed ratio for the task first; if its output
   has no usable file for the task, take the next one. Ledger records labelled `selftest…`, `locktest`,
   `queue test`, `calibrate…` or `sample…` are harness setup tests, not prior work (their verified
   mutants still count as verified candidates). Building on a verified public best beats any from-scratch idea by
   orders of magnitude.
1. Finishing verification of a candidate that already passed quick but has no full/remote result.
2. Ideas that reduce the dominant cost term of the current best (read the verifier's cost breakdown
   in the latest ledger result: Toffoli count, qubit count, T/rotation count, failure rate).
3. Structural ideas from BRIEF.md not yet tried.
4. Re-tuning parameters of the current best (cheap but small gains).

Write the hypothesis and the expected gain into NOTES.md "進行中" before implementing.

Legitimacy audit before verifying any circuit (own or public): it must be correct on **every** input of the
register contract, not only on states the verifier happens to reach. Items marked FORBIDDEN or グレー in
BRIEF.md / BRIEF_OVERVIEW.md override any recommendation elsewhere in the briefs; when in doubt (gray
area, host ruling pending), do not use the technique and note it in NOTES.md "知見".
Exception: when the task's BRIEF.md states a human-decided failure-rate policy (dqi: §6, rare failures with true
rate ≲1e-5 under the input distribution are allowed), apply that policy instead of "correct on every input".
Audit concretely: simulate the circuit (or its loader) on every address including out-of-range ones and
on arbitrary initial target words, and check outputs and phases against the contract in the task's
BRIEF.md; keep the audit script in `work/<task>/src/` and reuse it. If a public circuit fails the audit,
record it as FORBIDDEN in "知見", try the next public circuit, and if none passes, rebuild the idea
legitimately (public data files such as coefficient tables may be reused; the circuit must be ours).
The official files in `data/raw/` (reference generators, sample circuits) are competition code, not
public notebooks: copying them into `work/<task>/src/` and running them with `-I` is allowed.
A quick run before the audit is fine; the audit must pass before full/remote.

## Step 3: Implement the candidate

- Put generator code in `work/<task>/src/` and outputs in `work/<task>/out/<short-name>/`.
  Generate circuits with the `kickmix` API from `.venv/bin/python`; read the reference generators in
  `data/raw/verifiers/<task>/` (read-only) to learn the register contract.
- Keep each generator deterministic and parameterized (command-line flags), so a result in the ledger
  can be regenerated. Record the exact command in NOTES.md.
- Before any expensive evaluation, check the register contract cheaply (load the circuit, count qubits
  and gates, simulate a handful of inputs with `kickmix`).
- Run heavy own computations with `nice -n 10`, at most ~2 cores, in the background with a log file.
- Never use `cd` in Bash; use absolute paths and `git -C`.

## Step 4: Evaluate

1. Quick tier first: `bin/vqcc-verify <task> <path> --tier quick --label "<idea, params>"`.
   FAIL → read the error, fix, or abandon with a reason in NOTES.md.
2. If quick PASS and the quick ratio beats the current verified best (or is within noise of it while
   lowering risk), run a full verification — remote preferred:
   `bin/vqcc-remote submit <task> <path> --label "<same label>"`; when Kaggle quota or queue is
   exhausted, use `bin/vqcc-verify <task> <path> --tier full --label ...`.
3. While a full verification runs, prepare the next candidate (do not idle-wait).
4. Interpret results exactly as `evaluating-vqcc-candidates` describes; never report a quick score as verified.

## Step 5: Record

After every evaluated candidate:

1. Update NOTES.md: move the item from "進行中" to "試したこと" with cand_id, tier, status, score,
   ratio and one line of insight; update "現在のベスト" when a full/remote PASS improves it;
   rewrite "次にやること" (top 5, ranked).
2. When a candidate enters "現在のベスト" (full/remote PASS on two salts), snapshot it with its generator:
   `bin/vqcc-model save <task> <path> --src work/<task>/src --cmd "<regenerate command>" --note "<what changed>"`.
   `bin/vqcc-model list <task>` shows the versions (v1, v2, …), their verified ratio and their Kaggle public LB.
3. Commit: `git -C work add -A && git -C work commit -q -m "<task>: <what changed>"`.

## Rules that override convenience

- Correct circuits only. Never exploit verifier gaps (unchecked subspaces, seed/salt-specific luck,
  behaviour tuned to the test shots). The LB re-verifies with a secret salt and hosts audit winners.
- Never submit to Kaggle, push, read `~/.kaggle/`, or edit `data/`, `ledger/`, `harness/`, `bin/`, `.claude/`,
  `AGENTS.md`, `models/` (write it only through `bin/vqcc-model save`). Write harness problems to `work/HARNESS_ISSUES.md` and continue with a workaround.
- Nobody answers questions at night. When stuck for more than ~45 minutes on one idea, write down why
  and switch to the next idea.
- When the Stop hook says the window is closing, start nothing new: collect running results, update
  NOTES.md, commit, and stop.

## NOTES.md template

```markdown
# <task> NOTES

## 現在のベスト
- cand_id / tier / score / ratio / 生成コマンド

## 現在地 (最終更新: YYYY-MM-DD HH:MM)
- 1〜3 行で今どこにいるか

## 進行中
- 仮説 / 期待 gain / 状態 (実装中・quick 待ち・remote 待ち <job>)

## 次にやること (優先順)
1. ...

## 試したこと
- YYYY-MM-DD cand_id tier status score ratio — 一行の知見

## 知見
- コスト構造・verifier の挙動・失敗パターンなど再利用できる事実
```
