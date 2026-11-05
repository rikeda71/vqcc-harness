"""Quick-tier verifier for invsqrt / dqi: reuses the official verifier modules (verify(), sampling,
tolerance and cost code are theirs), only changing which shots are run.

Usage: python -I harness/quick_worker.py {invsqrt|dqi} <file.kmb> [params] [--salt S] [--max-workers N]
Prints one JSON object like the official CLI (exit 0 = PASS, 1 = FAIL; other exceptions propagate -> ERROR).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "data", "raw", "verifiers"))

import kickmix as km  # noqa: E402

CATCH = (AssertionError, ValueError, RuntimeError, OSError)  # same as the official CLIs


def invsqrt_xs(mod, stride: int, shots: int, boundary_shots: int, batch_size: int) -> np.ndarray:
    max_x = 3 * (2**mod.N_P - 2) ** 2
    base = np.arange(1, max_x + 1, stride, dtype=np.uint64)
    b = set(range(1, 65)) | set(range(max_x - 32, max_x + 1))
    for k in range(0, 2 * mod.N_P + 2):
        for d in range(-2, 3):
            b.add(2**k + d)  # 2^k boundaries; every 4^k is among them (the exponent normalisation pairs bits)
    b = np.array(sorted(x for x in b if 1 <= x <= max_x), dtype=np.uint64)
    xs = np.concatenate([np.tile(base, shots), np.tile(b, boundary_shots)])
    pad = (-len(xs)) % batch_size
    if pad:
        xs = np.concatenate([xs, base[:pad]])
    return xs


def run_invsqrt(args) -> dict:
    import invsqrt.verify_submission_invsqrt as mod

    xs = invsqrt_xs(mod, args.stride, args.shots, args.boundary_shots, args.batch_size)

    class QuickSpec(mod.ProblemSpecInvSqrt):
        @property
        def num_shots(self) -> int:
            return len(xs)

    mod.sample_shots = lambda spec, start, stop: xs[start:stop]  # run_chunk looks this up as a module global
    spec = QuickSpec.for_n_p(mod.N_P, shots_per_x=1)
    circuit = km.Circuit.from_file(args.circuit_file, format="auto")
    out = mod.verify(circuit, spec, salt=args.salt, batch_size=args.batch_size, max_workers=args.max_workers)
    out["quick"] = {"tier": "invsqrt-stride-sample", "stride": args.stride, "shots": args.shots,
                    "boundary_shots": args.boundary_shots, "distinct_x": int(len(np.unique(xs))),
                    "note": "subset of x; score is a reference value, not the LB value"}
    return out


def run_dqi(args) -> dict:
    import dqi.verify_submission_dqi as mod

    spec = mod.ProblemSpecDQI.benchmark_instance(num_shots=args.num_shots)
    circuit = km.Circuit.from_file(args.circuit_file, format="auto")
    out = mod.verify(circuit, spec, salt=args.salt, batch_size=args.batch_size, log2_delta=args.log2_delta,
                     max_workers=args.max_workers)
    # Project to the official N = 102400, delta = 2^-128: failure rate k/N_quick scaled to N_full.
    n_full = mod.DEFAULT_NUM_SHOTS
    k = out["value_errors"] + out["phase_errors"]
    k_full = int(np.ceil(k / args.num_shots * n_full)) if k else 0
    eps_plus_full = mod.clopper_pearson_upper_bound(k_full, n_full, mod.DEFAULT_LOG2_DELTA)
    out["quick"] = {"tier": "dqi-few-shots", "num_shots": args.num_shots, "log2_delta_used": args.log2_delta,
                    "score_at_quick_delta": out["score"], "projected_failures_full": k_full,
                    "projected_effective_error_rate": eps_plus_full,
                    "note": "score = projection to N=102400, delta=2^-128 assuming the observed failure rate; "
                            "reference value only"}
    if eps_plus_full >= 1 / 16:
        raise AssertionError(f"projected effective error rate {eps_plus_full:.4f} >= 1/16 "
                             f"(quick failures {k}/{args.num_shots})")
    out["effective_error_rate"] = eps_plus_full
    out["repetition_factor"] = mod.repetition_overhead(eps_plus_full)
    out["score"] = out["repetition_factor"] * out["num_qubits"] ** 2 * out["toffoli_count"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("task", choices=("invsqrt", "dqi"))
    ap.add_argument("circuit_file")
    ap.add_argument("--stride", type=int, default=16)
    ap.add_argument("--shots", type=int, default=4)
    ap.add_argument("--boundary-shots", type=int, default=16)
    ap.add_argument("--num-shots", type=int, default=2560)
    ap.add_argument("--log2-delta", type=float, default=-16.0)
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--salt", default="")
    ap.add_argument("--max-workers", type=int, default=None)
    args = ap.parse_args()
    try:
        out = run_invsqrt(args) if args.task == "invsqrt" else run_dqi(args)
    except CATCH as ex:
        print(json.dumps({"status": "FAIL", "error": str(ex)}, indent=2))
        sys.exit(1)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
