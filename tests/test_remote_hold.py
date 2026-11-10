"""dispatch() holds remote verifier pushes while a vqcc-compute GPU job is RUNNING. Run: pytest tests/test_remote_hold.py -q"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness import remote  # noqa: E402

CFG = {"max_concurrent": 5, "hold_while_gpu_running": True}


@pytest.fixture
def env(tmp_path, monkeypatch):
    cj = tmp_path / "compute_jobs.jsonl"
    monkeypatch.setattr(remote, "COMPUTE_JOBS", cj)
    monkeypatch.setattr(remote, "JOBS", tmp_path / "jobs.jsonl")
    pushed = []
    monkeypatch.setattr(remote, "ensure_dataset", lambda job, path: None)
    monkeypatch.setattr(remote.ledger, "stored_path", lambda t, c: Path("x"))
    monkeypatch.setattr(remote, "push_kernel", lambda job, cfg: pushed.append(job["job_id"]))
    return cj, pushed


def jobs():
    return {"j1": {"job_id": "j1", "state": "queued", "submitted": "1", "task": "dqi", "cand_id": "c", "kernel_ref": "me/k"}}


def write(cj, *rows):
    cj.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_hold_while_gpu_running(env):
    cj, pushed = env
    write(cj, {"job_id": "g", "kind": "gpu", "state": "pushed"}, {"job_id": "g", "kind": "gpu", "state": "running"})
    remote.dispatch(CFG, jobs())
    assert pushed == []
    remote.dispatch(dict(CFG, hold_while_gpu_running=False), jobs())
    assert pushed == ["j1"]


@pytest.mark.parametrize("state", ["pushed", "done", "error"])
def test_no_hold_when_gpu_queued_or_finished(env, state):
    cj, pushed = env
    write(cj, {"job_id": "g", "kind": "gpu", "state": "running"}, {"job_id": "g", "kind": "gpu", "state": state})
    remote.dispatch(CFG, jobs())
    assert pushed == ["j1"]


def test_cpu_compute_job_does_not_hold(env):
    cj, pushed = env
    write(cj, {"job_id": "c", "kind": "cpu", "state": "running"})
    remote.dispatch(CFG, jobs())
    assert pushed == ["j1"]
