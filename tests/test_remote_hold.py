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


def _ts(ago_min):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - ago_min * 60))


def test_stale_queued_kernel_is_repushed_then_errors(env, monkeypatch):
    cj, pushed = env
    cfg = dict(CFG, stale_queued_min=60, max_repush=2, cleanup=False)
    monkeypatch.setattr(remote, "kernel_status", lambda ref: "QUEUED")
    errors = []
    monkeypatch.setattr(remote, "_ledger_error", lambda job, msg: errors.append(msg))
    job = {"job_id": "j1", "state": "pushed", "submitted": "1", "pushed": _ts(30), "task": "dqi", "cand_id": "c",
           "kernel_ref": "me/k"}
    remote.save(job)
    remote.do_poll(cfg)                       # 30 min: not stale yet
    assert pushed == []
    job["pushed"] = _ts(90)
    remote.save(job)
    remote.do_poll(cfg)                       # 90 min: re-pushed
    assert pushed == ["j1"]
    j = remote.load_jobs()["j1"]
    assert j["repushes"] == 1 and j["state"] == "pushed"
    remote.do_poll(cfg)                       # just re-pushed: the clock restarts
    assert pushed == ["j1"]
    j["repushed"] = _ts(90)
    j["repushes"] = 2
    remote.save(j)
    remote.do_poll(cfg)                       # max_repush reached: error, ledger notified
    assert remote.load_jobs()["j1"]["state"] == "error" and errors


def test_running_kernel_is_not_repushed(env, monkeypatch):
    cj, pushed = env
    monkeypatch.setattr(remote, "kernel_status", lambda ref: "RUNNING")
    remote.save({"job_id": "j1", "state": "pushed", "submitted": "1", "pushed": _ts(600), "task": "dqi",
                 "cand_id": "c", "kernel_ref": "me/k"})
    remote.do_poll(dict(CFG, stale_queued_min=60, max_repush=3, cleanup=False))
    assert pushed == [] and remote.load_jobs()["j1"]["state"] == "running"
