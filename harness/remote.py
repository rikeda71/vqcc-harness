"""vqcc-remote: run the official verifier on Kaggle CPU kernels (see bin/vqcc-remote).

Flow: submit -> ledger.store + queue -> (capacity) upload candidate as private dataset
vqcc-cand-<task>-<cand12> -> push private script kernel vqcc-job-<job_id> (competition + dataset
sources, template harness/remote_kernel.py) -> poll -> `kaggle kernels output` -> ledger record
(tier=remote, host=kaggle) -> delete the kernel / dataset that this tool created.

State: state/remote/jobs.jsonl (append-only event log, last line per job_id wins),
state/remote/out/<job_id>/ (downloaded kernel output). Stdlib only; talks to the `kaggle` CLI.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness import ledger  # noqa: E402

STATE = ROOT / "state" / "remote"
JOBS = STATE / "jobs.jsonl"
OUT = STATE / "out"
LOCK = STATE / ".lock"
CONF = ROOT / "harness" / "remote.toml"
TEMPLATE = ROOT / "harness" / "remote_kernel.py"
COMPUTE_JOBS = ROOT / "state" / "compute" / "jobs.jsonl"
PAYLOAD = "cand.tar.gz"
ACTIVE = ("pushed", "running")


# ---------- config / kaggle CLI ----------

def config() -> dict:
    import tomllib
    c = {"competition": "google-verifiable-quantum-circuit-compilation", "max_concurrent": 3,
         "kernel_timeout_s": 43200, "cleanup": True, "kernel_prefix": "vqcc-job-",
         "dataset_prefix": "vqcc-cand-", "hold_while_gpu_running": True,
         "stale_queued_min": 60, "max_repush": 3}
    if CONF.exists():
        c.update(tomllib.loads(CONF.read_text()))
    if os.environ.get("VQCC_REMOTE_MAX_CONCURRENT"):
        c["max_concurrent"] = int(os.environ["VQCC_REMOTE_MAX_CONCURRENT"])
    return c


def kaggle_bin() -> str:
    return shutil.which("kaggle") or str(Path.home() / ".local" / "bin" / "kaggle")


def kaggle(*args: str, check: bool = True, timeout: int = 3600) -> subprocess.CompletedProcess:
    p = subprocess.run([kaggle_bin(), *args], capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"kaggle {' '.join(args[:2])} failed ({p.returncode}): {(p.stdout + p.stderr)[-1500:]}")
    return p


def kaggle_user() -> str:
    if os.environ.get("VQCC_KAGGLE_USER"):
        return os.environ["VQCC_KAGGLE_USER"]
    out = kaggle("config", "view").stdout  # prints username/path/proxy/competition only, never the token
    m = re.search(r"^- username:\s*(\S+)", out, re.M)
    if not m:
        raise RuntimeError("cannot determine Kaggle username (set VQCC_KAGGLE_USER)")
    return m.group(1)


# ---------- job store ----------

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
    print(f"[vqcc-remote {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# ---------- dataset / kernel ----------

def make_payload(stored: Path, dest: Path) -> dict:
    """tar.gz the candidate (file or directory contents). .kmb compresses ~25x (dqi 886 MB -> ~35 MB)."""
    t = time.time()
    with tarfile.open(dest, "w:gz", compresslevel=3) as tf:
        if stored.is_dir():
            for f in sorted(stored.iterdir()):
                tf.add(f, arcname=f.name)
        else:
            tf.add(stored, arcname=stored.name)
    log(f"packed {stored.name}: {dest.stat().st_size / 1e6:.1f} MB in {time.time() - t:.0f}s")
    return {"kind": "dir" if stored.is_dir() else "file", "name": stored.name}


def dataset_ready(ref: str) -> bool:
    p = kaggle("datasets", "status", ref, check=False)
    return p.returncode == 0 and p.stdout.strip().lower().startswith("ready")


def ensure_dataset(job: dict, stored: Path) -> None:
    ref = job["dataset_ref"]
    if dataset_ready(ref):
        log(f"dataset {ref} already exists, reusing")
        return
    with tempfile.TemporaryDirectory(prefix="vqcc-ds-") as td:
        td = Path(td)
        make_payload(stored, td / PAYLOAD)
        slug = ref.split("/", 1)[1]
        (td / "dataset-metadata.json").write_text(json.dumps(
            {"title": slug, "id": ref, "licenses": [{"name": "other"}]}))
        t = time.time()
        p = kaggle("datasets", "create", "-p", str(td), "-t", check=False)
        log(f"datasets create {ref}: {time.time() - t:.0f}s rc={p.returncode}")
        if p.returncode != 0 and not dataset_ready(ref):
            raise RuntimeError("datasets create failed: " + (p.stdout + p.stderr)[-1500:])
    for _ in range(90):  # up to ~7.5 min
        if dataset_ready(ref):
            return
        time.sleep(5)
    raise RuntimeError(f"dataset {ref} not ready in time")


def push_kernel(job: dict, cfg: dict) -> None:
    job_json = {k: job[k] for k in ("job_id", "task", "salt", "args", "kind", "name", "payload")}
    job_json["dataset_slug"] = job["dataset_ref"].split("/", 1)[1]
    src = TEMPLATE.read_text()
    assert "__JOB_JSON__" in src
    src = src.replace("__JOB_JSON__", json.dumps(job_json))
    slug = job["kernel_ref"].split("/", 1)[1]
    with tempfile.TemporaryDirectory(prefix="vqcc-kn-") as td:
        td = Path(td)
        (td / "script.py").write_text(src)
        (td / "kernel-metadata.json").write_text(json.dumps({
            "id": job["kernel_ref"], "title": slug, "code_file": "script.py", "language": "python",
            "kernel_type": "script", "is_private": True, "enable_gpu": False, "enable_internet": False,
            "dataset_sources": [job["dataset_ref"]], "competition_sources": [cfg["competition"]],
            "kernel_sources": [], "model_sources": []}))
        p = kaggle("kernels", "push", "-p", str(td), "-t", str(cfg["kernel_timeout_s"]), check=False)
        if p.returncode != 0 or "successfully pushed" not in p.stdout:
            raise RuntimeError("kernels push failed: " + (p.stdout + p.stderr)[-1500:])


def parse_ts(s: str | None) -> float | None:
    try:
        return dt.datetime.fromisoformat(s).timestamp() if s else None
    except ValueError:
        return None


def stale_queued(job: dict, cfg: dict, now: float | None = None) -> bool:
    """Kaggle sometimes leaves a pushed kernel QUEUED forever (seen 2026-11-09: 30 h+, while fresh pushes
    ran at once). True when the current version has been QUEUED longer than stale_queued_min."""
    t0 = parse_ts(job.get("repushed") or job.get("pushed"))
    return t0 is not None and (now or time.time()) - t0 > float(cfg["stale_queued_min"]) * 60


def kernel_status(ref: str) -> str:
    p = kaggle("kernels", "status", ref, check=False)
    m = re.search(r'status "(?:KernelWorkerStatus\.)?([A-Z_]+)"', p.stdout)
    if not m:
        return "UNKNOWN: " + (p.stdout + p.stderr).strip()[-300:]
    return m.group(1)


# ---------- actions ----------

def gpu_running() -> list[str]:
    """job_ids of vqcc-compute GPU jobs that Kaggle reports RUNNING (last line per job_id wins)."""
    last: dict[str, dict] = {}
    if COMPUTE_JOBS.exists():
        for line in COMPUTE_JOBS.read_text().splitlines():
            try:
                j = json.loads(line)
                last[j["job_id"]] = j
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
    return [k for k, j in last.items() if j.get("kind") == "gpu" and j.get("state") == "running"]


def dispatch(cfg: dict, jobs: dict[str, dict]) -> None:
    """Push queued jobs (oldest first) while fewer than max_concurrent are pushed/running.

    While a vqcc-compute GPU job is RUNNING (and hold_while_gpu_running), nothing new is pushed."""
    queued = [j for j in jobs.values() if j["state"] == "queued"]
    if queued and cfg.get("hold_while_gpu_running", True):
        gpu = gpu_running()
        if gpu:
            log(f"holding {len(queued)} queued job(s) while GPU job {', '.join(gpu)} is running")
            return
    active = sum(1 for j in jobs.values() if j["state"] in ACTIVE)
    for job in sorted((j for j in jobs.values() if j["state"] == "queued"), key=lambda j: j["submitted"]):
        if active >= cfg["max_concurrent"]:
            break
        try:
            ensure_dataset(job, ledger.stored_path(job["task"], job["cand_id"]))
            push_kernel(job, cfg)
        except Exception as e:  # noqa: BLE001
            if "Maximum batch CPU session count" in str(e):  # Kaggle-side cap hit: stay queued, retry later
                log(f"{job['job_id']}: Kaggle concurrent-session cap reached, staying queued")
                break
            job["attempts"] = job.get("attempts", 0) + 1
            job["last_error"] = str(e)[-1500:]
            log(f"{job['job_id']}: push failed (attempt {job['attempts']}): {job['last_error'][-300:]}")
            if job["attempts"] >= 3:
                job["state"] = "error"
                job["finished"] = ledger.now()
                _ledger_error(job, "push failed: " + job["last_error"])
            save(job)
            continue
        job["state"] = "pushed"
        job["pushed"] = ledger.now()
        save(job)
        active += 1
        log(f"{job['job_id']}: pushed -> https://www.kaggle.com/code/{job['kernel_ref']}")


def _ratio(task: str, score) -> float | None:
    ref = ledger.load_refs().get(task)
    return (ref / score) if (ref and score) else None


def _ledger_error(job: dict, text: str) -> None:
    ledger.append({
        "ts": ledger.now(), "task": job["task"], "cand_id": job["cand_id"], "sha256": job["sha256"],
        "tier": "remote" if not job["args"] else "quick", "host": "kaggle", "kickmix": None,
        "salt": job["salt"], "args": job["args"], "status": "ERROR", "score": None, "ratio": None,
        "wall_s": None, "label": job["label"], "result": {"error": text, "job_id": job["job_id"],
                                                          "kernel": job["kernel_ref"]}})
    job["ledger_written"] = True


def _log_tail(outdir: Path) -> str:
    """Last stderr/stdout lines from the downloaded kernel log (JSON list of stream events)."""
    for f in outdir.glob("*.log"):
        try:
            ev = json.loads(f.read_text())
            lines = "".join(e.get("data", "") for e in ev if e.get("stream_name") in ("stderr", "stdout"))
            return "\n".join(lines.strip().splitlines()[-25:])
        except Exception:  # noqa: BLE001
            return f.read_text()[-2000:]
    return ""


def collect(job: dict) -> None:
    outdir = OUT / job["job_id"]
    shutil.rmtree(outdir, ignore_errors=True)
    outdir.mkdir(parents=True)
    kaggle("kernels", "output", job["kernel_ref"], "-p", str(outdir), "-o", check=False)
    rf = outdir / "result.json"
    res = json.loads(rf.read_text()) if rf.exists() else None
    base = {"ts": ledger.now(), "task": job["task"], "cand_id": job["cand_id"], "sha256": job["sha256"],
            "tier": "remote" if not job["args"] else "quick", "host": "kaggle", "salt": job["salt"],
            "args": job["args"], "label": job["label"]}
    if res is None or res.get("error") or (res.get("verifier") is None):
        why = (res or {}).get("error") or ((res or {}).get("stderr_tail", "") + (res or {}).get("stdout", ""))[-1500:] \
            or "no result.json produced; log tail:\n" + _log_tail(outdir)
        rec = {**base, "kickmix": (res or {}).get("kickmix_version"), "status": "ERROR", "score": None,
               "ratio": None, "wall_s": (res or {}).get("verify_wall_s"),
               "result": {"error": why, "job_id": job["job_id"], "kernel": job["kernel_ref"]}}
    else:
        v = res["verifier"]
        st = "PASS" if v.get("status") == "PASS" else "FAIL"
        score = v.get("score") if st == "PASS" else None
        rec = {**base, "kickmix": res.get("kickmix_version") or ("so:" + res["kickmix_so_sha256"][:12]),
               "status": st, "score": score, "ratio": _ratio(job["task"], score),
               "wall_s": res.get("verify_wall_s"),
               "result": {**v, "_remote": {"job_id": job["job_id"], "kernel": job["kernel_ref"],
                                          "cpu_count": res.get("cpu_count"), "python": res.get("python"),
                                          "kickmix_so_sha256": res.get("kickmix_so_sha256"),
                                          "unpack_s": res.get("unpack_s"),
                                          "kernel_wall_s": res.get("kernel_wall_s")}}}
    ledger.append(rec)
    job["ledger_written"] = True
    job["result_status"] = rec["status"]
    job["score"] = rec["score"]
    job["wall_s"] = rec["wall_s"]


def cleanup(cfg: dict, jobs: dict[str, dict]) -> None:
    """Delete harness-created Kaggle objects for finished jobs (names are checked against prefixes)."""
    if not cfg["cleanup"]:
        return
    user = kaggle_user()
    for job in jobs.values():
        if job["state"] not in ("done", "error") or job.get("cleaned") or not job.get("ledger_written"):
            continue
        kref = job["kernel_ref"]
        if kref.startswith(f"{user}/{cfg['kernel_prefix']}"):
            p = kaggle("kernels", "delete", kref, "-y", check=False)
            log(f"{job['job_id']}: delete kernel rc={p.returncode}")
        dref = job["dataset_ref"]
        still = any(o["job_id"] != job["job_id"] and o["dataset_ref"] == dref
                    and o["state"] in ("queued", *ACTIVE) for o in jobs.values())
        if not still and dref.startswith(f"{user}/{cfg['dataset_prefix']}"):
            p = kaggle("datasets", "delete", dref, "-y", check=False)
            log(f"{job['job_id']}: delete dataset rc={p.returncode}")
        job["cleaned"] = True
        save(job)


def do_poll(cfg: dict) -> dict[str, dict]:
    jobs = load_jobs()
    for job in jobs.values():
        if job["state"] not in ACTIVE:
            continue
        st = kernel_status(job["kernel_ref"])
        if st == "COMPLETE":
            collect(job)
            job["state"] = "done"
            job["finished"] = ledger.now()
            log(f"{job['job_id']}: complete -> {job['result_status']} score={job['score']} wall_s={job['wall_s']}")
        elif st in ("ERROR", "CANCEL_ACKNOWLEDGED", "CANCEL_REQUESTED"):
            outdir = OUT / job["job_id"]
            outdir.mkdir(parents=True, exist_ok=True)
            kaggle("kernels", "output", job["kernel_ref"], "-p", str(outdir), "-o", check=False)
            _ledger_error(job, f"kernel {st}; log tail:\n{_log_tail(outdir)}")
            job["state"] = "error"
            job["finished"] = ledger.now()
            log(f"{job['job_id']}: {st}")
        elif st == "RUNNING" and job["state"] != "running":
            job["state"] = "running"
        elif st == "QUEUED" and stale_queued(job, cfg):
            if job.get("repushes", 0) >= cfg["max_repush"]:
                _ledger_error(job, f"kernel stuck QUEUED on Kaggle after {job.get('repushes', 0)} re-pushes")
                job["state"] = "error"
                job["finished"] = ledger.now()
                log(f"{job['job_id']}: still QUEUED after {job.get('repushes', 0)} re-pushes -> error")
            else:  # a new kernel version replaces the stuck one
                try:
                    push_kernel(job, cfg)
                except Exception as e:  # noqa: BLE001
                    job["last_error"] = str(e)[-1500:]
                    log(f"{job['job_id']}: re-push failed: {job['last_error'][-300:]}")
                job["repushes"] = job.get("repushes", 0) + 1
                job["repushed"] = ledger.now()
                log(f"{job['job_id']}: QUEUED > {cfg['stale_queued_min']} min on Kaggle -> re-pushed "
                    f"({job['repushes']}/{cfg['max_repush']})")
        elif st.startswith("UNKNOWN"):
            job["last_error"] = st
        else:
            continue
        save(job)
    cleanup(cfg, jobs)
    dispatch(cfg, jobs)
    return jobs


def fmt_jobs(jobs: dict[str, dict]) -> str:
    rows = [f"{'job_id':34} {'state':8} {'tier':6} {'status':6} {'score':>14} {'wall_s':>8}  label"]
    for j in sorted(jobs.values(), key=lambda j: j["submitted"]):
        s = j.get("score")
        rows.append(f"{j['job_id']:34} {j['state']:8} {'quick' if j['args'] else 'remote':6} "
                    f"{j.get('result_status', '-'):6} {'-' if s is None else format(s, '.6g'):>14} "
                    f"{str(j.get('wall_s') or '-'):>8}  {j['label']}")
    return "\n".join(rows)


def main() -> int:
    ap = argparse.ArgumentParser(prog="vqcc-remote", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit", help="queue a candidate for official verification on Kaggle")
    s.add_argument("task", choices=ledger.TASKS)
    s.add_argument("candidate", type=Path)
    s.add_argument("--salt", default="")
    s.add_argument("--label", default="")
    s.add_argument("--verifier-args", nargs=argparse.REMAINDER, default=[],
                   help="extra official-verifier args (e.g. --shots-per-x 1); recorded as tier=quick. Must be last.")
    p = sub.add_parser("poll", help="refresh job states, collect results into the ledger, push queued jobs")
    p.add_argument("--wait", type=int, metavar="SEC", default=0,
                   help="keep polling every 60s until no job is queued/active, at most SEC seconds")
    st = sub.add_parser("status", help="list jobs")
    st.add_argument("--json", action="store_true")
    a = ap.parse_args()
    cfg = config()

    if a.cmd == "status":
        jobs = load_jobs()
        print(json.dumps(jobs, indent=1) if a.json else fmt_jobs(jobs))
        return 0

    if a.cmd == "submit":
        with Lock():
            cand_id, sha, stored = ledger.store(a.task, a.candidate)
            jobs = load_jobs()
            for j in jobs.values():
                if (j["task"], j["cand_id"], j["salt"], j["args"]) == (a.task, cand_id, a.salt, a.verifier_args) \
                        and j["state"] in ("queued", *ACTIVE):
                    print(j["job_id"])
                    log("identical job already in flight")
                    return 0
            user = kaggle_user()
            job_id = f"{a.task}-{cand_id[:8]}-{time.strftime('%m%d%H%M%S')}"
            job = {"job_id": job_id, "task": a.task, "cand_id": cand_id, "sha256": sha, "salt": a.salt,
                   "args": a.verifier_args, "label": a.label, "state": "queued", "submitted": ledger.now(),
                   "payload": PAYLOAD, "kind": "dir" if stored.is_dir() else "file", "name": stored.name,
                   "dataset_ref": f"{user}/{cfg['dataset_prefix']}{a.task}-{cand_id[:12]}",
                   "kernel_ref": f"{user}/{cfg['kernel_prefix']}{job_id}"}
            save(job)
            jobs[job_id] = job
            dispatch(cfg, jobs)
        print(job_id)
        return 0

    if a.cmd == "poll":
        deadline = time.time() + a.wait
        while True:
            with Lock():
                jobs = do_poll(cfg)
            pending = [j for j in jobs.values() if j["state"] in ("queued", *ACTIVE)]
            if not a.wait or not pending or time.time() > deadline:
                break
            time.sleep(60)
        print(fmt_jobs(jobs))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
