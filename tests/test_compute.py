"""Unit tests for harness/compute.py (kaggle CLI mocked). Run: pytest tests/test_compute.py -q"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness import compute  # noqa: E402

CFG = {"competition": "c", "accelerator": "NvidiaTeslaT4", "max_concurrent_gpu": 1, "weekly_gpu_hours_budget": 25,
       "max_src_mb": 1, "gpu_check_max_age_h": 24, "default_hours": 3, "max_hours": 12, "gpu_check_wait_s": 900,
       "cleanup": True, "kernel_prefix": "vqcc-gpu-", "dataset_prefix": "vqcc-src-"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    st = tmp_path / "state"
    monkeypatch.setattr(compute, "STATE", st)
    monkeypatch.setattr(compute, "JOBS", st / "jobs.jsonl")
    monkeypatch.setattr(compute, "CHECK", st / "gpu_check.json")
    monkeypatch.setattr(compute, "PENDING", st / "pending.json")
    monkeypatch.setattr(compute, "LOCK", st / ".lock")
    monkeypatch.setattr(compute, "WORK_ROOT", tmp_path / "work")
    src = tmp_path / "work" / "dfthcsos" / "src"
    src.mkdir(parents=True)
    (src / "main.py").write_text("print(1)\n")
    calls = []

    def fake_kaggle(*args, check=True, timeout=0):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(compute, "kaggle", fake_kaggle)
    monkeypatch.setattr(compute, "kaggle_user", lambda: "me")
    monkeypatch.setattr(compute, "ensure_dataset", lambda ref, s: calls.append(("ensure", ref)))
    monkeypatch.setattr(compute, "push_kernel", lambda ref, jj, **kw: calls.append(("push", ref, kw)))
    return tmp_path, src, calls


def write_check(ok=True, age_h=1, acc="NvidiaTeslaT4"):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - age_h * 3600))
    compute.STATE.mkdir(parents=True, exist_ok=True)
    compute.CHECK.write_text(json.dumps({"ok": ok, "ts": ts, "accelerator": acc}))


def ns(src, **kw):
    d = dict(task="dfthcsos", src=str(src), entry="main.py", args=[], label="t", hours=None, accelerator=None, cpu=False)
    d.update(kw)
    return argparse.Namespace(**d)


def test_src_must_be_under_work_task(env):
    tmp, src, _ = env
    assert compute.resolve_src("dfthcsos", src) == src.resolve()
    with pytest.raises(compute.Refused):
        compute.resolve_src("dfthcsos", tmp)
    other = tmp / "work" / "dqi" / "src"
    other.mkdir(parents=True)
    with pytest.raises(compute.Refused):
        compute.resolve_src("dfthcsos", other)
    with pytest.raises(compute.Refused):
        compute.resolve_src("dfthcsos", src / ".." / ".." / "dqi")


def test_excludes_and_payload(env, tmp_path):
    _, src, _ = env
    for d in (".venv", "__pycache__", ".git", "sub/__pycache__"):
        (src / d).mkdir(parents=True)
        (src / d / "x.txt").write_text("big")
    (src / "sub" / "a.py").write_text("a")
    names = sorted(str(r) for r, _ in compute.iter_src_files(src))
    assert names == ["main.py", "sub/a.py"]
    h1 = compute.src_stats(src, 1)[0]
    (src / ".venv" / "y").write_text("z")
    assert compute.src_stats(src, 1)[0] == h1  # excluded content does not change the hash
    (src / "sub" / "a.py").write_text("b")
    assert compute.src_stats(src, 1)[0] != h1
    import tarfile
    compute.make_payload(src, tmp_path / "p.tgz")
    assert sorted(tarfile.open(tmp_path / "p.tgz").getnames()) == ["main.py", "sub/a.py"]


def test_size_limit(env):
    _, src, _ = env
    (src / "big.bin").write_bytes(b"0" * 1_500_000)
    with pytest.raises(compute.Refused, match="max_src_mb"):
        compute.src_stats(src, 1)
    (src / ".venv").mkdir()
    (src / ".venv" / "huge").write_bytes(b"0" * 5_000_000)  # excluded: not counted
    (src / "big.bin").unlink()
    compute.src_stats(src, 1)


def test_entry_validation(env):
    _, src, _ = env
    write_check()
    with pytest.raises(compute.Refused):
        compute.cmd_submit(CFG, ns(src, entry="../x.py"))
    with pytest.raises(compute.Refused):
        compute.cmd_submit(CFG, ns(src, entry="nope.py"))
    with pytest.raises(compute.Refused):
        compute.cmd_submit(CFG, ns(src, hours=13))


def test_refused_without_or_with_stale_gpu_check(env):
    _, src, calls = env
    with pytest.raises(compute.Refused, match="gpu-check"):
        compute.cmd_submit(CFG, ns(src))
    write_check(ok=False)
    with pytest.raises(compute.Refused, match="gpu-check"):
        compute.cmd_submit(CFG, ns(src))
    write_check(age_h=25)
    with pytest.raises(compute.Refused, match="old"):
        compute.cmd_submit(CFG, ns(src))
    write_check(acc="NvidiaTeslaP100")
    with pytest.raises(compute.Refused, match="not NvidiaTeslaT4"):
        compute.cmd_submit(CFG, ns(src))
    assert not any(c[0] == "push" for c in calls)


def test_cpu_job_skips_gpu_validation(env):
    _, src, calls = env
    assert compute.cmd_submit(CFG, ns(src, cpu=True)) == 0
    push = [c for c in calls if c[0] == "push"][0]
    assert push[2]["gpu"] is False and push[2]["accelerator"] is None


def test_gpu_submit_ok_and_concurrency(env):
    _, src, calls = env
    write_check()
    assert compute.cmd_submit(CFG, ns(src)) == 0
    push = [c for c in calls if c[0] == "push"][0]
    assert push[2]["gpu"] is True and push[2]["accelerator"] == "NvidiaTeslaT4"
    assert push[1].startswith("me/vqcc-gpu-dfthcsos-")
    jobs = compute.load_jobs()
    assert [j["state"] for j in jobs.values()] == ["pushed"]
    with pytest.raises(compute.Refused, match="max_concurrent_gpu"):
        compute.cmd_submit(CFG, ns(src))
    compute_cfg2 = dict(CFG, max_concurrent_gpu=2)
    time.sleep(1.1)  # job ids have 1 s resolution
    assert compute.cmd_submit(compute_cfg2, ns(src)) == 0


def _job(jid, pushed_ago_h, hours_run, kind="gpu", state="done"):
    now = time.time()
    f = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t))  # noqa: E731
    return {"job_id": jid, "kind": kind, "state": state, "pushed": f(now - pushed_ago_h * 3600),
            "finished": f(now - (pushed_ago_h - hours_run) * 3600), "hours": 3, "submitted": "x"}


def test_weekly_budget(env):
    _, src, _ = env
    write_check()
    compute.save(_job("a", 50, 10))
    compute.save(_job("b", 30, 12))
    compute.save(_job("cpu", 20, 12, kind="cpu"))   # CPU jobs do not count
    compute.save(_job("old", 24 * 9, 12))           # older than 7 days: does not count
    assert abs(compute.gpu_hours_used(compute.load_jobs()) - 22) < 0.1
    with pytest.raises(compute.Refused, match="weekly GPU budget"):
        compute.cmd_submit(CFG, ns(src, hours=4))   # 22 + 4 > 25
    assert compute.cmd_submit(CFG, ns(src, hours=3)) == 0


def test_cleanup_prefix_check(env):
    _, _, calls = env
    jobs = {
        "ok": {"job_id": "ok", "state": "done", "collected": True, "kernel_ref": "me/vqcc-gpu-ok",
               "dataset_ref": "me/vqcc-src-dfthcsos-aaa"},
        "bad": {"job_id": "bad", "state": "done", "collected": True, "kernel_ref": "me/important-kernel",
                "dataset_ref": "other/vqcc-src-x"},
        "notyet": {"job_id": "notyet", "state": "done", "collected": False, "kernel_ref": "me/vqcc-gpu-notyet",
                   "dataset_ref": "me/vqcc-src-dfthcsos-bbb"},
        "active": {"job_id": "active", "state": "running", "kernel_ref": "me/vqcc-gpu-active",
                   "dataset_ref": "me/vqcc-src-dfthcsos-ccc"},
        "shared": {"job_id": "shared", "state": "done", "collected": True, "kernel_ref": "me/vqcc-gpu-shared",
                   "dataset_ref": "me/vqcc-src-dfthcsos-ddd"},
        "user_of_ddd": {"job_id": "user_of_ddd", "state": "running", "kernel_ref": "me/vqcc-gpu-u",
                        "dataset_ref": "me/vqcc-src-dfthcsos-ddd"},
    }
    compute.do_cleanup(CFG, jobs)
    deleted = [c[:3] for c in calls if c[0] in ("kernels", "datasets") and c[1] == "delete"]
    assert ("kernels", "delete", "me/vqcc-gpu-ok") in deleted
    assert ("datasets", "delete", "me/vqcc-src-dfthcsos-aaa") in deleted
    assert ("kernels", "delete", "me/vqcc-gpu-shared") in deleted
    assert ("datasets", "delete", "me/vqcc-src-dfthcsos-ddd") not in deleted   # still used by a running job
    flat = str(deleted)
    assert "important-kernel" not in flat and "other/vqcc-src-x" not in flat and "notyet" not in flat
    assert jobs["bad"].get("cleaned") and not jobs["notyet"].get("cleaned")


def test_poll_collects_and_cleans(env, monkeypatch):
    tmp, src, calls = env
    write_check()
    compute.cmd_submit(CFG, ns(src))
    monkeypatch.setattr(compute, "kernel_status", lambda ref: "COMPLETE")

    def fake_kaggle(*args, check=True, timeout=0):
        calls.append(args)
        if args[:2] == ("kernels", "output"):
            out = Path(args[args.index("-p") + 1])
            (out / "out").mkdir(parents=True, exist_ok=True)
            (out / "result.json").write_text(json.dumps({"exit_code": 0, "run_wall_s": 5, "error": None,
                                                         "gpu": {"gpu_name": "Tesla T4"}}))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(compute, "kaggle", fake_kaggle)
    monkeypatch.setattr(compute, "resolve_gpu_check", lambda cfg: "none")
    jobs = compute.do_poll(CFG)
    j = next(iter(jobs.values()))
    assert j["state"] == "done" and j["gpu_name"] == "Tesla T4" and j["cleaned"]
    assert (tmp / "work/dfthcsos/out/compute" / j["job_id"] / "result.json").exists()
    assert any(c[:2] == ("kernels", "delete") for c in calls) and any(c[:2] == ("datasets", "delete") for c in calls)


def test_gpu_hours_use_actual_run_time(env):
    now = time.time()
    f = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t))  # noqa: E731
    jobs = {
        # pushed 17 h before it finished, but the kernel itself ran 3.37 h
        "a": {"job_id": "a", "kind": "gpu", "state": "done", "pushed": f(now - 20 * 3600),
              "finished": f(now - 3 * 3600), "kernel_wall_s": 12135, "run_wall_s": 12000, "hours": 4},
        # only run_wall_s known
        "b": {"job_id": "b", "kind": "gpu", "state": "done", "pushed": f(now - 10 * 3600),
              "finished": f(now - 1 * 3600), "run_wall_s": 3600, "hours": 4},
        # still queued on Kaggle for 30 h: no GPU used
        "q": {"job_id": "q", "kind": "gpu", "state": "pushed", "pushed": f(now - 30 * 3600), "hours": 4},
        # running for 2 h (queued 10 h before that)
        "r": {"job_id": "r", "kind": "gpu", "state": "running", "pushed": f(now - 12 * 3600),
              "started": f(now - 2 * 3600), "hours": 4},
    }
    assert abs(compute.gpu_hours_used(jobs, now) - (12135 / 3600 + 1 + 2)) < 0.01


def test_args_single_string_is_shlex_split(env):
    _, src, _ = env
    write_check()
    assert compute.cmd_submit(CFG, ns(src, args=["--seeds 'a b' --n 2"])) == 0
    (job,) = compute.load_jobs().values()
    assert job["args"] == ["--seeds", "a b", "--n", "2"]


def test_args_list_kept(env):
    _, src, _ = env
    write_check()
    assert compute.cmd_submit(CFG, ns(src, args=["--seeds", "file:x.npz,file:y.npz"])) == 0
    (job,) = compute.load_jobs().values()
    assert job["args"] == ["--seeds", "file:x.npz,file:y.npz"]
