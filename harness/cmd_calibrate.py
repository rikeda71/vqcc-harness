"""bin/vqcc-calibrate [--tasks ...] [--invsqrt-1shot]

Verify the official sample submissions at the full tier and write ref_cost to harness/refs.json.
 * ref_cost(task) = raw verifier score of the sample submission (LB factor 1.0 by our assumption).
 * Measured: invsqrt full (64 shots/x, batch 256) takes ~11 min on an M2 (8 cores), dqi ~4 min, dfthcsos ~1.5 min,
   so all three are run at the full tier (results are served from the ledger cache when already done).
 * --invsqrt-1shot: use the 1-shot-per-x run (batch 4, ~6 min) instead and mark the ref provisional.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402
import runner  # noqa: E402

SAMPLES = {
    "invsqrt": ledger.ROOT / "data/raw/sample_submission/invsqrt.kmb",
    "dqi": ledger.ROOT / "data/raw/sample_submission/dqi.kmb",
    "dfthcsos": ledger.ROOT / "data/raw/sample_submission/dfthcsos",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", nargs="*", default=list(ledger.TASKS), choices=ledger.TASKS)
    ap.add_argument("--invsqrt-1shot", action="store_true", help="provisional invsqrt ref from 1 shot/x")
    ap.add_argument("--salt", default="")
    a = ap.parse_args()
    refs_path = ledger.REFS_FILE
    cur = json.loads(refs_path.read_text()) if refs_path.exists() else {}
    meta = cur.get("_meta", {})
    meta.setdefault("provisional", {})
    meta.setdefault("source", {})
    for t in a.tasks:
        t0 = time.time()
        if t == "invsqrt" and a.invsqrt_1shot:
            rec = runner.verify_candidate(t, SAMPLES[t], tier="quick", salt=a.salt, label="calibrate sample (1 shot/x)",
                                          raw_args=runner.INVSQRT_1SHOT_ARGS, jobs=8)
            provisional = True
            how = "official CLI --shots-per-x 1 --batch-size 4 (all x once; stored as tier=quick)"
        else:
            rec = runner.verify_candidate(t, SAMPLES[t], tier="full", salt=a.salt, label="calibrate sample (full)")
            provisional = False
            how = "full tier (official default args)"
        print(f"{t}: {rec['status']} score={rec.get('score')} wall={rec['wall_s']}s cached={rec['cached']} [{how}]",
              file=sys.stderr)
        if rec["status"] != "PASS":
            print(json.dumps({"task": t, "status": rec["status"], "error": rec["result"].get("error")}))
            continue
        cur[t] = rec["score"]
        meta["source"][t] = {"cand_id": rec["cand_id"], "how": how, "salt": a.salt, "wall_s": rec["wall_s"],
                             "kickmix": rec["kickmix"], "ts": rec["ts"]}
        if provisional:
            meta["provisional"][t] = "score from 1 shot per x (batch 4), not the 64-shot full tier"
        else:
            meta["provisional"].pop(t, None)
    meta["lb_formula_note"] = "Per-task score S from the paper (lower is better). LB = geometric mean over tasks of ref/S with ref = official sample score; confirmed by reproducing the public LB 24.73387 from public notebooks' per-task ratios (21.2085*65.109*10.9578)^(1/3)=24.73."
    cur["_meta"] = meta
    refs_path.write_text(json.dumps(cur, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in cur.items() if k != "_meta"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
