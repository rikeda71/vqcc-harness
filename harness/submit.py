"""Deterministic submitter (no LLM in the loop).

Policy for --auto (every check is recorded; the first failing one is the "reason"):
  * Kaggle's submission list must be readable. It is synced first into state/kaggle_submissions.json
    (our submissions only, matched by the message we write: status, public score, the cand_ids
    and model versions it contained, our prediction)
  * state/SUBMIT_BLOCKED must not exist. It is created when one of our Kaggle submissions scores more
    than LB_GAP_BLOCK below its prediction (our verification no longer predicts the LB): a human
    reviews and deletes the file
  * a submission that comes back as ERROR does not stop auto-submit: its candidates are "suspects"
    and are left out of the selection (the next verified candidate of that task is used instead;
    error_suspects). The suspects of an ERROR submission are released once any one of its candidates
    is put on its task's BLOCKLIST (the culprit was identified); a candidate is also released when a
    COMPLETE submission of ours contained it
  * no earlier submission of ours may still be scoring on Kaggle (learn its result first)
  * every component must be "verified": all full/remote ledger runs of that candidate PASS
    (ledger.verified_best); tasks without one fall back to the official sample (ratio 1.0)
  * the exact combination of candidates must not have been submitted before
  * the predicted LB (geometric mean of per-task ratio = ref / score) must beat, by > MIN_GAIN
    (relative), both every previous submission's prediction and the best public score Kaggle
    actually gave us
  * at most VQCC_MAX_SUBMITS_PER_DAY submissions per UTC day (counted from Kaggle itself and
    from our own log, whichever is larger)
Every attempt (submitted or not) is appended to state/submissions.jsonl. The upload is
submissions/<UTC timestamp>/submission.zip (Kaggle requires that exact file name). --auto exits 1
when `kaggle competitions submit` itself failed, 0 otherwise (including "policy said no").

  vqcc-submit                 show what would be submitted
  vqcc-submit --auto          submit if the policy allows (supervisor)
  vqcc-submit --sync          only refresh state/kaggle_submissions.json and print the history
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402

ROOT = ledger.ROOT
STATE = ROOT / "state"
LOG = STATE / "submissions.jsonl"
KSUBS = STATE / "kaggle_submissions.json"
BLOCK = STATE / "SUBMIT_BLOCKED"
ERR_SEEN = STATE / "kaggle_errors_seen"
OUT = ROOT / "submissions"
MODELS = ROOT / "models"
SAMPLE = ROOT / "data" / "raw" / "sample_submission"
COMP = os.environ.get("VQCC_COMPETITION", "google-verifiable-quantum-circuit-compilation")
MAX_PER_DAY = int(os.environ.get("VQCC_MAX_SUBMITS_PER_DAY", "5"))
MIN_GAIN = 1e-4
MSG_RE = re.compile(r"vqcc-harness pred=([0-9.]+)\s+(.*)")


def sample_path(task: str) -> Path:
    if task == "dfthcsos":
        return SAMPLE / "dfthcsos"
    for ext in (".kmb", ".kmx"):
        p = SAMPLE / f"{task}{ext}"
        if p.exists():
            return p
    raise FileNotFoundError(task)


def model_version(task: str, cand_id: str) -> str | None:
    """models/<task>/v<N> that holds cand_id (see harness/cmd_model.py), or None."""
    d = MODELS / task
    if cand_id == "sample" or not d.is_dir():
        return None
    for meta in d.glob("v*/meta.json"):
        try:
            if json.loads(meta.read_text()).get("cand_id") == cand_id:
                return meta.parent.name
        except (OSError, json.JSONDecodeError):
            pass
    return None


_KV_BLOCK = re.compile(r"^-{21}(Request|Response)-{21,22}$")
_KV_END = "-" * 50
_SECRET_FIELD = re.compile(r'("(?:token|blobFileTokens|blob_file_tokens|createUrl|create_url|[A-Za-z_]*[Tt]oken)"\s*:\s*)'
                           r'("(?:[^"\\]|\\.)*"|\[[^\]]*\])')
_URL_QUERY = re.compile(r"(https?://[^\s\"'?]+)\?[^\s\"']*")
_AUTH_HEADER = re.compile(r"(?i)^\s*(proxy-)?(authorization|cookie|set-cookie|x-[a-z-]*(token|key|auth)[a-z-]*)\s*:")
_AUTH_VALUE = re.compile(r"(?i)\b(bearer|basic)\s+\S+|\b(KGAT_)\w+")


def sanitize_kaggle_output(stdout: str, stderr: str) -> tuple[str, str, str]:
    """Strip what the kaggle CLI prints with VERBOSE=1 down to what is safe to log.

    The verbose client prints every HTTP request (its headers carry the API credentials) and every
    response. Request blocks and all headers are dropped; only the status line and body of the
    responses are kept (signed URLs lose their query string, token-like JSON fields are masked), so
    a 400 from CreateSubmission leaves Kaggle's reason in state/submissions.jsonl. stderr loses the
    `logging` DEBUG/INFO lines that VERBOSE turns on. Returns (stdout, stderr, last response)."""
    out, responses, block, cur = [], [], None, []
    for line in stdout.splitlines():
        m = _KV_BLOCK.match(line)
        if m:
            block, cur = m.group(1), []
            continue
        if block is not None:
            if line == _KV_END:
                if block == "Response" and cur:
                    status, rest = cur[0], cur[1:]
                    body = "\n".join(rest[rest.index("") + 1:]) if "" in rest else ""
                    responses.append(f"{status} {body}".strip())
                block = None
            else:
                cur.append(line)
            continue
        out.append(line)

    def clean(s: str) -> str:
        # Safety net in case the CLI changes its banners: drop credential headers, mask auth schemes/keys.
        s = "\n".join(l for l in s.splitlines() if not _AUTH_HEADER.match(l))
        s = _AUTH_VALUE.sub(lambda m: f"{m.group(1)} <redacted>" if m.group(1) else "<redacted>", s)
        return _URL_QUERY.sub(r"\1?<redacted>", _SECRET_FIELD.sub(r'\1"<redacted>"', s))

    err = "\n".join(l for l in stderr.splitlines() if not re.match(r"^(DEBUG|INFO):", l))
    return clean("\n".join(out)), clean(err), clean(responses[-1]) if responses else ""


def error_suspects(ours: list[dict]) -> dict[str, dict[str, str]]:
    """{task: {cand_id: Kaggle ref}} for the candidates of our ERROR submissions that are still
    suspects (see the module docstring for when they are released)."""
    cleared = {(t, i) for k in ours if k.get("status") == "complete" for t, i in k.get("ids", {}).items()}
    out: dict[str, dict[str, str]] = {}
    for k in ours:
        if k.get("status") != "error":
            continue
        ids = k.get("ids", {})
        if any(i in ledger.blocklist(t) for t, i in ids.items()):
            continue
        for t, i in ids.items():
            if i != "sample" and (t, i) not in cleared:
                out.setdefault(t, {})[i] = str(k.get("ref"))
    return out


def choose(exclude: dict[str, dict[str, str]] | None = None) -> dict:
    refs = ledger.load_refs()
    comps = {}
    for task in ledger.TASKS:
        best = ledger.verified_best(task, exclude=(exclude or {}).get(task, {}))
        if best is not None and task in refs:
            comps[task] = {"cand_id": best["cand_id"], "path": str(ledger.stored_path(task, best["cand_id"])),
                           "score": best["score"], "ratio": refs[task] / best["score"], "label": best["label"],
                           "version": model_version(task, best["cand_id"])}
        else:
            comps[task] = {"cand_id": "sample", "path": str(sample_path(task)), "score": refs.get(task),
                           "ratio": 1.0, "label": "official sample", "version": None}
    pred = math.exp(sum(math.log(c["ratio"]) for c in comps.values()) / len(comps))
    return {"components": comps, "predicted": pred}


def build_zip(choice: dict, name: str) -> Path:
    # Kaggle rejects (400) any upload not named exactly "submission.zip" for this competition,
    # so each attempt gets its own directory: submissions/<name>/submission.zip
    zpath = OUT / name / "submission.zip"
    zpath.parent.mkdir(parents=True, exist_ok=True)
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
    out = []
    for l in LOG.read_text().splitlines():
        try:
            out.append(json.loads(l))
        except json.JSONDecodeError:
            pass
    return out


# ---------------------------------------------------------------- Kaggle submission history

def kaggle_rows() -> list[dict] | None:
    """All rows of `kaggle competitions submissions` (fields: ref fileName date description status
    publicScore privateScore). None if the API call fails."""
    try:
        out = subprocess.run(["kaggle", "competitions", "submissions", COMP, "--format", "json",
                              "--page-size", "200"], capture_output=True, text=True, timeout=120).stdout
    except Exception:  # noqa: BLE001
        return None
    if "No submissions" in out:
        return []
    try:
        return json.loads(out[out.index("["):])
    except (ValueError, json.JSONDecodeError):
        return None


def kstatus(row: dict) -> str:
    s = str(row.get("status", "")).upper()
    if "COMPLETE" in s:
        return "complete"
    if "ERROR" in s or "FAIL" in s:
        return "error"
    return "pending"


def fnum(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


LB_GAP_BLOCK = 0.05  # public LB this much below our prediction = our verification is miscalibrated
PENDING_MAX_H = 6  # a submission "pending" longer than this is assumed stuck and no longer waits us


def age_h(date: str, now: dt.datetime) -> float:
    """Hours since a Kaggle submission date (UTC, e.g. '2026-11-05 05:23:15.123'); 0 if unparsable."""
    try:
        d = dt.datetime.fromisoformat(date.replace("Z", "+00:00").split(".")[0].replace("T", " "))
    except ValueError:
        return 0.0
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return (now - d).total_seconds() / 3600


def parse_message(desc: str) -> tuple[float | None, dict]:
    m = MSG_RE.search(desc or "")
    if not m:
        return None, {}
    ids = dict(tok.split("=", 1) for tok in m.group(2).split() if "=" in tok)
    return fnum(m.group(1)), {t: i for t, i in ids.items() if t in ledger.TASKS}


def sync_kaggle(rows: list[dict]) -> list[dict]:
    """Rewrite state/kaggle_submissions.json from Kaggle's list (ours only, oldest first) and block
    automatic submission when a new COMPLETE score falls far below its prediction (an ERROR is
    handled by error_suspects instead). Returns the records."""
    ours = []
    for r in rows:
        pred, ids = parse_message(str(r.get("description", "")))
        if not ids:
            continue
        ours.append({"ref": r.get("ref"), "date": str(r.get("date", "")), "status": kstatus(r),
                     "raw_status": str(r.get("status", "")), "public": fnum(r.get("publicScore")),
                     "private": fnum(r.get("privateScore")), "predicted": pred, "ids": ids,
                     "versions": {t: model_version(t, i) for t, i in ids.items()},
                     "fileName": r.get("fileName"), "description": r.get("description")})
    ours.sort(key=lambda x: x["date"])
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = KSUBS.with_suffix(".tmp")
    tmp.write_text(json.dumps(ours, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, KSUBS)

    seen = set(ERR_SEEN.read_text().split()) if ERR_SEEN.exists() else set()
    def bad(k: dict) -> str:
        if (k["status"] == "complete" and k["public"] is not None and k["predicted"]
                and k["public"] < k["predicted"] * (1 - LB_GAP_BLOCK)):
            return (f"public LB {k['public']} is more than {LB_GAP_BLOCK:.0%} below our prediction "
                    f"{k['predicted']}: local verification no longer predicts the LB")
        return ""
    new_err = [k for k in ours if bad(k) and str(k["ref"]) not in seen]
    if new_err:
        with open(BLOCK, "a") as fh:
            for k in new_err:
                fh.write(f"{ledger.now()} Kaggle submission {k['ref']} ({k['date']}) ids={k['ids']}: {bad(k)}. "
                         f"Find the cause, BLOCKLIST the bad candidate if needed, then delete this file to resume "
                         f"auto-submit.\n")
        with open(ERR_SEEN, "a") as fh:
            fh.writelines(f"{k['ref']}\n" for k in new_err)
    return ours


def load_ksubs() -> list[dict]:
    try:
        return json.loads(KSUBS.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def print_history(ours: list[dict]) -> None:
    print("date                 status    public    predicted  versions / ids")
    for k in ours:
        comp = " ".join(f"{t}={k['versions'].get(t) or k['ids'][t]}" for t in k["ids"])
        print(f"{k['date'][:19]:<20} {k['status']:<9} "
              + (f"{k['public']:<9.5f}" if k["public"] is not None else "-        ")
              + " " + (f"{k['predicted']:<10.5f}" if k["predicted"] is not None else "-         ")
              + f" {comp}")
    if not ours:
        print("(no harness submissions on Kaggle yet)")
    sus = error_suspects(ours)
    if sus:
        print(f"\nERROR suspects left out of auto-submit (task: cand_id -> Kaggle submission): {sus}\n"
              f"Put the culprit on its work/<task>/BLOCKLIST to release the other candidates of that submission.")
    if BLOCK.exists():
        print(f"\nAUTO-SUBMIT BLOCKED ({BLOCK.relative_to(ROOT)}):\n{BLOCK.read_text().strip()}")


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true", help="submit if policy allows")
    ap.add_argument("--dry-run", action="store_true", help="with --auto: evaluate the policy, do not submit")
    ap.add_argument("--sync", action="store_true", help="only refresh the Kaggle submission history and print it")
    a = ap.parse_args()

    if a.sync:
        rows = kaggle_rows()
        if rows is None:
            print("could not read Kaggle submissions; showing the last synced history", file=sys.stderr)
            print_history(load_ksubs())
            return 1
        print_history(sync_kaggle(rows))
        return 0

    if a.auto:
        rows = kaggle_rows()
        ours = sync_kaggle(rows) if rows is not None else load_ksubs()
    else:
        rows, ours = None, load_ksubs()
    suspects = error_suspects(ours)
    choice = choose(suspects)
    now = dt.datetime.now(dt.timezone.utc)
    ids = {t: c["cand_id"] for t, c in choice["components"].items()}
    versions = {t: c["version"] for t, c in choice["components"].items()}
    print(json.dumps({"predicted": choice["predicted"], "components": {
        t: {k: c[k] for k in ("cand_id", "version", "score", "ratio", "label")}
        for t, c in choice["components"].items()}, "excluded_error_suspects": suspects}, indent=2))

    if not a.auto:
        return 0

    prev = [r for r in past() if r.get("submitted")]
    prev_best = max((r["predicted"] for r in prev), default=0.0)
    public_best = max((k["public"] for k in ours if k["status"] == "complete" and k["public"] is not None),
                      default=0.0)
    baseline = max(prev_best, public_best)
    today = now.date().isoformat()
    ours_today = sum(1 for r in prev if r["ts"].startswith(today))
    kag_today = None if rows is None else sum(1 for r in rows if str(r.get("date", "")).startswith(today))
    used = max(ours_today, kag_today if kag_today is not None else ours_today)
    done_combos = [r.get("ids") for r in prev] + [k["ids"] for k in ours]
    pending = [k for k in ours if k["status"] == "pending" and age_h(k["date"], now) < PENDING_MAX_H]
    rec = {"ts": now.isoformat(timespec="seconds"), "predicted": choice["predicted"], "ids": ids,
           "versions": versions, "prev_best_predicted": prev_best, "best_public": public_best,
           "used_today": used, "submitted": False, "excluded_error_suspects": suspects}

    if rows is None:
        rec["reason"] = "could not read Kaggle submissions"
    elif BLOCK.exists():
        rec["reason"] = f"blocked by {BLOCK.relative_to(ROOT)} (the Kaggle LB disagreed with our verification; human review)"
    elif pending:
        rec["reason"] = f"previous submission {pending[-1]['ref']} still being scored on Kaggle"
    elif used >= MAX_PER_DAY:
        rec["reason"] = f"daily cap {MAX_PER_DAY} reached"
    elif all(v == "sample" for v in ids.values()):
        rec["reason"] = "nothing verified yet"
    elif ids in done_combos:
        rec["reason"] = "this combination was already submitted"
    elif choice["predicted"] <= baseline * (1 + MIN_GAIN):
        rec["reason"] = (f"no improvement over max(previous prediction {prev_best:.5f}, "
                         f"best public LB {public_best:.5f})")
    elif a.dry_run:
        rec["reason"] = "dry-run: would submit"
        print(json.dumps({k: rec[k] for k in ("reason", "predicted", "prev_best_predicted", "best_public",
                                               "used_today")}))
        return 0
    else:
        name = now.strftime("%Y%m%dT%H%M%SZ")
        zpath = build_zip(choice, name)
        msg = f"vqcc-harness pred={choice['predicted']:.5f} " + " ".join(f"{t}={i}" for t, i in ids.items())
        # VERBOSE=1 makes the CLI print Kaggle's response body (the reason of a 400); the raw output
        # also holds credentials, so only the sanitized form is logged.
        r = subprocess.run(["kaggle", "competitions", "submit", COMP, "-f", str(zpath), "-m", msg],
                           capture_output=True, text=True, timeout=3600, env={**os.environ, "VERBOSE": "1"})
        out, err, resp = sanitize_kaggle_output(r.stdout, r.stderr)
        rec.update({"zip": str(zpath), "message": msg, "returncode": r.returncode,
                    "stdout": out[-2000:], "stderr": err[-2000:], "kaggle_response": resp[-2000:],
                    "submitted": r.returncode == 0})
        rec["reason"] = "submitted" if r.returncode == 0 else (
            "kaggle submit failed" + (f": {' '.join(resp.split())[:300]}" if resp else ""))
    log(rec)
    print(json.dumps({k: rec[k] for k in ("submitted", "reason", "predicted", "prev_best_predicted",
                                           "best_public", "used_today", "excluded_error_suspects")}))
    return 1 if rec.get("returncode") not in (None, 0) else 0


if __name__ == "__main__":
    sys.exit(main())
