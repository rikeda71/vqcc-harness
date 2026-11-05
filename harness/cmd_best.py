"""bin/vqcc-best: verified best + latest/best quick per task and the predicted LB score.

Predicted LB = geometric mean over tasks of ref_cost / score (ref = official sample submission).
NOTE: the aggregation formula is NOT given in the paper (it defines only the per-task score S);
this is our working assumption. Tasks without a verified result count as ratio 1.0 (= sample).
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402


def fmt(x, nd=6):
    return "-" if x is None else f"{x:.{nd}g}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--min-salts", type=int, default=1, help="distinct salts required for 'verified'")
    a = ap.parse_args()
    refs = ledger.load_refs()
    recs = list(ledger.records())
    rows, out = [], {}
    ratios_v, ratios_q = [], []
    for t in ledger.TASKS:
        ref = refs.get(t)
        vb = ledger.verified_best(t, min_salts=a.min_salts)
        quick = [r for r in recs if r.get("task") == t and r.get("tier") == "quick" and r.get("host") == "local"
                 and r.get("status") in ("PASS", "FAIL")]
        blocked = ledger.blocklist(t)
        qpass = [r for r in quick if r.get("status") == "PASS" and r.get("score") and r["cand_id"] not in blocked]
        qlast = quick[-1] if quick else None
        qbest = min(qpass, key=lambda r: r["score"]) if qpass else None
        v_ratio = ref / vb["score"] if (vb and ref) else None
        q_ratio = ref / qbest["score"] if (qbest and ref) else None
        ratios_v.append(v_ratio if v_ratio else 1.0)
        # optimistic view: best of verified and quick
        cands = [r for r in (v_ratio, q_ratio) if r]
        ratios_q.append(max(cands) if cands else 1.0)
        out[t] = {"ref": ref, "verified_best": vb, "verified_ratio": v_ratio,
                  "quick_last": qlast and {"cand_id": qlast["cand_id"], "status": qlast["status"],
                                           "score": qlast.get("score"), "ts": qlast["ts"]},
                  "quick_best": qbest and {"cand_id": qbest["cand_id"], "score": qbest["score"]},
                  "quick_best_ratio": q_ratio}
        rows.append((t, fmt(ref, 9), vb["cand_id"] if vb else "-", fmt(vb["score"] if vb else None, 9),
                     fmt(v_ratio, 5), (qlast["cand_id"] + " " + qlast["status"]) if qlast else "-",
                     fmt(qlast.get("score") if qlast else None, 9),
                     fmt(q_ratio, 5)))
    gm = lambda xs: math.exp(sum(math.log(x) for x in xs) / len(xs))  # noqa: E731
    out["predicted_lb_verified"] = gm(ratios_v)
    out["predicted_lb_with_quick"] = gm(ratios_q)
    out["refs_meta"] = json.loads(ledger.REFS_FILE.read_text()).get("_meta") if ledger.REFS_FILE.exists() else None
    if a.json:
        print(json.dumps(out))
        return 0
    hdr = ("task", "ref", "verified", "v.score", "v.ratio", "latest quick", "q.score", "bestQ.ratio")
    w = [max(len(str(r[i])) for r in rows + [hdr]) for i in range(len(hdr))]
    for r in [hdr] + rows:
        print("  ".join(str(c).ljust(w[i]) for i, c in enumerate(r)))
    print(f"\npredicted LB (geomean of ref/score, verified only; missing task = 1.0): {out['predicted_lb_verified']:.5f}")
    print(f"predicted LB (incl. best quick results, optimistic)                 : {out['predicted_lb_with_quick']:.5f}")
    prov = (out["refs_meta"] or {}).get("provisional") if out["refs_meta"] else None
    if prov:
        print(f"note: provisional refs: {prov}")
    print("note: LB = geomean of ref/score, ref = official sample (see harness/refs.json _meta)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
