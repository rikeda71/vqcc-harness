"""harness/submit.py: an ERROR submission leaves its candidates out of auto-submit instead of blocking it.
Run: uv run --no-project --with pytest pytest -q tests/test_submit_suspects.py"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import ledger  # noqa: E402
import submit  # noqa: E402

ERR = {"ref": 7, "status": "error", "ids": {"invsqrt": "a1", "dqi": "b1", "dfthcsos": "c1"}}


@pytest.fixture
def bl(monkeypatch):
    lists: dict[str, set] = {}
    monkeypatch.setattr(ledger, "blocklist", lambda t: lists.get(t, set()))
    return lists


def test_error_candidates_are_suspects(bl):
    assert submit.error_suspects([ERR]) == {"invsqrt": {"a1": "7"}, "dqi": {"b1": "7"}, "dfthcsos": {"c1": "7"}}


def test_blocklisting_the_culprit_releases_the_rest(bl):
    bl["dfthcsos"] = {"c1"}
    assert submit.error_suspects([ERR]) == {}


def test_complete_submission_clears_a_candidate(bl):
    ok = {"ref": 8, "status": "complete", "ids": {"invsqrt": "a1", "dqi": "b2", "dfthcsos": "c2"}}
    assert submit.error_suspects([ERR, ok]) == {"dqi": {"b1": "7"}, "dfthcsos": {"c1": "7"}}


def test_error_no_longer_writes_submit_blocked(tmp_path, monkeypatch, bl):
    monkeypatch.setattr(submit, "STATE", tmp_path)
    monkeypatch.setattr(submit, "KSUBS", tmp_path / "k.json")
    monkeypatch.setattr(submit, "BLOCK", tmp_path / "SUBMIT_BLOCKED")
    monkeypatch.setattr(submit, "ERR_SEEN", tmp_path / "seen")
    monkeypatch.setattr(submit, "model_version", lambda t, i: None)
    row = {"ref": 7, "date": "2026-11-09 16:30:36", "status": "SubmissionStatus.ERROR",
           "description": "vqcc-harness pred=25.1 invsqrt=a1 dqi=b1 dfthcsos=c1"}
    ours = submit.sync_kaggle([row])
    assert ours[0]["status"] == "error" and not (tmp_path / "SUBMIT_BLOCKED").exists()
    low = dict(row, ref=9, status="SubmissionStatus.COMPLETE", publicScore="20.0")
    submit.sync_kaggle([low])
    assert (tmp_path / "SUBMIT_BLOCKED").exists()   # LB far below prediction still blocks


def test_verified_best_skips_excluded(monkeypatch, bl):
    recs = [{"task": "dqi", "tier": "full", "status": "PASS", "cand_id": c, "score": s, "salt": ""}
            for c, s in (("b1", 1.0), ("b2", 2.0))]
    monkeypatch.setattr(ledger, "records", lambda: recs)
    assert ledger.verified_best("dqi")["cand_id"] == "b1"
    assert ledger.verified_best("dqi", exclude={"b1"})["cand_id"] == "b2"
