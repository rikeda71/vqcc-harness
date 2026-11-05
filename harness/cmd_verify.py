"""bin/vqcc-verify <task> <candidate-path> [--tier quick|full] [--salt S] [--label TEXT] [--jobs N] [--json]

Exit code: PASS=0, FAIL=1, ERROR=2. The last stdout line is a one-line summary JSON.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runner  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(prog="vqcc-verify", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task", choices=runner.TASKS)
    ap.add_argument("candidate", help="kmb file (invsqrt, dqi) or run directory (dfthcsos)")
    ap.add_argument("--tier", choices=("quick", "full"), default="quick")
    ap.add_argument("--salt", default="")
    ap.add_argument("--label", default="")
    ap.add_argument("--jobs", type=int, default=None, help="worker processes for the verifier (speed only)")
    ap.add_argument("--force", action="store_true", help="ignore the cache and re-run")
    ap.add_argument("--timeout", type=float, default=None, help="kill the verifier after this many seconds -> ERROR")
    ap.add_argument("--json", action="store_true", help="print the full ledger record as the last JSON line")
    a = ap.parse_args()
    try:
        rec = runner.verify_candidate(a.task, a.candidate, tier=a.tier, salt=a.salt, label=a.label,
                                      jobs=a.jobs, force=a.force, timeout=a.timeout)
    except (FileNotFoundError, ValueError) as ex:
        print(json.dumps({"task": a.task, "tier": a.tier, "status": "ERROR", "error": str(ex), "recorded": False}))
        return 2
    s = runner.summary_line(rec)
    if not a.json:
        print(f"{rec['task']} {rec['cand_id']} tier={rec['tier']} {rec['status']}"
              + (f" score={rec['score']:.6g}" if rec.get("score") else "")
              + (f" ratio={rec['ratio']:.4f}" if rec.get("ratio") else "")
              + f" wall={rec['wall_s']}s" + (" (cached)" if rec["cached"] else ""))
        if rec["status"] != "PASS":
            print("  " + str(rec["result"].get("error", ""))[:600])
    print(json.dumps(rec if a.json else s))
    return {"PASS": 0, "FAIL": 1}.get(rec["status"], 2)


if __name__ == "__main__":
    sys.exit(main())
