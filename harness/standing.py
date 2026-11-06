"""bin/vqcc-standing: where we stand on the public LB and how far the medal lines / target are.

Deterministic facts only (no judgement): downloads the full public leaderboard with the kaggle CLI
(which authenticates internally; the token is never read here), finds our rank, the Kaggle medal
cutoffs, and turns each gap into "multiply the product of the 3 task ratios by x" and "one task
alone would need ratio y". Used by the strategist session (skill planning-vqcc-strategy) and the
morning report.

Writes state/standing.json (latest), appends state/standing_history.jsonl, keeps the CSV under
state/leaderboard/. With --no-download (or when the download fails) the newest cached CSV is used.

Optional input: work/strategy/public_best.json = {"invsqrt": 21.25, "dqi": 65.2, ...} (best
public per-task ratio the strategist found); shown next to ours when present.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402

ROOT = ledger.ROOT
STATE = ROOT / "state"
LBDIR = STATE / "leaderboard"
COMP = os.environ.get("VQCC_COMPETITION", "google-verifiable-quantum-circuit-compilation")
TEAM = os.environ.get("VQCC_TEAM_NAME", "ikechan")
PUBLIC_BEST = ROOT / "work" / "strategy" / "public_best.json"
TARGET = os.environ.get("VQCC_TARGET", "")  # "gold" | "silver" | "bronze" | a score; default: next medal up


def download() -> tuple[Path | None, str]:
    LBDIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        try:
            p = subprocess.run(["kaggle", "competitions", "leaderboard", COMP, "-d", "-p", td, "-q"],
                               capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, f"download failed: {e!r}"
        if p.returncode != 0:
            return None, f"download failed: kaggle exit {p.returncode}: {(p.stderr or p.stdout).strip()[:200]}"
        zips = list(Path(td).glob("*.zip"))
        csvs = list(Path(td).glob("*.csv"))
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = LBDIR / f"publicleaderboard-{stamp}.csv"
        if zips:
            with zipfile.ZipFile(zips[0]) as z:
                names = [n for n in z.namelist() if n.endswith(".csv")]
                if not names:
                    return None, "download failed: zip without csv"
                out.write_bytes(z.read(names[0]))
        elif csvs:
            out.write_bytes(csvs[0].read_bytes())
        else:
            return None, "download failed: no file"
    # keep the newest 30 snapshots
    snaps = sorted(LBDIR.glob("publicleaderboard-*.csv"))
    for old in snaps[:-30]:
        old.unlink()
    return out, "downloaded"


def latest_cached() -> Path | None:
    snaps = sorted(LBDIR.glob("publicleaderboard-*.csv"))
    return snaps[-1] if snaps else None


def medal_counts(n: int) -> dict[str, int]:
    """Kaggle progression-system medal cutoffs (number of teams that get each medal)."""
    if n < 100:
        return {"gold": max(1, round(n * 0.10)), "silver": max(1, round(n * 0.20)), "bronze": max(1, round(n * 0.40))}
    if n < 250:
        return {"gold": 10, "silver": round(n * 0.20), "bronze": round(n * 0.40)}
    if n < 1000:
        return {"gold": 10 + int(n * 0.002), "silver": 50, "bronze": 100}
    return {"gold": 10 + int(n * 0.002), "silver": int(n * 0.05), "bronze": int(n * 0.10)}


def deadline() -> str | None:
    try:
        p = subprocess.run(["kaggle", "competitions", "list", "-s", COMP, "--format", "json"],
                           capture_output=True, text=True, timeout=90)
        rows = json.loads(p.stdout[p.stdout.index("["):])
        mine = [r for r in rows if str(r.get("ref", "")).rstrip("/").endswith("/" + COMP)]
        return mine[0]["deadline"] if mine else None
    except Exception:  # noqa: BLE001 - informational only
        return None


def progress(refs: dict[str, float]) -> dict[str, dict[str, float | None]]:
    """Verified best ratio per task as of now, 24h ago and 72h ago (full/remote runs, BLOCKLIST applied,
    every run of a candidate must PASS) - the improvement velocity the strategist reasons about."""
    now = dt.datetime.now(dt.timezone.utc)
    recs = [r for r in ledger.records() if r.get("tier") in ("full", "remote") and r.get("status") in ("PASS", "FAIL")]
    out = {}
    for t in ledger.TASKS:
        blocked = ledger.blocklist(t)
        row = {}
        for name, hours in (("now", 0), ("24h_ago", 24), ("72h_ago", 72)):
            cut = now - dt.timedelta(hours=hours)
            by: dict[str, list] = {}
            for r in recs:
                if r.get("task") == t and r["cand_id"] not in blocked and dt.datetime.fromisoformat(r["ts"]) <= cut:
                    by.setdefault(r["cand_id"], []).append(r)
            ok = [max(x["score"] for x in runs) for runs in by.values() if all(x["status"] == "PASS" for x in runs)]
            row[name] = (refs[t] / min(ok)) if (ok and t in refs) else None
        out[t] = row
    return out


def gm(xs):
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-download", action="store_true", help="use the newest cached leaderboard CSV")
    ap.add_argument("--json", action="store_true", help="print state/standing.json instead of the table")
    ap.add_argument("--target", default=TARGET, help="gold|silver|bronze|<score> (default: next medal above us)")
    a = ap.parse_args()

    src, how = (None, "--no-download") if a.no_download else download()
    if src is None:
        note = how
        src = latest_cached()
        how = f"cached ({note})" if src else note
    if src is None:
        print(f"no leaderboard available: {how}")
        return 1
    rows = list(csv.DictReader(io.StringIO(src.read_text(encoding="utf-8-sig"))))
    rows = [r for r in rows if r.get("Score") not in (None, "")]
    scores = sorted((float(r["Score"]) for r in rows), reverse=True)
    n = len(scores)

    # our position: the LB row of our team if present, else our best public score from our submissions
    ks = json.loads((STATE / "kaggle_submissions.json").read_text()) if (STATE / "kaggle_submissions.json").exists() else []
    pubs = [k["public"] for k in ks if k.get("public") is not None]
    mine = [r for r in rows if r.get("TeamName", "").strip().lower() == TEAM.lower()]
    our_lb = float(mine[0]["Score"]) if mine else (max(pubs) if pubs else None)
    our_rank = (1 + sum(s > our_lb for s in scores)) if our_lb is not None else None

    # verified (submittable) ratios per task and the predicted LB they give
    refs = ledger.load_refs()
    ratios = {}
    for t in ledger.TASKS:
        b = ledger.verified_best(t)
        ratios[t] = refs[t] / b["score"] if (b and t in refs) else 1.0
    pred = gm(list(ratios.values()))
    base = max(pred, our_lb or 0.0)  # what we can already submit vs what is already on the LB

    mc = medal_counts(n)
    lines = {}
    for m, k in mc.items():
        if k <= n:
            cut = scores[k - 1]
            lines[m] = {"rank": k, "score": cut, "tied_at_cut": sum(abs(s - cut) < 5e-6 for s in scores),
                        "score_to_be_safe": cut + 1e-5}
    marks = {f"rank{r}": scores[r - 1] for r in (1, 3, 5, 10, 50, 100) if r <= n}

    def gap(score):
        mult = (score / pred) ** 3 if pred > 0 else None  # multiplier on the product of the 3 ratios
        return {"target_score": score, "product_multiplier": mult,
                "each_task_equal": mult ** (1 / 3) if mult else None,
                "one_task_alone_ratio": {t: ratios[t] * mult for t in ledger.TASKS} if mult else None}

    targets = {m: gap(v["score_to_be_safe"]) for m, v in lines.items()}
    targets.update({k: gap(v) for k, v in marks.items()})
    tgt = a.target.strip().lower()
    if tgt in lines:
        primary = tgt
    elif tgt:
        try:
            targets["custom"] = gap(float(tgt)); primary = "custom"
        except ValueError:
            primary = ""
    else:
        primary = ""
    if not primary:  # next medal line we are not yet above
        primary = next((m for m in ("bronze", "silver", "gold") if m in lines and base < lines[m]["score_to_be_safe"]), "")
        if not primary:
            primary = "rank1"

    public_best = json.loads(PUBLIC_BEST.read_text()) if PUBLIC_BEST.exists() else {}
    dl = None if a.no_download else deadline()
    if not dl and (STATE / "standing.json").exists():  # offline / CLI failure: reuse the last known deadline
        try:
            dl = json.loads((STATE / "standing.json").read_text()).get("deadline_utc")
        except (ValueError, OSError):
            dl = None
    days_left = None
    if dl:
        days_left = (dt.datetime.fromisoformat(dl).replace(tzinfo=dt.timezone.utc)
                     - dt.datetime.now(dt.timezone.utc)).total_seconds() / 86400
    prog = progress(refs)
    out = {
        "ts": ledger.now(), "leaderboard_csv": str(src.relative_to(ROOT)), "source": how, "teams": n,
        "team_name": TEAM, "our_public_lb": our_lb, "our_rank": our_rank,
        "our_percentile": (our_rank / n * 100) if our_rank else None,
        "deadline_utc": dl, "days_left": days_left, "progress": prog,
        "predicted_lb_verified": pred, "verified_ratios": ratios, "public_best_ratios": public_best,
        "medal_lines": lines, "marks": marks, "targets": targets, "primary_target": primary,
        "teams_at_or_above": {str(x): sum(s >= x for s in scores) for x in (24.7, 24.95, 25, 26, 27, 28)},
    }
    (STATE / "standing.json").write_text(json.dumps(out, indent=1) + "\n")
    with (STATE / "standing_history.jsonl").open("a") as f:
        f.write(json.dumps({k: out[k] for k in ("ts", "teams", "our_public_lb", "our_rank", "predicted_lb_verified",
                                                 "verified_ratios")} | {"lines": {m: v["score"] for m, v in lines.items()}}) + "\n")
    if a.json:
        print(json.dumps(out, indent=1))
        return 0

    print(f"leaderboard: {out['leaderboard_csv']} ({how}), {n} teams")
    print(f"us ({TEAM}): public LB {our_lb if our_lb is not None else '-'}  rank {our_rank or '-'}"
          + (f" (top {out['our_percentile']:.1f}%)" if our_rank else "")
          + f"  | predicted LB of verified best: {pred:.5f}")
    print("verified ratios: " + "  ".join(f"{t} {ratios[t]:.4f}" for t in ledger.TASKS)
          + ("" if not public_best else "   | public best: " + "  ".join(f"{t} {public_best.get(t, '-')}" for t in ledger.TASKS)))
    print("progress (verified ratio now / 24h ago / 72h ago): " + "  ".join(
        f"{t} {'/'.join('-' if v is None else f'{v:.3f}' for v in prog[t].values())}" for t in ledger.TASKS))
    if dl:
        print(f"deadline: {dl} UTC ({days_left:.1f} days left)")
    print()
    print(f"{'line':8} {'rank':>5} {'score':>9} {'tied':>5} {'x product':>10} {'x each':>7}  one task alone needs ratio")
    for name in [m for m in ("bronze", "silver", "gold") if m in lines] + list(marks) + (["custom"] if "custom" in targets else []):
        g = targets[name]
        rank = (lines[name]["rank"] if name in lines else int(name[4:]) if name.startswith("rank")
                else sum(sc > g["target_score"] for sc in scores) + 1)
        tied = lines[name]["tied_at_cut"] if name in lines else ""
        flag = " <= target" if name == primary else ""
        alone = "  ".join(f"{t} {g['one_task_alone_ratio'][t]:.3f}" for t in ledger.TASKS)
        print(f"{name:8} {rank:>5} {g['target_score']:>9.5f} {tied!s:>5} {g['product_multiplier']:>10.4f} "
              f"{g['each_task_equal']:>7.4f}  {alone}{flag}")
    print()
    print("x product = factor the product of the 3 task ratios must grow by (LB = cube root of the product); "
          "x each = the same split equally over the 3 tasks. Medal lines use Kaggle's cutoffs for this team count; "
          "'tied' = teams on exactly the cutoff score (ties are broken by submission time, so beat it, do not match it).")
    print("teams at or above: " + "  ".join(f">={k}: {v}" for k, v in out["teams_at_or_above"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
