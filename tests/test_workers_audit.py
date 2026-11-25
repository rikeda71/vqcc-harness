"""Plateau measure (Stop-hook audit trigger) and the remote-result notice of the activity hook.
Run: pytest tests/test_workers_audit.py -q"""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import ledger  # noqa: E402  (workers.py imports this same module)
import workers  # noqa: E402

T0 = dt.datetime(2026, 11, 25, 0, 0, tzinfo=dt.timezone.utc)


def rec(minutes, cand, score, tier="remote", status="PASS", salt="", task="dqi", label="x"):
    return {"ts": (T0 + dt.timedelta(minutes=minutes)).isoformat(), "task": task, "cand_id": cand, "tier": tier,
            "salt": salt, "status": status, "score": score, "ratio": 100.0 / score, "label": label}


@pytest.fixture
def write(tmp_path, monkeypatch):
    lg = tmp_path / "results.jsonl"
    refs = tmp_path / "refs.json"
    refs.write_text(json.dumps({"dqi": 100.0}))
    monkeypatch.setattr(ledger, "LEDGER", lg)
    monkeypatch.setattr(ledger, "REFS_FILE", refs)
    monkeypatch.setattr(ledger, "ROOT", tmp_path)  # no BLOCKLIST
    return lambda rs: lg.write_text("".join(json.dumps(r) + "\n" for r in rs))


def test_plateau_counts_from_the_first_pass_of_the_current_best(write):
    write([rec(0, "a", 10.0), rec(30, "b", 8.0), rec(60, "b", 8.0, salt="s2"),
           rec(90, "c", 9.0), rec(100, "d", 7.0, tier="quick")])  # worse full and quick runs do not reset it
    assert workers.plateau_min("dqi", now=T0 + dt.timedelta(minutes=200)) == 170


def test_plateau_resets_on_a_new_best_and_ignores_failed_candidates(write):
    write([rec(0, "a", 10.0), rec(50, "e", 5.0, status="FAIL"), rec(120, "f", 6.0)])
    assert workers.plateau_min("dqi", now=T0 + dt.timedelta(minutes=150)) == 30


def test_plateau_is_none_without_a_verified_best(write):
    write([rec(0, "a", 10.0, tier="quick")])
    assert workers.plateau_min("dqi") is None


def test_new_results_lists_only_remote_runs_after_since_with_delta(write):
    write([rec(0, "a", 10.0), rec(30, "b", 8.0, tier="full"), rec(60, "c", 12.5, salt="s2", label="try c"),
           rec(70, "z", 1.0, task="invsqrt")])
    since = (T0 + dt.timedelta(minutes=10)).timestamp()
    out = workers.new_results("dqi", since)
    assert out.startswith("c remote/s2 PASS ratio 8.00000")
    assert "[try c]" in out
    assert "best 比 -36.000%" in out  # 8.0 against the verified best b (full, score 8 -> ratio 12.5)
    listed = out.split(" — ")[0]
    assert "b full" not in listed and "z " not in listed
    assert out.endswith("— verified best b ratio 12.50000")
    assert workers.new_results("dqi", (T0 + dt.timedelta(minutes=60)).timestamp()) is None
