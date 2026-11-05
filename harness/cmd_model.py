"""bin/vqcc-model: versioned store of adopted circuits and the code that generated them.

Layout (gitignored, local only — the competition forbids private code sharing):
  models/<task>/v<N>/MODEL.md     metadata: cand_id, sha256, ledger status at save time, note, regenerate cmd
  models/<task>/v<N>/candidate/   exact copy of the candidate (.kmb file or dfthcsos directory)
  models/<task>/v<N>/src/         snapshot of the generator source (if given)
  models/<task>/current           symlink to the version holding the verified best (= what the
                                  supervisor submits next); refreshed by `save`, `list` and `sync`

Commands:
  vqcc-model save <task> <candidate> [--src DIR] [--cmd "regenerate command"] [--note TEXT]
  vqcc-model list [<task>]     versions with verified ratio and Kaggle public LB (if submitted)
  vqcc-model sync          (supervisor) give the verified best of every task a version if it has none
Versions are append-only: never edit or delete an existing v<N>; save a new one instead.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402

ROOT = ledger.ROOT
MODELS = ROOT / "models"
SKIP_DIRS = {".venv", "__pycache__", ".git", "out", ".ipynb_checkpoints"}
MAX_SRC_FILE = 100 * 1024 * 1024  # skip single source files over 100 MB (data dumps, not code)


def versions(task: str) -> list[tuple[int, Path, dict]]:
    out = []
    d = MODELS / task
    if not d.is_dir():
        return out
    for p in d.iterdir():
        m = re.fullmatch(r"v(\d+)", p.name)
        if m and p.is_dir() and (p / "meta.json").exists():
            out.append((int(m.group(1)), p, json.loads((p / "meta.json").read_text())))
    return sorted(out)


def ledger_summary(task: str, cand_id: str) -> dict:
    runs = [r for r in ledger.records() if r.get("task") == task and r.get("cand_id") == cand_id]
    s: dict = {}
    for r in runs:
        key = f"{r.get('tier')}:{r.get('status')}"
        s[key] = s.get(key, 0) + 1
    ref = ledger.load_refs().get(task)
    ratios = [ref / r["score"] for r in runs if ref and r.get("score") and r.get("tier") in ("full", "remote")
              and r.get("status") == "PASS"]
    salts = sorted({r.get("salt", "") for r in runs if r.get("tier") in ("full", "remote") and r.get("status") == "PASS"})
    return {"runs": s, "verified_ratio_worst": min(ratios) if ratios else None,
            "verified_salts": salts}


def last_submitted() -> dict:
    f = ROOT / "state" / "submissions.jsonl"
    if not f.exists():
        return {}
    ids = {}
    for line in f.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("submitted"):
            ids = r.get("ids", {})
    return ids


def kaggle_results(task: str, cand_id: str) -> str:
    """Kaggle outcome of submissions that contained this candidate (state/kaggle_submissions.json,
    refreshed by `bin/vqcc-submit --sync` / --auto): best public LB, or pending / ERROR."""
    try:
        ks = json.loads((ROOT / "state" / "kaggle_submissions.json").read_text())
    except (OSError, json.JSONDecodeError):
        return "-"
    mine = [k for k in ks if k.get("ids", {}).get(task) == cand_id]
    if not mine:
        return "-"
    pub = [k["public"] for k in mine if k.get("status") == "complete" and k.get("public") is not None]
    if any(k.get("status") == "error" for k in mine):
        return "ERROR"
    if pub:
        return f"{max(pub):.5f}"
    return "pending"


def copy_src(src: Path, dest: Path) -> list[str]:
    skipped = []
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.is_file():
            if p.stat().st_size > MAX_SRC_FILE:
                skipped.append(str(rel))
                continue
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest / rel)
    return skipped


def save(task: str, cand: Path, src: Path | None, cmd: str, note: str, auto: bool = False) -> int:
    cand = cand.resolve()
    if not cand.exists():
        print(f"ERROR: candidate not found: {cand}", file=sys.stderr)
        return 2
    sha = ledger.content_sha256(cand)
    cand_id = sha[:16]
    for n, p, meta in versions(task):
        if meta["cand_id"] == cand_id:
            print(f"{task} v{n} already holds {cand_id}: {p.relative_to(ROOT)}")
            refresh_current(task)
            return 0
    if cand_id in ledger.blocklist(task):
        print(f"ERROR: {cand_id} is in work/{task}/BLOCKLIST", file=sys.stderr)
        return 2
    if src is not None and not src.is_dir():
        print(f"ERROR: --src must be a directory: {src}", file=sys.stderr)
        return 2
    if src is not None:
        src = src.resolve()
        try:
            src = src.relative_to(ROOT)
        except ValueError:
            pass
    n = (versions(task)[-1][0] + 1) if versions(task) else 1
    dest = MODELS / task / f"v{n}"
    tmp = MODELS / task / f".tmp-v{n}"
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "candidate").mkdir(parents=True)
    if cand.is_dir():
        shutil.copytree(cand, tmp / "candidate" / cand.name)
    else:
        shutil.copy2(cand, tmp / "candidate" / cand.name)
    skipped = []
    if src is not None:
        (tmp / "src").mkdir()
        skipped = copy_src((ROOT / src).resolve(), tmp / "src")
    summ = ledger_summary(task, cand_id)
    meta = {"task": task, "version": n, "created": ledger.now(), "cand_id": cand_id, "sha256": sha,
            "candidate": cand.name, "src_from": str(src) if src else None, "regenerate": cmd or None,
            "note": note, "auto": auto, "ledger_at_save": summ, "src_skipped_large": skipped,
            "worker": os.environ.get("VQCC_WORKER", "")}
    (tmp / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    vr = summ["verified_ratio_worst"]
    md = [f"# {task} v{n}", "",
          f"- created: {meta['created']}" + (" (auto: supervisor sync, no source given)" if auto else ""),
          f"- cand_id: `{cand_id}` (sha256 `{sha}`)",
          f"- candidate: `candidate/{cand.name}`",
          f"- source: " + (f"`src/` (copied from `{src}`)" if src else "(none)"),
          f"- regenerate: " + (f"`{cmd}`" if cmd else "(not given)"),
          f"- ledger at save: {summ['runs']}; verified worst ratio "
          + (f"{vr:.6g}" if vr else "-") + f"; salts {summ['verified_salts']}",
          "", "## note", "", note or "-", ""]
    if skipped:
        md += ["## skipped large source files", ""] + [f"- {s}" for s in skipped] + [""]
    (tmp / "MODEL.md").write_text("\n".join(md))
    os.replace(tmp, dest)
    print(f"saved {task} v{n}: {dest.relative_to(ROOT)} (cand {cand_id})")
    refresh_current(task)
    return 0


def refresh_current(task: str) -> str | None:
    vb = ledger.verified_best(task)
    link = MODELS / task / "current"
    target = None
    if vb:
        for n, p, meta in versions(task):
            if meta["cand_id"] == vb["cand_id"]:
                target = p.name
    if link.is_symlink() or link.exists():
        if target and os.readlink(link) == target:
            return target
        link.unlink()
    if target:
        link.symlink_to(target)
    return target


def sync() -> int:
    for task in ledger.TASKS:
        vb = ledger.verified_best(task)
        try:
            if vb and not any(m["cand_id"] == vb["cand_id"] for _, _, m in versions(task)):
                save(task, ledger.stored_path(task, vb["cand_id"]), None, "",
                     f"verified best saved automatically (label: {vb['label']})", auto=True)
            refresh_current(task)
        except Exception as e:  # one broken task must not stop the others
            print(f"sync {task}: {e!r}", file=sys.stderr)
    return 0


def list_(tasks: list[str]) -> int:
    refs = ledger.load_refs()
    sub = last_submitted()
    for task in tasks:
        cur = refresh_current(task)
        vs = versions(task)
        print(f"== {task}  (current = {cur or '-'}; marks: * current/next submission, S last submitted; "
              "LB = best Kaggle public score of a submission containing it)")
        if not vs:
            print("   (no versions)")
        for n, p, meta in vs:
            summ = ledger_summary(task, meta["cand_id"])
            vr = summ["verified_ratio_worst"]
            mark = ("*" if p.name == cur else " ") + ("S" if sub.get(task) == meta["cand_id"] else " ")
            blocked = " BLOCKED" if meta["cand_id"] in ledger.blocklist(task) else ""
            print(f"{mark} v{n:<3} {meta['created'][:16]} {meta['cand_id']} ratio "
                  + (f"{vr:<10.6g}" if vr else "-         ")
                  + f" salts {len(summ['verified_salts'])} src {'y' if meta.get('src_from') else 'n'}"
                  + f" LB {kaggle_results(task, meta['cand_id']):<8}"
                  + f"{blocked}  {meta.get('note', '')[:60]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="vqcc-model")
    sp = ap.add_subparsers(dest="action", required=True)
    s = sp.add_parser("save")
    s.add_argument("task", choices=ledger.TASKS)
    s.add_argument("candidate", type=Path)
    s.add_argument("--src", type=Path, help="generator source directory to snapshot (e.g. work/<task>/src)")
    s.add_argument("--cmd", default="", help="command that regenerates the candidate from src")
    s.add_argument("--note", default="", help="what changed vs the previous version")
    l = sp.add_parser("list")
    l.add_argument("task", nargs="?", choices=ledger.TASKS)
    sp.add_parser("sync")
    a = ap.parse_args()
    if a.action == "save":
        return save(a.task, a.candidate, a.src, a.cmd, a.note)
    if a.action == "list":
        return list_([a.task] if a.task else list(ledger.TASKS))
    return sync()


if __name__ == "__main__":
    sys.exit(main())
