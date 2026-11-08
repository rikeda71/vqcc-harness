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
2. Read, in this order, whatever exists: `work/<task>/DIRECTIVE.md` (the strategist's current orders,
   see Step 2), `work/<task>/NOTES.md`, `work/<task>/BRIEF.md`,
   `work/BRIEF_OVERVIEW.md`, `work/HARNESS_ISSUES.md`. On your first session (or when NOTES.md does not
   mention it yet), also read `docs/intel/kaggle-vqcc-primer.md` (cost tables, score trade-offs, verifier
   behaviour, invsqrt approximation from an external guide) and note in NOTES.md which ideas apply.
3. Run `bin/vqcc-best` to see the verified best per task and the predicted LB score.
4. Run `bin/vqcc-remote status` to see remote jobs still in flight for this task.
5. If `work/<task>/NOTES.md` does not exist, create it from the template below before any experiment.

## Step 2: Choose the next experiment

When `work/<task>/DIRECTIVE.md` exists, it sets this task's target, mode (TUNE / RESTRUCTURE / IMPORT /
HOLD) and ranked experiments. The strategist (skill `planning-vqcc-strategy`) wrote it from the whole
competition's standing, which this worker cannot see. Follow it:
- Put its 優先実験 at the top of "次にやること" in that order, and drop what it lists under やめること.
  Follow its stop rules: when an item's stop rule fires, record why and go to the next item.
- Stop rules judge progress, not arrival. While each attempt moves the item's metric (stage cost,
  error, λ, qubit peak, quick ratio) toward its target, keep going past the time box (up to 2×) even if
  the target looks out of reach: a smaller verified gain still counts. Stop an item only after at least
  2 different methods for its blocker gave no improvement, and record the methods and numbers.
- Call something no-go only on evidence (a proof, a BRIEF / hard-limit violation, a verifier fact, a
  measured prototype), not on a napkin estimate.
- In RESTRUCTURE mode, do not spend the session on parameter re-tuning (item 4 below) until the
  structural spike has met its stop rule.
- Deviate only on evidence the directive did not have: a ledger result, an audit failure, or a
  contradiction with BRIEF / AGENTS.md (those always win). Record each deviation in NOTES.md
  "指令との差分" with the evidence, so the next strategist pass can adjust.
- A hook notice (`(harness) 作戦参謀 ... DIRECTIVE.md を更新しました`) means the directive changed
  mid-session: re-read it when the current step is done and re-rank.

### Park when the directive is exhausted

When every 優先実験 in DIRECTIVE.md has finished or met its stop rule, do not repeat variations of
exhausted items that have stopped producing gains: that spends the shared usage budget for nothing.
Either continue with something that still makes progress, or park:

1. Continue with an idea that is not in the directive and not a variant of a stopped item, with a
   concrete spike plan (a first candidate you can build and quick-test within ~2 h, plus a progress-based
   stop rule): a structural idea against the dominant cost term (gain ≥ ~1.5% if it works while
   `seven_day_pct` in `state/usage.json` is below 50, else ≥ 3%), or, when the next medal line is within
   ×1.001 (STRATEGY.md 目標と距離), a sure small gain (a few Toffoli / T / qubits).
   Record it in NOTES.md "指令との差分" (idea, expected gain, stop rule) and run it like a directive
   item. Otherwise park: hand an idea that needs more design to the strategist through IDLE.
2. To park: record finished results (`bin/vqcc-remote status`, local logs) in NOTES. Do not wait for
   jobs still running: put their job ids / log paths in NOTES "進行中" and in IDLE, with what to do when they
   finish (e.g. "両 PASS なら save"). The supervisor polls remote jobs into the ledger and wakes you when
   all of this task's remote jobs have finished (see 4). Then write `work/<task>/IDLE` with the time
   (`YYYY-MM-DD HH:MM`), one line per exhausted item (its result), running jobs, and up to 3 structural
   ideas for the strategist with expected ratio gain. Derive them from this task's cost anatomy (which
   stage owns the dominant term, how far it is from the lowest cost any formulation could reach), not
   from "wait for the next public circuit": the strategist turns them into RESTRUCTURE spikes.
   Update NOTES.md 現在地 ("parked: DIRECTIVE 消化済み, 新指令待ち") and commit.
3. Start this wait with Bash `run_in_background: true` (absolute paths):
   `until [ ! -f <root>/work/<task>/IDLE ] || [ <root>/work/<task>/DIRECTIVE.md -nt <root>/work/<task>/IDLE ]; do sleep 60; done; echo woken`
   Then end the turn with a one-line report. The Stop hook lets a parked worker stop; the session uses
   no tokens until the strategist rewrites DIRECTIVE.md or the supervisor wakes you for finished results.
4. When woken (wait command finished, or a supervisor/hook notice), find out why:
   - `state/workers/<task>/woken` exists → the supervisor woke you because the in-flight runs finished.
     It moved your IDLE to `state/workers/<task>/IDLE.woken`; the woken file lists the new results. Do
     what IDLE.woken said to do with them (record in NOTES, save, quick → remote of a follow-up the
     directive lists), then, if the directive is still exhausted, park again from 2 (leave the woken files;
     the supervisor removes them once your new IDLE exists). Do not start unrelated work.
   - Otherwise DIRECTIVE.md was updated: delete `work/<task>/IDLE`, re-read DIRECTIVE.md and continue
     from Step 2. If the new directive still gives nothing to do (e.g. the strategist chose to keep
     this task parked), park again from 2.
5. On a session start or restart, if `work/<task>/IDLE` exists and DIRECTIVE.md is not newer than it,
   you are still parked: check for finished in-flight results only, then go straight to 3. If
   DIRECTIVE.md is newer than IDLE, or `state/workers/<task>/woken` exists, you were woken: do 4.

Without a directive, or for anything it leaves open (but see "Park" above once it is exhausted), pick from "次にやること" in NOTES.md, ranked by
(expected ratio gain) × (probability it passes) ÷ (hours). Prefer, in order:

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

## Step 4: Evaluate

1. Quick tier first: `bin/vqcc-verify <task> <path> --tier quick --label "<idea, params>"`.
   FAIL → read the error, fix, or abandon with a reason in NOTES.md.
2. Escalate to full verification (remote preferred, then a second salt for a new best) exactly as
   skill `evaluating-vqcc-candidates` Procedure 3–4 says, and read results as it describes.
3. While a full verification runs, prepare the next candidate (do not idle-wait).

## Step 5: Record

After every evaluated candidate:

1. Update NOTES.md: move the item from "進行中" to "試したこと" with cand_id, tier, status, score,
   ratio and one line of insight; update "現在のベスト" when a candidate that PASSed full/remote on two
   salts improves it;
   rewrite "次にやること" (top 5, ranked).
2. When a candidate enters "現在のベスト" (full/remote PASS on two salts), snapshot it with its generator:
   `bin/vqcc-model save <task> <path> --src work/<task>/src --cmd "<regenerate command>" --note "<what changed>"`.
   `bin/vqcc-model list <task>` shows the versions (v1, v2, …), their verified ratio and their Kaggle public LB.
3. Commit: `git -C work add -A && git -C work commit -q -m "<task>: <what changed>"`.

## Rules that override convenience

- AGENTS.md 禁止事項 and このマシンでの注意 apply throughout (correct circuits only, no submit/push,
  no edits outside `work/`, `nice` for heavy jobs, no `cd`). Write harness problems to
  `work/HARNESS_ISSUES.md` and continue with a workaround.
- Nobody answers questions at night. When one method has made no progress for ~90 minutes, write down
  why and try a different method for the same blocker; move to the next idea after 2 different methods
  gave nothing (see Step 2).
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

## 指令との差分
- YYYY-MM-DD HH:MM DIRECTIVE の項目 / 従わなかった・変えた内容 / 根拠 (cand_id・ledger・監査結果)
```
