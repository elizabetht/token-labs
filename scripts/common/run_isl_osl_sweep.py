#!/usr/bin/env python3
"""
ISL/OSL Performance Sweep
Runs vllm bench serve for each (ISL, OSL) combo at multiple concurrency levels.
Writes results to results/qwen25-7b-llmd-isl-osl-sweep-YYYY-MM-DD.json
"""
import json
import re
import subprocess
import sys
from datetime import datetime, timezone

MODEL       = "Qwen/Qwen2.5-7B-Instruct"
BASE_URL    = "http://localhost:8000"
POD         = "ms-qwen25-7b-exp9-llm-d-modelservice-decode-849979bb88-g8stv"
NAMESPACE   = "token-labs"
CONTAINER   = "vllm"
COMBOS = [
    (128,  128),
    (128,  512),
    (1024, 128),
    (1024, 512),
    (4096, 512),
]

CONCURRENCY_LEVELS = [1, 4, 8, 16, 32]

def run_params(isl, osl, concurrency):
    """
    Choose num_prompts and timeout so each run completes in ~5 min.
    At c=1 requests are serial; at c=N they batch N at a time.
    Estimate per-request time: TTFT ~(isl/4096)s + decode ~(osl*85ms).
    """
    import math
    est_req_s = max(0.1, isl / 4096.0) + osl * 0.085
    # pick n such that ceil(n/c)*est_req_s ~ 280s
    n = max(5, min(50, int(280 * concurrency / est_req_s)))
    total_est_s = math.ceil(n / concurrency) * est_req_s
    timeout = max(300, int(total_est_s * 2 + 120))
    return n, timeout

def run_bench(isl, osl, concurrency):
    np, to = run_params(isl, osl, concurrency)
    cmd = [
        "kubectl", "exec", "-n", NAMESPACE, POD, "-c", CONTAINER, "--",
        "bash", "-c",
        (
            f"FLASHINFER_DISABLE_VERSION_CHECK=1 vllm bench serve "
            f"--model {MODEL} "
            f"--base-url {BASE_URL} "
            f"--dataset-name random "
            f"--random-input-len {isl} "
            f"--random-output-len {osl} "
            f"--num-prompts {np} "
            f"--max-concurrency {concurrency}"
        ),
    ]
    print(f"  → ISL{isl}/OSL{osl} c={concurrency} (n={np}, timeout={to}s) ...", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=to)  # noqa: E501
    if result.returncode != 0:
        print(f"    ERROR: {result.stderr[-500:]}", flush=True)
        return None
    return parse_output(result.stdout + result.stderr)

def parse_output(text):
    def extract(pattern, txt):
        m = re.search(pattern, txt)
        return round(float(m.group(1)), 2) if m else None

    return {
        "throughput_tok_s": extract(r"Output token throughput.*?:\s*([\d.]+)", text),
        "ttft_p50_ms":      extract(r"[Mm]edian TTFT.*?:\s*([\d.]+)", text),
        "ttft_p99_ms":      extract(r"[Pp]99 TTFT.*?:\s*([\d.]+)", text),
        "itl_p50_ms":       extract(r"[Mm]edian ITL.*?:\s*([\d.]+)", text),
        "itl_p99_ms":       extract(r"[Pp]99 ITL.*?:\s*([\d.]+)", text),
        "e2e_p50_ms":       extract(r"[Mm]edian [Ee]2[Ee].*?:\s*([\d.]+)", text),
    }

def main():
    output_path = (
        f"/home/nvidia/src/github.com/elizabetht/token-labs/results/"
        f"qwen25-7b-llmd-isl-osl-sweep-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
    )

    # Resume from existing partial results
    try:
        with open(output_path) as f:
            existing = json.load(f)
        results = existing.get("combos", {})
        print(f"Resuming from existing file: {output_path}", flush=True)
    except FileNotFoundError:
        results = {}

    total = len(COMBOS) * len(CONCURRENCY_LEVELS)
    done  = sum(len(v["levels"]) for v in results.values())
    print(f"Starting at {done}/{total}", flush=True)

    for isl, osl in COMBOS:
        key = f"ISL{isl}/OSL{osl}"
        existing_levels = results.get(key, {}).get("levels", [])
        completed_c = {l["concurrency"] for l in existing_levels}
        remaining = [c for c in CONCURRENCY_LEVELS if c not in completed_c]
        if not remaining:
            print(f"\n=== {key} already complete, skipping ===", flush=True)
            continue
        print(f"\n=== {key} (remaining: c={remaining}) ===", flush=True)
        levels = list(existing_levels)
        for c in remaining:
            metrics = run_bench(isl, osl, c)
            if metrics:
                metrics["concurrency"] = c
                levels.append(metrics)
                print(f"    tput={metrics.get('throughput_tok_s')} tok/s  "
                      f"TTFT_p50={metrics.get('ttft_p50_ms')}ms  "
                      f"ITL_p50={metrics.get('itl_p50_ms')}ms", flush=True)
            done += 1
            # Write partial results after each run
            results[key] = {"isl": isl, "osl": osl, "levels": levels}
            payload = {
                "model": MODEL,
                "base_url": "http://10.244.1.152:8000",
                "experiment": "isl-osl-sweep",
                "config": "full-stack (exp9)",
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "progress": f"{done}/{total}",
                "combos": results,
            }
            with open(output_path, "w") as f:
                json.dump(payload, f, indent=2)
            print(f"    saved ({done}/{total})", flush=True)

    print(f"\nDone. Results at {output_path}", flush=True)
    return output_path

if __name__ == "__main__":
    main()
