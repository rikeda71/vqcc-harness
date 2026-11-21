"""Daily disk cleanup of leftover intermediates (human decision 2026-11-21: no approval needed).

Run by bin/night-supervisor at night start and whenever free disk drops below
VQCC_CLEANUP_FREE_MIN_GB (checked hourly), or by hand: bin/vqcc-cleanup [--dry-run].

What it deletes (sizes and ages from env, defaults in brackets):
  1. work/<task>/out/** files >= VQCC_CLEANUP_MIN_MB [100] MB that are
       a. tracked in work/ git, committed at HEAD and unmodified, older than VQCC_CLEANUP_TRACKED_H [24] h
          (restorable: git -C work checkout HEAD -- <path>), or
       b. untracked / git-ignored, older than VQCC_CLEANUP_UNTRACKED_H [48] h (regenerable intermediates).
     Files whose out/ path is named in the task's DIRECTIVE.md or the top of NOTES.md (現在地 / 進行中)
     are kept.
  2. data/intel/outputs/<dir> whose newest file is older than VQCC_CLEANUP_INTEL_D [3] days
     (public notebook outputs; re-downloadable, copies used by workers live in work/<task>/out/pub_*).
  3. artifacts/<task>/<cand_id> older than VQCC_CLEANUP_ARTIFACT_D [3] days with no full/remote PASS
     in the ledger, not saved as a model, and not in an unfinished remote job.
Never touches models/, ledger/, submissions/, work/.git, src/ or anything outside these three areas.
Every deleted path is listed in state/cleanup/<UTC timestamp>.txt.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
TASKS = ("invsqrt", "dqi", "dfthcsos")
NOTES_TOP_LINES = 150


def env_num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


MIN_BYTES = env_num("VQCC_CLEANUP_MIN_MB", 100) * 1e6
TRACKED_S = env_num("VQCC_CLEANUP_TRACKED_H", 24) * 3600
UNTRACKED_S = env_num("VQCC_CLEANUP_UNTRACKED_H", 48) * 3600
INTEL_S = env_num("VQCC_CLEANUP_INTEL_D", 3) * 86400
ARTIFACT_S = env_num("VQCC_CLEANUP_ARTIFACT_D", 3) * 86400


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(WORK), *args], capture_output=True, text=True, check=False).stdout


def newest_mtime(p: Path) -> float:
    if p.is_file():
        return p.stat().st_mtime
    m = p.stat().st_mtime
    for dp, _dn, fn in os.walk(p):
        for f in fn:
            try:
                m = max(m, os.lstat(os.path.join(dp, f)).st_mtime)
            except OSError:
                pass
    return m


def du(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    n = 0
    for dp, _dn, fn in os.walk(p):
        for f in fn:
            try:
                n += os.lstat(os.path.join(dp, f)).st_size
            except OSError:
                pass
    return n


def protected_text(task: str) -> str:
    txt = ""
    d = WORK / task / "DIRECTIVE.md"
    if d.exists():
        txt += d.read_text(errors="replace")
    n = WORK / task / "NOTES.md"
    if n.exists():
        with n.open(errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= NOTES_TOP_LINES:
                    break
                txt += line
    return txt


def work_outputs(now: float) -> list[tuple[Path, int, str]]:
    out: list[tuple[Path, int, str]] = []
    head = set(git("ls-tree", "-r", "--name-only", "HEAD").splitlines())
    index = set(git("ls-files").splitlines())
    modified = set(git("diff", "--name-only", "HEAD").splitlines())
    for task in TASKS:
        base = WORK / task / "out"
        if not base.is_dir():
            continue
        prot = protected_text(task)
        for dp, _dn, fn in os.walk(base):
            for f in fn:
                p = Path(dp) / f
                try:
                    st = p.lstat()
                except OSError:
                    continue
                if not p.is_file() or p.is_symlink() or st.st_size < MIN_BYTES:
                    continue
                rel = p.relative_to(WORK).as_posix()           # dqi/out/x/y.kmx
                rel_task = p.relative_to(WORK / task).as_posix()  # out/x/y.kmx
                if rel_task in prot or rel in prot:
                    continue
                age = now - st.st_mtime
                if rel in index:
                    if rel in head and rel not in modified and age >= TRACKED_S:
                        out.append((p, st.st_size, "tracked@HEAD"))
                elif age >= UNTRACKED_S:
                    out.append((p, st.st_size, "untracked"))
    return out


def intel_outputs(now: float) -> list[tuple[Path, int, str]]:
    base = ROOT / "data" / "intel" / "outputs"
    res = []
    if base.is_dir():
        for d in base.iterdir():
            if now - newest_mtime(d) >= INTEL_S:
                res.append((d, du(d), "intel-output"))
    return res


def artifacts(now: float) -> list[tuple[Path, int, str]]:
    keep: set[str] = set()
    led = ROOT / "ledger" / "results.jsonl"
    if led.exists():
        with led.open() as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("status") == "PASS" and r.get("tier") in ("full", "remote"):
                    keep.add(r.get("cand_id", ""))
    for meta in (ROOT / "models").glob("*/v*/meta.json"):
        try:
            keep.add(json.loads(meta.read_text()).get("cand_id", ""))
        except ValueError:
            pass
    jobs = ROOT / "state" / "remote" / "jobs.jsonl"
    if jobs.exists():
        last: dict[str, dict] = {}
        with jobs.open() as fh:
            for line in fh:
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                last[j.get("job_id", "")] = j
        for j in last.values():
            if j.get("state") not in ("done", "complete", "error", "failed", "cancelled"):
                keep.add(j.get("cand_id", ""))
    res = []
    for task in TASKS:
        base = ROOT / "artifacts" / task
        if not base.is_dir():
            continue
        for d in base.iterdir():
            if d.name in keep or d.name.startswith("."):
                continue
            if now - newest_mtime(d) >= ARTIFACT_S:
                res.append((d, du(d), "artifact-noPASS"))
    return res


def main() -> int:
    dry = "--dry-run" in sys.argv
    now = time.time()
    found = work_outputs(now) + intel_outputs(now) + artifacts(now)
    total = sum(s for _p, s, _k in found)
    free_before = shutil.disk_usage(ROOT).free
    lines = []
    for p, s, kind in found:
        lines.append(f"{s / 1e9:8.3f}G  {kind:16s} {p.relative_to(ROOT)}")
        if not dry:
            try:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
            except OSError as e:
                lines[-1] += f"  (failed: {e})"
    free_after = shutil.disk_usage(ROOT).free
    by_kind: dict[str, float] = {}
    for _p, s, k in found:
        by_kind[k] = by_kind.get(k, 0) + s / 1e9
    summary = (f"cleanup{' (dry-run)' if dry else ''}: {len(found)} paths, {total / 1e9:.1f}G "
               f"({', '.join(f'{k} {v:.1f}G' for k, v in sorted(by_kind.items()))}); "
               f"free {free_before / 1e9:.0f}G -> {free_after / 1e9:.0f}G")
    if not dry and found:
        logdir = ROOT / "state" / "cleanup"
        logdir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        (logdir / f"{stamp}.txt").write_text(
            summary + "\nrestore tracked files: git -C work checkout HEAD -- <path without work/>\n"
            + "\n".join(lines) + "\n")
    if dry:
        print("\n".join(lines))
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
