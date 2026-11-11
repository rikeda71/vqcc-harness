"""Morning report: what the night produced. Writes reports/<night_id>.md and prints a short summary."""
from __future__ import annotations

import datetime as dt
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402
import workers  # noqa: E402

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

    # Leaderboard standing (bin/vqcc-standing, run by the supervisor just before this report).
    sj = STATE / "standing.json"
    if sj.exists():
        st = json.loads(sj.read_text())
        lines += ["## Leaderboard standing (bin/vqcc-standing)", "",
                  f"- {st['ts']}: {st['teams']} teams, us {st.get('our_public_lb')} → rank {st.get('our_rank')}"
                  + (f" (top {st['our_percentile']:.1f}%)" if st.get("our_percentile") else "")
                  + (f", {st['days_left']:.1f} days left" if st.get("days_left") else ""), "",
                  "| line | rank | score | x product | one task alone (invsqrt / dqi / dfthcsos) |", "|---|---|---|---|---|"]
        for m in ("bronze", "silver", "gold", "rank1"):
            g = st["targets"].get(m)
            if g:
                rk = st["medal_lines"][m]["rank"] if m in st["medal_lines"] else 1
                lines.append(f"| {m}{' ←' if m == st.get('primary_target') else ''} | {rk} | {g['target_score']:.5f} | "
                             f"{g['product_multiplier']:.4f} | "
                             + " / ".join(f"{g['one_task_alone_ratio'][t]:.3f}" for t in ledger.TASKS) + " |")
        lines.append("")
    lines += ["## Worker state at the end of the night (harness/workers.py)", "",
              "PARKED = the worker finished its directive and waits for a new one; the indented lines are the head of "
              "work/<task>/IDLE (what was done and why it stopped), or NOTES.md「現在地」for a working task.", "",
              "```", *workers.lines(idle_lines=12), "```", ""]
    strat = ROOT / "work" / "STRATEGY.md"
    if strat.exists():
        lines += ["## work/STRATEGY.md (head)", "", *strat.read_text().splitlines()[:40], ""]

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
    import submit  # noqa: PLC0415 (same directory; only for the ERROR-suspect rule)
    sus = submit.error_suspects(ks)
    if sus:
        lines += ["", "**ERROR suspects left out of auto-submit** (candidates of an ERROR submission; put the "
                  "culprit on its work/<task>/BLOCKLIST to release the others): "
                  + "; ".join(f"{t}: " + ", ".join(f"{i} (submission {r})" for i, r in c.items()) for t, c in sus.items())]
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
    # Day-by-day progress: one row per night in reports/DAILY.md (rerunning a night replaces its row).
    daily = out_dir / "DAILY.md"
    head = ["# VQCC nightly progress", "",
            "| night | predicted LB | change | runs (PASS) | submitted | best public LB | invsqrt | dqi | dfthcsos |",
            "|---|---|---|---|---|---|---|---|---|"]
    rows = [l for l in (daily.read_text().splitlines() if daily.exists() else []) if l.startswith("| 2")]
    rows = [l for l in rows if not l.startswith(f"| {nid} ")]
    prev = None
    if rows:
        try:
            prev = float(rows[-1].split("|")[2])
        except ValueError:
            prev = None
    pubs = [k["public"] for k in ks if k.get("public") is not None]
    delta = f"{pred - prev:+.4f}" if prev is not None else "-"
    rows.append(f"| {nid} | {pred:.4f} | {delta} | {len(recs)} ({sum(r['status']=='PASS' for r in recs)}) | "
                f"{sum(s['submitted'] for s in subs)} | {max(pubs) if pubs else '-'} | "
                + " | ".join(f"{ratios[t]:.4f}" for t in ledger.TASKS) + " |")
    daily.write_text("\n".join(head + rows) + "\n")
    summary = (f"VQCC 夜間ラン {nid}: 検証 {len(recs)} 回, 予測 LB {pred:.4f} ({delta} vs 前夜) "
               f"(invsqrt {ratios['invsqrt']:.3f} / dqi {ratios['dqi']:.3f} / dfthcsos {ratios['dfthcsos']:.3f}), "
               f"提出 {sum(s['submitted'] for s in subs)} 件。worker: {workers.short()}。詳細: {path}")
    print(summary)
    (STATE / "last_report_summary.txt").write_text(summary + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
