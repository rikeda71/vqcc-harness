"""vqcc-compute: run worker optimisation code on Kaggle GPU notebooks (see bin/vqcc-compute).

Flow: gpu-check (validate that this account can actually run a GPU kernel) -> submit (tar.gz of
work/<task>/<dir> -> private dataset vqcc-src-<task>-<hash12>; private script kernel vqcc-gpu-<job_id>,
template harness/compute_kernel.py, internet disabled) -> poll (`kaggle kernels output` into
work/<task>/out/compute/<job_id>/, then delete the kernel / dataset this tool created).

State: state/compute/jobs.jsonl (append-only, last line per job_id wins), state/compute/gpu_check.json.
Stdlib only; talks to the `kaggle` CLI via harness/remote.py helpers. Nothing is written to the ledger.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import fcntl
import hashlib
import json
import os
import shlex
import shutil
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness import ledger, remote  # noqa: E402
from harness.remote import kaggle, kaggle_user, kernel_status, dataset_ready  # noqa: E402

STATE = ROOT / "state" / "compute"
JOBS = STATE / "jobs.jsonl"
CHECK = STATE / "gpu_check.json"
PENDING = STATE / "gpu_check_pending.json"
LOCK = STATE / ".lock"
WORK_ROOT = ROOT / "work"
CONF = ROOT / "harness" / "compute.toml"
TEMPLATE = ROOT / "harness" / "compute_kernel.py"
PAYLOAD = "src.tar.gz"
ACTIVE = ("pushed", "running")
EXCLUDE_DIRS = {".venv", "__pycache__", ".git"}
WEEK_S = 7 * 24 * 3600


class Refused(Exception):
    """submit/gpu-check refused by a validation rule (message is shown to the worker)."""


# ---------- config / state ----------

def config() -> dict:
    import tomllib
    c = {"competition": "google-verifiable-quantum-circuit-compilation", "accelerator": "NvidiaTeslaT4",
         "max_concurrent_gpu": 1, "weekly_gpu_hours_budget": 25, "max_src_mb": 1024,
         "gpu_check_max_age_h": 24, "default_hours": 3, "max_hours": 12, "gpu_check_wait_s": 900,
         "cleanup": True, "kernel_prefix": "vqcc-gpu-", "dataset_prefix": "vqcc-src-"}
    if CONF.exists():
        c.update(tomllib.loads(CONF.read_text()))
    if os.environ.get("VQCC_COMPUTE_MAX_CONCURRENT_GPU"):
        c["max_concurrent_gpu"] = int(os.environ["VQCC_COMPUTE_MAX_CONCURRENT_GPU"])
    return c


class Lock:
    def __enter__(self):
        STATE.mkdir(parents=True, exist_ok=True)
        self.fh = open(LOCK, "w")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def load_jobs() -> dict[str, dict]:
    jobs: dict[str, dict] = {}
    if JOBS.exists():
        for line in JOBS.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    j = json.loads(line)
                    jobs[j["job_id"]] = j
                except (json.JSONDecodeError, KeyError):
                    pass
    return jobs


def save(job: dict) -> None:
    job["updated"] = ledger.now()
    JOBS.parent.mkdir(parents=True, exist_ok=True)
    with open(JOBS, "a") as fh:
        fh.write(json.dumps(job, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def log(msg: str) -> None:
    print(f"[vqcc-compute {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def parse_ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def read_check() -> dict | None:
    try:
        return json.loads(CHECK.read_text())
    except (OSError, json.JSONDecodeError):
        return None


# ---------- validation ----------

def resolve_src(task: str, src: str | Path) -> Path:
    base = (WORK_ROOT / task).resolve()
    p = Path(src).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    p = p.resolve()
    if p != base and base not in p.parents:
        raise Refused(f"--src must be inside {base} (got {p})")
    if not p.is_dir():
        raise Refused(f"--src is not a directory: {p}")
    return p


def iter_src_files(src: Path):
    """Yield (relpath, abspath) of regular files, skipping .venv/__pycache__/.git and symlinks."""
    for dp, dn, fn in os.walk(src):
        dn[:] = sorted(d for d in dn if d not in EXCLUDE_DIRS and not os.path.islink(os.path.join(dp, d)))
        for f in sorted(fn):
            a = Path(dp) / f
            if a.is_symlink() or not a.is_file() or f.endswith(".pyc"):
                continue
            yield a.relative_to(src), a


def src_stats(src: Path, max_mb: float) -> tuple[str, int, int]:
    h = hashlib.sha256()
    total = n = 0
    for rel, a in iter_src_files(src):
        size = a.stat().st_size
        total += size
        n += 1
        if total > max_mb * 1e6:
            raise Refused(f"src is larger than max_src_mb={max_mb} MB (excluding .venv/__pycache__/.git); "
                          f"move big data out of {src}")
        h.update(f"{rel}\0{size}\0".encode())
        with open(a, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    if n == 0:
        raise Refused(f"src has no files: {src}")
    return h.hexdigest(), total, n


def make_payload(src: Path, dest: Path) -> None:
    t = time.time()
    with tarfile.open(dest, "w:gz", compresslevel=3) as tf:
        for rel, a in iter_src_files(src):
            tf.add(a, arcname=str(rel), recursive=False)
    log(f"packed {src.name}: {dest.stat().st_size / 1e6:.1f} MB in {time.time() - t:.0f}s")


def gpu_hours_used(jobs: dict[str, dict], now: float | None = None) -> float:
    """GPU hours in the trailing 7 days, counted by actual run time.

    Finished jobs: the kernel's own wall time (result.json kernel_wall_s, else run_wall_s), ending at `finished`;
    without either, `started` (or `pushed`) -> `finished`. Running jobs: `started` (or `pushed`) -> now, capped at
    --hours. Jobs still queued on Kaggle (pushed, not yet RUNNING) use no GPU and count 0.
    """
    now = now or time.time()
    lo = now - WEEK_S
    tot = 0.0
    for j in jobs.values():
        if j.get("kind") != "gpu":
            continue
        s = parse_ts(j.get("started")) or parse_ts(j.get("pushed"))
        e = parse_ts(j.get("finished"))
        if e is not None:
            wall = j.get("kernel_wall_s") or j.get("run_wall_s")
            if wall:
                s = e - float(wall)
            if s is None:
                continue
        elif j.get("state") == "running" and s is not None:
            e = min(now, s + float(j.get("hours", 12)) * 3600)
        else:
            continue
        tot += max(0.0, min(e, now) - max(s, lo))
    return tot / 3600


def validate_gpu_submit(cfg: dict, jobs: dict[str, dict], accelerator: str, hours: float) -> None:
    chk = read_check()
    if not chk or not chk.get("ok"):
        raise Refused("GPU is not validated (state/compute/gpu_check.json missing or ok=false"
                      + (f": {chk.get('error')}" if chk else "") + "). Run: bin/vqcc-compute gpu-check")
    ts = parse_ts(chk.get("ts"))
    age_h = (time.time() - ts) / 3600 if ts else 1e9
    if age_h > cfg["gpu_check_max_age_h"]:
        raise Refused(f"gpu_check is {age_h:.1f} h old (> {cfg['gpu_check_max_age_h']} h). Run: bin/vqcc-compute gpu-check")
    if chk.get("accelerator") != accelerator:
        raise Refused(f"gpu_check validated {chk.get('accelerator')}, not {accelerator}. "
                      f"Run: bin/vqcc-compute gpu-check --accelerator {accelerator}")
    active = [j["job_id"] for j in jobs.values() if j.get("kind") == "gpu" and j["state"] in ACTIVE]
    if len(active) >= cfg["max_concurrent_gpu"]:
        raise Refused(f"{len(active)} GPU job(s) already running ({', '.join(active)}); "
                      f"max_concurrent_gpu={cfg['max_concurrent_gpu']}. Wait for `bin/vqcc-compute poll`.")
    used = gpu_hours_used(jobs)
    if used + hours > cfg["weekly_gpu_hours_budget"]:
        raise Refused(f"weekly GPU budget: {used:.1f} h used in the last 7 days + {hours:g} h requested "
                      f"> {cfg['weekly_gpu_hours_budget']} h. Lower --hours or wait.")


# ---------- kaggle objects ----------

def ensure_dataset(ref: str, src: Path) -> None:
    if dataset_ready(ref):
        log(f"dataset {ref} already exists, reusing")
        return
    with tempfile.TemporaryDirectory(prefix="vqcc-cds-") as td:
        td = Path(td)
        make_payload(src, td / PAYLOAD)
        slug = ref.split("/", 1)[1]
        (td / "dataset-metadata.json").write_text(json.dumps(
            {"title": slug, "id": ref, "licenses": [{"name": "other"}], "isPrivate": True}))
        p = kaggle("datasets", "create", "-p", str(td), "-t", check=False)  # private by default (no --public)
        if p.returncode != 0 and not dataset_ready(ref):
            raise RuntimeError("datasets create failed: " + (p.stdout + p.stderr)[-1500:])
    for _ in range(90):
        if dataset_ready(ref):
            return
        time.sleep(5)
    raise RuntimeError(f"dataset {ref} not ready in time")


def push_kernel(kernel_ref: str, job_json: dict, *, gpu: bool, accelerator: str | None, timeout_s: int,
                dataset_ref: str | None, competition: str | None) -> None:
    src = TEMPLATE.read_text()
    assert "__JOB_B64__" in src
    src = src.replace("__JOB_B64__", base64.b64encode(json.dumps(job_json).encode()).decode())
    slug = kernel_ref.split("/", 1)[1]
    with tempfile.TemporaryDirectory(prefix="vqcc-ckn-") as td:
        td = Path(td)
        (td / "script.py").write_text(src)
        (td / "kernel-metadata.json").write_text(json.dumps({
            "id": kernel_ref, "title": slug, "code_file": "script.py", "language": "python",
            "kernel_type": "script", "is_private": True, "enable_gpu": gpu, "enable_internet": False,
            "dataset_sources": [dataset_ref] if dataset_ref else [],
            "competition_sources": [competition] if competition else [],
            "kernel_sources": [], "model_sources": []}))
        args = ["kernels", "push", "-p", str(td), "-t", str(timeout_s)]
        if gpu and accelerator:
            args += ["--accelerator", accelerator]
        p = kaggle(*args, check=False)
        if p.returncode != 0 or "successfully pushed" not in p.stdout:
            raise RuntimeError("kernels push failed: " + (p.stdout + p.stderr)[-1500:])


def delete_kernel(user: str, cfg: dict, ref: str) -> None:
    if ref.startswith(f"{user}/{cfg['kernel_prefix']}"):
        p = kaggle("kernels", "delete", ref, "-y", check=False)
        log(f"delete kernel {ref} rc={p.returncode}")
    else:
        log(f"refusing to delete {ref}: prefix mismatch")


def delete_dataset(user: str, cfg: dict, ref: str) -> None:
    if ref.startswith(f"{user}/{cfg['dataset_prefix']}"):
        p = kaggle("datasets", "delete", ref, "-y", check=False)
        log(f"delete dataset {ref} rc={p.returncode}")
    else:
        log(f"refusing to delete {ref}: prefix mismatch")


def log_tail(outdir: Path) -> str:
    return remote._log_tail(outdir)


# ---------- gpu-check ----------

def write_check(rec: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    CHECK.write_text(json.dumps(rec, indent=1))


def start_gpu_check(cfg: dict, accelerator: str) -> dict:
    user = kaggle_user()
    ref = f"{user}/{cfg['kernel_prefix']}check-{time.strftime('%m%d%H%M%S')}"
    pend = {"kernel_ref": ref, "accelerator": accelerator, "pushed": ledger.now(), "t0": time.time()}
    try:
        push_kernel(ref, {"job_id": ref.split("/", 1)[1], "mode": "gpu_check"}, gpu=True,
                    accelerator=accelerator, timeout_s=cfg["gpu_check_wait_s"] + 300,
                    dataset_ref=None, competition=None)
    except RuntimeError as e:
        write_check({"ok": False, "ts": ledger.now(), "accelerator": accelerator, "gpu_name": None,
                     "torch": None, "error": str(e)[-1500:], "stage": "push"})
        return {}
    STATE.mkdir(parents=True, exist_ok=True)
    PENDING.write_text(json.dumps(pend))
    log(f"gpu-check kernel pushed: https://www.kaggle.com/code/{ref}")
    return pend


def resolve_gpu_check(cfg: dict) -> str:
    """Advance the pending gpu-check. Returns 'none' | 'pending' | 'finished'."""
    if not PENDING.exists():
        return "none"
    pend = json.loads(PENDING.read_text())
    ref, acc = pend["kernel_ref"], pend["accelerator"]
    st = kernel_status(ref)
    elapsed = time.time() - pend["t0"]
    rec = {"ok": False, "ts": ledger.now(), "accelerator": acc, "gpu_name": None, "torch": None, "error": None,
           "kernel": ref, "elapsed_s": round(elapsed)}
    if st in ("COMPLETE", "ERROR", "CANCEL_ACKNOWLEDGED", "CANCEL_REQUESTED"):
        outdir = STATE / "gpu_check_out"
        shutil.rmtree(outdir, ignore_errors=True)
        outdir.mkdir(parents=True)
        kaggle("kernels", "output", ref, "-p", str(outdir), "-o", check=False)
        rf = outdir / "result.json"
        res = json.loads(rf.read_text()) if rf.exists() else None
        if res:
            g = res.get("gpu", {})
            rec.update({"ok": bool(g.get("gpu_ok")) and not res.get("error"), "gpu_name": g.get("gpu_name"),
                        "torch": g.get("torch"), "cuda_available": g.get("cuda_available"),
                        "matmul_4096_s": g.get("matmul_4096_s"), "nvidia_smi": g.get("nvidia_smi"),
                        "kernel_wall_s": res.get("kernel_wall_s"), "error": res.get("error")})
        else:
            rec["error"] = f"kernel {st}, no result.json; log tail:\n{log_tail(outdir)}"[-1500:]
    elif st.startswith("UNKNOWN") or st in ("QUEUED", "RUNNING", "NEW"):
        if elapsed <= cfg["gpu_check_wait_s"]:
            return "pending"
        rec["error"] = f"kernel still {st} after {elapsed:.0f} s (queue wait / quota?)"
    else:
        return "pending"
    write_check(rec)
    PENDING.unlink(missing_ok=True)
    delete_kernel(kaggle_user(), cfg, ref)
    return "finished"


def print_check(rec: dict | None) -> None:
    if not rec:
        print("gpu_check: (never run)")
        return
    age = (time.time() - (parse_ts(rec.get("ts")) or 0)) / 3600
    print(f"gpu_check: ok={rec.get('ok')} accelerator={rec.get('accelerator')} gpu={rec.get('gpu_name')} "
          f"torch={rec.get('torch')} elapsed={rec.get('elapsed_s')}s age={age:.1f}h"
          + (f"\n  error: {str(rec.get('error'))[-600:]}" if rec.get("error") else ""))


def cmd_gpu_check(cfg: dict, a) -> int:
    acc = a.accelerator or cfg["accelerator"]
    with Lock():
        if PENDING.exists():
            log("a gpu-check is already pending; waiting for it")
        elif not start_gpu_check(cfg, acc):
            print_check(read_check())
            return 1
    if a.no_wait:
        print("gpu-check pushed; run `bin/vqcc-compute poll` later")
        return 0
    deadline = time.time() + cfg["gpu_check_wait_s"] + 60
    while True:
        with Lock():
            r = resolve_gpu_check(cfg)
        if r != "pending" or time.time() > deadline:
            break
        time.sleep(20)
    print_check(read_check())
    return 0 if (read_check() or {}).get("ok") else 1


# ---------- submit / poll ----------

def cmd_submit(cfg: dict, a) -> int:
    hours = a.hours if a.hours is not None else cfg["default_hours"]
    if len(a.args) == 1 and any(c.isspace() for c in a.args[0]):  # --args "--seed 1 --n 2" given as one string
        a.args = shlex.split(a.args[0])
    if not 0 < hours <= cfg["max_hours"]:
        raise Refused(f"--hours must be in (0, {cfg['max_hours']}]")
    acc = a.accelerator or cfg["accelerator"]
    src = resolve_src(a.task, a.src)
    entry = Path(a.entry)
    if entry.is_absolute() or ".." in entry.parts or not (src / entry).is_file():
        raise Refused(f"--entry must be a file inside --src (relative path): {a.entry}")
    sha, nbytes, nfiles = src_stats(src, cfg["max_src_mb"])
    with Lock():
        jobs = load_jobs()
        if not a.cpu:
            validate_gpu_submit(cfg, jobs, acc, hours)
        user = kaggle_user()
        job_id = f"{a.task}-{time.strftime('%m%d%H%M%S')}"
        job = {"job_id": job_id, "task": a.task, "kind": "cpu" if a.cpu else "gpu", "src": str(src),
               "entry": str(entry), "args": a.args, "label": a.label, "hours": hours,
               "accelerator": None if a.cpu else acc, "src_sha256": sha, "src_bytes": nbytes, "src_files": nfiles,
               "state": "queued", "submitted": ledger.now(),
               "dataset_ref": f"{user}/{cfg['dataset_prefix']}{a.task}-{sha[:12]}",
               "kernel_ref": f"{user}/{cfg['kernel_prefix']}{job_id}"}
        save(job)
        try:
            ensure_dataset(job["dataset_ref"], src)
            push_kernel(job["kernel_ref"],
                        {"job_id": job_id, "mode": "run", "entry": str(entry), "args": a.args,
                         "payload": PAYLOAD, "dataset_slug": job["dataset_ref"].split("/", 1)[1],
                         "gpu_required": not a.cpu, "run_timeout_s": int(hours * 3600) - 300},
                        gpu=not a.cpu, accelerator=None if a.cpu else acc, timeout_s=int(hours * 3600),
                        dataset_ref=job["dataset_ref"], competition=cfg["competition"])
        except Exception as e:  # noqa: BLE001
            job.update(state="error", finished=ledger.now(), last_error=str(e)[-1500:], collected=True)
            save(job)
            print(f"submit failed: {job['last_error']}", file=sys.stderr)
            return 1
        job.update(state="pushed", pushed=ledger.now())
        save(job)
    log(f"{job_id}: pushed -> https://www.kaggle.com/code/{job['kernel_ref']}")
    print(job_id)
    return 0


def outdir_for(job: dict) -> Path:
    return WORK_ROOT / job["task"] / "out" / "compute" / job["job_id"]


def collect(job: dict) -> None:
    outdir = outdir_for(job)
    shutil.rmtree(outdir, ignore_errors=True)
    outdir.mkdir(parents=True)
    kaggle("kernels", "output", job["kernel_ref"], "-p", str(outdir), "-o", check=False)
    rf = outdir / "result.json"
    res = json.loads(rf.read_text()) if rf.exists() else None
    if res is None:
        job["result_error"] = "no result.json; log tail:\n" + log_tail(outdir)[-1500:]
    else:
        job["result_error"] = res.get("error")
        job["exit_code"] = res.get("exit_code")
        job["run_wall_s"] = res.get("run_wall_s")
        job["kernel_wall_s"] = res.get("kernel_wall_s")
        job["gpu_name"] = (res.get("gpu") or {}).get("gpu_name")
    job["collected"] = any(outdir.iterdir())
    job["out_dir"] = str(outdir)


def do_cleanup(cfg: dict, jobs: dict[str, dict]) -> None:
    if not cfg["cleanup"]:
        return
    user = None
    for job in jobs.values():
        if job["state"] not in ("done", "error") or job.get("cleaned") or not job.get("collected"):
            continue
        user = user or kaggle_user()
        delete_kernel(user, cfg, job["kernel_ref"])
        dref = job["dataset_ref"]
        if not any(o["job_id"] != job["job_id"] and o["dataset_ref"] == dref and not o.get("cleaned")
                   and o["state"] in ("queued", *ACTIVE) for o in jobs.values()):
            delete_dataset(user, cfg, dref)
        job["cleaned"] = True
        save(job)


def do_poll(cfg: dict) -> dict[str, dict]:
    resolve_gpu_check(cfg)
    jobs = load_jobs()
    for job in jobs.values():
        if job["state"] not in ACTIVE:
            continue
        st = kernel_status(job["kernel_ref"])
        if st == "COMPLETE" or st in ("ERROR", "CANCEL_ACKNOWLEDGED", "CANCEL_REQUESTED"):
            collect(job)
            job["state"] = "done" if st == "COMPLETE" and not job.get("result_error") else "error"
            if st != "COMPLETE" and not job.get("result_error"):
                job["result_error"] = f"kernel {st}"
            job["finished"] = ledger.now()
            log(f"{job['job_id']}: {st} -> {job['state']} {job.get('result_error') or ''}")
        elif st == "RUNNING" and job["state"] != "running":
            job["state"] = "running"
            job["started"] = ledger.now()
        elif st.startswith("UNKNOWN"):
            job["last_error"] = st
        else:
            continue
        save(job)
    do_cleanup(cfg, jobs)
    return jobs


def fmt_jobs(cfg: dict, jobs: dict[str, dict]) -> str:
    rows = [f"{'job_id':26} {'kind':4} {'state':8} {'exit':>4} {'run_s':>8}  {'gpu':14} label"]
    for j in sorted(jobs.values(), key=lambda j: j["submitted"]):
        rows.append(f"{j['job_id']:26} {j['kind']:4} {j['state']:8} {str(j.get('exit_code', '-')):>4} "
                    f"{str(j.get('run_wall_s') or '-'):>8}  {str(j.get('gpu_name') or '-')[:14]:14} {j.get('label', '')}"
                    + (f"  [{str(j['result_error'])[:80]}]" if j.get("result_error") else ""))
    rows.append(f"GPU hours, last 7 days (estimated): {gpu_hours_used(jobs):.1f} / {cfg['weekly_gpu_hours_budget']}")
    return "\n".join(rows)


def main() -> int:
    ap = argparse.ArgumentParser(prog="vqcc-compute", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gpu-check", help="validate that a Kaggle GPU kernel runs (needed before GPU submit)")
    g.add_argument("--accelerator")
    g.add_argument("--no-wait", action="store_true", help="push and return; `poll` finishes it")
    s = sub.add_parser("submit", help="run <entry> from work/<task>/<src> on a Kaggle GPU kernel")
    s.add_argument("task", choices=ledger.TASKS)
    s.add_argument("--src", required=True, help="directory inside work/<task>/")
    s.add_argument("--entry", required=True, help="python file relative to --src")
    s.add_argument("--args", nargs=argparse.REMAINDER, default=[], help="arguments for the entry. Must be last. A single quoted string is split like a shell (shlex).")
    s.add_argument("--label", default="")
    s.add_argument("--hours", type=float, help="kernel timeout in hours (default/max in compute.toml)")
    s.add_argument("--accelerator")
    s.add_argument("--cpu", action="store_true", help="CPU kernel (no GPU validation / budget)")
    p = sub.add_parser("poll", help="refresh states, collect outputs into work/<task>/out/compute/<job_id>/, cleanup")
    p.add_argument("--wait", type=int, metavar="SEC", default=0,
                   help="keep polling every 60s until no job is active, at most SEC seconds")
    st = sub.add_parser("status", help="list jobs, gpu_check, weekly GPU hours")
    st.add_argument("--json", action="store_true")
    a = ap.parse_args()
    cfg = config()
    try:
        if a.cmd == "status":
            jobs = load_jobs()
            if a.json:
                print(json.dumps({"jobs": jobs, "gpu_check": read_check(), "gpu_check_pending": PENDING.exists(),
                                  "gpu_hours_7d": round(gpu_hours_used(jobs), 2),
                                  "budget": cfg["weekly_gpu_hours_budget"]}, indent=1))
            else:
                print(fmt_jobs(cfg, jobs))
                print_check(read_check())
                if PENDING.exists():
                    print("gpu_check: a check is pending (run poll)")
            return 0
        if a.cmd == "gpu-check":
            return cmd_gpu_check(cfg, a)
        if a.cmd == "submit":
            return cmd_submit(cfg, a)
        if a.cmd == "poll":
            deadline = time.time() + a.wait
            while True:
                with Lock():
                    jobs = do_poll(cfg)
                pending = [j for j in jobs.values() if j["state"] in ACTIVE] + ([1] if PENDING.exists() else [])
                if not a.wait or not pending or time.time() > deadline:
                    break
                time.sleep(60)
            print(fmt_jobs(cfg, jobs))
            return 0
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
