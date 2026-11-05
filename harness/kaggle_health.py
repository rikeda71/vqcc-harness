"""Is the competition still running and is Kaggle reachable? Used by bin/night-supervisor.

Exit codes:  0 ok   1 Kaggle unreachable (CLI error, auth, network, timeout)   3 competition over
Prints one line explaining the result. Uses the kaggle CLI (which authenticates internally);
never reads the token itself.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys

COMP = "google-verifiable-quantum-circuit-compilation"
TIMEOUT_S = 90  # the CLI normally answers in a few seconds


def main() -> int:
    try:
        p = subprocess.run(["kaggle", "competitions", "list", "-s", COMP, "--format", "json"],
                           capture_output=True, text=True, timeout=TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"unreachable: {e!r}")
        return 1
    if p.returncode != 0:
        print(f"unreachable: kaggle exit {p.returncode}: {(p.stderr or p.stdout).strip()[:200]}")
        return 1
    try:
        rows = json.loads(p.stdout[p.stdout.index("["):])
    except (ValueError, json.JSONDecodeError):
        print(f"unreachable: unexpected output: {p.stdout.strip()[:200]}")
        return 1
    mine = [r for r in rows if str(r.get("ref", "")).rstrip("/").endswith("/" + COMP)]
    if not mine:
        print(f"over: competition {COMP} no longer listed")
        return 3
    deadline = dt.datetime.fromisoformat(mine[0]["deadline"]).replace(tzinfo=dt.timezone.utc)
    now = dt.datetime.now(dt.timezone.utc)
    if now >= deadline:
        print(f"over: deadline {deadline.isoformat()} has passed")
        return 3
    print(f"ok: deadline {deadline.isoformat()} ({(deadline - now).days} days left)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
