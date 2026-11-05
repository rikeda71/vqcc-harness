"""Kaggle-side runner for bin/vqcc-remote (this file is the kernel script template).

harness/remote.py replaces __JOB_JSON__ with the job parameters and pushes the result as a
private Kaggle script kernel. The kernel mounts the competition data (kickmix/, verifiers/) and a
private dataset that holds the candidate as cand.tar.gz, runs the OFFICIAL verifier unmodified
with its default arguments (plus whatever the job lists in "args"), and writes
/kaggle/working/result.json. The kernel itself always exits 0 once it got far enough to write
result.json; verifier failures are reported inside result.json.
"""
import glob
import hashlib
import json
import os
import platform
import subprocess
import sys
import tarfile
import time

JOB = json.loads(r'''__JOB_JSON__''')
WORK = "/kaggle/working"
T0 = time.time()
result = {"job_id": JOB["job_id"], "task": JOB["task"], "python": platform.python_version(),
          "cpu_count": os.cpu_count(), "argv": None, "exit_code": None}


def finish(code=0):
    result["kernel_wall_s"] = round(time.time() - T0, 2)
    with open(os.path.join(WORK, "result.json"), "w") as fh:
        json.dump(result, fh, indent=1)
    print(json.dumps({k: v for k, v in result.items() if k not in ("stdout", "stderr_tail")}), flush=True)
    sys.exit(code)


def find_one(pattern, what):
    hits = sorted(glob.glob(pattern, recursive=True))
    if not hits:
        result["error"] = f"{what} not found: {pattern}"
        print(result["error"], flush=True)
        for dp, dn, fn in os.walk("/kaggle/input"):
            if dp.count("/") <= 6:
                print(dp, dn[:10], fn[:10], flush=True)
        finish(0)
    return hits[0]


comp = os.path.dirname(find_one("/kaggle/input/**/kickmix/kickmix.so", "kickmix.so"))
comp = os.path.dirname(comp)
verifiers = os.path.join(comp, "verifiers")
ds_dir = find_one(f"/kaggle/input/**/{JOB['dataset_slug']}", "candidate dataset")
print("competition dir:", comp, "| candidate dataset dir:", ds_dir, flush=True)
# Kaggle extracts uploaded archives when it mounts a dataset, so the candidate is normally already
# unpacked (read-only) in ds_dir. Fall back to unpacking cand.tar.gz if it was left as an archive.
cand_dir = ds_dir
result["unpack_s"] = 0.0
if os.path.exists(os.path.join(ds_dir, JOB["payload"])):
    cand_dir = "/tmp/vqcc_cand"
    os.makedirs(cand_dir, exist_ok=True)
    t = time.time()
    with tarfile.open(os.path.join(ds_dir, JOB["payload"]), "r:gz") as tf:
        tf.extractall(cand_dir)
    result["unpack_s"] = round(time.time() - t, 2)
cand = os.path.join(cand_dir, JOB["name"]) if JOB["kind"] == "file" else cand_dir
if not os.path.exists(cand):
    result["error"] = f"candidate {cand} missing: {os.listdir(cand_dir)}"
    finish(0)

# Which kickmix is this? (it is the competition's kickmix.so, i.e. the LB's)
so = os.path.join(comp, "kickmix", "kickmix.so")
h = hashlib.sha256()
with open(so, "rb") as fh:
    for chunk in iter(lambda: fh.read(1 << 20), b""):
        h.update(chunk)
result["kickmix_so_sha256"] = h.hexdigest()
probe = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, sys.argv[1]); import kickmix; print(getattr(kickmix, '__version__', ''))",
     os.path.join(comp, "kickmix")], capture_output=True, text=True)
result["kickmix_version"] = probe.stdout.strip() or None
if probe.returncode:
    result["error"] = "kickmix import failed: " + probe.stderr[-2000:]
    finish(0)

SCRIPTS = {
    "invsqrt": "invsqrt/verify_submission_invsqrt.py",
    "dqi": "dqi/verify_submission_dqi.py",
    "dfthcsos": "dfthcsos/verify.py",
}
script = os.path.join(verifiers, SCRIPTS[JOB["task"]])
vargs = [cand] + (["--salt", JOB["salt"]] if JOB.get("salt") else []) + list(JOB.get("args", []))
# Same launch recipe as harness/run_official.py: only the verifiers dir (and kickmix) on sys.path.
launcher = ("import runpy,sys; sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2]); "
            "s = sys.argv[3]; sys.argv = [s] + sys.argv[4:]; runpy.run_path(s, run_name='__main__')")
argv = [sys.executable, "-c", launcher, os.path.join(comp, "kickmix"), verifiers, script] + vargs
result["argv"] = [a.replace(cand_dir, "<cand>") for a in argv[3:]]
print("running:", result["argv"], flush=True)
t = time.time()
proc = subprocess.run(argv, capture_output=True, text=True, cwd=WORK)
result["verify_wall_s"] = round(time.time() - t, 2)
result["exit_code"] = proc.returncode
result["stdout"] = proc.stdout[-20000:]
result["stderr_tail"] = proc.stderr[-4000:]
try:
    result["verifier"] = json.loads(proc.stdout)
except Exception:
    result["verifier"] = None
print(proc.stdout[-3000:], flush=True)
print(proc.stderr[-3000:], file=sys.stderr, flush=True)
finish(0)
