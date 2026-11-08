"""Kaggle-side runner for bin/vqcc-compute (this file is the kernel script template).

harness/compute.py replaces __JOB_B64__ (base64 JSON) with the job parameters and pushes the result as a
private Kaggle script kernel (internet disabled). mode "gpu_check": report nvidia-smi / torch.cuda and run a
small matmul. mode "run": verify the GPU (unless gpu_required is false), unpack the worker's src (private
dataset, src.tar.gz) to /kaggle/working/src, run `python <entry> <args>` there with VQCC_OUT=/kaggle/working/out,
and write /kaggle/working/result.json. The kernel exits 0 once result.json is written; failures are inside it.
"""
import base64
import collections
import glob
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import threading
import time

JOB = json.loads(base64.b64decode("__JOB_B64__").decode())
WORK = "/kaggle/working"
OUT = os.path.join(WORK, "out")
SRC = os.path.join(WORK, "src")
T0 = time.time()
result = {"job_id": JOB["job_id"], "mode": JOB["mode"], "python": platform.python_version(),
          "cpu_count": os.cpu_count(), "exit_code": None, "error": None}
os.makedirs(OUT, exist_ok=True)


def write_result():
    result["kernel_wall_s"] = round(time.time() - T0, 2)
    with open(os.path.join(WORK, "result.json"), "w") as fh:
        json.dump(result, fh, indent=1)


def finish():
    shutil.rmtree(SRC, ignore_errors=True)  # keep the downloadable output small
    write_result()
    print(json.dumps({k: v for k, v in result.items() if k not in ("output_tail", "nvidia_smi")}), flush=True)
    sys.exit(0)


def probe_gpu():
    info = {"nvidia_smi": None, "nvidia_smi_ok": False, "gpu_name": None, "torch": None,
            "cuda_available": None, "torch_error": None}
    try:
        p = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=60)
        info["nvidia_smi"] = (p.stdout + p.stderr)[-4000:]
        info["nvidia_smi_ok"] = p.returncode == 0
        q = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=60)
        if q.returncode == 0 and q.stdout.strip():
            name, mem, drv = (q.stdout.strip().splitlines()[0].split(", ") + ["", ""])[:3]
            info.update(gpu_name=name, gpu_mem=mem, driver=drv, gpu_count=len(q.stdout.strip().splitlines()))
    except Exception as e:  # noqa: BLE001
        info["nvidia_smi"] = f"nvidia-smi failed: {e!r}"
    try:
        import torch
        info["torch"] = torch.__version__
        info["cuda_available"] = bool(torch.cuda.is_available())
        if info["cuda_available"]:
            info["torch_device_count"] = torch.cuda.device_count()
            info["torch_device_name"] = torch.cuda.get_device_name(0)
            info["gpu_name"] = info["gpu_name"] or info["torch_device_name"]
            t = time.time()
            a = torch.randn(4096, 4096, device="cuda")
            b = (a @ a).sum().item()
            torch.cuda.synchronize()
            info["matmul_4096_s"] = round(time.time() - t, 3)
            info["matmul_ok"] = bool(b == b)
    except Exception as e:  # noqa: BLE001
        info["torch_error"] = repr(e)[-500:]
    info["gpu_ok"] = bool(info["nvidia_smi_ok"] and info["cuda_available"] is not False
                          and (info["cuda_available"] or info["torch"] is None))
    return info


gpu = probe_gpu()
result["gpu"] = gpu
print("nvidia-smi:\n", gpu["nvidia_smi"], "\ntorch:", gpu["torch"], "cuda:", gpu["cuda_available"],
      "name:", gpu["gpu_name"], flush=True)

if JOB["mode"] == "gpu_check":
    if not gpu["gpu_ok"]:
        result["error"] = "no usable GPU: " + (gpu["torch_error"] or gpu["nvidia_smi"] or "")[-500:]
    finish()

if JOB.get("gpu_required", True) and not gpu["gpu_ok"]:
    result["error"] = "GPU required but not available; entry not executed. " + \
        (gpu["torch_error"] or (gpu["nvidia_smi"] or ""))[-500:]
    finish()

# ---- unpack src ----
hits = sorted(glob.glob(f"/kaggle/input/**/{JOB['dataset_slug']}", recursive=True))
if not hits:
    result["error"] = "src dataset not found under /kaggle/input"
    finish()
ds_dir = hits[0]
tgz = os.path.join(ds_dir, JOB["payload"])
if os.path.exists(tgz):
    os.makedirs(SRC, exist_ok=True)
    with tarfile.open(tgz, "r:gz") as tf:
        tf.extractall(SRC)
else:  # Kaggle may have auto-extracted the archive
    shutil.copytree(ds_dir, SRC, dirs_exist_ok=True)
cwd = SRC
if not os.path.exists(os.path.join(cwd, JOB["entry"])):
    alt = glob.glob(os.path.join(SRC, "**", JOB["entry"]), recursive=True)
    if alt:
        cwd = os.path.dirname(alt[0]) if os.sep not in JOB["entry"] else SRC
if not os.path.exists(os.path.join(cwd, JOB["entry"])):
    result["error"] = f"entry {JOB['entry']} not found in src: {os.listdir(SRC)[:30]}"
    finish()

# ---- run ----
argv = [sys.executable, "-u", JOB["entry"], *JOB.get("args", [])]
result["argv"] = argv
env = dict(os.environ, VQCC_OUT=OUT, PYTHONUNBUFFERED="1")
tail = collections.deque(maxlen=400)
write_result()  # early result.json so a hard kill still leaves a trace
t = time.time()
proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, errors="replace")
timed_out = []


def killer():
    if proc.poll() is None:
        timed_out.append(1)
        proc.kill()


timer = threading.Timer(JOB["run_timeout_s"], killer)
timer.start()
for line in proc.stdout:
    print(line, end="", flush=True)
    tail.append(line)
proc.wait()
timer.cancel()
result["run_wall_s"] = round(time.time() - t, 2)
result["exit_code"] = proc.returncode
result["timed_out"] = bool(timed_out)
result["output_tail"] = "".join(tail)[-20000:]
if timed_out:
    result["error"] = f"entry killed after {JOB['run_timeout_s']} s (kernel time budget)"
elif proc.returncode != 0:
    result["error"] = f"entry exited with code {proc.returncode}"
finish()
