"""Local verification runner shared by bin/vqcc-verify, bin/vqcc-calibrate and friends.

verify_candidate() = ledger.store -> cache lookup -> global lock (state/locks) -> run an
official verifier in a subprocess (full: the official CLI with default args via
harness/run_official.py; quick: harness/quick_worker.py / official CLI with fewer shots)
-> ledger.append.

Everything that prints goes to stderr, except what the CLI wrappers explicitly print.
"""
from __future__ import annotations

import fcntl
import json
import os
import resource
import subprocess
import sys
import threading
import time
from importlib import metadata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402

ROOT = ledger.ROOT
HARNESS = ROOT / "harness"
LOCK_DIR = ROOT / "state" / "locks"
PY = sys.executable
TASKS = ledger.TASKS

# Number of concurrently running quick verifications (a full run excludes everything else).
QUICK_SLOTS = int(os.environ.get("VQCC_QUICK_SLOTS", "2"))

# ----- tier definitions ------------------------------------------------------------------------
# "args" are the semantic arguments recorded in the ledger and used in the cache key.
# A change of QUICK_PARAMS must change "args" (version tag) so stale cache entries are not reused.
QUICK_PARAMS = {
    # stride sample of x (every `stride`-th x) x `shots` + boundary x (2^k, 4^k +-2, 1..64, max_x-32..max_x) x `boundary_shots`
    "invsqrt": {"v": "q1", "stride": 16, "shots": 4, "boundary_shots": 16, "batch_size": 256},
    # N = 2560 shots; log2_delta relaxed to -16 for the pass/fail; score projected to N = 102400 / delta = 2^-128
    "dqi": {"v": "q1", "num_shots": 2560, "batch_size": 256, "log2_delta": -16.0},
    # official CLI with fewer shots per address
    "dfthcsos": {"v": "q1", "shots_per_address": 8},
}
# invsqrt "calibration quick": every x exactly once through the official CLI (about 6 min on 8 cores)
INVSQRT_1SHOT_ARGS = ["--shots-per-x", "1", "--batch-size", "4"]


def tier_args(task: str, tier: str) -> list[str]:
    """Semantic args of a tier (ledger `args`)."""
    if tier == "full":
        return []
    p = QUICK_PARAMS[task]
    return [f"{k}={v}" for k, v in p.items()]


def default_jobs(task: str, tier: str) -> int | None:
    ncpu = os.cpu_count() or 1
    if task == "dqi":
        return 4  # official default (each worker needs memory)
    if task == "invsqrt":
        return None if tier == "full" else max(1, ncpu // QUICK_SLOTS)
    return None  # dfthcsos is single-core


def build_command(task: str, tier: str, path: Path, salt: str, jobs: int | None,
                  extra: list[str] | None = None) -> list[str]:
    """Command line of the verifier subprocess. `extra` replaces the tier's semantic args when it is a
    list of raw official-CLI args (used by calibrate for the invsqrt 1-shot run)."""
    if extra is not None:
        cmd = [PY, "-I", str(HARNESS / "run_official.py"), task, str(path)] + list(extra)
        if salt:
            cmd += ["--salt", salt]
        if jobs and task in ("invsqrt", "dqi"):
            cmd += ["--max-workers", str(jobs)]
        return cmd
    if tier == "full":
        cmd = [PY, "-I", str(HARNESS / "run_official.py"), task, str(path)]
        if salt:
            cmd += ["--salt", salt]
        if jobs and task in ("invsqrt", "dqi"):
            cmd += ["--max-workers", str(jobs)]
        return cmd
    p = QUICK_PARAMS[task]
    if task == "dfthcsos":
        cmd = [PY, "-I", str(HARNESS / "run_official.py"), task, str(path),
               "--shots-per-address", str(p["shots_per_address"])]
        if salt:
            cmd += ["--salt", salt]
        return cmd
    cmd = [PY, "-I", str(HARNESS / "quick_worker.py"), task, str(path)]
    for k, v in p.items():
        if k != "v":
            cmd += ["--" + k.replace("_", "-"), str(v)]
    if salt:
        cmd += ["--salt", salt]
    if jobs:
        cmd += ["--max-workers", str(jobs)]
    return cmd


# ----- locks ---------------------------------------------------------------------------------------

class _Lock:
    def __init__(self, name: str):
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        self.fh = open(LOCK_DIR / name, "a+")

    def try_lock(self, shared: bool = False) -> bool:
        try:
            fcntl.flock(self.fh, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def unlock(self) -> None:
        try:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        finally:
            self.fh.close()


def log(msg: str) -> None:
    print(f"[vqcc {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


class Slots:
    """Global CPU semaphore.

    full (and any dqi run): holds `gate` (exclusive, serialises fulls and makes quicks wait) and every quick slot.
    quick: waits until `gate` is free, then holds one of QUICK_SLOTS slot locks.
    flock locks vanish when the process dies, so a crashed worker never wedges the system.
    """

    def __init__(self, tier: str, who: str, exclusive: bool | None = None):
        # dqi needs ~9 GB RSS even for a quick run (3.5 GB circuit in the parent + workers), so it is exclusive too.
        self.tier, self.who = tier, who
        self.exclusive = (tier == "full") if exclusive is None else exclusive
        self.held: list[_Lock] = []
        self.wait_s = 0.0

    def __enter__(self) -> "Slots":
        t0 = time.time()
        last = 0.0

        def note(what: str) -> None:
            nonlocal last
            if time.time() - last > 30:
                log(f"{self.who}: waiting for {what} ({time.time() - t0:.0f}s so far)")
                last = time.time()

        if self.exclusive:
            gate = _Lock("gate")
            while not gate.try_lock():
                note("full gate (another full is running/queued)")
                time.sleep(1.0)
            self.held.append(gate)
            for i in range(QUICK_SLOTS):
                s = _Lock(f"slot-{i}")
                while not s.try_lock():
                    note(f"quick slot {i} to drain")
                    time.sleep(1.0)
                self.held.append(s)
        else:
            while True:
                g = _Lock("gate")
                free = g.try_lock(shared=True)  # fails while a full holds/queues the gate
                g.unlock()
                if free:
                    got = None
                    for i in range(QUICK_SLOTS):
                        s = _Lock(f"slot-{i}")
                        if s.try_lock():
                            got = s
                            break
                        s.fh.close()
                    if got:
                        self.held.append(got)
                        break
                note("a quick slot / full to finish")
                time.sleep(1.0)
        self.wait_s = time.time() - t0
        if self.wait_s > 1:
            log(f"{self.who}: lock acquired after {self.wait_s:.0f}s")
        return self

    def __exit__(self, *exc: Any) -> None:
        for lk in reversed(self.held):
            lk.unlock()


# ----- subprocess with RSS sampling -----------------------------------------------------------------

def _tree_rss_kb(root_pid: int) -> int:
    try:
        out = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True,
                             timeout=10).stdout
    except Exception:
        return 0
    rows = [tuple(int(x) for x in ln.split()) for ln in out.splitlines() if ln.strip()]
    kids: dict[int, list[tuple[int, int]]] = {}
    for pid, ppid, rss in rows:
        kids.setdefault(ppid, []).append((pid, rss))
    rss_of = {pid: rss for pid, _, rss in rows}
    total, stack = 0, [root_pid]
    while stack:
        p = stack.pop()
        total += rss_of.get(p, 0)
        stack.extend(c for c, _ in kids.get(p, []))
    return total


def run_subprocess(cmd: list[str], timeout: float | None = None) -> dict[str, Any]:
    """Run cmd; returns stdout, stderr, returncode, wall_s, peak RSS (sum over the process tree and the
    largest single process, MB; shared copy-on-write pages are counted per process so the tree sum is an upper bound)."""
    stop = threading.Event()
    peak = {"tree": 0}
    t0 = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         stdin=subprocess.DEVNULL)

    def sampler() -> None:
        while not stop.wait(2.0):
            peak["tree"] = max(peak["tree"], _tree_rss_kb(p.pid))

    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    timed_out = False
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        p.kill()
        out, err = p.communicate()
    finally:
        stop.set()
        th.join(timeout=5)
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {"stdout": out, "stderr": err, "returncode": p.returncode, "wall_s": time.time() - t0,
            "timed_out": timed_out,
            "peak_tree_rss_mb": round(peak["tree"] / 1024, 1),
            "peak_proc_rss_mb": round(ru.ru_maxrss / (1024 * 1024), 1)}  # macOS: bytes


def parse_result(stdout: str) -> dict[str, Any] | None:
    i = stdout.find("{")
    if i < 0:
        return None
    try:
        obj = json.JSONDecoder().raw_decode(stdout[i:])[0]
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) and "status" in obj else None


# ----- main entry ------------------------------------------------------------------------------------

def kickmix_version() -> str:
    try:
        return metadata.version("kickmix")
    except Exception:
        return "unknown"


def find_cached(cand_id: str, tier: str, salt: str, args: list[str]) -> dict[str, Any] | None:
    hit = None
    for r in ledger.records():
        if (r.get("cand_id") == cand_id and r.get("tier") == tier and r.get("salt", "") == salt
                and r.get("args", []) == args and r.get("host") == "local"
                and r.get("status") in ("PASS", "FAIL")):
            hit = r  # latest wins
    return hit


def verify_candidate(task: str, path: str | Path, tier: str = "full", salt: str = "", label: str = "",
                     jobs: int | None = None, force: bool = False, timeout: float | None = None,
                     raw_args: list[str] | None = None, use_cache: bool = True) -> dict[str, Any]:
    """Verify one candidate and record it. Returns the ledger record (+ `cached` flag). Never raises for
    verifier outcomes; raises FileNotFoundError / ValueError only for unusable inputs (nothing recorded)."""
    if task not in TASKS:
        raise ValueError(f"unknown task {task!r}")
    if tier not in ("quick", "full"):
        raise ValueError(f"unknown tier {tier!r}")
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"candidate not found: {path}")
    cand_id, sha, stored = ledger.store(task, path)
    args = list(raw_args) if raw_args is not None else tier_args(task, tier)
    if use_cache and not force:
        hit = find_cached(cand_id, tier, salt, args)
        if hit is not None:
            log(f"cache hit: {task} {cand_id} tier={tier} salt={salt!r} -> {hit['status']}")
            return {**hit, "cached": True}
    if jobs is None:
        jobs = default_jobs(task, tier)
    cmd = build_command(task, tier, stored, salt, jobs, extra=raw_args)
    who = f"{task}/{cand_id}/{tier}"
    with Slots(tier, who, exclusive=(tier == "full" or task == "dqi")) as slots:
        log(f"{who}: running ({' '.join(cmd[2:])})")
        res = run_subprocess(cmd, timeout)
    out = parse_result(res["stdout"])
    err_tail = (res["stderr"] or "")[-3000:]
    if res["timed_out"]:
        status, result = "ERROR", {"status": "ERROR", "error": f"timeout after {timeout}s", "stderr": err_tail}
    elif out is not None and out.get("status") in ("PASS", "FAIL"):
        status, result = out["status"], out
        if res["returncode"] != (0 if status == "PASS" else 1):
            status, result = "ERROR", {"status": "ERROR", "error": f"inconsistent exit code {res['returncode']}",
                                       "stdout": res["stdout"][-2000:], "stderr": err_tail}
    else:
        status, result = "ERROR", {"status": "ERROR", "error": f"verifier crashed (exit {res['returncode']})",
                                   "stdout": res["stdout"][-2000:], "stderr": err_tail}
    score = result.get("score") if status == "PASS" else None
    refs = ledger.load_refs()
    ratio = (refs[task] / score) if (status == "PASS" and score and task in refs) else None
    rec = {
        "ts": ledger.now(), "task": task, "cand_id": cand_id, "sha256": sha, "tier": tier, "host": "local",
        "kickmix": kickmix_version(), "salt": salt, "args": args, "status": status, "score": score,
        "ratio": ratio, "wall_s": round(res["wall_s"], 2), "label": label, "result": result,
        "jobs": jobs, "lock_wait_s": round(slots.wait_s, 1), "peak_tree_rss_mb": res["peak_tree_rss_mb"],
        "peak_proc_rss_mb": res["peak_proc_rss_mb"], "source_name": path.name,
    }
    ledger.append(rec)
    return {**rec, "cached": False}


def summary_line(rec: dict[str, Any]) -> dict[str, Any]:
    res = rec.get("result", {})
    return {
        "task": rec["task"], "cand_id": rec["cand_id"], "tier": rec["tier"], "status": rec["status"],
        "score": rec.get("score"), "ratio": rec.get("ratio"), "wall_s": rec.get("wall_s"),
        "cached": rec.get("cached", False), "salt": rec.get("salt", ""),
        "error": res.get("error") if rec["status"] != "PASS" else None,
    }
