"""Morning report: what the night produced. Writes reports/<night_id>.md and prints a short summary."""
from __future__ import annotations

import datetime as dt
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402

ROOT = ledger.ROOT
STATE = ROOT / "state"


def night_start() -> tuple[str, dt.datetime]:
    nid = (STATE / "night_id").read_text().strip() if (STATE / "night_id").exists() else ""
    try:
        start = dt.datetime.strptime(nid, "%Y%m%d-%H%M").astimezone()
    except ValueError:
        start = dt.datetime.now().astimezone() - dt.timedelta(hours=8)
    return nid or start.strftime("%Y%m%d-%H%M"), start


def main() -> int:
    nid, start = night_start()
    refs = ledger.load_refs()
    recs = [r for r in ledger.records() if dt.datetime.fromisoformat(r["ts"]) >= start]
    lines = [f"# VQCC night report {nid}", ""]
    lines.append(f"- verification runs tonight: {len(recs)} "
                 f"(PASS {sum(r['status']=='PASS' for r in recs)}, FAIL {sum(r['status']=='FAIL' for r in recs)}, "
                 f"ERROR {sum(r['status']=='ERROR' for r in recs)})")
    ratios = {}
    lines += ["", "## Verified best per task", "", "| task | cand | score | ratio | label |", "|---|---|---|---|---|"]
    for t in ledger.TASKS:
        b = ledger.verified_best(t)
        if b and t in refs:
            ratios[t] = refs[t] / b["score"]
            lines.append(f"| {t} | {b['cand_id']} | {b['score']:.6g} | {ratios[t]:.4f} | {b['label'][:60]} |")
        else:
            ratios[t] = 1.0
            lines.append(f"| {t} | sample | - | 1.0000 | |")
    pred = math.exp(sum(math.log(v) for v in ratios.values()) / 3)
    lines += ["", f"**predicted LB (geometric mean): {pred:.4f}**", ""]

    lines += ["## Tonight's best quick/full results per task", ""]
    for t in ledger.TASKS:
        tr = [r for r in recs if r["task"] == t and r["status"] == "PASS" and r.get("score")]
        if tr:
            b = min(tr, key=lambda r: r["score"])
            lines.append(f"- {t}: best {b['tier']} score {b['score']:.6g} ratio {b.get('ratio') or 0:.4f} ({b['cand_id']}, {b.get('label','')[:60]})")
        else:
            lines.append(f"- {t}: no PASS tonight")

    subs = [json.loads(l) for l in (STATE / "submissions.jsonl").read_text().splitlines()
            if l.strip()] if (STATE / "submissions.jsonl").exists() else []
    subs = [s for s in subs if dt.datetime.fromisoformat(s["ts"]) >= start]
    lines += ["", "## Submission attempts", ""]
    lines += [f"- {s['ts']} submitted={s['submitted']} pred={s['predicted']:.4f} ({s.get('reason','')})" for s in subs] or ["- none"]
    ks = json.loads((STATE / "kaggle_submissions.json").read_text()) if (STATE / "kaggle_submissions.json").exists() else []
    lines += ["", "## Kaggle results of harness submissions (state/kaggle_submissions.json)", "",
              "| date | status | public LB | predicted | invsqrt | dqi | dfthcsos |", "|---|---|---|---|---|---|---|"]
    for k in ks[-15:]:
        comp = [f"{k['versions'].get(t) or ''} {k['ids'].get(t, '')}".strip() for t in ledger.TASKS]
        lines.append(f"| {k['date'][:16]} | {k['status']} | {k['public'] if k['public'] is not None else '-'} | "
                     f"{k['predicted'] if k['predicted'] is not None else '-'} | " + " | ".join(comp) + " |")
    if not ks:
        lines.append("| (none) | | | | | | |")
    if (STATE / "SUBMIT_BLOCKED").exists():
        lines += ["", "**AUTO-SUBMIT BLOCKED** (state/SUBMIT_BLOCKED; delete after review):", "", "```",
                  (STATE / "SUBMIT_BLOCKED").read_text().strip(), "```"]
    lines.append("")

    usage = (STATE / "usage.json").read_text().strip() if (STATE / "usage.json").exists() else "{}"
    lines += ["", "## Claude usage (last seen)", "", f"`{usage}`", ""]
    sup = (STATE / "logs" / "supervisor.log")
    if sup.exists():
        tail = [l for l in sup.read_text().splitlines() if l[:16] >= start.strftime("%Y-%m-%dT%H:%M")][-40:]
        lines += ["## Supervisor log (tail)", "", "```", *tail, "```", ""]
    for t in ledger.TASKS:
        notes = ROOT / "work" / t / "NOTES.md"
        if notes.exists():
            lines += [f"## work/{t}/NOTES.md (head)", "", *notes.read_text().splitlines()[:30], ""]

    out_dir = ROOT / "reports"; out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{nid}.md"
    path.write_text("\n".join(lines) + "\n")
    summary = (f"VQCC 夜間ラン {nid}: 検証 {len(recs)} 回, 予測 LB {pred:.4f} "
               f"(invsqrt {ratios['invsqrt']:.3f} / dqi {ratios['dqi']:.3f} / dfthcsos {ratios['dfthcsos']:.3f}), "
               f"提出 {sum(s['submitted'] for s in subs)} 件。詳細: {path}")
    print(summary)
    (STATE / "last_report_summary.txt").write_text(summary + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
