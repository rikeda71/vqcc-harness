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


if __name__ == "__main__":
    print("\n".join(lines()))
    sys.exit(0)
