"""Smoke tests for bin/vqcc-verify error handling (run: harness/pyrun tests/test_verify_errors.py).

Uses the real ledger/artifacts (records are labelled 'selftest'); takes about a minute.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERIFY = os.path.join(ROOT, "bin", "vqcc-verify")
SAMPLE = os.path.join(ROOT, "data", "raw", "sample_submission")


def run(*args):
    p = subprocess.run([VERIFY, *args, "--label", "selftest"], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1]), p.stderr


def check(name, cond, info=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {info}" if not cond else ""))
    return bool(cond)


ok = True
with tempfile.TemporaryDirectory() as td:
    # 1. missing file -> ERROR(2), nothing recorded
    rc, s, _ = run("invsqrt", os.path.join(td, "nope.kmb"), "--tier", "quick")
    ok &= check("missing file -> exit 2 / ERROR", rc == 2 and s["status"] == "ERROR" and s["recorded"] is False, (rc, s))
    # 2. garbage bytes -> not PASS, recorded
    bad = os.path.join(td, "broken.kmb")
    with open(bad, "wb") as f:
        f.write(os.urandom(4096))
    for task in ("invsqrt", "dqi"):
        rc, s, _ = run(task, bad, "--tier", "quick")
        ok &= check(f"broken file ({task}) -> FAIL/ERROR recorded", rc in (1, 2) and s["status"] in ("FAIL", "ERROR")
                    and s.get("cand_id"), (rc, s))
    # 3. wrong-task circuit: invsqrt circuit as dqi / dfthcsos-dir as invsqrt
    rc, s, _ = run("dqi", os.path.join(SAMPLE, "invsqrt.kmb"), "--tier", "quick")
    ok &= check("invsqrt circuit as dqi -> FAIL", rc == 1 and s["status"] == "FAIL", (rc, s))
    rc, s, _ = run("invsqrt", os.path.join(SAMPLE, "dfthcsos"), "--tier", "quick")
    ok &= check("dfthcsos dir as invsqrt -> not PASS", rc in (1, 2), (rc, s))
    # 4. cache: second identical run is served from the ledger
    rc1, s1, _ = run("dqi", os.path.join(SAMPLE, "invsqrt.kmb"), "--tier", "quick")
    ok &= check("cache hit on repeat", s1["cached"] is True, s1)
    rc2, s2, _ = run("dqi", os.path.join(SAMPLE, "invsqrt.kmb"), "--tier", "quick", "--force")
    ok &= check("--force re-runs", s2["cached"] is False, s2)
    # 5. timeout -> ERROR (not cached)
    rc, s, _ = run("invsqrt", os.path.join(SAMPLE, "invsqrt.kmb"), "--tier", "quick", "--force", "--timeout", "0.5")
    ok &= check("timeout -> ERROR", rc == 2 and s["status"] == "ERROR", (rc, s))
sys.exit(0 if ok else 1)
