#!/usr/bin/env python3
"""
Framework Comparison ISL/OSL Sweep
Runs vllm bench serve against a given framework pod and saves results.

Usage:
    python3 run_framework_sweep.py --framework sglang --pod qwen25-7b-sglang-leader --container sglang
    python3 run_framework_sweep.py --framework trtllm --pod qwen25-7b-trtllm-leader --container trtllm
"""
import argparse
import json
import math
import re
import subprocess
import sys
from datetime import datetime, timezone

MODEL     = "Qwen/Qwen2.5-7B-Instruct"
BASE_URL  = "http://localhost:8000"
NAMESPACE = "token-labs"

# spark-01 has vllm installed in a venv — use it for benchmarks when the
# target container doesn't have vllm (e.g. SGLang, TRT-LLM containers).
SPARK01_VLLM = "/home/nvidia/src/github.com/sara4dev/ai-dynamo-the-hard-way/.venv/bin/vllm"
SPARK01_HOST = "nvidia@192.168.1.76"
SPARK02_HOST = "nvidia@192.168.1.77"

COMBOS = [
    (128,  128),
    (128,  512),
    (1024, 128),
    (1024, 512),
    (4096, 512),
]
CONCURRENCY_LEVELS = [1, 4, 8, 16, 32]


def run_params(isl, osl, concurrency):
    est_req_s = max(0.1, isl / 4096.0) + osl * 0.085
    n = max(5, min(50, int(280 * concurrency / est_req_s)))
    total_est_s = math.ceil(n / concurrency) * est_req_s
    timeout = max(300, int(total_est_s * 2 + 120))
    return n, timeout


def wait_for_ready(pod, container, timeout_s=600):
    print(f"Waiting for {pod} to be ready...", flush=True)
    deadline = datetime.now().timestamp() + timeout_s
    while datetime.now().timestamp() < deadline:
        result = subprocess.run(
            ["kubectl", "exec", "-n", NAMESPACE, pod, "-c", container, "--",
             "curl", "-sf", "http://localhost:8000/health"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            print(f"  {pod} is ready.", flush=True)
            return True
        remaining = int(deadline - datetime.now().timestamp())
        print(f"  Not ready yet ({remaining}s remaining)...", flush=True)
        import time; time.sleep(15)
    print(f"  Timed out waiting for {pod}", flush=True)
    return False


def get_pod_ip(pod):
    r = subprocess.run(
        ["kubectl", "get", "pod", "-n", NAMESPACE, pod, "-o", "jsonpath={.status.podIP}"],
        capture_output=True, text=True, timeout=15,
    )
    return r.stdout.strip()


def run_bench(pod, container, isl, osl, concurrency, framework):
    np, to = run_params(isl, osl, concurrency)

    # Determine benchmark target URL: prefer pod IP (direct, avoids LB overhead)
    pod_ip = get_pod_ip(pod)
    bench_url = f"http://{pod_ip}:8000" if pod_ip else "http://192.168.1.204:8000"

    # Always SSH to spark-01 for vllm bench serve — vllm venv only exists there.
    # bench_url already targets pod IP directly so node doesn't matter.
    ssh_host = SPARK01_HOST

    bench_cmd = (
        f"FLASHINFER_DISABLE_VERSION_CHECK=1 {SPARK01_VLLM} bench serve "
        f"--model '{MODEL}' "
        f"--base-url {bench_url} "
        f"--dataset-name random "
        f"--random-input-len {isl} "
        f"--random-output-len {osl} "
        f"--num-prompts {np} "
        f"--max-concurrency {concurrency}"
    )
    cmd = ["ssh", "-o", "StrictHostKeyChecking=no", ssh_host, bench_cmd]

    print(f"  → {framework} ISL{isl}/OSL{osl} c={concurrency} (n={np}, timeout={to}s, url={bench_url})...", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=to)
    if result.returncode != 0:
        print(f"    ERROR: {result.stderr[-600:]}", flush=True)
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
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--framework", required=True, choices=["sglang", "trtllm", "vllm"])
    parser.add_argument("--pod",       required=True)
    parser.add_argument("--container", required=True)
    args = parser.parse_args()

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_path = (
        f"/home/nvidia/src/github.com/elizabetht/token-labs/results/"
        f"qwen25-7b-{args.framework}-isl-osl-sweep-{date_str}.json"
    )

    # Resume from existing partial results
    try:
        with open(output_path) as f:
            existing = json.load(f)
        results = existing.get("combos", {})
        print(f"Resuming from {output_path}", flush=True)
    except FileNotFoundError:
        results = {}

    total = len(COMBOS) * len(CONCURRENCY_LEVELS)
    done  = sum(len(v["levels"]) for v in results.values())
    print(f"Starting at {done}/{total}", flush=True)

    # Wait for pod ready
    if not wait_for_ready(args.pod, args.container):
        print("Pod not ready — aborting.", flush=True)
        sys.exit(1)

    for isl, osl in COMBOS:
        key = f"ISL{isl}/OSL{osl}"
        existing_levels = results.get(key, {}).get("levels", [])
        completed_c = {l["concurrency"] for l in existing_levels}
        remaining = [c for c in CONCURRENCY_LEVELS if c not in completed_c]
        if not remaining:
            print(f"\n=== {key} already complete ===", flush=True)
            continue
        print(f"\n=== {key} (remaining: c={remaining}) ===", flush=True)
        levels = list(existing_levels)
        for c in remaining:
            metrics = run_bench(args.pod, args.container, isl, osl, c, args.framework)
            if metrics:
                metrics["concurrency"] = c
                levels.append(metrics)
                print(f"    tput={metrics.get('throughput_tok_s')} tok/s  "
                      f"TTFT_p50={metrics.get('ttft_p50_ms')}ms  "
                      f"ITL_p50={metrics.get('itl_p50_ms')}ms", flush=True)
            done += 1
            results[key] = {"isl": isl, "osl": osl, "levels": levels}
            payload = {
                "model": MODEL,
                "framework": args.framework,
                "pod": args.pod,
                "experiment": "framework-comparison-isl-osl-sweep",
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "progress": f"{done}/{total}",
                "combos": results,
            }
            with open(output_path, "w") as f:
                json.dump(payload, f, indent=2)
            print(f"    saved ({done}/{total})", flush=True)

    print(f"\nDone. Results at {output_path}", flush=True)


if __name__ == "__main__":
    main()
