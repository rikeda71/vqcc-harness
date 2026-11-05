"""Run an official verifier CLI (data/raw/verifiers) from outside the data directory.

Usage: python -I harness/run_official.py {invsqrt|dqi|dfthcsos} [verifier args...]
The verifiers are the competition's own scoring code; we only put their directory on sys.path.
"""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERIFIERS = os.path.join(ROOT, "data", "raw", "verifiers")
SCRIPTS = {
    "invsqrt": "invsqrt/verify_submission_invsqrt.py",
    "dqi": "dqi/verify_submission_dqi.py",
    "dfthcsos": "dfthcsos/verify.py",
}

def main() -> None:
    task = sys.argv[1]
    script = os.path.join(VERIFIERS, SCRIPTS[task])
    sys.path.insert(0, VERIFIERS)
    sys.argv = [script] + sys.argv[2:]
    runpy.run_path(script, run_name="__main__")

if __name__ == "__main__":
    main()
