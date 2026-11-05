"""Candidate store + results ledger shared by every harness tool.

Layout (all gitignored):
  artifacts/<task>/<id>/...      immutable copy of a candidate (id = first 16 hex of content sha256)
  ledger/results.jsonl           one JSON object per verification run (append-only)

A ledger record:
  ts            ISO-8601 UTC time the run finished
  task          invsqrt | dqi | dfthcsos
  cand_id       artifacts/<task>/<cand_id>
  sha256        full content sha256 (see content_sha256)
  tier          quick | full | remote
  host          local | kaggle
  kickmix       version string of the kickmix that ran the verifier
  salt          salt passed to the verifier ("" = none)
  args          extra verifier args (list of str)
  status        PASS | FAIL | ERROR
  score         verifier "score" (raw cost, lower is better) when PASS, else null
  ratio         ref_cost / score (the per-task LB factor, higher is better) when PASS, else null
  wall_s        wall-clock seconds
  label         free text from the worker (what idea this candidate tests)
  result        the verifier's full JSON output (or error text)

Only tier=full or tier=remote PASS records count as "verified" for assembling a submission.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"
LEDGER = ROOT / "ledger" / "results.jsonl"
TASKS = ("invsqrt", "dqi", "dfthcsos")

# Raw verifier score of the official sample submission (= LB factor 1.0) per task.
# Filled in / confirmed by harness/refs.json (written by bin/vqcc-calibrate); these are fallbacks.
REFS_FILE = ROOT / "harness" / "refs.json"


def load_refs() -> dict[str, float]:
    if REFS_FILE.exists():
        return {k: float(v) for k, v in json.loads(REFS_FILE.read_text()).items() if k in TASKS}
    return {}


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _files_of(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*") if p.is_file())


def content_sha256(path: Path) -> str:
    """sha256 over (relative name, bytes) of every file; a single file hashes as its basename."""
    path = Path(path)
    h = hashlib.sha256()
    base = path if path.is_dir() else path.parent
    for f in _files_of(path):
        h.update(str(f.relative_to(base)).encode())
        h.update(b"\0")
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        h.update(b"\0")
    return h.hexdigest()


def store(task: str, path: Path) -> tuple[str, str, Path]:
    """Copy a candidate (file or dir) into the content-addressed store. Returns (cand_id, sha, stored_path)."""
    assert task in TASKS, task
    path = Path(path).resolve()
    sha = content_sha256(path)
    cand_id = sha[:16]
    dest_dir = ARTIFACTS / task / cand_id
    stored = dest_dir / path.name
    if not stored.exists():
        dest_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest_dir / (".tmp-" + path.name)
        if path.is_dir():
            shutil.copytree(path, tmp)
        else:
            shutil.copy2(path, tmp)
        os.replace(tmp, stored)
    return cand_id, sha, stored


def stored_path(task: str, cand_id: str) -> Path:
    d = ARTIFACTS / task / cand_id
    entries = [p for p in d.iterdir() if not p.name.startswith(".")]
    assert len(entries) == 1, f"unexpected store layout in {d}"
    return entries[0]


def append(record: dict[str, Any]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True)
    with open(LEDGER, "a") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def records() -> Iterable[dict[str, Any]]:
    if not LEDGER.exists():
        return []
    out = []
    with open(LEDGER) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def blocklist(task: str) -> set[str]:
    """cand_ids a worker judged illegitimate (work/<task>/BLOCKLIST, one "<cand_id> <reason>" per line)."""
    f = ROOT / "work" / task / "BLOCKLIST"
    if not f.exists():
        return set()
    return {l.split()[0] for l in f.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")}


def verified_best(task: str, min_salts: int = 1) -> dict[str, Any] | None:
    """Best (lowest score) candidate whose every full/remote run PASSed, with >= min_salts distinct salts."""
    by_cand: dict[str, list[dict[str, Any]]] = {}
    for r in records():
        # ERROR = infrastructure/crash, not evidence about the circuit: ignore it here.
        if (r.get("task") == task and r.get("tier") in ("full", "remote")
                and r.get("status") in ("PASS", "FAIL")):
            by_cand.setdefault(r["cand_id"], []).append(r)
    best = None
    blocked = blocklist(task)
    for cand_id, runs in by_cand.items():
        if cand_id in blocked or any(r["status"] != "PASS" for r in runs):
            continue
        if len({r.get("salt", "") for r in runs}) < min_salts:
            continue
        score = max(r["score"] for r in runs)  # pessimistic across salts
        if best is None or score < best["score"]:
            best = {"cand_id": cand_id, "score": score, "runs": len(runs),
                    "salts": sorted({r.get("salt", "") for r in runs}),
                    "label": runs[-1].get("label", "")}
    return best
