"""Deterministic submitter (no LLM in the loop).

Policy for --auto:
  * every component must be "verified": all full/remote ledger runs of that candidate PASS
    (ledger.verified_best); tasks without one fall back to the official sample (ratio 1.0)
  * the predicted LB score (geometric mean of per-task ratio = ref / score) must beat every
    previous harness submission's prediction by > MIN_GAIN (relative)
  * at most VQCC_MAX_SUBMITS_PER_DAY submissions per UTC day (counted from Kaggle itself and
    from our own log, whichever is larger)
Every attempt (submitted or not) is appended to state/submissions.jsonl.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402

ROOT = ledger.ROOT
STATE = ROOT / "state"
LOG = STATE / "submissions.jsonl"
OUT = ROOT / "submissions"
SAMPLE = ROOT / "data" / "raw" / "sample_submission"
COMP = os.environ.get("VQCC_COMPETITION", "google-verifiable-quantum-circuit-compilation")
MAX_PER_DAY = int(os.environ.get("VQCC_MAX_SUBMITS_PER_DAY", "4"))
MIN_GAIN = 1e-4


def sample_path(task: str) -> Path:
    if task == "dfthcsos":
        return SAMPLE / "dfthcsos"
    for ext in (".kmb", ".kmx"):
        p = SAMPLE / f"{task}{ext}"
        if p.exists():
            return p
    raise FileNotFoundError(task)


def choose() -> dict:
    refs = ledger.load_refs()
    comps = {}
    for task in ledger.TASKS:
        best = ledger.verified_best(task)
        if best is not None and task in refs:
            comps[task] = {"cand_id": best["cand_id"], "path": str(ledger.stored_path(task, best["cand_id"])),
                           "score": best["score"], "ratio": refs[task] / best["score"], "label": best["label"]}
        else:
            comps[task] = {"cand_id": "sample", "path": str(sample_path(task)), "score": refs.get(task),
                           "ratio": 1.0, "label": "official sample"}
    pred = math.exp(sum(math.log(c["ratio"]) for c in comps.values()) / len(comps))
    return {"components": comps, "predicted": pred}


def build_zip(choice: dict, name: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    zpath = OUT / f"{name}.zip"
    tmp = zpath.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for task, c in choice["components"].items():
            p = Path(c["path"])
            if task == "dfthcsos":
                assert p.is_dir(), p
                for f in sorted(p.iterdir()):
                    if f.is_file() and (f.suffix in (".kmb", ".kmx") or f.name == "submission.npz"):
                        z.write(f, f"dfthcsos/{f.name}")
            else:
                z.write(p, f"{task}{p.suffix}")
    os.replace(tmp, zpath)
    return zpath


def log(rec: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def past() -> list[dict]:
    if not LOG.exists():
        return []
    return [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()]


def kaggle_today_count() -> int | None:
    """Number of submissions Kaggle shows for today (UTC). None if the API call fails."""
    try:
        out = subprocess.run(["kaggle", "competitions", "submissions", COMP, "--format", "json",
                              "--page-size", "50"], capture_output=True, text=True, timeout=120).stdout
    except Exception:
        return None
    if "No submissions" in out:
        return 0
    try:
        rows = json.loads(out[out.index("["):])
    except (ValueError, json.JSONDecodeError):
        return None
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    def day(r: dict) -> str:
        for k in ("date", "submittedAt", "dateSubmitted"):
            if r.get(k):
                return str(r[k])
        return ""
    return sum(1 for r in rows if day(r).startswith(today))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true", help="submit if policy allows")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    choice = choose()
    now = dt.datetime.now(dt.timezone.utc)
    ids = {t: c["cand_id"] for t, c in choice["components"].items()}
    print(json.dumps({"predicted": choice["predicted"], "components": {
        t: {k: c[k] for k in ("cand_id", "score", "ratio", "label")} for t, c in choice["components"].items()}},
        indent=2))

    if not a.auto:
        return 0

    prev = [r for r in past() if r.get("submitted")]
    prev_best = max((r["predicted"] for r in prev), default=0.0)
    today = now.date().isoformat()
    ours_today = sum(1 for r in prev if r["ts"].startswith(today))
    kag_today = kaggle_today_count()
    used = max(ours_today, kag_today if kag_today is not None else ours_today)
    rec = {"ts": now.isoformat(timespec="seconds"), "predicted": choice["predicted"], "ids": ids,
           "prev_best_predicted": prev_best, "used_today": used, "submitted": False}

    if kag_today is None:
        rec["reason"] = "could not read Kaggle submission count"
    elif used >= MAX_PER_DAY:
        rec["reason"] = f"daily cap {MAX_PER_DAY} reached"
    elif all(v == "sample" for v in ids.values()):
        rec["reason"] = "nothing verified yet"
    elif choice["predicted"] <= prev_best * (1 + MIN_GAIN):
        rec["reason"] = "no predicted improvement"
    elif a.dry_run:
        rec["reason"] = "dry-run: would submit"
        print(json.dumps({k: rec[k] for k in ("reason", "predicted", "prev_best_predicted", "used_today")}))
        return 0
    else:
        name = now.strftime("%Y%m%dT%H%M%SZ")
        zpath = build_zip(choice, name)
        msg = f"vqcc-harness pred={choice['predicted']:.5f} " + " ".join(f"{t}={i}" for t, i in ids.items())
        r = subprocess.run(["kaggle", "competitions", "submit", COMP, "-f", str(zpath), "-m", msg],
                           capture_output=True, text=True, timeout=3600)
        rec.update({"zip": str(zpath), "message": msg, "returncode": r.returncode,
                    "stdout": r.stdout[-2000:], "stderr": r.stderr[-2000:], "submitted": r.returncode == 0})
        rec["reason"] = "submitted" if r.returncode == 0 else "kaggle submit failed"
    log(rec)
    print(json.dumps({k: rec[k] for k in ("submitted", "reason", "predicted", "prev_best_predicted", "used_today")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
