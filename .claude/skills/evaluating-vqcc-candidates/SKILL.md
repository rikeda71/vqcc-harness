---
name: evaluating-vqcc-candidates
description: This skill should be used by a VQCC night worker to evaluate a candidate circuit with the harness — choosing between the quick, full and remote (Kaggle) tiers, running bin/vqcc-verify and bin/vqcc-remote, reading their JSON results and exit codes, checking robustness with extra salts, and interpreting bin/vqcc-best and the ledger. Triggers include "候補を評価", "quick で確認", "full 検証", "remote に投げて", "verified best", "ledger を見て", a finished candidate in work/TASK/out/, and a remote job result that needs interpreting.
---

# Evaluating VQCC Candidates

## Overview

Every claim of progress must be backed by a ledger record (`ledger/results.jsonl`) produced by the
harness tools. The tools copy the candidate into a content-addressed store (`artifacts/<task>/<cand_id>/`),
run the **official** verifier, and append one record per run. Only `full` and `remote` PASS records are
"verified" and eligible for submission.

Do not use this skill for choosing what to build (use `optimizing-vqcc-circuits`) or for public
notebooks (use `gathering-vqcc-intel`). Never run `data/raw/verifiers/*` or `harness/run_official.py`
directly: that bypasses the CPU locks shared by all workers and leaves no ledger record.

## Score

- `ratio = ref / score` per task, LB = geometric mean of the three (AGENTS.md 目的とスコア). A +1% ratio on one task = +0.33% LB. A FAIL on the LB = the whole submission is worthless, so
  correctness dominates everything.

## Tiers

| tier | command | what runs | typical wall (M2) |
|---|---|---|---|
| quick | `bin/vqcc-verify <task> <path> --tier quick --label "..."` | official `verify()` on a subset of shots | invsqrt 5 s, dqi 15 s, dfthcsos 15 s |
| full | `bin/vqcc-verify <task> <path> --tier full --label "..."` | official CLI, official default args (= LB judgment) | invsqrt 8–11 min, dqi 4–6 min, dfthcsos 0.5–1 min |
| remote | `bin/vqcc-remote submit <task> <path> --label "..."` | official CLI on a Kaggle CPU kernel with the competition's kickmix | invsqrt ~13 min, dqi ~2.5 min, dfthcsos ~1.5 min (+ upload/queue) |

Candidate formats: invsqrt and dqi are a single `.kmb`; dfthcsos is a directory holding
`angle_k_*.kmb` and `submission.npz`.

### Quick-tier blind spots (know them before trusting a quick PASS)

- invsqrt: strided x values plus boundary values, few shots. Its score can read ~1e-5 (relative) lower than full
  (rotation eps derived from fewer shots). A CCX deletion that quick passes may still fail full.
- dqi: 2560 shots, projected to the full N. A failure rate below ~1e-3 can go unseen; a quick PASS says
  nothing about R(ε+) beyond "probably ≈1.19". Uses ~9 GB RAM, so dqi runs are exclusive even at quick.
- dfthcsos: deterministic score, identical to full; only the HMR shot count differs (8 vs 64).

## Procedure

1. Quick first. Exit codes: `0` PASS, `1` FAIL (official verdict, recorded), `2` ERROR (crash, timeout,
   missing path; not evidence about the circuit). The last stdout line is a one-line JSON summary
   (`--json` prints the full ledger record). Do not pipe the command if you need its exit code. Identical (cand_id, tier, salt, args) PASS/FAIL results are
   cached and returned instantly; `--force` reruns.
2. On FAIL, read `result` in the summary JSON (the verifier's reason: register not restored, phase error,
   eps+ ≥ 1/16, λ/E_gap/error thresholds, qubit limit, Z_POW use, …). Fix or abandon with the reason in NOTES.md.
3. If the candidate already has a full/remote PASS in the ledger (from anyone, see step 4), skip to step 4. Otherwise escalate
   to full verification only when quick PASS and the quick ratio beats the verified best by more
   than the quick-tier noise above (for invsqrt, > ~1e-5 relative) (`bin/vqcc-best`), or ties it
   within that noise while lowering a risk (e.g. failure rate, a gray technique removed). Prefer remote:
   - `bin/vqcc-remote submit <task> <path> --label "<same label>"` returns immediately with a job id.
     Up to 5 Kaggle jobs run at once; extra jobs stay `queued` and start automatically.
   - Results arrive via `bin/vqcc-remote poll` (the supervisor runs it every 10 min; calling it yourself is
     fine, `--wait SEC` blocks). `bin/vqcc-remote status` lists jobs.
   - Do not pass `--verifier-args` for verification; any non-default args make the run tier=quick.
   - If Kaggle is unavailable (push errors, quota), fall back to `--tier full` locally. Only one local full
     runs at a time; the tool waits on a lock (progress on stderr). Run it in the background.
4. For a candidate that would become the new best, add one more salt to check robustness:
   `bin/vqcc-verify <task> <path> --tier full --salt s2 --label ...` (or `vqcc-remote submit ... --salt s2`).
   The LB re-verifies with a secret salt; a candidate that passes only some salts is not acceptable.
   Salt names are free text (`s2`, `s3`, …); the default empty salt counts as one salt. Any existing full/remote
   PASS in the ledger counts, whoever ran it (including harness self-tests); do not rerun an identical
   (tier, salt) check. The submitter needs one full/remote PASS, but write a candidate into NOTES.md
   "現在のベスト" only after it PASSes on two salts.
5. Read `bin/vqcc-best`: per task the ref, the verified best (cand_id, score, ratio), the latest and best
   quick, and two predicted LBs (verified-only, and optimistic incl. quick; never base a decision on the
   optimistic one). The verified-only one is
   what the supervisor submits from.

## Reading the ledger directly

```bash
.venv/bin/python -I -c "import json;[print(r['ts'],r['tier'],r['status'],r['score'],r['ratio'],r['label']) for r in map(json.loads,open('ledger/results.jsonl')) if r['task']=='<task>']"
```

Fields: `ts task cand_id sha256 tier host kickmix salt args status score ratio wall_s label result`.
`ratio` is null in records written before `harness/refs.json` existed; compute `ref / score` instead
(`bin/vqcc-best` always does).
`verified_best` ignores ERROR records, requires every full/remote PASS/FAIL record of a candidate to be
PASS, and takes the worst score across salts. One FAIL on any salt disqualifies the candidate.

## Rules

- Never claim an improvement from a quick result in NOTES.md "現在のベスト"; write quick values as quick.
- Never tune a circuit to a particular salt, seed or shot set. Never edit `ledger/` or `artifacts/`.
- A candidate judged illegitimate (FORBIDDEN technique, failed legitimacy audit) goes into
  `work/<task>/BLOCKLIST` as one line `<cand_id> <reason>`; it is then excluded from the verified best,
  `bin/vqcc-best` and submissions even if it PASSes. Never verify a blocklisted candidate further.
- Report tool bugs or confusing behaviour in `work/HARNESS_ISSUES.md` and continue with a workaround.
