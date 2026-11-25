"""Per-task worker state: working or parked, and why. Used by `bin/vqcc-schedule status` and the night report.

A task is parked when work/<task>/IDLE exists and DIRECTIVE.md is not newer (same rule as vqcc_parked in
config.sh). The reason is the head of IDLE (the worker writes there what it finished and what it is waiting for);
for a working task, the first bullet of NOTES.md「現在地」. Remote jobs still queued/running are listed per task.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = ("invsqrt", "dqi", "dfthcsos")
WIDTH = 220  # characters kept per line: enough for one IDLE/NOTES bullet, short enough for a terminal


def _hm(p: Path) -> str:
    return dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")


def _cut(s: str) -> str:
    s = s.rstrip()
    return s if len(s) <= WIDTH else s[:WIDTH - 1] + "…"


def _directive_title(d: Path) -> str:
    first = d.read_text(errors="replace").splitlines()[:1]
    return first[0].lstrip("# ").strip() if first else ""


def _notes_now(n: Path) -> list[str]:
    """First bullet under the first「## 現在地」heading of NOTES.md."""
    if not n.exists():
        return []
    lines = n.read_text(errors="replace").splitlines()
    for i, l in enumerate(lines):
        if l.startswith("## 現在地"):
            for b in lines[i + 1:]:
                if b.startswith("## "):
                    break
                if b.startswith("- "):
                    return [b]
            break
    return []


def _remote_jobs() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {t: [] for t in TASKS}
    f = ROOT / "state" / "remote" / "jobs.jsonl"
    if not f.exists():
        return out
    jobs: dict[str, dict] = {}
    for line in f.read_text().splitlines():
        try:
            j = json.loads(line)
            jobs[j["job_id"]] = j
        except (json.JSONDecodeError, KeyError):
            pass
    for j in jobs.values():
        if j.get("state") in ("queued", "pushed", "running") and j.get("task") in out:
            out[j["task"]].append(f"{j['job_id']} ({j['state']}{', salt ' + j['salt'] if j.get('salt') else ''})")
    return out


def lines(idle_lines: int = 6) -> list[str]:
    remote = _remote_jobs()
    out: list[str] = []
    for t in TASKS:
        w = ROOT / "work" / t
        idle, d = w / "IDLE", w / "DIRECTIVE.md"
        parked = idle.exists() and not (d.exists() and d.stat().st_mtime > idle.stat().st_mtime)
        dtxt = f"directive {_hm(d)}" if d.exists() else "no directive"
        if parked:
            out.append(f"{t:<9}: PARKED since {_hm(idle)} ({dtxt}; resumes when the strategist updates DIRECTIVE.md)")
            body = [l for l in idle.read_text(errors="replace").splitlines() if l.strip()][:idle_lines]
            out += [f"           | {_cut(l)}" for l in body]
        else:
            note = " (IDLE older than the directive: unparked)" if idle.exists() else ""
            out.append(f"{t:<9}: WORKING ({dtxt}{note})")
            if d.exists():
                out.append(f"           | directive: {_cut(_directive_title(d))}")
            out += [f"           | 現在地: {_cut(l[2:])}" for l in _notes_now(w / "NOTES.md")]
        for r in remote[t]:
            out.append(f"           | remote: {r}")
    return out


def short() -> str:
    """One-line form for the report summary, e.g. 'invsqrt WORKING / dqi PARKED 11-08 18:58 / ...'."""
    parts = []
    for t in TASKS:
        w = ROOT / "work" / t
        idle, d = w / "IDLE", w / "DIRECTIVE.md"
        parked = idle.exists() and not (d.exists() and d.stat().st_mtime > idle.stat().st_mtime)
        parts.append(f"{t} PARKED {_hm(idle)}" if parked else f"{t} WORKING")
    return " / ".join(parts)


def wake_check(task: str) -> str | None:
    """For a parked task: a one-line summary of the full/remote results recorded since it parked, once none
    of its remote jobs is still queued/running (a pair of salts wakes the worker once, not twice); else None."""
    w = ROOT / "work" / task
    idle, d = w / "IDLE", w / "DIRECTIVE.md"
    if not idle.exists() or (d.exists() and d.stat().st_mtime > idle.stat().st_mtime) or _remote_jobs()[task]:
        return None
    sys.path.insert(0, str(ROOT / "harness"))
    import ledger  # noqa: E402
    since = dt.datetime.fromtimestamp(idle.stat().st_mtime).astimezone()
    new = [r for r in ledger.records() if r.get("task") == task and r.get("tier") in ("full", "remote")
           and dt.datetime.fromisoformat(r["ts"]) > since]
    if not new:
        return None
    return "; ".join(f"{r['cand_id']} {r['tier']}{'/' + r['salt'] if r.get('salt') else ''} {r['status']}"
                     + (f" ratio {r['ratio']:.5f}" if r.get("ratio") else "") for r in new[-6:])


def _ledger():
    sys.path.insert(0, str(ROOT / "harness"))
    import ledger  # noqa: E402
    return ledger


def plateau_min(task: str, now: dt.datetime | None = None) -> int | None:
    """Minutes since the task's current verified best got its first full/remote PASS (how long the verified
    best has not improved); None without a verified best. The Stop hook starts the auditor from this."""
    ledger = _ledger()
    best = ledger.verified_best(task)
    if not best:
        return None
    first = min(dt.datetime.fromisoformat(r["ts"]) for r in ledger.records()
                if r.get("task") == task and r.get("cand_id") == best["cand_id"]
                and r.get("tier") in ("full", "remote") and r.get("status") == "PASS")
    now = now or dt.datetime.now().astimezone()
    return max(0, int((now - first).total_seconds() // 60))


def new_results(task: str, since: float, limit: int = 6) -> str | None:
    """One line on the task's remote results recorded after the epoch time `since`, each with its ratio against
    the verified best; None when there are none. Remote jobs land in the ledger asynchronously (supervisor
    poll), so the worker would not see them otherwise; quick/full runs print their result to the caller."""
    ledger = _ledger()
    t0 = dt.datetime.fromtimestamp(since).astimezone()
    new = [r for r in ledger.records() if r.get("task") == task and r.get("tier") == "remote"
           and dt.datetime.fromisoformat(r["ts"]) > t0]
    if not new:
        return None
    best = ledger.verified_best(task)
    ref = ledger.load_refs().get(task)
    bratio = ref / best["score"] if best and ref and best["score"] else None
    parts = []
    for r in new[-limit:]:
        p = f"{r['cand_id']} {r['tier']}{'/' + r['salt'] if r.get('salt') else ''} {r['status']}"
        if r.get("ratio"):
            p += f" ratio {r['ratio']:.5f}"
            if bratio:
                p += f" (best 比 {r['ratio'] / bratio - 1:+.3%})"
        if r.get("label"):
            p += f" [{_cut(r['label'])[:60]}]"
        parts.append(p)
    more = f" ほか {len(new) - limit} 件" if len(new) > limit else ""
    best_txt = f"verified best {best['cand_id']} ratio {bratio:.5f}" if bratio else "verified best なし"
    return "; ".join(parts) + more + f" — {best_txt}"


if __name__ == "__main__":
    # workers.py                  -> the worker-state lines (bin/vqcc-schedule status)
    # workers.py wake-check TASK  -> exit 0 and print the new results when a parked TASK should be woken
    # workers.py plateau TASK     -> minutes since TASK's verified best first PASSed (exit 1 without one)
    # workers.py new-results TASK EPOCH -> TASK's remote results since EPOCH (exit 1 when none)
    if len(sys.argv) == 3 and sys.argv[1] == "plateau":
        m = plateau_min(sys.argv[2])
        if m is not None:
            print(m)
        sys.exit(0 if m is not None else 1)
    if len(sys.argv) == 4 and sys.argv[1] == "new-results":
        msg = new_results(sys.argv[2], float(sys.argv[3]))
        if msg:
            print(msg)
        sys.exit(0 if msg else 1)
    if len(sys.argv) == 3 and sys.argv[1] == "wake-check":
        msg = wake_check(sys.argv[2])
        if msg:
            print(msg)
        sys.exit(0 if msg else 1)
    print("\n".join(lines()))
    sys.exit(0)
